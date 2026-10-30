#!/usr/bin/env python3
"""Replay a random sample of an application's alternatives, each with the pins of its whole
horizon (stretto.confirm.horizon_pins) and with its control, and record what each run did.

On correct, wildcard-heavy code the analysis reports alternatives that are warnings (another
sender at a racing receive, another message seen by a probe); whether they are harmless is a
claim about the program's continuation, which the trace does not determine. A sample of
replays measures it: an alternative whose replay finishes while its control finishes is
realisable and, in that run, harmless; one that hangs or fails while its control finishes is a
consequence the analysis could not see. Replays and controls run under the tracer when it is
built (stretto.confirm.run_checked), and each row says whether the run is one of the program
without its pins (run_valid, control_valid) and, if not, why.

--from-csv replays the alternatives of an earlier sample instead, named by the finding
column of its rows, on the same trace: no analysis runs, which on a trace of ten million
records (Nekbone) saves the 33 GB it takes. The pins are computed for the whole sample before
the first replay and the trace is then let go, so that judging a replay's own trace, as large,
does not come on top of it.

The pool is every L1 alternative with a message: receive and probe alternatives and probe
flips. --flips adds the flips without one, a probe returning false (probe-unflip) and a failed
test finding its receive complete (test-flip); horizon_pins forces each with its own pin and
pins the recorded result in its control.

With KEEP_TRACES set, each replay's and control's trace is kept (stretto.keep) under the
table's campaign (amg/replay_sample, valid/nekbone, scale/samples/amg-64) or --keep-campaign,
as <k>-<class>-<pin>-replay and -control, k the row of the sample.

--threads replays a program of several threads (stretto.confirm, chain pins and order gates):
OMP_NUM_THREADS is left alone, and the rows gain the columns of THREAD_KEYS. --repeat replays each
alternative and its control K times, since the interleaving the gates leave free varies.

usage: replay_sample.py --out CSV [--sample N] [--seed K] [--timeout S] [--mpi openmpi|mpich]
                        [--from-csv CSV] [--flips] [--threads] [--repeat K] [--only REGEX] [--cls C,...] [--grep REGEX]
                        [--fresh-cwd DIR] [--env NAME=VALUE ...] <trace dir> <np> <exe> [args ...]
The options come first: everything after <exe> is the program's own command line, which may
start with a dash, and the replays run it from the current directory, as the traced run was.
"""
from __future__ import annotations
import argparse
import csv
import gc
import os
import random
import re
import shutil
import sys
from typing import Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stretto import keep, trace  # noqa: E402
from stretto.confirm import all_matched, horizon_pins, pin_name, required_matches, run_checked, with_sync  # noqa: E402
from stretto.report import Finding, analyze  # noqa: E402
from provenance import code_commit  # noqa: E402

KEYS = ["finding", "cls", "pin", "site", "pins", "replay", "matched", "run_valid", "violation", "control", "control_matched",
        "control_valid", "control_violation", "commit"]
# --threads: the repetitions, the pins and gates that did not apply, the synchronisation of the
# replay against the trace's (stretto.confirm.sync_divergence) and the lines --grep matches
THREAD_KEYS = ["repeat", "misapplied", "diverged", "control_misapplied", "control_diverged", "hits", "control_hits"]

_PROBE = re.compile(r"^(?:i?m?probe)\((\d+), (\d+)\) .*? could observe send\((\d+), (\d+)\)")
_RECV = re.compile(r"^recv\((\d+), (\d+)\) .*? <- send\((\d+), (\d+)\)")
_FLIP = re.compile(r"^(?:i?m?probe)\((\d+), (\d+)\) .*? could return true with send\((\d+), (\d+)\)")
_UNFLIP = re.compile(r"^(?:i?m?probe)\((\d+), (\d+)\) .*? could return false instead of reporting send\(")
_TEST_FLIP = re.compile(r"^\w+ at rank (\d+) #(\d+) could find recv\(")
FLIPS = ("probe-flip", "probe-unflip", "test-flip")


