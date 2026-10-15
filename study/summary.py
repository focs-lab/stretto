#!/usr/bin/env python3
"""Fractions for the WP0a gate from sites.csv: how many wildcard sites depend on
metadata only, on payload, or on nothing."""
import csv
import sys
from collections import Counter, defaultdict

rows = [r for r in csv.DictReader(l for l in open(sys.argv[1] if len(sys.argv) > 1 else "sites.csv") if not l.startswith("#"))
        if r.get("app")]
if not rows:
    print("sites.csv is empty: classify sites first (study/sites.sh > sites.txt, then fill sites.csv)")
    sys.exit(0)
per_app = defaultdict(Counter)
for r in rows:
    per_app[r["app"]][r["dependence_class"]] += 1
total = Counter()
for app, c in sorted(per_app.items()):
    n = sum(c.values())
    meta = c["none"] + c["metadata"]
    print(f"{app:<12} sites={n:<3} metadata-or-none={meta} ({100*meta/n:.0f}%)  payload={n-meta}  {dict(c)}")
    total.update(c)
n = sum(total.values())
meta = total["none"] + total["metadata"]
print(f"{'ALL':<12} sites={n:<3} metadata-or-none={meta} ({100*meta/n:.0f}%)  payload={n-meta}")
print("gate (a): 'high fraction' if the metadata-or-none share is clearly above one half AND the payload sites are dominated by size headers (a bounded pattern)")
