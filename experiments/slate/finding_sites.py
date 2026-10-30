#!/usr/bin/env python3
"""The functions in which the receives of Stretto's findings on a trace were called.

For each finding of a JSON report (`stretto analyze --json`) the receive it changes is looked up
in the trace, and its call site is named by the function that holds it (gdb's "info symbol" on
the object of the site record; no debug information needed). Prints one line per (class,
function) with the number of findings and their tags.

usage: finding_sites.py TRACE_DIR REPORT.json
Needs gdb and c++filt in PATH.
"""
import json
import os
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "analysis"))
from stretto import trace  # noqa: E402
from stretto.confirm import _site_of  # noqa: E402


def symbol(obj, off, cache={}):
    if (obj, off) not in cache:
        # the return address minus one lies inside the call
        out = subprocess.run(["gdb", "-batch", "-ex", f"info symbol {off - 1:#x}", obj],
                             capture_output=True, text=True).stdout.splitlines()
        name = out[0].split(" in section")[0].rsplit(" + ", 1)[0] if out else "?"
        name = subprocess.run(["c++filt"], input=name, capture_output=True, text=True).stdout.strip()
        cache[(obj, off)] = name.split("(")[0] or "?"
    return cache[(obj, off)]


def main():
    t = trace.load(sys.argv[1])
    with open(sys.argv[2]) as f:
        rep = json.load(f)
    rows = defaultdict(list)
    for f in rep["findings"]:
        if not f.get("recv"):
            rows[(f["cls"], "-")].append(None)
            continue
        r = t.recvs.get(tuple(f["recv"]))
        site = _site_of(t, r) if r else None
        rows[(f["cls"], symbol(*site) if site else "?")].append(r.tag if r else None)
    for (cls, fn), tags in sorted(rows.items()):
        print(f"{cls} {len(tags)} {fn} tags {sorted(set(x for x in tags if x is not None))}")


if __name__ == "__main__":
    main()
