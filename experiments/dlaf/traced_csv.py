"""One CSV row per traced run of experiments/dlaf/traced.sh or experiments/parsec/traced.sh:
the run's result and Stretto's report on its trace (status; L1 alternatives at receives and
the tags of their receives; waitany-order warnings; observed thread findings).
   traced_csv.py OUTDIR > traced.csv"""
import collections
import json
import os
import re
import sys

root = sys.argv[1]
print("cell,variant,run,result,colliding_pairs,stretto_status,l1_receive_findings,l1_receive_tags,"
      "waitany_order,observed_2_thread_collective,observed_2_thread_wait,observed_other")
for name in sorted(os.listdir(root), key=lambda n: (n.rsplit("-", 2)[0], n.rsplit("-", 2)[1], int(n.rsplit("-", 1)[1]))):
    d = os.path.join(root, name)
    cell, variant, run = name.rsplit("-", 2)
    res = open(os.path.join(d, "result")).read().split()
    rep = json.load(open(os.path.join(d, "report.json")))
    f = rep["findings"]
    recv = [x for x in f if x["level"] == "L1" and x["cls"] in ("1a", "1b", "1c", "1d", "1e", "1f")]
    tags = sorted({int(m.group(1)) for x in recv for m in [re.match(r"recv\([^)]*\) src=\S+ tag=(\d+)", x["title"])] if m})
    obs = collections.Counter(x["cls"] for x in f if x["level"] == "observed")
    other = sum(v for k, v in obs.items() if k not in ("2-thread-collective", "2-thread-wait"))
    print(",".join(map(str, [cell, variant, run, res[0], res[1] if len(res) > 1 else "", rep["status"].replace(",", "+"),
                             len(recv), " ".join(map(str, tags)),
                             sum(x["cls"] == "waitany-order" for x in f), obs["2-thread-collective"], obs["2-thread-wait"], other])))
