"""The analysis of traces whose ranks have several chains (docs/threads.md, section 3).

A chain plays the role of a rank in program order, and "posted before" between two
operations of one process becomes happens-before (stretto.chains.HB): on one chain the record
order, between chains the recorded synchronisation. Two operations of a process that neither
precedes are concurrent: neither non-overtaking clause orders them, and the alternatives here
are those that thread scheduling produces on an implementation that matches in the physical
order of postings and issues. For each concurrent pair whose order decides a match, the
constraint graph gets one orientation (orientation_edges); a pick adds constraints, so an
accepted alternative is realisable, but the other pick might accept an alternative this one
rejects (section 3.6 of docs/threads.md: the check is incomplete there).

Nothing here runs on a trace in which every rank has one chain (Trace.multi is false).
"""
from __future__ import annotations
from bisect import bisect_left, bisect_right
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .chains import physical_key
from .trace import ANY, Key, Probe, Recv, Send, Trace


# ------------------------------------------------------------------------------ order

def before(t: Trace, a, b) -> bool:
    """Does operation a return before operation b is called (same process)?"""
    if t.hb is None:
        return a.rank == b.rank and a.idx < b.idx
    return t.hb.before(a.rank, a.idx, b.rank, b.idx)


def concurrent(t: Trace, a, b) -> bool:
    return a.rank != b.rank and not before(t, a, b) and not before(t, b, a)


def window(t: Trace, x, chain: int) -> Tuple[int, int]:
    """The records of chain that are concurrent with record x of another chain of its process:
    those with index in (lo, hi)."""
    n = len(t.ranks.get(chain, ()))
    lo = t.hb.last_before(x.rank, x.idx, chain)
    hi = t.hb.first_after(x.rank, x.idx, chain, n)
    return lo, hi


def _latest(t: Trace, key, limit: int):
    idx = t._recv_class_idx.get(key)
    if not idx:
        return None
    j = bisect_right(idx, limit) - 1
    return t._recv_class[key][j] if j >= 0 else None


def earlier_competitors(t: Trace, r, s: Send) -> List[Recv]:
    """Trace.earlier_competitors for a receive or probe r of a split rank: per chain and
    envelope class that accepts s, the latest receive that precedes r."""
    out = []
    for c in t.chains_of(r.proc):
        limit = r.idx - 1 if c == r.rank else t.hb.last_before(r.rank, r.idx, c)
        if limit < 0:
            continue
        for src in (s.proc, ANY):
            for tag in (s.tag, ANY):
                x = _latest(t, (c, r.comm, src, tag), limit)
                if x is not None:
                    out.append(x)
    return out


def earlier_sends(t: Trace, r, s: Send) -> List[Send]:
    """Trace.earlier_sends for a send s of a split rank: per chain of the sender and tag r
    accepts, the latest send of s's channel that precedes s and that r could take."""
    out = []
    for c in t.chains_of(s.proc):
        limit = s.idx - 1 if c == s.rank else t.hb.last_before(s.rank, s.idx, c)
        if limit < 0:
            continue
        q = (c, s.dst, s.comm)
        tags = (r.tag,) if r.tag != ANY else t._tags_on.get(q, ())
        for tag in tags:
            key = q + (tag,)
            idx = t._send_tag_idx.get(key)
            if not idx:
                continue
            j = bisect_right(idx, limit) - 1
            sends = t._send_tag[key]
            while j >= 0 and not sends[j].available_to(r):
                j -= 1
            if j >= 0:
                out.append(sends[j])
    return out


def send_before(t: Trace, a: Send, b: Send) -> bool:
    return a.proc == b.proc and before(t, a, b)


def same_channel(a: Send, b: Send) -> bool:
    return a.proc == b.proc and a.dst == b.dst and a.comm == b.comm


# ----------------------------------------------------------------------- matching order

