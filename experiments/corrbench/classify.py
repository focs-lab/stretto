#!/usr/bin/env python3
"""The label and the scope of every MPI-CorrBench a05644b program outside micro-benches/0-level/openmp
(513 programs: coll, conflo, correct, pt2pt, rma, usertypes), fixed before any of them ran under
Stretto (committed first, results after). Prints programs.csv:

  program,area,conflo,label,kind,scope,expect,why

label: CorrBench's own, erroneous unless under correct/ (the rule of its harness and of
experiments/mustvc/corrbench.sh). kind: the CorrBench error kind from the file name, ArgError,
ArgMismatch, MisplacedCall or MissingCall (collect-error-cases.py), "correct" under correct/.
area: coll, pt2pt, rma or usertypes (conflo/<area>, the same errors behind control flow;
correct/datatype counts as usertypes). conflo: 1 for conflo/.

scope: in when the error, in the run the program's comment describes, is one of the things
Stretto's report has a class for:
  a call that never completes or a message never received (a deadlock or a lost message:
  2-blocked, 1c, 1d; with a dependence on buffering, 2-buffering), a collective sequence that
  differs between the ranks or misses one (2-collective), a buffer of a pending operation
  touched before it completes (3-local with the buffer guard; 3-race), a message race (1a-1f);
out for everything else, each with its reason:
  an invalid argument of one call (ArgError, and an ArgMismatch whose destination is no rank of
  its communicator), a type or count signature that differs between the send and the receive
  of the match the run makes (Stretto checks signatures only for alternative matches, 1a),
  collective arguments that differ between the ranks (count, operation, root, type: the report
  compares only which collective each rank calls), one-sided communication (no model), the
  life of a datatype or of a request without a hang or a lost message, a missing MPI_Finalize
  and a call before MPI_Init (the tracer starts at MPI_Init and writes at MPI_Finalize).
expect: the classes an in-scope program should show (informational; the score counts any
error class, experiments/mbi/score.py).

usage: classify.py CORRBENCH_0_LEVEL_DIR > programs.csv"""
import csv
import os
import re
import sys

DIRS = ("coll", "conflo", "correct", "pt2pt", "rma", "usertypes")

# (regex on the file name, scope, expect, why), first match wins; for erroneous programs only
RULES = [
    (r".", "out", "", "one-sided communication: no model", "rma"),
    (r"^ArgError-", "out", "", "an invalid argument of one call", None),
    (r"^ArgMismatch-.*-Tag-", "in", "2-blocked 1c", "the receive's tag matches no send: it never completes", None),
    (r"^ArgMismatch-MPIIrecv-buffer-overlap", "in", "3-local 3-race", "two pending receives write one buffer", None),
    (r"^ArgMismatch-.*-Communicator-", "out", "", "the destination is no rank of its communicator (an invalid argument)", None),
    (r"^ArgMismatch-MPIISend-Type", "out", "", "a null datatype (an invalid argument)", None),
    (r"^ArgMismatch-MPI(Gather|Reduce)-", "out", "", "collective arguments differ between the ranks", None),
    (r"^ArgMismatch-.*-Type", "out", "", "the signature of the run's own match differs (checked for alternatives only)", None),
    (r"^(Misplaced|Missing)Call-MPIType", "out", "", "the life of a datatype", None),
    (r"^MisplacedCall-MPISend\.c$", "out", "", "a call before MPI_Init", None),
    (r"^MisplacedCall-MPIWait", "in", "3-local", "the send buffer is written before the wait", None),
    (r"^MisplacedCall-MPIRecv-Deadlock-[24]", "in", "2-buffering 2-blocked", "a deadlock unless the sends are buffered", None),
    (r"-Deadlock", "in", "2-blocked 2-collective 1c", "a deadlock in every run", None),
    (r"^MissingCall-MPIRecv", "in", "1c", "a message is never received", None),
    (r"^MissingCall-MPIFinalize", "out", "", "a missing MPI_Finalize", None),
    (r"^MissingCall-MPI(Wait|IBcast)", "out", "", "the life of a request: no hang, no lost message", None),
]


def kind_of(name: str) -> str:
    m = re.match(r"(ArgError|ArgMismatch|MisplacedCall|MissingCall)-", name)
    return m.group(1) if m else "?"


def classify(rel: str) -> dict:
    parts = rel.split("/")
    conflo = parts[0] == "conflo"
    label = "correct" if parts[0] == "correct" else "erroneous"
    area = parts[1] if parts[0] in ("conflo", "correct") else parts[0]
    area = "usertypes" if area == "datatype" else area
    name = parts[-1]
    row = {"program": rel, "area": area, "conflo": int(conflo), "label": label}
    if label == "correct":
        row.update(kind="correct", scope="", expect="", why="")
        return row
    row["kind"] = kind_of(name)
    for rx, scope, expect, why, only_area in RULES:
        if (area == only_area) if only_area else re.search(rx, name):
            row.update(scope=scope, expect=expect, why=why)
            return row
    raise SystemExit(f"classify.py: no rule for {rel}")


def programs(root: str):
    out = []
    for d in DIRS:
        for dp, _, fs in os.walk(os.path.join(root, d)):
            if os.path.basename(dp) == "include":
                continue
            out += [os.path.relpath(os.path.join(dp, f), root) for f in fs if f.endswith(".c")]
    return sorted(out)


def main():
    root = sys.argv[1]
    w = csv.DictWriter(sys.stdout, fieldnames=["program", "area", "conflo", "label", "kind", "scope", "expect", "why"],
                       lineterminator="\n")
    w.writeheader()
    for rel in programs(root):
        w.writerow(classify(rel))


if __name__ == "__main__":
    main()
