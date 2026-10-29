"""experiments/history_predict.py with every trace loaded without its cancel records.

ADLB's xlb_sync_finalize (lb/code/src/sync.c:208-211) cancels its pre-posted SYNC_REQUEST receives
at shutdown and never completes them. The analysis reports such a trace as "Cancel (outcome not
recorded)", degraded, and predicts nothing in it (a limit of the tool, docs/findings.md). The
filter drops the cancel records, the last records of each server but three, and renumbers the
records after them (the loader needs consecutive sequence numbers); the receives then stay
pending at the end of the run, as they did. It applies to the traced runs and to the validity
check of every replay and control, since stretto.confirm reads their traces through
stretto.trace.load too.

usage: python3 experiments/latent/swiftt/strip_cancel.py <history_predict.py arguments>"""
import os
import re
import runpy
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from stretto import trace as T  # noqa: E402

_load = T.load
SEQ = re.compile(r'"seq":\d+')


def load(d, *a, **k):
    tmp = tempfile.mkdtemp(prefix="strip-")
    try:
        for f in os.listdir(d):
            src = os.path.join(d, f)
            if f.startswith("rank-") and f.endswith(".jsonl"):
                n = 0
                with open(src) as fi, open(os.path.join(tmp, f), "w") as fo:
                    for line in fi:
                        if '"op":"cancel"' in line:
                            continue
                        fo.write(SEQ.sub(f'"seq":{n}', line, count=1))
                        n += 1
            elif os.path.isfile(src):
                shutil.copy(src, tmp)
        return _load(tmp, *a, **k)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


T.load = load
script = os.path.join(ROOT, "experiments", "history_predict.py")
sys.argv = [script] + sys.argv[1:]
runpy.run_path(script, run_name="__main__")
