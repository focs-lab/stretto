#!/usr/bin/env python3
"""The wrong-worker alternatives of the traces of run.sh, by I/O concentrator instance.

t_subfiling_vfd opens its files one after another; for each, rank 0's main thread creates four
worker threads and then the I/O concentrator thread (chains 0.0 to 0.4 for the first file, 0.5 to
0.9 for the second, ...: instance k // 5 of chain 0.k). An instance ends on an atomic flag that
the tracer does not record, so happens-before leaves the workers of two instances concurrent, and
L1 also predicts that a worker of one file takes data that a worker of another file took: an
alternative no run can realise (an unrecorded synchronisation, docs/threads.md section 2.3). The
alternatives whose two receives (the changed one and the one that took its message in the run)
are on workers of one instance are the race the fix removes.

usage: instances.py --out CSV TRACE_DIR ...   writes one row per L1 alternative at a receive of
tag WRITE_INDEP_DATA (0xd8da02): the trace, replay_sample.py's finding, cls, pin and site
columns, the two chains' names, same_instance and a regular expression that matches the
alternative's title alone (for replay_sample.py --only). With --only-same TRACE_DIR it prints
instead the alternation of those of that trace's alternatives that lie within one instance."""
import argparse
import csv
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto import trace  # noqa: E402
from stretto.confirm import chain_label  # noqa: E402
from stretto.report import analyze  # noqa: E402

DATA_TAG = 14211586


def instance(name: str):
    parts = name.split(".")
    return int(parts[1]) // 5 if len(parts) == 2 and parts[0] == "0" and parts[1].isdigit() else None


def title_regex(title: str) -> str:
    """The alternative's receive and the send it takes, as a regular expression of its title."""
    m = re.match(r"(recv\([0-9.]+, \d+\)) .*? <- (send\([0-9.]+, \d+\))", title)
    return "^" + re.escape(m.group(1)) + " .*? <- " + re.escape(m.group(2)) if m else "^" + re.escape(title)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--only-same", action="store_true", help="print the --only alternation of the one trace given")
    ap.add_argument("traces", nargs="+")
    args = ap.parse_args()
    rows = []
    for d in args.traces:
        t = trace.load(d)
        for f in analyze(t, with_sp=False).findings:
            if f.level != "L1" or f.recv is None or f.send is None or t.recvs[f.recv].tag != DATA_TAG:
                continue
            victim = next((k for k, s in t.observed.items() if s == f.send and k != f.recv), None)
            a = chain_label(t, t.recvs[f.recv].rank)
            b = chain_label(t, t.recvs[victim].rank) if victim is not None else ""
            same = victim is not None and instance(a) is not None and instance(a) == instance(b)
            rows.append({"trace": d, "finding": f.title, "cls": f.cls, "pin": f.pin or "", "site": "",
                         "changed": a, "other": b, "same_instance": same, "regex": title_regex(f.title)})
    if args.only_same:
        print("|".join(f"(?:{r['regex']})" for r in rows if r["same_instance"]))
        return 0
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["trace"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} alternatives, {sum(r['same_instance'] for r in rows)} within one instance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
