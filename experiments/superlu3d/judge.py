"""What pddrive3d printed, for experiments/history_predict.py and history_stress.py --judge.

pddrive3d solves A x = b for a b made from a known x (xtrue) and prints the relative error
||X - Xtrue|| / ||X|| of the solution. A run is "ok" when it printed that error and the error is
at most XTOL; correct runs print 2e-15 to 4e-9 on the matrices of experiments/superlu3d/run.sh
(the solves are direct, without iterative refinement). Otherwise the verdict is "wrong x <error>",
or "no x error" when the run printed none (it hung, or stopped before the check)."""
import re

XERR = re.compile(r"\|\|X - Xtrue\|\| / \|\|X\|\| = (\S+)")
XTOL = 1e-4


def judge(stdout: str) -> str:
    m = XERR.search(stdout)
    if not m:
        return "no x error"
    try:
        x = float(m.group(1))
    except ValueError:
        return f"wrong x {m.group(1)}"
    return "ok" if x <= XTOL else f"wrong x {m.group(1)}"
