"""Reference implementations kept for differential tests: the straightforward versions the
fast ones in stretto replaced. They are correct by construction and slow."""
from typing import Dict, List, Tuple

from stretto.matching import Alternative, observed_hb
from stretto.trace import Key, Trace


def candidates_full_slow(t, hb=None) -> Tuple[List[Alternative], List[Tuple[str, str]]]:
    """All single-change alternatives at wildcard receives (completed or not). Sends that
    causally follow the receive's completion in the observed run are pruned and listed.
    hb is observed_hb(t), passed in when the caller already has it."""
    hb = hb or observed_hb(t)
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
            if rc is not None and hb(rc, s.issue_node()):
                pruned.append((f"{r} <- {s}", "send causally follows the receive's completion"))
                continue
            r2k = sender_of.get(s.key)
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


