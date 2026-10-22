"""What the first-read analysis knows about MPI calls (argument positions from 0, C bindings).

A call that posts a receive names its buffer; a call that delivers results (a completed
receive's status, a test's flag, a Waitany index, a probe's status or message handle) names
the arguments it writes them to. OUTPUT_ONLY lists the pointer arguments a call writes and does
not read; a call that keeps a pointer for later (captures) is an escape of what it points to;
any other pointer argument of an MPI call counts as a read of what it points to, which covers
forwarding a received buffer (a send of it), MPI_Get_count on a status and every call this
table does not know."""
from __future__ import annotations
from typing import Dict, FrozenSet, Optional, Tuple

RECV_BUFFER: Dict[str, int] = {"MPI_Recv": 0, "MPI_Irecv": 0, "MPI_Mrecv": 0, "MPI_Imrecv": 0,
                               "MPI_Sendrecv": 5, "MPI_Sendrecv_replace": 0}
BLOCKING_RECV = frozenset({"MPI_Recv", "MPI_Mrecv", "MPI_Sendrecv", "MPI_Sendrecv_replace"})
NONBLOCKING_RECV = frozenset({"MPI_Irecv", "MPI_Imrecv"})
COMPLETION = frozenset({"MPI_Wait", "MPI_Waitall", "MPI_Waitany", "MPI_Waitsome",
                        "MPI_Test", "MPI_Testall", "MPI_Testany", "MPI_Testsome"})
PROBES = frozenset({"MPI_Probe", "MPI_Iprobe", "MPI_Mprobe", "MPI_Improbe"})

# the arguments a call writes its results to
RESULTS: Dict[str, Tuple[int, ...]] = {
    "MPI_Recv": (6,), "MPI_Mrecv": (4,), "MPI_Sendrecv": (11,), "MPI_Sendrecv_replace": (8,),
    "MPI_Wait": (1,), "MPI_Waitall": (2,), "MPI_Waitany": (2, 3), "MPI_Waitsome": (2, 3, 4),
    "MPI_Test": (1, 2), "MPI_Testall": (2, 3), "MPI_Testany": (2, 3, 4), "MPI_Testsome": (2, 3, 4),
    "MPI_Probe": (3,), "MPI_Iprobe": (3, 4), "MPI_Mprobe": (3, 4), "MPI_Improbe": (3, 4, 5),
}

OUTPUT_ONLY: Dict[str, FrozenSet[int]] = {k: frozenset(v) for k, v in {
    "MPI_Recv": {0, 6}, "MPI_Irecv": {0, 6}, "MPI_Mrecv": {0, 4}, "MPI_Imrecv": {0, 4},
    "MPI_Sendrecv": {5, 11}, "MPI_Sendrecv_replace": {8},
    "MPI_Wait": {1}, "MPI_Waitall": {2}, "MPI_Waitany": {2, 3}, "MPI_Waitsome": {2, 3, 4},
    "MPI_Test": {1, 2}, "MPI_Testall": {2, 3}, "MPI_Testany": {2, 3, 4}, "MPI_Testsome": {2, 3, 4},
    "MPI_Probe": {3}, "MPI_Iprobe": {3, 4}, "MPI_Mprobe": {3, 4}, "MPI_Improbe": {3, 4, 5},
    "MPI_Isend": {6}, "MPI_Issend": {6}, "MPI_Ibsend": {6}, "MPI_Irsend": {6},
    "MPI_Comm_rank": {1}, "MPI_Comm_size": {1}, "MPI_Get_count": {2}, "MPI_Get_elements": {2},
}.items()}

# calls that keep a pointer argument, or its address as an integer, after they return: MPI or
# the program may then read the memory at a later call that does not name it, so passing a
# tracked object to one of them is an escape. Persistent requests (every MPI_*_init but
# MPI_Init: point-to-point, partitioned and collective) read or write their buffers at each
# MPI_Start; an attached buffer is read by later buffered sends; a window's memory is read by
# other processes' MPI_Get; MPI_Get_address (and MPI_Address, its MPI-1 name) gives the address
# as an MPI_Aint, which a datatype with absolute displacements turns back into an access by a
# call whose buffer is MPI_BOTTOM; an attribute value, a keyval's or a generalised request's or
# a data representation's extra_state is handed back to the program (MPI_Comm_get_attr, the
# callbacks) as a pointer the analysis does not trace
CAPTURES = frozenset({"MPI_Buffer_attach", "MPI_Comm_attach_buffer", "MPI_Session_attach_buffer",
                      "MPI_Win_create", "MPI_Win_attach", "MPI_Get_address", "MPI_Address",
                      "MPI_Comm_set_attr", "MPI_Type_set_attr", "MPI_Win_set_attr", "MPI_Attr_put",
                      "MPI_Comm_create_keyval", "MPI_Type_create_keyval", "MPI_Win_create_keyval",
                      "MPI_Keyval_create", "MPI_Grequest_start", "MPI_Register_datarep"})


def captures(name: str) -> bool:
    """Does MPI call `name` keep a pointer argument after it returns (CAPTURES, or the
    initialisation of a persistent request)?"""
    return name in CAPTURES or (name.endswith("_init") and name != "MPI_Init")


def mpi_name(callee: Optional[str]) -> Optional[str]:
    """'@MPI_Recv' or '@PMPI_Recv' -> 'MPI_Recv'; None for anything else."""
    if callee is None or not callee.startswith("@"):
        return None
    name = callee[1:]
    if name.startswith("PMPI_"):
        name = name[1:]
    return name if name.startswith("MPI_") else None