def matching_order_ok(t: Trace, M, kept_recv, ranks: Optional[Set[int]] = None,
                      kept_probe=None, probe_alt: Optional[Dict[Key, Key]] = None) -> Optional[str]:
    """matching.matching_order_ok with "posted before" read as happens-before. Concurrent
    operations carry no obligation here: their order is the orientation edges' business."""
    matched_send: Dict[Key, Key] = {sk: rk for rk, sk in M.items()}
    first_unmatched: Dict[Tuple[int, int, int, int], Recv] = {}
    for r in t.recvs.values():
        if r.key not in M and not r.cancelled and not r.mrecv and (ranks is None or r.rank in ranks):
            k = (r.rank, r.comm, r.src, r.tag)
            if k not in first_unmatched or r.idx < first_unmatched[k].idx:
                first_unmatched[k] = r

    def unmatched_before(x, s: Send) -> bool:
        for c in t.chains_of(x.proc):
            for src in (s.proc, ANY):
                for tag in (s.tag, ANY):
                    u = first_unmatched.get((c, x.comm, src, tag))
                    if u is not None and before(t, u, x):
                        return True
        return False

    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        if ranks is not None and r.rank not in ranks:
            continue
        if not kept_recv(rk):
            continue
        if not r.accepts(s):
            return f"{r} does not accept {s}"
        for s2 in t.earlier_sends(r, s):
            r2k = matched_send.get(s2.key)
            if r2k is None or before(t, r, t.recvs[r2k]):
                return f"non-overtaking: {s2} would reach {r} before {s}"
        if unmatched_before(r, s):
            return f"receiver order: an earlier receive of rank {r.proc} accepts {s} and has no message in this matching"
        for r2 in t.earlier_competitors(r, s):
            if r2.key not in M:
                continue
            s2 = t.sends[M[r2.key]]
            if same_channel(s2, s) and send_before(t, s, s2):
                return f"receiver order: earlier {r2} would take {s} before {s2}"
    if kept_probe is not None:
        probe_alt = probe_alt or {}
        for q in t.probes.values():
            if (ranks is not None and q.rank not in ranks) or not q.completed or not (q.flag or q.key in probe_alt) \
                    or not kept_probe(q):
                continue
            sk = probe_alt.get(q.key, q.seen)
            if sk is None:
                continue
            s = t.sends[sk]
            for s2 in t.earlier_sends(q, s):
                r2k = matched_send.get(s2.key)
                if r2k is None or before(t, q, t.recvs[r2k]):
                    return f"probe order: {s2} would be reported by {q} before {s}"
            if unmatched_before(q, s):
                return f"probe order: an earlier receive of rank {q.proc} accepts {s} and has no message in this matching"
            rk = matched_send.get(sk)
            if rk is not None and t.recvs[rk].proc == q.proc and before(t, t.recvs[rk], q) and not t.recvs[rk].mrecv:
                return f"probe order: {t.recvs[rk]}, posted before {q}, takes {s}"
    return None


# ------------------------------------------------------------------- graph edges

def _recvs_in_window(t: Trace, x, s: Send, chain: int) -> Iterable[Recv]:
    """Receives of chain, concurrent with record x, whose envelope accepts s."""
    lo, hi = window(t, x, chain)
    if hi - lo <= 1:
        return
    for src in (s.proc, ANY):
        for tag in (s.tag, ANY):
            key = (chain, x.comm, src, tag)
            idx = t._recv_class_idx.get(key)
            if not idx:
                continue
            for j in range(bisect_right(idx, lo), bisect_left(idx, hi)):
                yield t._recv_class[key][j]


def _sends_in_window(t: Trace, s: Send, chain: int, r) -> Iterable[Send]:
    """Sends of s's channel on chain, concurrent with s, that r accepts."""
    lo, hi = window(t, s, chain)
    if hi - lo <= 1:
        return
    q = (chain, s.dst, s.comm)
    for tag in ((r.tag,) if r.tag != ANY else t._tags_on.get(q, ())):
        key = q + (tag,)
        idx = t._send_tag_idx.get(key)
        if not idx:
            continue
        for j in range(bisect_right(idx, lo), bisect_left(idx, hi)):
            x = t._send_tag[key][j]
            if r.accepts(x) and not x.cancelled:
                yield x


