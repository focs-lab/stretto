"""Every pair of collectives of one communicator that two threads of one process call with no
recorded synchronisation between them, in a trace of the threads (format 3). `stretto analyze`
reports the first such pair per process and communicator (2-thread-collective); this lists them
all, grouped by the call sites, operation and size of the two sides (README, "Other collectives
on MPI_COMM_WORLD").

    PYTHONPATH=analysis python3 experiments/uintah/pairs.py TRACEDIR

Run where the traced objects are at the paths the trace names (the container that recorded it):
sites are named with addr2line, whose function name is used even without debug information.
"""
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from functools import lru_cache

from stretto import trace
from stretto.threads import concurrent

# the loader drops the dictionaries of collective records once it has built Coll objects; keep
# them, for the count and type size of each call
_set = trace.Rec.__setattr__


def _keep_coll_raw(self, k, v):
    if k == "raw" and v is None and self.op == "coll" and getattr(self, "raw", None) is not None:
        return
    _set(self, k, v)


trace.Rec.__setattr__ = _keep_coll_raw


@lru_cache(maxsize=None)
def fn_at(obj: str, off: int) -> str:
    """The function holding the return address obj+off (a position-independent object)."""
    if not os.path.exists(obj) or shutil.which("addr2line") is None:
        return "?"
    out = subprocess.run(["addr2line", "-f", "-C", "-e", obj, hex(off - 1)], capture_output=True, text=True).stdout.split("\n")
    return out[0] if out and out[0] else "?"


def site(t, c):
    """(object, offset) of collective c's call site. A chain's records carry the site ids of
    their process: the tracer numbers sites per process."""
    raw = t.ranks[c.rank][c.idx].raw or {}
    s = t.sites.get((c.proc, raw.get("site", -1)))
    if s is None:
        return t.site_of(c.rank, c.idx)
    return s.get("obj", "?"), s.get("addr", 0) - s.get("base", 0)


def side(t, c):
    raw = t.ranks[c.rank][c.idx].raw or {}
    s = site(t, c)
    where = f"{os.path.basename(s[0])}+{s[1]:#x} {fn_at(s[0], s[1])}" if s else "no site"
    return f"{c.name}(count {raw.get('count', '?')} x {raw.get('tsize', '?')} B) at {where}"


def main(tracedir: str) -> int:
    t = trace.load(tracedir)
    if not t.chains:
        print("no process with several chains: nothing to pair")
        return 0
    world = trace.WORLD if hasattr(trace, "WORLD") else 0
    groups = Counter()
    procs = defaultdict(set)
    example = {}
    for proc in sorted(t.chains):
        by_comm = defaultdict(list)
        for c in t.colls:
            if c.proc == proc:
                by_comm[c.comm].append(c)
        for comm, cs in by_comm.items():
            for i, a in enumerate(cs):
                for b in cs[i + 1:]:
                    if concurrent(t, a, b):
                        sa, sb = sorted((side(t, a), side(t, b)))
                        key = ("world" if comm == world else f"comm {comm} ({len(t.comm_members.get(comm, []))} ranks)", sa, sb)
                        groups[key] += 1
                        procs[key].add(proc)
                        example.setdefault(key, (proc, a.ordinal, b.ordinal))
    print(f"{tracedir}: {sum(groups.values())} concurrent pairs in {len(groups)} groups")
    for key, n in groups.most_common():
        comm, sa, sb = key
        p, oa, ob = example[key]
        print(f"{n:6d}  {comm}, processes {sorted(procs[key])}\n        {sa}\n        {sb}\n"
              f"        (e.g. process {p}, ordinals {oa} and {ob} on the communicator)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
