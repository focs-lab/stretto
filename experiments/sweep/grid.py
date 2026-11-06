#!/usr/bin/env python3
"""The cells of the configuration sweep, from the rule of RULE.md and the projects' own lists.

usage: grid.py INPUTS_DIR OUT_DIR
Reads inputs/slate-run_tests-medium-d.txt (the command lines of SLATE's runner),
inputs/dlaf-miniapp-options.txt (the options of DLA-Future's miniapps) and
inputs/parsec-addtest.txt (PaRSEC's test registrations), and writes grid-slate.csv,
grid-dlaf.csv and grid-parsec.csv: one row per cell, with the driver, the axis values, the
process count, the threads, the environment and the program's arguments. Deterministic: the
pairwise covering is greedy over the axes' product in its natural order.
"""
import csv
import itertools
import os
import re
import shlex
import sys

GRIDS = [(1, 2), (2, 1), (1, 4), (2, 2), (4, 1)]
THREADS = [2, 4]
SHAPES = ["square", "tall", "wide"]


def pairwise(axes):
    """Cells covering every pair of values of two axes (rule 8); axes is a list of (name, values)."""
    names = [a for a, _ in axes]
    vals = [v for _, v in axes]
    if len(axes) < 2:
        return [dict(zip(names, c)) for c in itertools.product(*vals)]
    todo = set()
    for i, j in itertools.combinations(range(len(axes)), 2):
        for a in range(len(vals[i])):
            for b in range(len(vals[j])):
                todo.add((i, a, j, b))
    cells = []
    combos = list(itertools.product(*[range(len(v)) for v in vals]))
    while todo:
        best, gain = None, -1
        for c in combos:
            g = sum((i, c[i], j, c[j]) in todo for i, j in itertools.combinations(range(len(c)), 2))
            if g > gain:
                best, gain = c, g
        cells.append(best)
        for i, j in itertools.combinations(range(len(best)), 2):
            todo.discard((i, best[i], j, best[j]))
    return [{names[k]: vals[k][c[k]] for k in range(len(c))} for c in cells]


# -- SLATE: the runner's medium class, largest member of each shape (rule 3) -----------------
SLATE_DIM = {"square": (500, 500), "tall": (1000, 500), "wide": (500, 1000)}


def slate_dim(tokens, shape):
    """The --dim of a cell, in the form of the runner's first --dim of this command."""
    m, n = SLATE_DIM[shape]
    first = tokens[0] if tokens else "500"
    parts = first.split("x")
    lead1 = parts[0] == "1" and len(parts) > 1        # the runner's "1x" for an unused m
    if len(parts) >= 3:
        return f"{m}x{n}x{n}"
    if lead1:
        return f"1x{n if shape != 'tall' else m}"
    return f"{m}x{n}"


def slate_rows(path):
    rows, seen = [], {}
    for line in open(path):
        args = shlex.split(line)[1:]                  # drop ./tester
        routine = args[-1]
        seen[routine] = seen.get(routine, 0) + 1
        driver = routine if seen[routine] == 1 else f"{routine}#{seen[routine]}"
        dims, keep, i = [], [], 0
        opts = args[:-1]
        while i < len(opts):
            o = opts[i]
            v = opts[i + 1] if i + 1 < len(opts) and not opts[i + 1].startswith("--") else None
            if o == "--dim":
                dims.append(v)
            elif v is None:
                keep.append(o)
            elif o == "--nb":
                keep += [o, v]                        # the runner's list, run by one process
            else:
                keep += [o, v.split(",")[0]]          # rule 2: first value of a list
            i += 2 if v is not None else 1
        axes = [("grid", GRIDS), ("shape", SHAPES), ("threads", THREADS)]
        for c in pairwise(axes):
            p, q = c["grid"]
            cell_args = keep + ["--grid", f"{p}x{q}", "--dim", slate_dim(dims, c["shape"]), routine]
            rows.append({"project": "slate", "driver": driver, "cell": f"{driver}:{c['shape']}:{p}x{q}:t{c['threads']}",
                         "shape": c["shape"], "grid": f"{p}x{q}", "block": "64,100", "vpmap": "",
                         "np": p * q, "threads": c["threads"], "env": "", "args": shlex.join(["tester"] + cell_args)})
    return rows


# -- DLA-Future: quarter-default blocks, D = 8 x the largest block (rule 3) ------------------
def dlaf_options(path):
    opts = {}
    for line in open(path):
        m = re.match(r'(miniapp_\w+)\.cpp:\("([a-z-]+)",.*default_value\( *(-?\d+) *\)', line)
        if m:
            opts.setdefault(m.group(1), {})[m.group(2)] = int(m.group(3))
    return opts


