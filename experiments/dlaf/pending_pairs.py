#!/usr/bin/env python3
"""Same-envelope receives of one rank that were pending at the same time, whatever the thread.

For a trace of the tracer's format 3: per rank, every receive (blocking or not) gets the interval
[posting, completion] in the rank's record order (seq); two receives with one envelope
(communicator, source, tag) whose intervals overlap were both pending, so the order in which they
were posted decided which message each took (MPI 4.1 section 4.5). Whether the program orders the
two postings is not in the trace: pika's tasks synchronise by atomics, and pika's polling
completes requests on whichever worker thread polls, so the tracer records neither. In
dlaf::matrix::copy every send and receive is a task of its own with no dependence on the others
(include/dlaf/matrix/copy.h), which the code says, not the trace. The scan therefore reports where
a colliding pair existed in the run, not that it was unordered.

usage: pending_pairs.py TRACE_DIR
Prints the number of such pairs per rank, with the posting and completing threads.
"""
import json
import os
import sys
from collections import Counter


def receives(path):
    out, pending, begins = [], {}, {}
    with open(path) as f:
        recs = [json.loads(line) for line in f if line.strip()]
    for r in recs:
        op, tid = r.get("op"), r.get("tid", 0)
        if op == "begin":
            begins[tid] = r["seq"]
        elif op in ("recv", "irecv"):
            o = {"key": (r.get("comm"), r.get("src"), r.get("tag")), "start": r["seq"],
                 "end": None, "tid": tid, "done_by": tid, "count": r.get("count")}
            if op == "recv":
                o["start"] = begins.pop(tid, r["seq"])
                o["end"] = r["seq"]
            elif r.get("req") is not None:
                pending[r["req"]] = o
            out.append(o)
        elif op == "wait":
            begins.pop(tid, None)
            for d in r.get("done", []):
                o = pending.pop(d.get("req"), None)
                if o is not None:
                    o["end"], o["done_by"] = r["seq"], tid
    for o in out:
        if o["end"] is None:
            o["end"] = float("inf")
    return out


def main():
    tdir = sys.argv[1]
    total = 0
    for fn in sorted(os.listdir(tdir)):
        if not fn.startswith("rank-"):
            continue
        by_key = {}
        for o in receives(os.path.join(tdir, fn)):
            by_key.setdefault(o["key"], []).append(o)
        pairs, threads, tags = 0, Counter(), Counter()
        for key, lst in by_key.items():
            lst.sort(key=lambda o: o["start"])
            for i, a in enumerate(lst):
                for b in lst[i + 1:]:
                    if b["start"] > a["end"]:
                        break
                    pairs += 1
                    tags[key[2]] += 1
                    threads["posted on " + ("one thread" if a["tid"] == b["tid"] else "two threads")] += 1
                    threads["completed by another thread than the poster"] += \
                        (a["done_by"] != a["tid"]) + (b["done_by"] != b["tid"])
        total += pairs
        print(f"{fn}: {pairs} pending same-envelope receive pairs; tags {dict(sorted(tags.items()))}; "
              f"{dict(threads)}")
    print(f"total {total}")


if __name__ == "__main__":
    main()
