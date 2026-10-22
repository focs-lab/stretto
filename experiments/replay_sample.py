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

With KEEP_TRACES set, each replay's and control's trace is kept (stretto.keep) under the
table's campaign (amg/replay_sample, valid/nekbone, scale/samples/amg-64) or --keep-campaign,
as <k>-<class>-<pin>-replay and -control, k the row of the sample.

usage: replay_sample.py --out CSV [--sample N] [--seed K] [--timeout S] [--mpi openmpi|mpich]
                        [--from-csv CSV] <trace dir> <np> <exe> [args ...]
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
import sys
from typing import Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stretto import keep, trace  # noqa: E402
from stretto.confirm import all_matched, horizon_pins, pin_name, required_matches, run_checked  # noqa: E402
from stretto.report import Finding, analyze  # noqa: E402
from provenance import code_commit  # noqa: E402

KEYS = ["finding", "cls", "pin", "site", "pins", "replay", "matched", "run_valid", "violation", "control", "control_matched",
        "control_valid", "control_violation", "commit"]

_PROBE = re.compile(r"^(?:i?m?probe)\((\d+), (\d+)\) .*? could observe send\((\d+), (\d+)\)")
_RECV = re.compile(r"^recv\((\d+), (\d+)\) .*? <- send\((\d+), (\d+)\)")


def finding_of(title: str, cls: str = "", pin: str = "", site: str = "") -> Optional[Finding]:
    """The alternative a sample row names, from its finding column (report.Finding titles:
    "iprobe(5, 501) src=ANY tag=2000 could observe send(6, 580)->5 ..." or "recv(1, 1093) ...
    <- send(2, 1088)->1 ..."); None if the title is of neither form."""
    m = _PROBE.match(title)
    if m:
        a, b, c, d = map(int, m.groups())
        return Finding("probe", cls or "probe-alt", "L1", title, probe=(a, b), send=(c, d), pin=pin or None, site=site or None)
    m = _RECV.match(title)
    if m:
        a, b, c, d = map(int, m.groups())
        return Finding("1", cls or "1f", "L1", title, recv=(a, b), send=(c, d), pin=pin or None, site=site or None)
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
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-campaign", default=None,
                    help="with KEEP_TRACES set, keep the replays' traces under this campaign (stretto.keep; default: the table's)")
    args = ap.parse_args()
    campaign = args.keep_campaign or keep.campaign_of(args.out)
    t = trace.load(args.tracedir)
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
        rng = random.Random(args.seed)
        sample = rng.sample(pool, min(args.sample, len(pool)))
        pool = len(pool)
        del rep
    # every pin first; then the trace goes, before the replays' own traces are read
    planned = [(f, horizon_pins(t, f), required_matches(t, f)) for f in sample]
    del t
    gc.collect()
    rows = []
    env = {"OMP_NUM_THREADS": "1"}           # one thread per rank, as in the traced run
    for k, (f, hp, required) in enumerate(planned, 1):
        row = {"finding": f.title[:160], "cls": f.cls, "pin": f.pin, "site": f.site, "commit": code_commit()}
        if hp is None:
            rows.append({**row, "replay": "no horizon"})
            continue
        alt, ctl = hp
        name = f"{k:02d}-{f.cls}-{pin_name(f.pin)}"
        ra = run_checked(args.np, args.exe, args.args, alt, args.timeout, mpi=args.mpi, extra_env=env,
                         keep_as=(campaign, name + "-replay"))
        rc = run_checked(args.np, args.exe, args.args, ctl, args.timeout, mpi=args.mpi, extra_env=env,
                         keep_as=(campaign, name + "-control"))
        rows.append({**row, "pins": sum(x.count(",") + 1 for x in alt),
                     "replay": ra.result, "matched": all_matched(alt, ra.proc.stderr, required, ra.seen),
                     "run_valid": ra.valid, "violation": ra.why[:300],
                     "control": rc.result, "control_matched": all_matched(ctl, rc.proc.stderr, None, rc.seen),
                     "control_valid": rc.valid, "control_violation": rc.why[:300]})
        print(f"[{k}/{len(planned)}] {rows[-1]['replay']} matched={rows[-1]['matched']} valid={ra.valid} control={rows[-1]['control']} "
              f"valid={rc.valid} {f.title[:90]}" + (f"\n    {ra.why[:200]}" if ra.why else "") + (f"\n    control: {rc.why[:200]}" if rc.why else ""),
              flush=True)
        write(args.out, rows)       # a long sample keeps what it has
    write(args.out, rows)
    print(f"alternatives {pool}, sampled {len(planned)}; wrote {args.out}")
    return 0


def write(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEYS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
