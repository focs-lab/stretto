"""The columns every row of the uniform campaign carries, and the outcome of a run
(experiments/protocol/RULE.md, Sections 1.5, 1.7, 1.8, 2.1, 2.2, 3 and 7).

Every campaign script of the campaign writes its rows through this module: experiments/protocol/
run.py for the applications, and `rows.py annotate` for the tables of the scripts that keep their
own (the suites and the thread campaigns), which adds the columns to each row as it lands in the
block's table. The columns:

  host        the machine (MACHINE, else the host name)
  cpus        the hardware threads the run was pinned to, as a cpuset list ("0-3,40-43")
  jobs        how many jobs the scheduler ran beside it at its start, itself included
  timeout     the run's T in seconds (Section 2.1; calibrate.py)
  seed        the seed of the run's random choices: Random(i*100+np) for random delays, NINJA's
              run identifier, the sample's seed of a replay; empty when the run draws nothing
  commit      the frozen commit (experiments/provenance.py; STRETTO_COMMIT)
  superseded  empty, or why the row was replaced (a declared harness failure, Section 1.8); a
              superseded row stays in the table
  outcome     the category of Section 3, from S (the bug's own symptom occurred) and F (the
              tool's report names the bug): predicted (F, not S), observed-reported (S and F),
              observed-not-reported (S, not F), missed (neither), tool-failure (the tool or its
              analysis did not complete)

On the fixed version the same categories are written: there F is a false alarm and S the fix
showing the symptom, both of which the tables report. A replay's row (replays.py) has its verdict
as its outcome: confirmed, not-confirmed or inconclusive.

Python 3.6 runs this file (the experiment machine's host runs the scheduler).
"""
import csv
import math
import os
import sys

COMMON = ["host", "cpus", "jobs", "timeout", "seed", "commit", "superseded", "outcome"]
OUTCOMES = ("predicted", "observed-reported", "observed-not-reported", "missed", "tool-failure")
Z95 = 1.959963984540054


def outcome(symptom, reported, failed=False):
    """The category of Section 3. symptom: S(r); reported: F(r); failed: the tool or its
    analysis did not complete, which decides before S and F do. For plain runs, random delays
    and NINJA, F is S (outcome(s, s))."""
    if failed:
        return "tool-failure"
    if reported:
        return "observed-reported" if symptom else "predicted"
    return "observed-not-reported" if symptom else "missed"


def revealed(category):
    """Whether a run of this category counts in the main cell: predicted + observed-reported."""
    return category in ("predicted", "observed-reported")


