#!/usr/bin/env python3
"""Score LLM judge answers against the benchmark key.
usage: score.py key.json answers_dir  -> prints tables; writes scores.csv next to the answers.
A run is one answer file family: <cond>-<model>-<rep>-b<k>.json; the run id is <cond>-<model>-<rep>."""
import csv, glob, json, os, re, sys, collections

key = json.load(open(sys.argv[1]))
adir = sys.argv[2]
runs = collections.defaultdict(dict)
for f in sorted(glob.glob(os.path.join(adir, "*.json"))):
    m = re.match(r"(.+)-b\d+\.json$", os.path.basename(f))
    if not m:
        continue
    try:
        data = json.load(open(f))
    except Exception as e:
        print("unreadable", f, e)
        continue
    for pid, ans in data.items():
        runs[m.group(1)][pid] = ans


def loc_ok(ans, gold):
    loc = (ans.get("location") or "")
    if not gold or not loc:
        return False
    for g in gold:
        gf, gl = g.rsplit(":", 1)
        gl = re.match(r"\d+", gl).group(0)
        for m in re.finditer(r"([\w./-]+):(\d+)", loc):
            if os.path.basename(m.group(1)) == os.path.basename(gf) and abs(int(m.group(2)) - int(gl)) <= 10:
                return True
    return False


rows = []
for run, answers in sorted(runs.items()):
    for pid, k in key.items():
        a = answers.get(pid)
        if a is None:
            rows.append(dict(run=run, id=pid, kind=k["kind"], label=k["label"], verdict="missing"))
            continue
        said = a.get("verdict")
        pos = k["label"] == "positive"
        rows.append(dict(run=run, id=pid, project=k["project"], kind=k["kind"], label=k["label"],
                         verdict=said, correct=(said == "race") == pos,
                         conf=a.get("confidence"), loc=loc_ok(a, k.get("locations", [])) if pos else "",
                         cons=(a.get("consequence") == k.get("consequence")) if pos and said == "race" else "",
                         pair=k.get("pair") or ""))
with open(os.path.join(adir, "scores.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["run", "id", "project", "kind", "label", "verdict", "correct", "conf", "loc", "cons", "pair"])
    w.writeheader()
    w.writerows(rows)

for run in sorted(runs):
    rr = [r for r in rows if r["run"] == run and r["verdict"] != "missing"]
    tp = sum(1 for r in rr if r["label"] == "positive" and r["verdict"] == "race")
    fn = sum(1 for r in rr if r["label"] == "positive" and r["verdict"] != "race")
    fp = sum(1 for r in rr if r["label"] == "negative" and r["verdict"] == "race")
    tn = sum(1 for r in rr if r["label"] == "negative" and r["verdict"] != "race")
    print(f"\n== {run}: {len(rr)} answered; TP {tp} FN {fn} FP {fp} TN {tn}; "
          f"precision {tp / max(tp + fp, 1):.2f} recall {tp / max(tp + fn, 1):.2f}")
    by = collections.defaultdict(lambda: [0, 0])
    for r in rr:
        by[r["kind"]][0] += bool(r["correct"])
        by[r["kind"]][1] += 1
    print("  correct by kind: " + ", ".join(f"{k} {c}/{n}" for k, (c, n) in sorted(by.items())))
    pos = [r for r in rr if r["label"] == "positive" and r["verdict"] == "race"]
    print(f"  of {len(pos)} positives called race: location within 10 lines {sum(1 for r in pos if r['loc'])}, consequence right {sum(1 for r in pos if r['cons'])}")
    # pairs: buggy called race AND its fixed version called no-race
    byid = {r["id"]: r for r in rr}
    pairs = [(i, key[i]["pair"]) for i in key if key[i]["label"] == "positive" and key[i].get("pair")]
    both = sum(1 for p, n in pairs if p in byid and n in byid and byid[p]["verdict"] == "race" and byid[n]["verdict"] != "race")
    print(f"  pairs told apart (buggy race, fixed no-race): {both}/{len(pairs)}")
    print("  per item: " + " ".join(f"{r['project'][:10]}/{r['kind'][:3]}={'ok' if r['correct'] else 'XX'}" for r in sorted(rr, key=lambda r: (r['kind'], r['project']))))
