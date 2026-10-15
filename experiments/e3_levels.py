#!/usr/bin/env python3
"""E3: level ablation. Counts findings per class and level (L1 trace-only, L1+SP
single-path, observed) over all report.json files under experiments/results and,
for the idioms, contrasts L1+SP with the L2 twins (the DSL programs in
analysis/tests/idioms_dsl.py): an SP finding the twin does not reproduce is spurious.
"""
from __future__ import annotations
import collections
import glob
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "analysis", "tests"))
from rematch import explore, report, trace  # noqa: E402


def main() -> int:
    counts = collections.Counter()
    programs = collections.defaultdict(set)
    for f in glob.glob(os.path.join(ROOT, "experiments", "results", "*", "*", "report.json")):
        corpus = f.split("/")[-3]
        for fd in json.load(open(f)):
            counts[(corpus, fd["level"], fd["cls"])] += 1
            programs[(corpus, fd["level"], fd["cls"])].add(f)
    print("| corpus | level | class | findings | programs |\n|---|---|---|---|---|")
    for k in sorted(counts):
        print(f"| {k[0]} | {k[1]} | {k[2]} | {counts[k]} | {len(programs[k])} |")

    # idioms: L1+SP vs L2 twin
    try:
        import idioms_dsl
    except ImportError:
        return 0
    print("\n| idiom | L1 classes | L1+SP classes | L2 twin outcomes (forced) | SP spurious? |\n|---|---|---|---|---|")
    for n, mk in sorted(idioms_dsl.ALL.items()):
        d = os.path.join(ROOT, "traces", "openmpi", f"{n:02d}_*")
        dirs = glob.glob(d)
        if not dirs:
            continue
        t = trace.load(dirs[0])
        rep = report.analyze(t)
        l1 = sorted({f.cls for f in rep.findings if f.level == "L1"})
        sp = sorted({f.cls for f in rep.findings if f.level == "L1+SP"})
        twin = mk()
        kinds = set()
        for f in rep.findings:
            if f.level == "L1" and f.recv is not None:
                try:
                    res = explore.explore(twin, 0, force={f.recv: f.send}, comms=t.comm_ranks)
                    kinds |= res.kinds()
                except Exception as e:  # noqa: BLE001
                    kinds.add(f"err:{e}")
        spurious = "yes" if ("1d" in sp and "deadlock" not in kinds) else ("no" if sp else "-")
        print(f"| {n:02d} | {' '.join(l1)} | {' '.join(sp)} | {' '.join(sorted(kinds))} | {spurious} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
