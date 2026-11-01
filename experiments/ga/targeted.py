"""L1+L judged at chosen call sites only, for traces whose full analysis does not finish (GA's
threadsafec: about 20 000 L1 candidates at the progress thread's receive, each judged by the
generic check). For every candidate over the hard happens-before (locks.view) whose changed
receive is at one of the sites, the steps of locks.alternatives: the order filter, then
locks.judge at B = 0 and B = inf. Candidates of L1 itself at those sites are listed too (none
on the traces this was written for). One row per candidate: its verdict and, when accepted,
the class the report would give it and the thresholds.
SHARD=k/n in the environment judges only the k-th of every n candidates at the sites (0 <= k < n),
so that one trace's candidates can be shared out between processes.
   PYTHONPATH=analysis python3 targeted.py SITE_REGEX TRACEDIR ... > targeted.csv"""
import csv
import os
import re
import sys
import time

from stretto import locks, matching, sites, trace
from stretto.report import classify
from stretto.threads import OrderFilter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from provenance import code_commit  # noqa: E402

commit = code_commit()

pat = re.compile(sys.argv[1])
shard, shards = (int(x) for x in os.environ.get("SHARD", "0/1").split("/"))
w = csv.writer(sys.stdout, lineterminator="\n")
w.writerow(["trace", "hb", "site", "location", "kind", "recv", "send", "verdict", "cls", "why", "B", "seconds", "commit"])
for d in sys.argv[2:]:
    t0 = time.time()
    t = trace.load(d)
    run = os.path.basename(os.path.dirname(os.path.abspath(d)))
    where = {label: re.sub(r"\S*/ga-[a-z]+-[a-z]+-src/", "", loc) for label, loc, _ in sites.sites(t)}
    views = [("L1", t)] + ([("L1+L", locks.view(t))] if locks.applies(t) else [])
    for hb, tv in views:
        alts, _ = matching.candidates_full(tv)
        order = OrderFilter(tv)
        seen = -1
        for a in alts:
            if a.recv not in tv.recvs:
                continue
            r = tv.recvs[a.recv]
            s = tv.site_label(r.rank, r.idx)
            loc = where.get(s, "")
            if not pat.search(loc):
                continue
            seen += 1
            if seen % shards != shard:
                continue
            t1 = time.time()
            why = order.violation(a)
            if why:
                w.writerow([run, hb, s, loc, a.kind, a.recv, a.send, "rejected (order)", "", str(why)[:200], "",
                            f"{time.time() - t1:.1f}", commit])
                continue
            held, verdict = [], None
            for B in (0, None):
                if hb == "L1":
                    from stretto.feasible import check
                    ok = check(tv, a.M, a.changed, B, "L1").feasible
                else:
                    st, verdict = locks.judge(tv, a.M, a.changed, B, {tv.recvs[k].rank for k in a.changed})
                    ok = st is not None
                if ok:
                    held.append("inf" if B is None else str(B))
            if held:
                cls, text = classify(r, t.sends[a.send], t.observed_send(r))
                w.writerow([run, hb, s, loc, a.kind, a.recv, a.send, "accepted", cls, text, " ".join(held),
                            f"{time.time() - t1:.1f}", commit])
            else:
                w.writerow([run, hb, s, loc, a.kind, a.recv, a.send, "rejected", "", "", "", f"{time.time() - t1:.1f}", commit])
            sys.stdout.flush()
    print(f"# {run}: {time.time() - t0:.0f} s", file=sys.stderr)
