"""The one meaning of --select in the corpus scripts: run_mbi.py (a run and --replay-only),
stress_baseline.py, mpiprv/stretto_corpus.py, and what ninja/run.py corpus should use too.

A corpus program is named by its table name: its path under the corpus's generated programs
without ".c", with "/" written "__" (MPI-BugBench's P2P/CallOrdering/x.c is
P2P__CallOrdering__x; MBI's names have no "/"). A pattern selects a program if it matches the
table name, or its path with or without ".c": "^P2P/CallOrdering" and "^P2P__CallOrdering"
select the same programs everywhere. They used not to: a run matched paths and the other
scripts table names, which is how the MPI-BugBench stress pattern once matched nothing.

The default selection of each corpus is the campaign's (experiments/run_remaining.sh). A run
writes its corpus's canonical table only for that selection: a smaller one (another pattern,
--limit) needs --out, so that it never replaces the table with a part of it."""
import re

DEFAULT_SELECT = {
    # MBI: every wildcard, probe, tag-matching and buffering program (115)
    "mbi": "MessageRace|ANY|Probe|Correct|ParamMatching_Tag|Buffering",
    # MPI-BugBench level 2: point-to-point and collective call-ordering, missing-call and
    # local-concurrency programs, and every correct program (for false positives)
    "mbb": "^(P2P|COLL)/(CallOrdering|MissingCall|LocalConcurrency|Correct)",
}


def table_name(rel_path: str) -> str:
    """P2P/CallOrdering/x.c -> P2P__CallOrdering__x"""
    return (rel_path[:-2] if rel_path.endswith(".c") else rel_path).replace("/", "__")


def selects(pattern: str, name: str) -> bool:
    """Does pattern select the program named name (a table name, or a path)?"""
    t = table_name(name)
    path = t.replace("__", "/")
    return any(re.search(pattern, x) for x in (t, path, path + ".c"))