def finding_of(title: str, cls: str = "", pin: str = "", site: str = "") -> Optional[Finding]:
    """The alternative a sample row names, from its finding column (report.Finding titles:
    "iprobe(5, 501) src=ANY tag=2000 could observe send(6, 580)->5 ..." or "recv(1, 1093) ...
    <- send(2, 1088)->1 ...", and the flag flips' "... could return true with send(...)",
    "... could return false instead of reporting send(...)" and "test at rank R #K could find
    recv(...) complete ..."); None if the title is of none of these forms."""
    m = _PROBE.match(title)
    if m:
        a, b, c, d = map(int, m.groups())
        return Finding("probe", cls or "probe-alt", "L1", title, probe=(a, b), send=(c, d), pin=pin or None, site=site or None)
    m = _RECV.match(title)
    if m:
        a, b, c, d = map(int, m.groups())
        return Finding("1", cls or "1f", "L1", title, recv=(a, b), send=(c, d), pin=pin or None, site=site or None)
    m = _FLIP.match(title)
    if m:
        a, b, c, d = map(int, m.groups())
        return Finding("probe", "probe-flip", "L1", title, probe=(a, b), send=(c, d), pin=pin or None, site=site or None)
    m = _UNFLIP.match(title)
    if m:
        a, b = map(int, m.groups())
        return Finding("probe", "probe-unflip", "L1", title, probe=(a, b), pin=pin or None, site=site or None)
    m = _TEST_FLIP.match(title)
    if m:
        a, b = map(int, m.groups())
        return Finding("waitany", "test-flip", "L1", title, event=(a, b), pin=pin or None, site=site or None)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tracedir")
    ap.add_argument("np", type=int)
    ap.add_argument("exe")
    ap.add_argument("args", nargs=argparse.REMAINDER)
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--mpi", default="openmpi")
    ap.add_argument("--from-csv", help="replay the alternatives of this earlier sample (its finding column) instead")
    ap.add_argument("--flips", action="store_true",
                    help="sample the flips without a message too (probe-unflip, test-flip; a probe-flip is sampled anyway)")
    ap.add_argument("--threads", action="store_true",
                    help="a program of several threads: leave OMP_NUM_THREADS alone, and add the columns of the chain pins "
                         "(sync divergence, the output pattern)")
    ap.add_argument("--repeat", type=int, default=1, help="replay each alternative and its control this many times")
    ap.add_argument("--only", default=None, help="sample only the alternatives whose title matches this regular expression")
    ap.add_argument("--cls", default=None, help="sample only the alternatives of these classes (comma-separated, as 1a,1f)")
    ap.add_argument("--grep", default=None,
                    help="count the lines of each run's output (stdout and stderr) that match this regular expression")
    ap.add_argument("--fresh-cwd", default=None,
                    help="run every replay and control in this directory, emptied before each run (a program whose "
                         "output files change its next run, as HDF5's do)")
    ap.add_argument("--keep-output", default=None, metavar="DIR",
                    help="write each replay's and control's stdout and stderr to DIR/<k>-<class>-<pin>[-<repeat>]-replay.out "
                         "and -control.out (an oracle that needs more than --grep's count)")
    ap.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                    help="give every rank of every replay and control this variable, as the traced run's launcher did (-x)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-campaign", default=None,
                    help="with KEEP_TRACES set, keep the replays' traces under this campaign (stretto.keep; default: the table's)")
    args = ap.parse_args()
    campaign = args.keep_campaign or keep.campaign_of(args.out)
    t = with_sync(trace.load(args.tracedir), args.tracedir)   # the lock order, if the run recorded it
    if args.from_csv:
        with open(args.from_csv) as fh:
            earlier = list(csv.DictReader(fh))
        sample = [finding_of(r["finding"], r.get("cls", ""), r.get("pin", ""), r.get("site", "")) for r in earlier]
        bad = [r["finding"] for r, f in zip(earlier, sample) if f is None]
        if bad:
            sys.exit(f"{args.from_csv}: rows whose finding names no alternative: {bad[:3]}")
        pool = len(sample)
    else:
        rep = analyze(t, with_sp=False)
        pool = [f for f in rep.findings if f.level == "L1" and f.send is not None and (f.probe is not None or f.recv is not None)]
        if args.flips:
            pool += [f for f in rep.findings if f.level == "L1" and f.cls in ("probe-unflip", "test-flip")]
        if args.only:
            pool = [f for f in pool if re.search(args.only, f.title)]
        if args.cls:
            pool = [f for f in pool if f.cls in args.cls.split(",")]
        rng = random.Random(args.seed)
        sample = rng.sample(pool, min(args.sample, len(pool)))
        pool = len(pool)
        del rep
    # every pin first; then the trace goes, before the replays' own traces are read
    planned = [(f, horizon_pins(t, f), required_matches(t, f)) for f in sample]
    del t
    gc.collect()
    rows = []
    # one thread per rank, as in the traced run; a program of several threads keeps its own setting
    env = {} if args.threads else {"OMP_NUM_THREADS": "1"}
    env.update(x.split("=", 1) for x in args.env)
    pattern = re.compile(args.grep) if args.grep else None

    def hits(r) -> int:
        return sum(1 for ln in (r.proc.stdout + r.proc.stderr).splitlines() if pattern.search(ln)) if pattern else 0

    def fresh():
        """The directory of the next run, emptied, or None: the current one."""
        if not args.fresh_cwd:
            return None
        shutil.rmtree(args.fresh_cwd, ignore_errors=True)
        os.makedirs(args.fresh_cwd)
        return args.fresh_cwd

    for k, (f, hp, required) in enumerate(planned, 1):
        row = {"finding": f.title[:160], "cls": f.cls, "pin": f.pin, "site": f.site, "commit": code_commit()}
        if hp is None:
            rows.append({**row, "replay": "no horizon"})
            continue
        alt, ctl = hp
        for rep_k in range(1, args.repeat + 1):
            name = f"{k:02d}-{f.cls}-{pin_name(f.pin)}" + (f"-{rep_k}" if args.repeat > 1 else "")
            ra = run_checked(args.np, args.exe, args.args, alt, args.timeout, mpi=args.mpi, extra_env=env, cwd=fresh(),
                             keep_as=(campaign, name + "-replay"))
            rc = run_checked(args.np, args.exe, args.args, ctl, args.timeout, mpi=args.mpi, extra_env=env, cwd=fresh(),
                             keep_as=(campaign, name + "-control"))
            if args.keep_output:
                os.makedirs(args.keep_output, exist_ok=True)
                for role, r in (("replay", ra), ("control", rc)):
                    with open(os.path.join(args.keep_output, f"{name}-{role}.out"), "w") as fh:
                        fh.write(r.proc.stdout + "\n--- stderr\n" + r.proc.stderr)
            rows.append({**row, "pins": sum(x.count(",") + 1 for x in alt),
                         "replay": ra.result, "matched": all_matched(alt, ra.proc.stderr, required, ra.seen),
                         "run_valid": ra.valid, "violation": ra.why[:300],
                         "control": rc.result, "control_matched": all_matched(ctl, rc.proc.stderr, None, rc.seen),
                         "control_valid": rc.valid, "control_violation": rc.why[:300],
                         "repeat": rep_k, "misapplied": "; ".join(ra.misapplied)[:300],
                         "diverged": "; ".join(ra.diverged)[:300], "control_misapplied": "; ".join(rc.misapplied)[:300],
                         "control_diverged": "; ".join(rc.diverged)[:300], "hits": hits(ra), "control_hits": hits(rc)})
            r = rows[-1]
            print(f"[{k}/{len(planned)}{f'.{rep_k}' if args.repeat > 1 else ''}] {r['replay']} matched={r['matched']} valid={ra.valid} "
                  f"control={r['control']} valid={rc.valid} {f.title[:90]}"
                  + (f" hits={r['hits']}/{r['control_hits']} diverged={bool(ra.diverged)}/{bool(rc.diverged)}" if args.threads else "")
                  + (f"\n    {ra.why[:200]}" if ra.why else "") + (f"\n    control: {rc.why[:200]}" if rc.why else ""),
                  flush=True)
            write(args.out, rows, args.threads)       # a long sample keeps what it has
    write(args.out, rows, args.threads)
    print(f"alternatives {pool}, sampled {len(planned)}; wrote {args.out}")
    return 0


def write(path: str, rows: list, threads: bool = False) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEYS + (THREAD_KEYS if threads else []), extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
