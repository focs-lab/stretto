"""The tables of targeted.py grouped: per call site, the candidates of the census (all traces)
and the sample's verdicts and classes, with the tests they come from.
   python3 -I targeted_summary.py TARGETED.csv ..."""
import collections
import csv
import re
import sys

census = collections.Counter()
verdicts = collections.defaultdict(collections.Counter)
tests = collections.defaultdict(set)
for fn in sys.argv[1:]:
    for r in csv.DictReader(open(fn)):
        loc = (r["location"] or r["site"]) + "  [chain " + r.get("chain", "?") + "]"
        test = re.sub(r"-(openmpi|mpich)-np(\d+)-\d+$", r" np\2 (\1)", r["run"])
        tests[loc].add(test)
        if r["kind"] == "census":
            census[loc] += int(r["verdict"].split()[0])
        else:
            verdicts[loc][(r["verdict"], r["cls"])] += 1
for loc in sorted(set(census) | set(verdicts), key=lambda x: -census[x]):
    v = verdicts[loc]
    print(f"{census[loc]:7d} candidates  {loc}")
    print("        sample: " + ", ".join(f"{k[0]}{' ' + k[1] if k[1] else ''} {n}" for k, n in sorted(v.items())))
    print("        " + ", ".join(sorted(tests[loc])))