def thread_edges(t: Trace, M, node_set, mnode: Dict[Key, tuple], probe_alt=None, orient: bool = True,
                 cut=None) -> List[Tuple[tuple, tuple]]:
    """The edges a split rank adds to the constraint graph: recorded synchronisation, and, if
    orient, one orientation per concurrent pair whose order decides a match of M or a
    probe's report (module docstring)."""
    from .matching import match_node
    edges: List[Tuple[tuple, tuple]] = []
    for a, ia, b, ib in t.hb.edges:
        edges.append(((a, ia, "c"), (b, ib, "i")))
    if not orient:
        return edges
    recv_of = {sk: rk for rk, sk in M.items()}

    def kept(x) -> bool:
        return x.issue_node() in node_set

    def phys_before(a, b) -> bool:
        return physical_key(t, a) < physical_key(t, b)

    def match_of(rk):
        return mnode.get(rk)

    # receives of a split rank: r takes s, x on another chain is concurrent and accepts s
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        m = match_of(rk)
        if m is None or r.proc not in t.chains:
            continue
        for c in t.chains_of(r.proc):
            if c == r.rank:
                continue
            for x in _recvs_in_window(t, r, s, c):
                if x.cancelled or x.mrecv or not kept(x) or not x.accepts(s):
                    continue
                s2k = M.get(x.key)
                s2 = t.sends[s2k] if s2k is not None else None
                if s2 is not None and same_channel(s2, s) and send_before(t, s2, s):
                    continue                     # the sender-side order edge says it already
                mx = match_of(x.key) if s2 is not None else None
                if s2 is not None and same_channel(s2, s):
                    # two messages of one channel: the postings follow the order of the sends
                    # (sends keep the order in which MPI saw them, unless happens-before says)
                    first_x = mx is not None and not send_before(t, s, s2) and phys_before(s2, s)
                else:
                    first_x = s2 is not None and mx is not None and phys_before(x, r)
                if not first_x:
                    edges.append((r.issue_node(), x.issue_node()))     # r is posted first
                else:
                    edges.append((mx, m))                               # x is matched first
    # sends of a split rank: r takes s, s' of the channel on another chain is concurrent
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        m = match_of(rk)
        if m is None or s.proc not in t.chains:
            continue
        for c in t.chains_of(s.proc):
            if c == s.rank:
                continue
            for s2 in _sends_in_window(t, s, c, r):
                if s2.issue_node() not in node_set:
                    continue
                r2k = recv_of.get(s2.key)
                m2 = match_of(r2k) if r2k is not None else None
                if m2 is None or before(t, r, t.recvs[r2k]) or not phys_before(s2, s):
                    edges.append((s.issue_node(), s2.issue_node()))    # s is issued first
                else:
                    edges.append((m2, m))                               # s2 is matched first
    # probes: a probe reports a message no concurrent receive has taken yet
    probe_alt = probe_alt or {}
    for p in t.probes.values():
        sk = probe_alt.get(p.key, p.seen)
        pc = p.complete_node()
        if sk is None or pc is None or pc not in node_set or p.proc not in t.chains:
            continue
        s = t.sends[sk]
        if s.proc in t.chains:
            # a message of the channel that a concurrent chain of the sender issued: it was
            # issued after s, or matched before the probe looked
            for c in t.chains_of(s.proc):
                if c == s.rank:
                    continue
                for s2 in _sends_in_window(t, s, c, p):
                    if s2.issue_node() not in node_set:
                        continue
                    r2k = recv_of.get(s2.key)
                    m2 = match_of(r2k) if r2k is not None else None
                    if m2 is not None and phys_before(s2, s):
                        edges.append((m2, pc))
                    else:
                        edges.append((s.issue_node(), s2.issue_node()))
        for c in t.chains_of(p.proc):
            if c == p.rank:
                continue
            for x in _recvs_in_window(t, p, s, c):
                if x.cancelled or x.mrecv or not kept(x) or not x.accepts(s):
                    continue
                if M.get(x.key) == sk:
                    mx = match_of(x.key)
                    if mx is not None:
                        edges.append((pc, mx))                          # matched after the report
                    continue
                s2k = M.get(x.key)
                mx = match_of(x.key) if s2k is not None else None
                if mx is not None and phys_before(x, p):
                    edges.append((mx, pc))
                else:
                    edges.append((pc, x.issue_node()))
    return edges


# -------------------------------------------------------------------------- candidates