def wilson(k, n, z=Z95):
    """Wilson's 95% score interval of k of n (the paper's interval, experiments/tables.py)."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError("no proportion of %r in %r" % (k, n))
    z2 = z * z
    centre = (k + z2 / 2) / (n + z2)
    half = z * math.sqrt(k * (n - k) / n + z2 / 4) / (n + z2)
    return (0.0 if k == 0 else max(0.0, centre - half), 1.0 if k == n else min(1.0, centre + half))


def cpuset(cpus):
    """A cpuset list for a collection of processor numbers: [0, 1, 2, 40, 41] -> "0-2,40-41"."""
    cpus = sorted(set(int(c) for c in cpus))
    out, i = [], 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        out.append(str(cpus[i]) if i == j else "%d-%d" % (cpus[i], cpus[j]))
        i = j + 1
    return ",".join(out)


def parse_cpuset(text):
    """The processor numbers of a cpuset list: "0-2,40" -> [0, 1, 2, 40]."""
    out = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def host():
    """The machine's name from MACHINE; never the hostname, which on some hosts is a person's."""
    return os.environ.get("MACHINE") or "unset"


def own_cpus():
    """The processors this process may run on, as a cpuset list."""
    try:
        return cpuset(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return ""


def common(timeout, seed="", jobs=None, cpus=None, commit=None, superseded="", category=""):
    """The common columns of one row. jobs and cpus default to what the scheduler passed down
    (PROTOCOL_JOBS, PROTOCOL_CPUS) or, outside it, 1 and this process's processors; commit to
    STRETTO_COMMIT, else experiments/provenance.py."""
    if commit is None:
        commit = os.environ.get("STRETTO_COMMIT")
        if not commit:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from provenance import code_commit
            commit = code_commit()
    return {"host": host(), "cpus": cpus if cpus is not None else (os.environ.get("PROTOCOL_CPUS") or own_cpus()),
            "jobs": jobs if jobs is not None else int(os.environ.get("PROTOCOL_JOBS") or 1),
            "timeout": timeout, "seed": seed, "commit": commit, "superseded": superseded, "outcome": category}


def header_of(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, newline="") as fh:
        return next(csv.reader(fh), None)


def append(path, rows, fields):
    """Append rows (dicts) to the table at path, writing its header when the table is new; a
    table with other columns is refused, since its rows would land in the wrong columns."""
    have = header_of(path)
    if have is not None and have != list(fields):
        raise ValueError("%s has the columns %s, not %s" % (path, ",".join(have), ",".join(fields)))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
        if have is None:
            w.writeheader()
        for r in rows:
            w.writerow(r)
        fh.flush()
        os.fsync(fh.fileno())


def read(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def supersede(path, match, reason):
    """Mark the rows of the table at path for which match(row) holds as superseded by reason
    (Section 1.8): they stay in the table, and a rerun's rows are appended after them. Returns
    how many rows were marked; a row already superseded keeps its first reason."""
    rows = read(path)
    fields = header_of(path)
    if not rows or "superseded" not in fields:
        return 0
    n = 0
    for r in rows:
        if not r.get("superseded") and match(r):
            r["superseded"] = reason
            n += 1
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    return n


def current(rows):
    """The rows that are not superseded."""
    return [r for r in rows if not r.get("superseded")]


def annotate(src, dst, extra, outcome_of=None):
    """Append the rows of a script's own table src to the block table dst with the common
    columns (extra: their values for every row, where the script left them empty; any other key
    of extra replaces the script's own column, as the run number of a job that ran the script for
    one run) and, when outcome_of(row) is given, the outcome of each row (outcome_of may keep a
    column of its own under another name first). Returns the number of rows appended."""
    rows = read(src)
    if not rows:
        return 0
    out, fields = [], []
    for r in rows:
        r = dict(r)
        category = outcome_of(r) if outcome_of is not None else None
        if "outcome" in r and "script_outcome" not in r:
            r["script_outcome"] = r["outcome"]     # the script's own verdict column, which outcome replaces
        for c, v in extra.items():
            if c not in COMMON:
                r[c] = v
            elif not r.get(c):
                r[c] = v
        for c in COMMON:
            r.setdefault(c, "")
        if category is not None:
            r["outcome"] = category
        for c in r:
            if c not in fields:
                fields.append(c)
        out.append(r)
    fields = [c for c in fields if c not in COMMON] + COMMON
    append(dst, out, fields)
    return len(out)


def main(argv):
    """rows.py annotate <src> <dst> [--set column=value ...]: annotate() from the command line;
    rows.py supersede <table> <column=value[,column=value...]> <reason>; rows.py wilson <k> <n>."""
    if len(argv) >= 3 and argv[0] == "annotate":
        extra = {}
        for a in argv[3:]:
            if a == "--set":
                continue
            k, _, v = a.partition("=")
            extra[k] = v
        extra.setdefault("host", host())
        extra.setdefault("commit", os.environ.get("STRETTO_COMMIT", ""))
        print(annotate(argv[1], argv[2], extra))
        return 0
    if len(argv) == 4 and argv[0] == "supersede":
        want = dict(kv.partition("=")[::2] for kv in argv[2].split(","))
        print(supersede(argv[1], lambda r: all(r.get(k) == v for k, v in want.items()), argv[3]))
        return 0
    if len(argv) == 3 and argv[0] == "wilson":
        lo, hi = wilson(int(argv[1]), int(argv[2]))
        print("%.3f %.3f" % (lo, hi))
        return 0
    print(main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
