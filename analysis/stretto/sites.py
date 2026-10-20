"""Call sites of a trace: the tracer records, for each MPI call made from user code, the
object and the return address of the call (site records); this module names them in the
source with addr2line, when the object is still there and carries debug information."""
from __future__ import annotations
import os
import shutil
import subprocess
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .trace import Trace

ET_DYN = 3


@lru_cache(maxsize=None)
def _is_dyn(obj: str) -> Optional[bool]:
    """Is obj position-independent (ELF type ET_DYN: a PIE executable or a shared library),
    so that addresses in it are offsets from its load address? None if it cannot be read."""
    try:
        with open(obj, "rb") as fh:
            head = fh.read(18)
    except OSError:
        return None
    if len(head) < 18 or head[:4] != b"\x7fELF":
        return None
    return int.from_bytes(head[16:18], "little" if head[5] == 1 else "big") == ET_DYN


@lru_cache(maxsize=None)
def symbolize(obj: str, addr: int, base: int) -> str:
    """'function at file:line' for the call that returns to addr, or '' if unknown."""
    dyn = _is_dyn(obj)
    if dyn is None or shutil.which("addr2line") is None:
        return ""
    pc = (addr - base if dyn else addr) - 1          # inside the call instruction
    try:
        out = subprocess.run(["addr2line", "-f", "-C", "-e", obj, hex(pc)], capture_output=True, text=True, timeout=30).stdout.split("\n")
    except (OSError, subprocess.SubprocessError):
        return ""
    if len(out) < 2 or out[1].startswith("??"):
        return ""
    return f"{out[0]} at {out[1].split(' (')[0]}"


def sites(t: Trace) -> List[Tuple[str, str, int]]:
    """(label, source location, number of records) per distinct site, most records first."""
    count: Dict[Tuple[str, int], int] = {}
    rec_of: Dict[Tuple[str, int], dict] = {}
    for (rank, sid), rec in t.sites.items():
        key = (rec.get("obj", "?"), rec.get("addr", 0) - rec.get("base", 0))
        rec_of.setdefault(key, rec)
    for rank, recs in t.ranks.items():
        for r in recs:
            s = t.site_of(rank, r.idx)
            if s is not None:
                count[s] = count.get(s, 0) + 1
    out = []
    for key, n in count.items():
        rec = rec_of[key]
        label = f"{os.path.basename(key[0])}+{key[1]:#x}"
        out.append((label, symbolize(rec.get("obj", ""), rec.get("addr", 0), rec.get("base", 0)), n))
    return sorted(out, key=lambda x: -x[2])
