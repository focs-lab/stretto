#!/usr/bin/env python3
"""Scout a program for wildcard matching bugs: trace clean runs of one driver, analyse each,
and replay its alternatives with the pins of their whole horizon and with their controls.

A probe or receive alternative is classified by the message it would take against the one it
took in the run: "cross-tag" if the tags differ (an ANY_TAG receive or probe taking a message
of another channel), "cross-phase" if the two messages were sent after different numbers of
collectives on their communicator by their senders (a message of another exchange, when the
exchanges are separated by collectives on the sending side, as in HYPRE's ParaSails), and
"same" otherwise. A same-phase alternative can still cross exchanges that nothing collective
separates; the replays decide.

Each replay and control runs under the tracer (stretto.confirm.run_checked), which judges it a
run of the program without its pins or not, and its standard output, with the lines matching
--ignore removed, is compared with the control's and with the traced run's. An alternative
counts as confirmed if every required pin matched, the replay hung, failed or printed other
output, and the control finished with the traced run's output.

usage: scout.py --code C --version V --driver NAME --np N [N ...] --runs R --out CSV
                [--sample S | --same-sample S] [--seed K] [--timeout T] [--jobs J]
                [--mpi openmpi|mpich] [--ignore REGEX] [--cwd DIR]
                [--output-file F [--normalize-script S]] [--no-replay] -- <exe> [args ...]
--sample replays a random sample of S alternatives of all the traced runs; --same-sample
replays every alternative of an error class or of another tag or exchange, and a random
sample of S of the others, the same-exchange alternatives, which in libraries that probe
from MPI_ANY_SOURCE within one exchange can number hundreds per run.
The program runs in --cwd (default: a fresh directory per run), as do its replays. With
--output-file, the file of that name the program writes there is compared instead of its
standard output, after the perl -p script S if given (deal.II's tests write their log to
"output", which deal.II's cmake/scripts/normalize.pl normalises).
Writes one row per replayed alternative and one per traced run without any: code, version,
driver, np, run, records, traced result, the run's findings by level and class (and kind), the
same by class, kind and call site of the changed receive or probe (whether replayed or not), the
realisable thresholds of its cross alternatives, pin, class,
level, kind, tag, call site, the thresholds at which the alternative is realisable (B) and
the size of its message, replay result, every required pin matched, whether the replay is
a run of the program and why not, whether its output differs from the control's and from the
traced run's, control result, control matched, whether the control is a run of the program,
whether its output differs from the traced run's, seed of the sample, commit. Appends to CSV
if it exists and has the same columns, so that several drivers share one table.
"""
from __future__ import annotations
import argparse
import bisect
import concurrent.futures
import csv
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stretto import trace  # noqa: E402
from stretto.confirm import all_matched, horizon_pins, required_matches, run_checked, tracer_lib  # noqa: E402
from stretto.report import ERROR_CLASSES, analyze  # noqa: E402
from stretto.sites import _is_dyn, sites  # noqa: E402
from provenance import code_commit  # noqa: E402

KEYS = ["code", "version", "driver", "np", "run", "records", "traced", "findings", "by_site", "cross_B", "pin", "cls", "level",
        "kind", "tag",
        "site", "B", "bytes", "replay", "matched", "run_valid", "violation", "differs_control", "differs_traced", "control",
        "control_matched", "control_valid", "control_violation", "control_differs_traced", "seed", "commit"]


def normalize(out: str, ignore) -> str:
    return "\n".join(line for line in out.splitlines() if not (ignore and ignore.search(line)))


def classify(t, f):
    """(kind, tag) of a probe or receive alternative, None for other findings."""
    if f.send is None or (f.probe is None and f.recv is None):
        return None
    s = t.sends[f.send]
    if f.probe is not None:
        seen = t.probes[f.probe].seen
    else:
        seen = t.observed.get(f.recv)
    if seen is None:
        return "unmatched", s.tag
    o = t.sends[seen]
    if o.tag != s.tag:
        return "cross-tag", s.tag
    return ("cross-phase" if phase(t, s) != phase(t, o) else "same"), s.tag


_phases = {}


def phase(t, s):
    before = _phases.get(id(t))
    if before is None:
        before = {}
        for c in t.colls:
            before.setdefault((c.rank, c.comm), []).append(c.idx)
        for v in before.values():
            v.sort()
        _phases.clear()
        _phases[id(t)] = before
    return bisect.bisect_left(before.get((s.rank, s.comm), []), s.idx)


_outer = {}


