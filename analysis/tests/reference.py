"""Reference implementations kept for differential tests: the straightforward versions the
fast ones in stretto replaced. They are correct by construction and slow."""
from typing import Dict, List, Optional, Tuple

from stretto.matching import Alternative, Graph, Node, build_graph
from stretto.trace import Key, Trace


def reaches_without(g: Graph, a: Node, b: Node, skip: Optional[Tuple[Node, Node]]) -> bool:
    """Is there a path from a to b in g that does not use the edge skip?"""
    seen, stack = {a}, [a]
    while stack:
        x = stack.pop()
        for y in g.succ.get(x, ()):
            if (x, y) == skip or y in seen:
                continue
            if y == b:
                return True
            seen.add(y)
            stack.append(y)
    return False


def candidates_full_slow(t) -> Tuple[List[Alternative], List[Tuple[str, str]]]:
    """All single-change alternatives at wildcard receives (completed or not). Sends that
    causally follow the receive's completion in the observed graph at B = inf are pruned and
    listed; the path may not use the edge the alternative replaces, the ready-send edge from
    the receive that took the send in the run (matching._follows)."""
    g = build_graph(t, t.observed, None, None)
    sender_of: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
    out: List[Alternative] = []
    pruned: List[Tuple[str, str]] = []
    for r in sorted(t.recvs.values(), key=lambda r: r.key):
        if not r.wildcard:
            continue
        s_obs = t.observed_send(r)
        rc = r.complete_node()
        for s in sorted(t.sends.values(), key=lambda s: s.key):
            if s_obs is not None and s.key == s_obs.key:
                continue
            if not s.available_to(r):
                continue
            r2k = sender_of.get(s.key)
            skip = (t.recvs[r2k].issue_node(), s.issue_node()) if s.ready_mode and r2k is not None else None
            if rc is not None and reaches_without(g, rc, s.issue_node(), skip):
                pruned.append((f"{r} <- {s}", "send causally follows the receive's completion"))
                continue
            M = dict(t.observed)
            M[r.key] = s.key
            if r2k is not None:
                r2 = t.recvs[r2k]
                if s_obs is not None and r2.accepts(s_obs):
                    M2 = dict(M)
                    M2[r2k] = s_obs.key
                    out.append(Alternative(r.key, s.key, M2, "swap", {r.key, r2k}, r2k))
                del M[r2k]
                out.append(Alternative(r.key, s.key, M, "steal", {r.key}, r2k))
            else:
                out.append(Alternative(r.key, s.key, M, "steal", {r.key}, None))
    return out, pruned
