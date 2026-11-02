"""S1 of the Smilei scan: for each L1 alternative of a hung trace, is it a receive still pending
when the run stopped paired with the only pending send that matches its envelope?"""
import sys
from stretto import report, trace

t = trace.load(sys.argv[1])
rep = report.analyze(t, with_sp=False)
alts = [f for f in rep.findings if f.level == "L1"]
matched = {o.key for o in (t.observed_send(x) for x in t.recvs.values()) if o is not None}
own = 0
for f in alts:
    r, s = t.recvs[f.recv], t.sends[f.send]
    pending_r = t.observed_send(r) is None
    unmatched = [x for x in t.sends.values() if x.key not in matched and x.dst == r.proc
                 and x.proc == r.src and x.tag == r.tag and x.comm == r.comm]
    ok = pending_r and [x.key for x in unmatched] == [s.key]
    own += ok
    if not ok:
        print("other:", f.title, pending_r, len(unmatched))
print(f"{sys.argv[1]}: {len(alts)} L1 alternatives, {own} of them a pending receive and the one pending send of its envelope")