def outer_names(t) -> dict:
    """Site label -> 'function at file:line' of the outermost function the MPI call is inlined
    into, with the innermost location in brackets when it is inlined. stretto.sites names the
    innermost one, which in libraries that wrap MPI in inline functions (PETSc's MPIU_Recv and
    the like in petscsys.h) is the same few wrapper lines for every caller. One addr2line per
    object for all its new addresses, cached across runs."""
    want = {}
    for rec in t.sites.values():
        obj, addr, base = rec.get("obj", "?"), rec.get("addr", 0), rec.get("base", 0)
        label = f"{os.path.basename(obj)}+{addr - base:#x}"
        dyn = _is_dyn(obj)
        if label not in _outer and dyn is not None:
            want.setdefault(obj, {})[(addr - base if dyn else addr) - 1] = label
    for obj, pcs in want.items():
        try:
            out = subprocess.run(["addr2line", "-a", "-i", "-f", "-C", "-e", obj, *map(hex, pcs)], capture_output=True,
                                 text=True, timeout=600).stdout.split("\n")
        except (OSError, subprocess.SubprocessError):
            continue
        blocks, cur = [], None
        for line in out:
            if re.fullmatch(r"0x[0-9a-f]+", line):
                cur = (int(line, 16), [])
                blocks.append(cur)
            elif cur is not None and line:
                cur[1].append(line)
        for pc, lines in blocks:
            pairs = [(lines[i], lines[i + 1].split(" (")[0]) for i in range(0, len(lines) - 1, 2)]
            pairs = [p for p in pairs if not p[1].startswith("??")]
            if pc in pcs and pairs:
                _outer[pcs[pc]] = f"{pairs[-1][0]} at {pairs[-1][1]}" + (f" [inlined {pairs[0][1]}]" if len(pairs) > 1 else "")
    return _outer


