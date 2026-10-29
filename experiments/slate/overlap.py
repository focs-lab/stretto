#!/usr/bin/env python3
"""Same-envelope point-to-point operations of two threads that were pending at the same time.

A trace of the tracer's format 3 records the start and the completion of each point-to-point
operation in one per-rank order (seq): a nonblocking call when it returns and its completion in
the wait or test that reports it, a blocking call by a `begin` record and its own record. This
scan pairs operations of two threads of one rank with one envelope (direction, communicator,
peer, tag) whose intervals [start, completion] overlap: the second was started before the first
completed.

What an overlap shows. For two blocking calls, that the second thread entered MPI while the
first was inside its call, so no synchronisation of the threads orders the two calls: they are
concurrent, and MPI may match them in either order (MPI 4.1 sections 4.5 and 12.6.1). For a
nonblocking operation the order that matters is that of the postings, and a synchronisation
between a posting and its completion could order a later posting of another thread after it
while the first is still pending; so an overlap shows concurrency only where each operation is
posted and completed by one call of one thread with no synchronisation in between, which has to
be read from the code (it holds for SLATE's permuteRows, internal_swap.cc). Operations completed
unobserved (MPI_Request_free) or by another thread than the one that posted them are therefore
left out and counted apart.

Stretto itself declines traces whose MPI calls are made inside OpenMP tasks (degraded
`threads-tasks`): tasks are not chains, so their happens-before is unknown. This scan needs no
happens-before; physical overlap is a lower bound on the concurrency.

usage: overlap.py TRACE_DIR [SITES]
SITES: a file of lines "rank site name" naming call sites (sites.sh), optional.
Environment: STEP, the largest tag step within one run (below).
"""
import json
import os
import sys
from collections import defaultdict


def load(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def ops_of(recs):
    """(key, start, end, tid, count, site, iseq) per point-to-point operation of one rank."""
    out, pending, begins = [], {}, {}
    for r in recs:
        op, tid = r.get("op"), r.get("tid", 0)
        if op == "begin":
            begins[tid] = r["seq"]
            continue
        if op in ("send", "recv", "irecv"):
            if op == "recv" or (op == "send" and not r.get("mode", "std").startswith("i")):
                start = begins.pop(tid, r["seq"])
                end = r["seq"]
            else:
                start, end = r["seq"], None
            d = "s" if op == "send" else "r"
            peer = r.get("dst") if d == "s" else r.get("src")
            o = {"key": (d, r.get("comm"), peer, r.get("tag")), "start": start, "end": end, "tid": tid,
                 "count": r.get("count"), "site": r.get("site"), "iseq": r.get("iseq"), "apart": None}
            out.append(o)
            if end is None and r.get("req") is not None:
                pending[r["req"]] = o
        elif op == "wait":
            begins.pop(tid, None)
            for dn in r.get("done", []):
                o = pending.pop(dn.get("req"), None)
                if o is not None:
                    o["end"] = r["seq"]
                    if tid != o["tid"]:
                        o["apart"] = "completed by another thread"
        elif op == "reqfree":
            o = pending.pop(r.get("req"), None)
            if o is not None:
                o["apart"] = "freed"
    for o in out:
        if o["end"] is None:
            o["end"] = float("inf")
            o["apart"] = o["apart"] or "never completed"
    # runs: a thread's consecutive operations whose tags stay or grow by at most STEP (the
    # process grid's column count: a rank owns every STEP-th tile column) belong to one call that
    # walks tile columns with tag base+j (SLATE's permuteRows); a run is named by its first and
    # last tags
    step = int(os.environ.get("STEP", "1"))
    last, runs = {}, []
    for o in out:
        prev = last.get(o["tid"])
        if prev is not None and 0 <= o["key"][3] - prev["key"][3] <= step:
            o["run"] = prev["run"]
            runs[o["run"]][1] = o["key"][3]
        else:
            o["run"] = len(runs)
            runs.append([o["key"][3], o["key"][3]])
        last[o["tid"]] = o
    for o in out:
        o["run"] = f"{runs[o['run']][0]}-{runs[o['run']][1]}"
    return out


def short(name):
    """A C++ symbol without its return type and argument list."""
    depth = 0
    for i, ch in enumerate(name):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "(" and depth == 0 and i > 0:
            name = name[:i]
            break
    return name.split(" ")[-1]


def main():
    tdir = sys.argv[1]
    names = {}
    if len(sys.argv) > 2:
        with open(sys.argv[2]) as f:
            for line in f:
                p = line.split(None, 2)
                if len(p) == 3:
                    names[(int(p[0]), int(p[1]))] = short(p[2].strip())
    total = 0
    summary = defaultdict(int)
    apart = defaultdict(int)
    for fn in sorted(os.listdir(tdir)):
        if not fn.startswith("rank-"):
            continue
        rank = int(fn[5:].split(".")[0])
        ops = ops_of(load(os.path.join(tdir, fn)))
        by_key = defaultdict(list)
        for o in ops:
            if o["apart"]:
                apart[(o["apart"], names.get((rank, o["site"]), o["site"]))] += 1
            else:
                by_key[o["key"]].append(o)
        for key, lst in by_key.items():
            lst.sort(key=lambda o: o["start"])
            for i, a in enumerate(lst):
                for b in lst[i + 1:]:
                    if b["start"] > a["end"]:
                        break
                    if a["tid"] != b["tid"]:
                        total += 1
                        sa = names.get((rank, a["site"]), a["site"])
                        sb = names.get((rank, b["site"]), b["site"])
                        runs = tuple(sorted((a["run"], b["run"])))
                        summary[(rank, key[0], key[2], key[3], a["count"] == b["count"], sa, sb, runs)] += 1
    print(f"overlapping same-envelope pairs on different threads: {total}")
    for (rank, d, peer, tag, same, sa, sb, runs), n in sorted(summary.items(), key=str):
        print(f"rank {rank} {'send to' if d == 's' else 'recv from'} {peer} tag {tag} "
              f"({'same' if same else 'different'} count), runs of tags {runs[0]} and {runs[1]}, "
              f"sites {sa} / {sb}: {n}")
    for (why, site), n in sorted(apart.items(), key=str):
        print(f"left out, {why}: {n} operations at site {site}")


if __name__ == "__main__":
    main()
