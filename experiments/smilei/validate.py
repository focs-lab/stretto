"""Check one Smilei run against the benchmark's reference with Smilei's own validation
(validation/easi, the comparison that validation.py makes after a run). Run from the build
tree's validation/ directory with the tree on PYTHONPATH (for happi):
   python3 validate.py BENCH.py SIMDIR     (SIMDIR holds restart000/ with the run's output)
Exit status 0 when every quantity matches its reference within the script's precision."""
import os
import sys

sys.path.insert(0, os.getcwd())             # easi, from the build tree's validation/
from easi import Validation  # noqa: E402

ok = Validation(verbose=False).compare(sys.argv[1], sys.argv[2])
sys.exit(0 if ok else 1)
