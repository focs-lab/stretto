"""Summaries of the MUST-vc comparison (experiments/mustvc/README.md) from its CSVs.

    python3 experiments/mustvc/summary.py [DIR] [micro|corrbench|idioms|apps ...]

prints Markdown tables: per program of the artifact's suite, MUST-vc's lit verdicts (clock and
counter layouts) and Stretto's; per directory and label of MPI-CorrBench's OpenMP programs, and
per program, how many runs each tool flags (MUST-vc on the programs as they are and with their
num_threads clauses removed); per idiom; per application cell, outcomes, reports and times.

A run is flagged by MUST-vc when its log has an [MUST-REPORT] error or warning, by Stretto when
its report has a finding (`any`) or a finding of an error class (`err`,
stretto.report.ERROR_CLASSES). In the per-group tables a program counts as flagged when every
run of it is (`all`) or some are (`some`).
"""
import collections
import csv
import os
import statistics
import sys


def rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def n_int(x):
    return int(x) if x not in (None, "") else 0


def flagged_stretto(r, err_only=False):
    return n_int(r["errors"]) > 0 if err_only else n_int(r["errors"]) + n_int(r["warnings"]) > 0


def flagged_must(r):
    return n_int(r["errors"]) + n_int(r["warnings"]) > 0


def classes(rs, field="classes", sep="*"):
    c = collections.Counter()
    for r in rs:
        for x in filter(None, (r.get(field) or "").split(";")):
            c[x.rsplit(sep, 1)[0] if field == "messages" else x.split(sep)[0]] += 1
    return ", ".join(f"{k} x{v}" for k, v in sorted(c.items()))


def stretto_by_program(d, name):
    """Stretto's rows per program; rows of NAME-gcc.csv (programs rebuilt from a copy gcc
    accepts) replace a BUILD-FAILED program's."""
    st = collections.defaultdict(list)
    for r in rows(os.path.join(d, name + ".csv")):
        st[r["program"]].append(r)
    gcc = collections.defaultdict(list)
    for r in rows(os.path.join(d, name + "-gcc.csv")):
        gcc[r["program"]].append(dict(r, note="gcc copy"))
    st.update(gcc)
    return st


def micro(d):
    must = collections.defaultdict(collections.Counter)
    for r in rows(os.path.join(d, "mustvc-micro.csv")):
        must[(r["suite"], r["group"], r["label"], r["test"], r["analysis"])][r["verdict"]] += 1
    st = {}
    for p, rs in stretto_by_program(d, "stretto-micro").items():
        test = os.path.splitext(os.path.basename(p))[0]
        suite = "COBE" if p.startswith("MPI-Corrbench") else "Own"
        group = p.split("/")[1]
        label = "correct" if "/correct/" in p else "erroneous"
        st[(suite, group, label, test)] = [r for r in rs if r["run"] != "0"]
    print("| suite | group | label | program | MUST-vc clock | MUST-vc counter | Stretto any | Stretto err | Stretto classes (runs) |")
    print("|---|---|---|---|---|---|---|---|---|")
    tot = collections.Counter()
    for key in sorted({k[:4] for k in must}):
        suite, group, label, test = key
        cells = []
        for a in ("clock", "counter"):
            c = must[key + (a,)]
            cells.append(" ".join(f"{v} {k}" for k, v in sorted(c.items())))
            tot[(a, label, "good")] += c["TP"] + c["TN"]
            tot[(a, label, "n")] += sum(c.values())
        rs = st.get(key, [])
        anyf = sum(flagged_stretto(r) for r in rs)
        errf = sum(flagged_stretto(r, True) for r in rs)
        note = classes(rs)
        status = collections.Counter(r["status"] for r in rs if r["status"] != "complete")
        res = collections.Counter(r["result"] for r in rs if r["result"] != "FINISHED")
        extra = [f"{k} x{v}" for k, v in sorted((status + res).items())]
        if any(r.get("note") for r in rs):
            extra.append("built from a copy gcc accepts")
        if extra:
            note += ("; " if note else "") + ", ".join(extra)
        for name, k in (("any", anyf), ("err", errf)):
            tot[("stretto-" + name, label, "good")] += k if label == "erroneous" else len(rs) - k
            tot[("stretto-" + name, label, "n")] += len(rs)
        print(f"| {suite} | {group} | {label} | {test} | {cells[0]} | {cells[1]} | {anyf}/{len(rs)} | {errf}/{len(rs)} | {note} |")
    print()
    print("| tool | runs of erroneous programs flagged | runs of correct programs not flagged |")
    print("|---|---|---|")
    for a in ("clock", "counter", "stretto-any", "stretto-err"):
        print(f"| {a} | {tot[(a, 'erroneous', 'good')]}/{tot[(a, 'erroneous', 'n')]} | "
              f"{tot[(a, 'correct', 'good')]}/{tot[(a, 'correct', 'n')]} |")
    print()


