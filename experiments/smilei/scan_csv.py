"""Tables of experiments/smilei/scan.sh: one row per traced run (-runs.csv) and one row per run and
call site of a changed receive or probe with its finding counts by level and class (-sites.csv).
A site is named by `stretto sites` (sites.txt of the run: label, records, source location).
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
    if not m or not os.path.isdir(d):
        continue
    res = open(os.path.join(d, "result")).read().split()
    mbf = os.path.join(d, "trace.mb")
    row = {**m.groupdict(), "result": res[0], "rc": res[1], "commit": commit,
           "trace_mb": open(mbf).read().strip() if os.path.exists(mbf) else ""}
    try:
        rep = json.load(open(os.path.join(d, "report.json")))
    except (OSError, ValueError):
        rep = None
    if rep is None:
        rc = open(os.path.join(d, "report.rc")).read().strip() if os.path.exists(os.path.join(d, "report.rc")) else ""
        if rc == "size":
            row.update(status="not analysed (trace of %s MB%s)" % (row["trace_mb"], ", run killed" if res[0] == "killed" else ""),
                       findings="", classes="")
        else:
            row.update(status="no report (rc %s)" % rc, findings="", classes="")
        runs.append(row)
        continue
    f = rep["findings"]
    cls = collections.Counter(f"{x['level']}:{x['cls']}" for x in f)
    row.update(status=rep["status"].replace(",", "+"), summary=rep["summary"].replace(",", ";"), findings=len(f),
               analysis_commit=rep.get("analysis_commit", ""),
               classes=" ".join(f"{k}={v}" for k, v in sorted(cls.items())))
    runs.append(row)
    where = {}
    if os.path.exists(os.path.join(d, "sites.txt")):
        for line in open(os.path.join(d, "sites.txt")):
            parts = line.split(None, 3)
            if len(parts) >= 3 and parts[2] == "records":
                where[parts[0]] = parts[3].strip() if len(parts) > 3 else ""
    per = collections.defaultdict(collections.Counter)
    for x in f:
        per[x.get("site") or "-"][f"{x['level']}:{x['cls']}"] += 1
    for s, c in sorted(per.items()):
        loc = re.sub(r"\S*/smilei-[a-z]+-[a-z]+/", "", where.get(s, ""))   # paths in the Smilei tree
        sites.append({**m.groupdict(), "site": s, "location": loc,
                      "counts": " ".join(f"{k}={v}" for k, v in sorted(c.items())), "commit": commit})

for fn, rows, cols in (("runs", runs, ["test", "mpi", "run", "result", "rc", "trace_mb", "status", "findings",
                                           "classes", "summary", "analysis_commit", "commit"]),
                       ("sites", sites, ["test", "mpi", "run", "site", "location", "counts", "commit"])):
    with open(f"{prefix}-{fn}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