def result_of(rc: int) -> str:
    return "FINISHED" if rc == 0 else ("TIMEOUT" if rc == 124 else f"rc={rc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--driver", required=True, help="a name for the driver and its arguments")
    ap.add_argument("--np", type=int, nargs="+", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--sample", type=int, default=0, help="replay a random sample of this many alternatives (0: all)")
    ap.add_argument("--same-sample", type=int, default=None,
                    help="replay every alternative of an error class, cross-tag or cross-phase, and a random sample of this "
                         "many of the same-exchange ones")
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--mpi", default="openmpi")
    ap.add_argument("--ignore", default="", help="regular expression of output lines that may differ between runs")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--output-file", default=None,
                    help="compare this file, which the program writes in its directory, instead of its standard output")
    ap.add_argument("--normalize-script", default=None, help="a perl -p script the output file goes through first")
    ap.add_argument("--no-replay", action="store_true", help="trace and analyse only: one row per traced run")
    ap.add_argument("--out", required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    exe, prog_args = os.path.abspath(cmd[0]), cmd[1:]
    ignore = re.compile(args.ignore) if args.ignore else None
    rows, traced = [], []

    def workdir():
        return args.cwd or tempfile.mkdtemp(prefix=f"scout-{args.code}-")

    def output_of(stdout: str, w: str) -> str:
        """What the run printed, or the file it wrote (normalised), without the ignored lines."""
        if not args.output_file:
            return normalize(stdout, ignore)
        path = os.path.join(w, args.output_file)
        if not os.path.exists(path):
            return ""
        if args.normalize_script:
            text = subprocess.run(["perl", "-p", args.normalize_script, path], capture_output=True, text=True).stdout
        else:
            with open(path, errors="replace") as fh:
                text = fh.read()
        return normalize(text, ignore)

    for np in args.np:
        for i in range(1, args.runs + 1):
            d = tempfile.mkdtemp(prefix=f"scout-trace-{np}-{i}-")
            w = workdir()
            mpirun = ["/usr/bin/timeout", "600", "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
                      "-x", f"LD_PRELOAD={tracer_lib(args.mpi)}", "-x", f"STRETTO_TRACE_DIR={d}", exe, *prog_args]
            r = subprocess.run(mpirun, cwd=w, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            traced_out = output_of(r.stdout, w)
            if not args.cwd:
                shutil.rmtree(w, ignore_errors=True)
            row = {"code": args.code, "version": args.version, "driver": args.driver, "np": np, "run": i,
                   "traced": result_of(r.returncode), "seed": args.seed if args.sample or args.same_sample is not None else "",
                   "commit": code_commit()}
            try:
                t = trace.load(d)
            except Exception as e:  # noqa: BLE001
                rows.append({**row, "replay": f"trace unreadable: {e}"[:200]})
                shutil.rmtree(d, ignore_errors=True)
                continue
            outer = outer_names(t)
            names = {label: outer.get(label) or name for label, name, _ in sites(t)}
            shutil.rmtree(d, ignore_errors=True)
            rep = analyze(t, with_sp=False)
            counts = Counter(f"{f.level}:{f.cls}" for f in rep.findings)
            alts, at = [], Counter()
            for f in rep.findings:
                k = classify(t, f)
                if k is not None and f.pin:
                    alts.append((f, k[0], k[1]))
                if f.level != "observed":
                    at[f"{f.cls}{'/' + k[0] if k else ''}@{names.get(f.site) or f.site or '?'}"] += 1
            counts.update(f"kind:{k}" for _, k, _ in alts)
            row.update(records=sum(len(v) for v in t.ranks.values()),
                       findings=";".join(f"{k}={n}" for k, n in sorted(counts.items())),
                       by_site=";".join(f"{k}={n}" for k, n in sorted(at.items())),
                       cross_B=";".join(sorted({f.B for f, k, _ in alts if k.startswith("cross")})))
            traced.append((row, t, names, traced_out, alts))
            print(f"{args.driver} np={np} run={i}: {row['traced']}, {row['records']} records, {row['findings']}", flush=True)
            for f in rep.findings:
                if f.level == "observed":
                    print(f"  observed: {f.cls} {f.title}", flush=True)
    pool = [] if args.no_replay else [(j, f, k, tag) for j, (*_, alts) in enumerate(traced) for f, k, tag in alts]
    if args.same_sample is not None:
        same = [c for c, (_, f, k, _) in enumerate(pool) if k == "same" and f.cls not in ERROR_CLASSES]
        rest = sorted(set(range(len(pool))) - set(same))
        chosen = sorted(rest + random.Random(args.seed).sample(same, min(args.same_sample, len(same))))
    elif args.sample:
        chosen = sorted(random.Random(args.seed).sample(range(len(pool)), min(args.sample, len(pool))))
    else:
        chosen = range(len(pool))
    todo = [pool[c] for c in chosen]
    print(f"replaying {len(todo)} of {len(pool)} alternatives", flush=True)

    def run(np, pins):
        """One pinned run: its Replay and its output."""
        w = workdir()
        try:
            p = run_checked(np, exe, prog_args, pins, args.timeout, mpi=args.mpi, cwd=w)
            return p, output_of(p.proc.stdout, w)
        finally:
            if not args.cwd:
                shutil.rmtree(w, ignore_errors=True)

    def replay(j, f, kind, tag):
        row, t, names, out, _ = traced[j]
        res = {**row, "pin": f.pin, "cls": f.cls, "level": f.level, "kind": kind, "tag": tag,
               "site": names.get(f.site) or f.site or "", "B": f.B, "bytes": t.sends[f.send].count * t.sends[f.send].tsize}
        hp = horizon_pins(t, f)
        if hp is None:
            return {**res, "replay": "no horizon"}
        alt, ctl = hp
        (ra, oa), (rc, oc) = run(row["np"], alt), run(row["np"], ctl)
        res.update(replay=ra.result, matched=all_matched(alt, ra.proc.stderr, required_matches(t, f), ra.seen),
                   run_valid=ra.valid, violation=ra.why[:300], differs_control=oa != oc, differs_traced=oa != out,
                   control=rc.result, control_matched=all_matched(ctl, rc.proc.stderr, None, rc.seen),
                   control_valid=rc.valid, control_violation=rc.why[:300], control_differs_traced=oc != out)
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for n, res in enumerate(ex.map(lambda x: replay(*x), todo), 1):
            rows.append(res)
            print({k: res.get(k) for k in ("np", "run", "pin", "cls", "kind", "tag", "site", "replay", "matched", "run_valid",
                                           "differs_control", "control", "control_valid", "control_differs_traced")},
                  flush=True)
    replayed = {j for j, *_ in todo}
    rows += [row for j, (row, *_) in enumerate(traced) if j not in replayed]
    rows.sort(key=lambda r: (int(r["np"]), int(r["run"])))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    new = not os.path.exists(args.out)
    if not new:
        with open(args.out, newline="") as fh:
            header = next(csv.reader(fh), [])
        if header != KEYS:      # rows appended under another header would land in the wrong columns
            sys.exit(f"scout.py: {args.out} has other columns than this version writes; remove it or choose another --out")
    with open(args.out, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEYS, extrasaction="ignore", lineterminator="\n")
        if new:
            w.writeheader()
        w.writerows(rows)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
