#!/usr/bin/env python3
"""Compare the reports of two versions of the analysis on many traces: a change that only
makes the analysis faster or leaner must leave every report as it was. Each trace directory
is analysed by `python3 -m stretto analyze` with each version's `analysis` directory on
PYTHONPATH, in each mode: the text report (`text`), `--json` (`json`), and `--full-profile
--json` (`full`, every breakpoint judged), with `--no-sp` added to every mode if asked. The
outputs and exit statuses must be equal byte for byte; a pair that differs is written to the
output directory, and summary.txt there lists every comparison with both times.

  experiments/report_diff.py --base <old analysis dir> [--new <analysis dir>] --out <dir>
      [--jobs N] [--modes text,json,full] [--no-sp] <trace dir | @list file> ...

The list file names one trace directory per line. --new defaults to this repository's
analysis directory. --synthetic DIR:N first writes N of E6's random programs and N random runs
with many probes per rank (analysis/tests/test_checker.py: e6_traces, random_probe_records) as
trace directories under DIR and compares them too. Exit status 1 if any comparison differs."""
import argparse
import concurrent.futures as cf
import json
import os
import random
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODES = {"text": [], "json": ["--json"], "full": ["--full-profile", "--json"]}


def write_synthetic(out: str, n: int):
    """Trace directories of the first n of E6's random programs and of n random probe runs."""
    sys.path[:0] = [os.path.join(ROOT, "analysis"), os.path.join(ROOT, "analysis", "tests")]
    import test_checker as tc

    def write(d, raw):
        os.makedirs(d, exist_ok=True)
        for r, recs in raw.items():
            with open(os.path.join(d, f"rank-{r}.jsonl"), "w") as fh:
                fh.writelines(json.dumps(x) + "\n" for x in recs)
        return d

    out_dirs = [write(os.path.join(out, f"e6-{i + 1:03d}"), raw) for i, raw in enumerate(tc.e6_traces(n, 5000, raw=True))]
    rng = random.Random(20261022)
    for i in range(n):
        nranks = rng.choice([3, 4, 5])
        raw = tc.random_probe_records(rng, nranks)
        raw = {r: [{"op": "init", "np": nranks}] + recs + [{"op": "finalize"}] for r, recs in raw.items()}
        out_dirs.append(write(os.path.join(out, f"probe-{i + 1:03d}"), raw))
    return out_dirs


def run(code: str, d: str, flags, timeout: int):
    env = dict(os.environ, PYTHONPATH=code)
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "stretto", "analyze", *flags, d], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout, p.stderr, time.time() - t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="the analysis directory of the reference version")
    ap.add_argument("--new", default=os.path.join(ROOT, "analysis"), help="the analysis directory compared with it")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--modes", default="text,json,full")
    ap.add_argument("--no-sp", action="store_true", help="pass --no-sp to every analysis")
    ap.add_argument("--timeout", type=int, default=7200, help="seconds per analysis")
    ap.add_argument("--synthetic", metavar="DIR:N", help="also write and compare N synthetic traces of each kind under DIR")
    ap.add_argument("traces", nargs="*")
    args = ap.parse_args()
    dirs = []
    if args.synthetic:
        where, n = args.synthetic.rsplit(":", 1)
        dirs += write_synthetic(where, int(n))
    for x in args.traces:
        if x.startswith("@"):
            with open(x[1:]) as fh:
                dirs += [line.strip() for line in fh if line.strip()]
        else:
            dirs.append(x)
    modes = [(m, MODES[m] + (["--no-sp"] if args.no_sp else [])) for m in args.modes.split(",")]
    os.makedirs(args.out, exist_ok=True)

    def one(d):
        res = []
        for mode, flags in modes:
            a, b = run(args.base, d, flags, args.timeout), run(args.new, d, flags, args.timeout)
            same = a[0] == b[0] and a[1] == b[1]
            if not same:
                tag = d.strip("/").replace("/", "_")
                for name, x in (("base", a), ("new", b)):
                    with open(os.path.join(args.out, f"{tag}.{mode}.{name}"), "wb") as fh:
                        fh.write(x[1] + b"\n--- stderr\n" + x[2] + f"\n--- exit status {x[0]}\n".encode())
            res.append((mode, same, a[0], a[3], b[3]))
        return d, res

    n = differ = 0
    tb = tn = 0.0
    with cf.ThreadPoolExecutor(args.jobs) as ex, open(os.path.join(args.out, "summary.txt"), "w") as fh:
        for d, res in ex.map(one, dirs):
            n += 1
            for mode, same, rc, ta, tnew in res:
                tb, tn = tb + ta, tn + tnew
                differ += not same
                fh.write(f"{'same' if same else 'DIFFERS'} {mode} exit={rc} base={ta:.2f}s new={tnew:.2f}s {d}\n")
            fh.flush()
        line = f"traces {n}, comparisons {n * len(modes)}, differing {differ}, base {tb:.0f} s, new {tn:.0f} s"
        fh.write(line + "\n")
    print(line)
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
