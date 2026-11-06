#!/usr/bin/env python3
"""One CSV row of the configuration sweep from a cell's directory (run.sh).

usage: cell_row.py --header
       cell_row.py PROJECT CELL_DIR COMMIT
The cell directory holds cell (the grid's row), run.rc (exit status and seconds), log, trace.kb,
report.json and report.rc (Stretto's JSON report, its exit status and seconds), sites.txt
(experiments/slate/finding_sites.py: class, level, count, function and tags of the findings'
receives) and pairs.txt (experiments/dlaf/pending_pairs.py). The row records what happened and
what Stretto reported; which findings are at a known bug's site is decided afterwards (match.py).
"""
import json
import os
import re
import sys

FIELDS = ["project", "cell", "rerun", "run_rc", "run_seconds", "outcome", "trace_kb", "stretto_rc",
          "stretto_status", "analysis_seconds", "findings", "L1", "L1L", "observed", "classes",
          "sites", "pending_pairs", "commit"]


def read(d, name, default=""):
    try:
        with open(os.path.join(d, name), errors="replace") as f:
            return f.read()
    except OSError:
        return default


def outcome(project, rc, log):
    if rc == 124:
        return "timeout"
    if project == "slate":
        if "All tests passed" in log:
            return "passed"
        m = re.search(r"(\d+) tests? FAILED", log)
        if m:
            return f"failed:{m.group(1)}"
    err = re.search(r"MPI_ERR_[A-Z_]+", log)
    if err:
        return "abort:" + err.group(0)
    return "exit0" if rc == 0 else f"exit{rc}"


def main():
    if sys.argv[1] == "--header":
        print(",".join(FIELDS))
        return
    project, d, commit = sys.argv[1:4]
    cell = read(d, "cell").split(",")[0].strip()
    rc, secs = (read(d, "run.rc", "? ?").split() + ["?", "?"])[:2]
    log = read(d, "log")
    src, ssecs = (read(d, "report.rc", "? ?").split() + ["?", "?"])[:2]
    status, nf, l1, l1l, obs, classes = "?", "", "", "", "", ""
    try:
        rep = json.loads(read(d, "report.json"))
        f = rep["findings"]
        status = rep["status"]
        nf = len(f)
        l1 = sum(x["level"] == "L1" for x in f)
        l1l = sum(x["level"] == "L1+L" for x in f)
        obs = sum(x["level"] == "observed" for x in f)
        cnt = {}
        for x in f:
            k = f"{x['cls']}@{x['level']}"
            cnt[k] = cnt.get(k, 0) + 1
        classes = "+".join(f"{k}:{v}" for k, v in sorted(cnt.items()))
    except (ValueError, KeyError):
        pass
    sites = []
    for line in read(d, "sites.txt").splitlines():
        m = re.match(r"(\S+) (\S+) (\d+) (.*?) tags (\[.*\])$", line)
        if m and m.group(2) == "L1":
            sites.append(f"{m.group(1)}:{m.group(3)}:{m.group(4)}:{m.group(5).replace(' ', '')}")
    pairs = re.search(r"^total (\d+)", read(d, "pairs.txt"), re.M)
    row = [project, cell, read(d, "rerun").strip(), rc, secs, outcome(project, int(rc) if rc.isdigit() else -1, log),
           read(d, "trace.kb").strip(), src, status.replace(",", "+"), ssecs, nf, l1, l1l, obs, classes,
           "|".join(sites).replace(",", ";"), pairs.group(1) if pairs else "", commit]
    print(",".join(str(x) for x in row))


if __name__ == "__main__":
    main()