def dlaf_rows(path):
    rows = []
    for app, o in sorted(dlaf_options(path).items()):
        axes = [("grid", GRIDS), ("threads", THREADS)]
        if "m" in o and "n" in o:
            axes.append(("shape", SHAPES))
        if "mb" in o and "nb" in o:
            bpair, base = ("mb", "nb"), o["mb"] // 4
        elif "block-size-dst" in o:
            bpair, base = ("block-size", "block-size-dst"), o["block-size"] // 4
        else:
            bpair, base = None, o["block-size"] // 4
        if bpair:
            axes.append(("block", SHAPES))
        for c in pairwise(axes):
            p, q = c["grid"]
            args = [app, "--grid-rows", str(p), "--grid-cols", str(q), "--nruns", "1", "--nwarmups", "0",
                    "--check-result", "last", "--type", "d"]
            b = c.get("block", "square")
            b1, b2 = {"square": (base, base), "tall": (2 * base, base), "wide": (base, 2 * base)}[b]
            if bpair:
                args += [f"--{bpair[0]}", str(b1), f"--{bpair[1]}", str(b2)]
            else:
                args += ["--block-size", str(b1)]
            d = 8 * max(b1, b2)
            if "shape" in c:
                m, n = {"square": (d, d), "tall": (2 * d, d), "wide": (d, 2 * d)}[c["shape"]]
                args += ["--m", str(m), "--n", str(n)]
            else:
                args += ["--matrix-size", str(d)]
            args += [f"--pika:threads={c['threads']}", "--pika:bind=none"]
            rows.append({"project": "dlaf", "driver": app,
                         "cell": f"{app}:{c.get('shape', '-')}:{b}:{p}x{q}:t{c['threads']}",
                         "shape": c.get("shape", ""), "grid": f"{p}x{q}", "block": f"{b1},{b2}" if bpair else str(b1),
                         "vpmap": "", "np": p * q, "threads": c["threads"], "env": "", "args": shlex.join(args)})
    return rows


# -- PaRSEC: registered tests x the documented vpmap values (rules 1, 5, 6) ------------------
def parsec_tests(path):
    for line in open(path):
        m = re.match(r'add_test\(\[=\[(.*?)\]=\] (.*)\)\s*$', line)
        if not m:
            continue
        name, argv = m.group(1), re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))
        prog = argv[0]
        if (prog.endswith("cmake") or prog.endswith("parsec-ptgpp") or "python" in prog or prog == "rm"
                or re.search(r":(gpu|hip|fortran)\b", name) or name.startswith("profiling/")):
            continue
        np_ = 1
        if prog.endswith("mpiexec"):
            np_, argv = int(argv[2]), argv[3:]
        yield name, np_, argv


def parsec_rows(path):
    rows = []
    for name, np_, argv in parsec_tests(path):
        own = argv[:argv.index("--")] if "--" in argv else argv
        cores = int(own[own.index("-c") + 1]) if "-c" in own else 4
        env0 = "" if "-c" in own else f"PARSEC_MCA_runtime_num_cores={cores}"
        h = max(cores // 2, 1)
        for vp in ["flat", "hwloc-host", "hwloc-2socket", "rr", "file"]:
            env = [env0] if env0 else []
            if vp == "flat":
                pass                                  # the default
            elif vp == "hwloc-host":
                env.append("PARSEC_MCA_runtime_vpmap=hwloc")
            elif vp == "hwloc-2socket":
                env += ["PARSEC_MCA_runtime_vpmap=hwloc", f"HWLOC_SYNTHETIC=package:2 core:{h} pu:1"]
            elif vp == "rr":
                env.append(f"PARSEC_MCA_runtime_vpmap=rr:2:{h}:{2 * h}")
            else:
                env.append("PARSEC_MCA_runtime_vpmap=file:@VPMAP@")   # two lines, written by the run script
            rows.append({"project": "parsec", "driver": name, "cell": f"{name}:{vp}", "shape": "", "grid": "",
                         "block": "", "vpmap": vp, "np": np_, "threads": cores, "env": ";".join(env),
                         "args": shlex.join(argv)})
    return rows


def main():
    inp, out = sys.argv[1], sys.argv[2]
    fields = ["project", "driver", "cell", "shape", "grid", "block", "vpmap", "np", "threads", "env", "args"]
    for name, rows in [("slate", slate_rows(os.path.join(inp, "slate-run_tests-medium-d.txt"))),
                       ("dlaf", dlaf_rows(os.path.join(inp, "dlaf-miniapp-options.txt"))),
                       ("parsec", parsec_rows(os.path.join(inp, "parsec-addtest.txt")))]:
        with open(os.path.join(out, f"grid-{name}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(name, len(rows), "cells,", len({r["driver"] for r in rows}), "drivers")


if __name__ == "__main__":
    main()
