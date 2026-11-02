"""Tables of experiments/athena/scan.sh: one row per traced mpirun call (-runs.csv: the test, its
result, the call's ranks and threads, the report's status and findings by level and class) and
one row per call and call site with a finding (-sites.csv: the source line in Athena++'s tree
that `stretto sites` names, and the counts).
   python3 -I scan_csv.py WORK PREFIX   (writes PREFIX-runs.csv and PREFIX-sites.csv)"""
import collections
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from provenance import code_commit  # noqa: E402

root, prefix = sys.argv[1], sys.argv[2]
commit = code_commit()
NAME = re.compile(r"^(?P<test>.+)-(?P<mpi>openmpi|mpich)-(?P<run>\d+)$")
runs, sites = [], []
for name in sorted(os.listdir(root)):
    m = NAME.match(name)
    d = os.path.join(root, name)
    if not m or not os.path.isdir(d) or not os.path.exists(os.path.join(d, "result")):
        continue
    res = open(os.path.join(d, "result")).read().split()
    calls = os.path.join(d, "calls")
    for c in sorted(os.listdir(calls), key=int) if os.path.isdir(calls) else []:
        cd = os.path.join(calls, c)
        cmd = open(os.path.join(cd, "cmd")).read().split()
        np_ = cmd[cmd.index("-n") + 1] if "-n" in cmd else ""
        thr = next((a.split("=", 1)[1] for a in reversed(cmd) if a.startswith("mesh/num_threads=")), "1")
        inp = next((cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"), "")
        row = {**m.groupdict(), "call": c, "input": re.sub(r"^.*inputs/", "", inp), "np": np_, "threads": thr,
               "test_result": res[0], "test_rc": res[1], "commit": commit,
               "trace_mb": open(os.path.join(cd, "trace.mb")).read().strip() if os.path.exists(os.path.join(cd, "trace.mb")) else ""}
        rc = open(os.path.join(cd, "report.rc")).read().strip() if os.path.exists(os.path.join(cd, "report.rc")) else ""
        try:
            rep = json.load(open(os.path.join(cd, "report.json")))
        except (OSError, ValueError):
            rep = None
        if rep is None:
            row.update(status={"size": "not analysed (trace of %s MB)" % row["trace_mb"], "none": "no trace",
                               "killed": "call killed (trace above 1500 MB)"}.get(rc, "no report (rc %s)" % rc))
            runs.append(row)
            continue
        f = rep["findings"]
        cls = collections.Counter(f"{x['level']}:{x['cls']}" for x in f)
        row.update(status=rep["status"].replace(",", "+"), summary=rep["summary"].replace(",", ";"), findings=len(f),
                   analysis_commit=rep.get("analysis_commit", ""),
                   classes=" ".join(f"{k}={v}" for k, v in sorted(cls.items())))
        runs.append(row)
        where = {}
        if os.path.exists(os.path.join(cd, "sites.txt")):
            for line in open(os.path.join(cd, "sites.txt")):
                parts = line.split(None, 3)
                if len(parts) >= 3 and parts[2] == "records":
                    where[parts[0]] = parts[3].strip() if len(parts) > 3 else ""
        per = collections.defaultdict(collections.Counter)
        for x in f:
            per[x.get("site") or "-"][f"{x['level']}:{x['cls']}"] += 1
        for s, cnt in sorted(per.items()):
            loc = re.sub(r"\S*/athena-[a-z]+-[a-z]+/", "", where.get(s, ""))   # paths in the Athena++ tree
            sites.append({**m.groupdict(), "call": c, "site": s, "location": loc,
                          "counts": " ".join(f"{k}={v}" for k, v in sorted(cnt.items())), "commit": commit})

for fn, rows, cols in (("runs", runs, ["test", "mpi", "run", "call", "input", "np", "threads", "test_result", "test_rc",
                                       "trace_mb", "status", "findings", "classes", "summary", "analysis_commit", "commit"]),
                       ("sites", sites, ["test", "mpi", "run", "call", "site", "location", "counts", "commit"])):
    with open(f"{prefix}-{fn}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
