"""MPI type signatures. The tracer writes a signature per datatype: a named type by its
name (``MPI_INT``), a derived type flattened into its basic constituents as
``name*count`` items separated by commas (``MPI_INT*4,MPI_DOUBLE*2``), or an opaque
``derived:...`` description when it cannot flatten. Two transfers are compatible when the
send's signature, repeated count times, is an initial segment of the receive's (MPI 4.1
§4.3.1); an opaque signature decides nothing. Two types are not compared by name:
MPI_PACKED, which a message of any type may be received as, and whose sent contents
carry types the trace does not show (§4.3.1, §6.2), and MPI_BYTE, untyped storage, which
is compared with MPI_BYTE only, by length (§4.3.1)."""
from __future__ import annotations
from typing import List, Optional, Tuple


def flatten(sig: Optional[str], count: int) -> Optional[List[Tuple[str, int]]]:
    """[(basic type, run length)] for count copies of the signature; None if unknown."""
    if sig is None or sig in ("null", "?") or sig.startswith("derived:"):
        return None
    items: List[Tuple[str, int]] = []
    for part in sig.split(","):
        name, _, n = part.partition("*")
        try:
            k = int(n) if n else 1
        except ValueError:
            return None
        if not name or k <= 0:
            return None
        if items and items[-1][0] == name:
            items[-1] = (name, items[-1][1] + k)
        else:
            items.append((name, k))
    if not items:
        return None
    out: List[Tuple[str, int]] = []
    for _ in range(count):
        for name, k in items:
            if out and out[-1][0] == name:
                out[-1] = (name, out[-1][1] + k)
            else:
                out.append((name, k))
    return out


PACKED, BYTE = "MPI_PACKED", "MPI_BYTE"


def compatible(send_sig: Optional[str], send_count: int, recv_sig: Optional[str], recv_count: int) -> Optional[bool]:
    """True/False when both signatures are known, None when either is opaque. A receive of
    MPI_PACKED takes any message (its length is the count check's business); a send of
    MPI_PACKED carries packed data of types the trace does not record; MPI_BYTE against a
    typed signature is left undecided rather than reported, as programs commonly move typed
    data as bytes on one side."""
    a, b = flatten(send_sig, send_count), flatten(recv_sig, recv_count)
    if a is None or b is None:
        return None
    sa, sb = {name for name, _ in a}, {name for name, _ in b}
    if sb == {PACKED}:
        return True
    if PACKED in sa or PACKED in sb:
        return None
    if (BYTE in sa) != (BYTE in sb) or (BYTE in sa and (sa != {BYTE} or sb != {BYTE})):
        return None
    i = 0
    for name, k in a:
        if i >= len(b) or b[i][0] != name:
            return False
        have = b[i][1]
        while k > 0:
            if have >= k:
                have -= k
                k = 0
            else:
                k -= have
                i += 1
                if i >= len(b) or b[i][0] != name:
                    return False
                have = b[i][1]
        if have == 0:
            i += 1
        else:
            b[i] = (name, have)
    return True
