"""What ph.x printed, for experiments/history_predict.py and history_stress.py --judge REF.

The run's elph_prt table ("Electron-phonon vertex |g|") is characterised against the table of a
reference run (experiments/qeelph/elphtab.py): for each spin block, which reference spin its e_k
and e_k+q columns belong to (to the printed digits) and which its |g_sym| column matches (the
gauge-invariant column, to a relative TOL). The reference is ph.x of the version with the bug run
with one pool (-nk 1), where the ionode holds both spins and elph_prt sends no message. The
pools of a judged run change only the order of the parallel sums: on 27 Oct a table of the fix
at -nk 3 agreed with a one-process table to 6e-4 in |g_sym| (relative), with the eigenvalues as
printed, while the largest relative difference between the two spins' |g_sym| columns of the
reference is 36 (73 the other way round). The verdict is "ok" for a correct table and elphtab's
name for another one: "g-swapped" (the |g| columns of the two spins exchanged), "spins-swapped"
(both blocks exchanged), "mixed:..." spelled out, or "no-table"."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elphtab  # noqa: E402

TOL = 5e-3
_REF = {}


def judge(stdout: str, ref: str) -> str:
    if ref not in _REF:
        _REF[ref] = elphtab.parse(ref)
    verdict, _ = elphtab.characterise(elphtab.parse_text(stdout), _REF[ref], TOL, "gs")
    return "ok" if verdict == "correct" else verdict