def candidates(t: Trace) -> Tuple[list, List[Tuple[str, str]]]:
    """Single-change alternatives of a trace with split ranks (docs/threads.md 3.3): for every
    receive, per source, the sends it could take that no receive before it took and that no
    other such send precedes (one per sending chain at most). A send taken in the run by a
    concurrent receive gives a steal, which leaves that receive without it, and a swap when
    that receive accepts the first one's message."""
    from .matching import Alternative, Overlay, _follows, build_graph
    g = build_graph(t, t.observed, cut=None, B=None, thread_orient=False)
    hb = g.reaches
    sender_of: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
    out: list = []
    pruned: List[Tuple[str, str]] = []
    seen_swaps: Set[Tuple[Key, Key]] = set()
    for r in sorted(t.recvs.values(), key=lambda r: r.key):
        if r.cancelled or r.mrecv:
            continue
        s_obs = t.observed_send(r)
        rc = r.complete_node()
        split_here = r.proc in t.chains
        for src in t.sources_to(r.proc, r.comm):
            if r.src != ANY and src != r.src:
                continue
            if not (r.wildcard or split_here or src in t.chains):
                continue
            firsts: List[Send] = []
            for c in t.chains_of(src):
                for s in t._sends_of.get(c, ()):
                    if s.dst != r.proc or s.comm != r.comm or not s.available_to(r):
                        continue
                    r2k = sender_of.get(s.key)
                    if r2k is not None and before(t, t.recvs[r2k], r):
                        continue                 # taken before r is posted
                    firsts.append(s)
                    break
            front = [s for s in firsts if not any(o is not s and send_before(t, o, s) for o in firsts)]
            for s in front:
                if s_obs is not None and s.key == s_obs.key:
                    continue
                r2k = sender_of.get(s.key)
                if rc is not None and _follows(hb, rc, s, r2k):
                    pruned.append((f"{r} <- {s}", "send causally follows the receive's completion"))
                    continue
                M = Overlay(t.observed, {r.key: s.key})
                if r2k is None:
                    out.append(Alternative(r.key, s.key, M, "steal", {r.key}, None))
                    continue
                r2 = t.recvs[r2k]
                if s_obs is not None and r2.accepts(s_obs) and not r2.mrecv and concurrent(t, r, r2):
                    pair = tuple(sorted((r.key, r2k)))
                    if pair not in seen_swaps:
                        seen_swaps.add(pair)
                        out.append(Alternative(r.key, s.key, M.with_(changed={r2k: s_obs.key}), "swap",
                                               {r.key, r2k}, r2k))
                out.append(Alternative(r.key, s.key, M.with_(removed=[r2k]), "steal", {r.key}, r2k))
    return out, pruned


def probe_candidates(t: Trace) -> List[Tuple[Probe, Send]]:
    """Other messages a probe that found one could have reported: as for receives, per source
    the sends it accepts that no receive before it took, minimal in their channel."""
    from .matching import build_graph
    hb = build_graph(t, t.observed, cut=None, B=None, thread_orient=False).reaches
    sender_of: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
    out = []
    for p in sorted(t.probes.values(), key=lambda p: p.key):
        if not p.flag or p.seen is None or p.complete_node() is None:
            continue
        if not (p.wildcard or p.proc in t.chains or t.sends[p.seen].proc in t.chains):
            continue
        for src in t.sources_to(p.proc, p.comm):
            if p.src != ANY and src != p.src:
                continue
            firsts = []
            for c in t.chains_of(src):
                for s in t._sends_of.get(c, ()):
                    if not p.accepts(s) or s.cancelled:
                        continue
                    if s.reserved_idx is not None and not s.available_to(p):
                        continue
                    r2k = sender_of.get(s.key)
                    if r2k is not None and before(t, t.recvs[r2k], p):
                        continue
                    firsts.append(s)
                    break
            for s in firsts:
                if s.key == p.seen or any(o is not s and send_before(t, o, s) for o in firsts):
                    continue
                if not hb(p.complete_node(), s.issue_node()):
                    out.append((p, s))
    return out


# --------------------------------------------------------------- observed thread findings

