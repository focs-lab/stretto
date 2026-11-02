"""L1 judged on a random sample of the candidates, for traces whose full analysis does not end
within the scan's limit (thousands of candidates per trace, each judged by the generic check).
Every candidate at a receive is counted by call site (the census); a sample of SAMPLE of them
(seed SEED) is judged as the report judges it: the order filter of the threads' analysis, then
feasible.check at B = 0 and B = inf. One row per candidate of the sample, its verdict and, when
accepted, the class the report would give it; and one row per site and chain with the count of
all its candidates (kind "census"). The chain is the changed receive's, by the thread's name in
the run ("0": a main thread; "0.k": the thread its main thread created k-th, from 0).
   PYTHONPATH=analysis python3 targeted.py TRACEDIR ... > targeted.csv"""
import collections
import csv
import os
import random
import re
import sys
import time

from stretto import matching, sites, trace
from stretto.feasible import check
from stretto.report import classify
from stretto.threads import OrderFilter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from provenance import code_commit  # noqa: E402

commit = code_commit()
SAMPLE, SEED = int(os.environ.get("SAMPLE", "100")), int(os.environ.get("SEED", "1"))
w = csv.writer(sys.stdout, lineterminator="\n")
w.writerow(["run", "site", "location", "chain", "kind", "recv", "send", "verdict", "cls", "why", "B", "seconds", "commit"])
for d in sys.argv[1:]:
    t0 = time.time()
    t = trace.load(d)
    run = os.path.basename(os.path.dirname(os.path.abspath(d)))
    where = {label: re.sub(r"\S*/spu-[a-z]+-[a-z]+-src/", "", loc) for label, loc, _ in sites.sites(t)}
    alts, _ = matching.candidates_full(t)
    alts = [a for a in alts if a.recv in t.recvs]
    label = lambda a: t.site_label(a.recv[0], t.recvs[a.recv].idx)
    name = lambda a: t.chain_name.get(a.recv[0], a.recv[0])   # "0": a main thread, "0.k": its k-th thread
    for (s, c, kind), n in sorted(collections.Counter((label(a), name(a), a.kind) for a in alts).items()):
        w.writerow([run, s, where.get(s, ""), c, "census", "", "", f"{n} candidates ({kind})", "", "", "",
                    f"{time.time() - t0:.1f}", commit])
    order = OrderFilter(t)
    pick = random.Random(SEED).sample(alts, min(SAMPLE, len(alts)))
    for a in pick:
        s = label(a)
        t1 = time.time()
        why = order.violation(a)
        if why:
            w.writerow([run, s, where.get(s, ""), name(a), a.kind, a.recv, a.send, "rejected (order)", "", str(why)[:200], "",
                        f"{time.time() - t1:.1f}", commit])
            continue
        held = ["inf" if B is None else str(B) for B in (0, None) if check(t, a.M, a.changed, B, "L1").feasible]
        r = t.recvs[a.recv]
        if held:
            cls, text = classify(r, t.sends[a.send], t.observed_send(r))
            w.writerow([run, s, where.get(s, ""), name(a), a.kind, a.recv, a.send, "accepted", cls, text[:200], " ".join(held),
                        f"{time.time() - t1:.1f}", commit])
        else:
            w.writerow([run, s, where.get(s, ""), name(a), a.kind, a.recv, a.send, "rejected", "", "", "",
                        f"{time.time() - t1:.1f}", commit])
        sys.stdout.flush()
    print(f"# {run}: {len(alts)} candidates, {len(pick)} judged, {time.time() - t0:.0f} s", file=sys.stderr)