def corrbench(d):
    tools = {"mustvc": rows(os.path.join(d, "mustvc-corrbench.csv")),
             "mustvc-compat": rows(os.path.join(d, "mustvc-corrbench-compat.csv"))}
    by = {t: collections.defaultdict(list) for t in tools}
    for t, rs in tools.items():
        for r in rs:
            if r["run"] != "0":
                by[t][r["program"]].append(r)
    st = {p: [r for r in rs if r["run"] != "0"] for p, rs in stretto_by_program(d, "stretto-corrbench").items()}
    progs = sorted(set(by["mustvc"]) | set(st))
    groups = collections.defaultdict(collections.Counter)
    detail = []
    for p in progs:
        label = "correct" if "/correct/" in p else "erroneous"
        g = (p.split("/")[0], label)
        groups[g]["programs"] += 1
        row = [p, label]
        for t, f in (("mustvc", flagged_must), ("mustvc-compat", flagged_must),
                     ("stretto-any", flagged_stretto), ("stretto-err", lambda r: flagged_stretto(r, True))):
            rs = st.get(p, []) if t.startswith("stretto") else by[t].get(p, [])
            k = sum(f(r) for r in rs)
            if rs and k == len(rs):
                groups[g][t] += 1
            elif k:
                groups[g][t + "-some"] += 1
            row.append(f"{k}/{len(rs)}")
        m = by["mustvc"].get(p, []) + by["mustvc-compat"].get(p, [])
        early = sum(n_int(r["early"]) for r in by["mustvc"].get(p, []))
        bad = collections.Counter(f"{r['result']}" for r in m if r["result"] != "FINISHED")
        row.append(str(early))
        row.append(", ".join(f"{k} x{v}" for k, v in bad.items()))
        row.append(classes(by["mustvc-compat"].get(p, []), "messages"))
        row.append(classes(st.get(p, [])))
        detail.append(row)
    print("| directory | label | programs | MUST-vc as is (all/some) | MUST-vc without num_threads | Stretto any finding | Stretto error class |")
    print("|---|---|---|---|---|---|---|")
    for g in sorted(groups):
        c = groups[g]
        print(f"| {g[0]} | {g[1]} | {c['programs']} | " + " | ".join(
            f"{c[t]}/{c[t + '-some']}" for t in ("mustvc", "mustvc-compat", "stretto-any", "stretto-err")) + " |")
    print()
    print("| program | label | MUST-vc as is | MUST-vc without num_threads | Stretto any | Stretto err | MUST-vc runs warning of OpenMP before MPI_Init (as is) | MUST-vc runs not finished | MUST-vc reports (without num_threads) | Stretto classes |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for x in detail:
        print("| " + " | ".join(x) + " |")
    print()


def idioms(d):
    must = collections.defaultdict(list)
    for r in rows(os.path.join(d, "mustvc-idioms.csv")):
        if r["run"] != "0":
            must[r["idiom"]].append(r)
    st = collections.defaultdict(list)
    for r in rows(os.path.join(d, "stretto-idioms.csv")):
        if r["run"] != "0":
            st[r["group"]].append(r)
    print("| idiom | MUST-vc runs flagged | MUST-vc reports | MUST-vc runs not finished | Stretto any | Stretto err | Stretto classes |")
    print("|---|---|---|---|---|---|---|")
    for i in sorted(set(must) | set(st)):
        m, s = must.get(i, []), st.get(i, [])
        bad = collections.Counter(r["result"] for r in m if r["result"] != "FINISHED")
        print(f"| {i} | {sum(flagged_must(r) for r in m)}/{len(m)} | {classes(m, 'messages')} | "
              f"{', '.join(f'{k} x{v}' for k, v in bad.items())} | {sum(flagged_stretto(r) for r in s)}/{len(s)} | "
              f"{sum(flagged_stretto(r, True) for r in s)}/{len(s)} | {classes(s)} |")
    print()


def apps(d):
    cells = collections.defaultdict(list)
    for name in ("mustvc-apps", "mustvc-apps-openmpi"):
        for r in rows(os.path.join(d, name + ".csv")):
            cells[(r["app"] + ("" if name == "mustvc-apps" else " (Open MPI)"), r["cell"], r["variant"], r["tool"])].append(r)
    print("| app | cell | variant | tool | runs | finished | outcomes | runs with the culprit pair reported | reports per run (errors/warnings, median) | median s |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for k in sorted(cells):
        rs = cells[k]
        fin = sum(r["result"] == "FINISHED" for r in rs)
        oc = collections.Counter(r["outcome"] for r in rs)
        must = k[3] == "mustvc"
        cu = f"{sum(n_int(r['culprit']) > 0 for r in rs)}/{len(rs)}" if must else ""
        rep = (f"{statistics.median(n_int(r['errors']) for r in rs):g}/"
               f"{statistics.median(n_int(r['warnings']) for r in rs):g}") if must else ""
        secs = statistics.median(float(r["seconds"]) for r in rs)
        print(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {len(rs)} | {fin} | "
              f"{', '.join(f'{a} {b}' for a, b in sorted(oc.items()))} | {cu} | {rep} | {secs:.1f} |")
    print()


def times(d):
    """Median seconds per run on the small programs, finished runs only."""
    def med(xs):
        xs = [float(x) for x in xs if x not in ("", None)]
        return f"{statistics.median(xs):.2f} ({len(xs)})" if xs else "-"
    print("| suite | tool | untraced / without MUST | traced / under MUST | analysis |")
    print("|---|---|---|---|---|")
    for name, label in (("corrbench-compat", "CorrBench (without num_threads)"), ("idioms-compat", "OpenMP idioms (without num_threads)"),
                        ("idioms", "idioms")):
        rs = [r for r in rows(os.path.join(d, f"mustvc-{name}.csv")) if r["result"] == "FINISHED"]
        print(f"| {label} | MUST-vc | {med(r['seconds'] for r in rs if r['run'] == '0')} | "
              f"{med(r['seconds'] for r in rs if r['run'] != '0')} | |")
    for name, label in (("corrbench", "CorrBench"), ("idioms", "idioms"), ("micro", "MUST-vc's suite")):
        rs = [r for r in rows(os.path.join(d, f"stretto-{name}.csv")) if r["result"] == "FINISHED"]
        print(f"| {label} | Stretto | {med(r['seconds'] for r in rs if r['run'] == '0')} | "
              f"{med(r['seconds'] for r in rs if r['run'] != '0')} | {med(r['analysis_seconds'] for r in rs if r['run'] != '0')} |")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    d = args.pop(0) if args and os.path.isdir(args[0]) else os.path.dirname(os.path.abspath(__file__))
    for part in args or ["micro", "corrbench", "idioms", "apps"]:
        globals()[part](d)