def observed_findings(t: Trace, tasks: bool = False) -> List[tuple]:
    """(class, title, details) of the errors §12.6.2 and §12.2.1 of MPI 4.1 name, read from the
    chains and their happens-before (docs/threads.md 3.7). tasks: MPI was called inside OpenMP
    tasks, which are no chains of their own, so the happens-before is not known and only the
    thread level is checked."""
    out = []
    level = t.thread_level
    for rank, chs in sorted(t.chains.items()):
        if tasks:
            chs = []                     # only the thread level below (and every chain for it)
        # collectives of one communicator on two chains that neither precedes
        by_comm: Dict[int, list] = {}
        for c in t.colls:
            if c.proc == rank:
                by_comm.setdefault(c.comm, []).append(c)
        for comm, cs in (sorted(by_comm.items()) if chs else []):
            hit = next(((a, b) for i, a in enumerate(cs) for b in cs[i + 1:] if concurrent(t, a, b)), None)
            if hit:
                a, b = hit
                out.append(("2-thread-collective", f"rank {rank}: {a.name} and {b.name} on comm {comm} are called by "
                            f"chains {a.rank} and {b.rank} with no synchronisation between them",
                            ["MPI 4.1 §12.6.2: matching of collective calls is by their order at each process; "
                             "concurrent threads must order them by synchronisation"]))
        # two waits or tests on one request that neither precedes
        waits = [rec for c in chs for rec in t.ranks[c] if rec.op == "wait" and rec.raw is not None]
        reqs_of = lambda rec: {q for q in rec.raw.get("reqs", []) if q is not None and q >= 0}
        found = None
        for i, a in enumerate(waits):
            for b in waits[i + 1:]:
                if a.rank != b.rank and reqs_of(a) & reqs_of(b) and concurrent(t, a, b):
                    found = (a, b)
                    break
            if found:
                break
        if found:
            a, b = found
            out.append(("2-thread-wait", f"rank {rank}: chains {a.rank} and {b.rank} complete request "
                        f"{sorted(reqs_of(a) & reqs_of(b))[0]} concurrently",
                        ["MPI 4.1 §12.6.2: a program in which two threads block, waiting on the same request, is erroneous"]))
        # finalize on another thread, or before the other threads' calls
        fin = [rec for c in chs for rec in t.ranks[c] if rec.op == "finalize"]
        for f in fin:
            if f.rank != rank:
                out.append(("2-thread-finalize", f"rank {rank}: MPI_Finalize called by chain {f.rank}, not by the "
                            "thread that initialised MPI", ["MPI 4.1 §12.6.2"]))
            late = next((rec for c in chs if c != f.rank for rec in t.ranks[c]
                         if rec.op not in ("overflow", "guard", "comm") and not before(t, rec, f)), None)
            if late is not None:
                out.append(("2-thread-finalize", f"rank {rank}: MPI_Finalize is not preceded by chain {late.rank}'s "
                            f"record #{late.idx} ({late.op})", ["MPI 4.1 §12.6.2: MPI_Finalize only after all "
                                                                 "threads have completed their MPI calls"]))
        if level in (0, 1) and len(t.chains[rank]) > 1:
            name = "MPI_THREAD_SINGLE" if level == 0 else "MPI_THREAD_FUNNELED"
            out.append(("2-thread-level", f"rank {rank}: {len(t.chains[rank]) - 1} thread(s) besides the main one call MPI "
                        f"under {name}", ["MPI 4.1 §12.2.1"]))
    for rank in sorted(set(t.thread_overlap)):
        if level < 3:
            out.append(("2-thread-level", f"rank {rank}: two threads were inside MPI calls at once under "
                        f"thread level {level}", ["MPI 4.1 §12.2.1: under MPI_THREAD_SERIALIZED calls are not made "
                                                  "concurrently from two threads"]))
    return out


def chain_note(t: Trace) -> str:
    parts = []
    for rank, chs in sorted(t.chains.items()):
        parts.append(f"rank {rank}: " + ", ".join(f"{c} ({t.chain_name.get(c, '?')})" for c in chs))
    return "chains (id, the thread's name in the run): " + "; ".join(parts)


# ------------------------------------------------------------------ contained deadlocks

