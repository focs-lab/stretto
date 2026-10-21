"""What the first-read analysis knows about MPI calls (argument positions from 0, C bindings).

A call that posts a receive names its buffer; a call that delivers results (a completed
receive's status, a test's flag, a Waitany index, a probe's status or message handle) names
the arguments it writes them to. OUTPUT_ONLY lists the pointer arguments a call writes and does
not read; any other pointer argument of an MPI call counts as a read of what it points to,
which covers forwarding a received buffer (a send of it), MPI_Get_count on a status and every
call this table does not know."""
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

# calls that keep a pointer argument after they return (persistent requests, attached buffers):
# MPI may read the memory at a later call that does not name it, so passing a tracked object
# to one of them is an escape
CAPTURES = frozenset({"MPI_Send_init", "MPI_Ssend_init", "MPI_Bsend_init", "MPI_Rsend_init", "MPI_Recv_init",
                      "MPI_Psend_init", "MPI_Precv_init", "MPI_Buffer_attach", "MPI_Comm_attach_buffer",
                      "MPI_Session_attach_buffer"})


def mpi_name(callee: Optional[str]) -> Optional[str]:
    """'@MPI_Recv' or '@PMPI_Recv' -> 'MPI_Recv'; None for anything else."""
    if callee is None or not callee.startswith("@"):
        return None
    name = callee[1:]
    if name.startswith("PMPI_"):
        name = name[1:]
    return name if name.startswith("MPI_") else None
