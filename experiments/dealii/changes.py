#!/usr/bin/env python3
"""Where did a run of deal.II's mapping_01 print other values? For every replay of
experiments/dealii/predict.py and every stress run of run.sh whose log differs from the
expected one (their logs are kept under outputs/), the ranks, objects and calls whose values
differ.

mapping_01 prints, per rank, 25 lines for its first object (points shared by several cells)
and 25 for its second (unique mapping), each with the four evaluations of that cached object
in the order they were made (avg, min, max, insert), so a column is a call. Every call sends
the same values (the vector does not change), so a call's output is right whenever its probes
took one message from each sender, of whatever call. A probe of call k that takes a sender's
message of call k+1 files that sender twice and another one not at all: the probing rank's
call k is wrong. The other sender's message is then one call late, and the rank's values are
wrong once more in the first later call in which it takes that sender twice: k+1 if the sender
that ran ahead waits for it, the object's last call if that sender stays a call ahead (its
messages for the next object cannot come before the collectives that set that object up). So
one such probe changes two calls of one object at one rank, and nothing else.

usage: changes.py --expected FILE [--predict CSV ...] [--stress CSV] [--outputs DIR] --out CSV
Writes one row per run whose log differs: version, run, pin (empty for a stress run), kind
(cross, same, control, stress-plain, stress-delays), the pinned rank, the ranks whose lines
differ, the evaluations that differ (rank:object:call), whether every changed rank and object
has exactly two wrong calls, whether those are consecutive too, commit.
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from provenance import code_commit  # noqa: E402

LINE = re.compile(r"^DEAL:(\d+)::(\d+) (.*)$")
CALLS = ("avg", "min", "max", "insert")
POINTS = 25


def values(text: str):
    """{(rank, object, point): the four evaluations} from a normalised log."""
    out, seen = {}, {}
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        rank, point = int(m.group(1)), int(m.group(2))
        k = seen.get(rank, 0)
        seen[rank] = k + 1
        out[(rank, k // POINTS + 1, point)] = m.group(3).split()[:4]
    return out


def compare(text: str, expected: dict):
    """{(rank, object): the calls whose values differ}."""
    got, wrong = values(text), {}
    for (rank, obj, point), want in expected.items():
        have = got.get((rank, obj, point))
        for c in range(len(CALLS)):
            if have is None or c >= len(have) or have[c] != want[c]:
                wrong.setdefault((rank, obj), set()).add(c)
    return wrong


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", required=True, help="the test's expected log")
    ap.add_argument("--predict", nargs="*", default=[], help="tables of predict.py")
    ap.add_argument("--stress", help="the stress table of run.sh")
    ap.add_argument("--outputs", help="the kept logs (default: outputs/ next to the first table)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with open(args.expected) as fh:
        expected = values(fh.read())
    tables = args.predict + ([args.stress] if args.stress else [])
    outputs = args.outputs or os.path.join(os.path.dirname(os.path.abspath(tables[0])), "outputs")
    rows = []

    def add(row, path, pinned):
        with open(path) as fh:
            wrong = compare(fh.read(), expected)
        pairs = bool(wrong) and all(len(c) == 2 for c in wrong.values())
        rows.append({**row, "pinned_rank": pinned, "ranks": " ".join(str(r) for r in sorted({r for r, _ in wrong})),
                     "evaluations": " ".join(f"{r}:{o}:{CALLS[c]}" for (r, o), cs in sorted(wrong.items()) for c in sorted(cs)),
                     "pairs": pairs, "consecutive": pairs and all(max(c) - min(c) == 1 for c in wrong.values()),
                     "commit": code_commit()})

    for table in args.predict:
        for r in csv.DictReader(open(table)):
            for role in ("replay", "control"):
                bad = r.get("differs") == "True" if role == "replay" else r.get("control_expected") == "False"
                if not bad:
                    continue
                spec = r["pin"].split("=", 1)[-1]
                path = os.path.join(outputs, f"{r['version']}-{r['np']}-{r['run']}-{spec.replace(':', '-')}-{role}.txt")
                add({"version": r["version"], "run": r["run"], "pin": r["pin"], "kind": r["kind"] if role == "replay" else "control"},
                    path, spec.split(":")[0])
    if args.stress:
        for r in csv.DictReader(open(args.stress)):
            if r["differs"] == "True":
                add({"version": r["version"], "run": r["run"], "pin": "", "kind": f"stress-{r['setting']}"},
                    os.path.join(outputs, f"stress-{r['np']}-{r['run']}-{r['setting']}.txt"), "")
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["version", "run", "pin", "kind", "pinned_rank", "ranks", "evaluations", "pairs",
                                           "consecutive", "commit"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} changed runs; wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