def contained_deadlock(t: Trace, M, changed: Set[Key], cut: Dict[int, int], issued: Dict[int, int],
                       B: Optional[int], rule1: Optional[Dict[int, int]] = None) -> Optional[Dict[int, str]]:
    """deadlock.contained_deadlock over chains (docs/threads.md, theorem T7 with chains): a
    chain also stops at a synchronisation whose predecessor is not reached, and the partners
    of an operation are the chains of the ranks that could complete it."""
    from .deadlock import _completed_at
    from .horizon import _cidx, coll_can_complete
    from .matching import send_blocks
    lengths = {ch: len(recs) for ch, recs in t.ranks.items()}
    if rule1 is None:
        rule1 = {}
        for rk in changed:
            r = t.recvs[rk]
            rule1[r.rank] = min(rule1.get(r.rank, _cidx(r) + 1), _cidx(r) + 1)

    def in_h(ch: int, idx: int) -> bool:
        return idx < cut[ch]

    def sent(s: Send) -> bool:
        return in_h(s.rank, s.idx) or issued.get(s.rank) == s.idx

    def chains(procs: Iterable[int]) -> Set[int]:
        return {c for p in procs for c in t.chains_of(p)}

    recv_of = {sk: rk for rk, sk in M.items()}
    terminated = set()
    for ch, n in lengths.items():
        if ch in rule1 and n >= rule1[ch] or getattr(cut, "dead", {}).get(ch) == n:
            continue                         # its continuation is not known: it may not have ended
        if cut[ch] == n and n and (t.ranks[ch][-1].op == "finalize" or t.proc.get(ch, ch) != ch):
            terminated.add(ch)

    def senders(x) -> Set[int]:
        if x.src != ANY:
            return chains([x.src])
        return chains(set(t.comm_ranks.get(x.comm, sorted(t.chains_of(x.proc)))) - {x.proc})

    def message_present(x) -> bool:
        for src in ([x.src] if x.src != ANY else t.sources_to(x.proc, x.comm)):
            for s in t.sends_to(src, x.proc, x.comm):
                if not sent(s) or not x.accepts(s) or s.cancelled:
                    continue
                rk = recv_of.get(s.key)
                if rk is None or not in_h(rk[0], t.recvs[rk].idx):
                    return True
        return False

    def receive_present(s: Send) -> bool:
        for c in t.chains_of(s.dst):
            for x in t.recvs_of(c):
                if not in_h(x.rank, x.idx):
                    break
                if x.cancelled or not x.accepts(s):
                    continue
                sk = M.get(x.key)
                if sk is None or not sent(t.sends[sk]):
                    return True
        return False

    def needs(x) -> Optional[Set[int]]:
        if isinstance(x, Recv):
            if x.mrecv:
                return None
            sk = M.get(x.key)
            if sk is not None and sent(t.sends[sk]):
                return None
            return None if message_present(x) else senders(x)
        if isinstance(x, Send):
            if not send_blocks(x, B):
                return None
            rk = recv_of.get(x.key)
            if rk is not None and in_h(rk[0], t.recvs[rk].idx):
                return None
            return None if receive_present(x) else chains([x.dst])
        if hasattr(x, "seen"):
            if x.kind not in ("probe", "mprobe"):
                return None
            return None if message_present(x) else senders(x)
        if hasattr(x, "ordinal"):
            group = [c for c in t.colls if c.comm == x.comm and c.ordinal == x.ordinal]
            have = {c.proc for c in group if in_h(c.rank, c.idx)}
            need = set(t.comm_members.get(x.comm, [c.proc for c in group]))
            pos = {r: i for i, r in enumerate(t.comm_ranks.get(x.comm, []))}
            if coll_can_complete(x, have, need, pos):
                return None
            return chains(need - have)
        return None

    # the synchronisation edges into each record
    into: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for a, ia, b, ib in t.hb.edges:
        into.setdefault((b, ib), []).append((a, ia))

    stops: Dict[int, Tuple[str, Set[int]]] = {}
    for ch, n in lengths.items():
        c = cut[ch]
        if c >= n or (ch in rule1 and c >= rule1[ch]):
            continue
        rec = t.ranks[ch][c]
        dead = getattr(cut, "dead", {})
        waiting = {a for a, ia in into.get((ch, c), ())
                   if ia + 1 > cut[a] or (ia + 1 == cut[a] and dead.get(a) == cut[a])}
        if waiting and issued.get(ch) != c:
            # it waits for a synchronisation point the horizon does not reach. Milestone A does not
            # claim deadlocks through synchronisation: the trace does not order the synchronisation
            # points between two records of a chain, so rule 8 treats all of them as unreached
            # once one is (docs/threads.md, section 9), and such a wait may be an artefact of that
            continue
        if rec.op not in ("send", "recv", "coll", "probe", "wait"):
            continue
        kind = rec.raw.get("kind") if rec.op == "wait" and rec.raw is not None else None
        if kind is not None and (kind.startswith("test") or kind == "getstatus"):
            continue
        if rec.op == "probe" and rec.raw is not None and rec.raw.get("kind") in ("iprobe", "improbe"):
            continue
        if kind in ("waitany", "waitsome"):
            ops = [x for q in rec.raw.get("reqs", []) if q is not None and q >= 0
                   for x in (t.recv_by_req(ch, q), t._by_req.get(("s", ch, q))) if x is not None]
        else:
            ops = _completed_at(t).get((ch, c), [])
        if not ops:
            continue
        reqs = [needs(x) for x in ops]
        if kind in ("waitany", "waitsome"):
            if any(r is None for r in reqs):
                continue
            partners = set().union(*reqs)
        else:
            blocked = [r for r in reqs if r is not None]
            if not blocked:
                continue
            partners = set().union(*blocked)
        stops[ch] = (f"{rec.op} #{rec.idx}", partners - {ch})

    D = set(stops)
    while True:
        drop = {r for r in D if not stops[r][1] <= D | terminated}
        if not drop:
            break
        D -= drop
    if not D:
        return None
    return {r: stops[r][0] for r in sorted(D)}
