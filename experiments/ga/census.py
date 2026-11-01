"""Candidate census of traces whose full analysis is too slow: the candidates (steals and swaps
at receives, before any feasibility check) per call site of the changed receive, over the
happens-before of L1 ("L1") and, where locks are recorded, over the hard happens-before that
L1+L reorders critical sections in ("L1+L", docs/threads.md section 13). A candidate is no
finding: the census says where the analysis would look, not what it would accept.
   PYTHONPATH=analysis python3 census.py TRACEDIR ... > census.csv"""
import collections
import os
import re
import sys
import time

from stretto import locks, matching, sites, trace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from provenance import code_commit  # noqa: E402

commit = code_commit()

print("trace,records,chains,hb,candidates,site,location,candidates_at_site,seconds,commit")
for d in sys.argv[1:]:
    t0 = time.time()
    t = trace.load(d)
    where = {label: loc for label, loc, _ in sites.sites(t)}
    run = os.path.basename(os.path.dirname(os.path.abspath(d)))
    nrec = sum(len(r) for r in t.ranks.values())
    nch = sum(len(c) for c in t.chains.values())
    views = [("L1", t)] + ([("L1+L", locks.view(t))] if locks.applies(t) else [])
    for hb, tv in views:
        alts, _ = matching.candidates_full(tv)
        per = collections.Counter(tv.site_label(a.recv[0], tv.recvs[a.recv].idx) if a.recv in tv.recvs else "-"
                                  for a in alts)
        secs = time.time() - t0
        for s, n in (sorted(per.items()) or [("-", 0)]):
            loc = re.sub(r"\S*/ga-[a-z]+-[a-z]+-src/", "", where.get(s, "")).replace(",", ";")
            print(f"{run},{nrec},{nch},{hb},{len(alts)},{s},{loc},{n},{secs:.1f},{commit}", flush=True)
