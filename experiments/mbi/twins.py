"""Level-L2 twins of the MBI message-race programs whose outcome depends on a received
value. The four MessageRace_{Recv,Irecv}_{Send,Isend}_nok programs receive nprocs - 1
messages at rank 0 from MPI_ANY_SOURCE and then check the last payload against 3; the
payload is the sender's rank (`buf2 = rank`), so the check is on metadata that the trace
records, and the twin writes it as a branch on the source of the last message: the value
skeleton of docs/proposal-delta.md item 9. The other message-race templates of MBI are
straight-line programs, for which the single-path explorer is already exact."""
from __future__ import annotations
import re
from typing import Dict, Optional

from rematch.dsl import ANY, Abort, If, Irecv, Isend, Program, Recv, Send, Wait

PAYLOAD_CHECK = re.compile(r"MessageRace_(Recv|Irecv)_(Send|Isend)_nok")


def payload_check_twin(np: int, recv_kind: str, send_kind: str, tag: int = 0) -> Program:
    """Rank 0 receives np - 1 messages from ANY, binding each to m0, m1, ...; the program
    aborts unless the last came from rank 3 (`if (buf1 != 3) abort()` with the payload
    mirroring the sender's rank). Ranks 1..np-1 send one message to rank 0."""
    ops = []
    for i in range(np - 1):
        if recv_kind == "Recv":
            ops.append(Recv(ANY, tag, 4, into=f"m{i}"))
        else:
            ops += [Irecv(ANY, tag, 4, req=f"r{i}", into=f"m{i}"), Wait(f"r{i}")]
    last = f"m{np - 2}"
    ops.append(If(lambda env, v=last: v in env and env[v].src != 3, (Abort("last message not from rank 3"),), ()))
    prog: Program = {0: ops}
    for r in range(1, np):
        prog[r] = [Send(0, tag, 4)] if send_kind == "Send" else [Isend(0, tag, 4, req="s"), Wait("s")]
    return prog


def twin_for(name: str, np: int, tag: int = 0) -> Optional[Program]:
    """The twin of an MBI program by name, or None when it has no value dependence. tag is
    the message tag the program uses (read from its trace)."""
    m = PAYLOAD_CHECK.match(name)
    if m:
        return payload_check_twin(np, m.group(1), m.group(2), tag)
    return None
