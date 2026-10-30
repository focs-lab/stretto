#!/usr/bin/env python3
"""Turn condition-3 answers (findings in a whole source tree) into score.py's per-item answers.
usage: c3_site.py key.json raw_dir out_dir [top]   -> writes out_dir/<run>-b<k>.json, prints each finding
top (default 5): only a judge's first `top` findings count (1 = its most likely finding alone).

A condition-3 judge sees a tree, not a question about one site, and lists up to 5 findings. A
finding counts for the item of its tree when it lies in the code the excerpt condition showed for
that item: a file of the item's excerpt (matched by path suffix) and a line within 10 lines of
one of the excerpt's ranges. The item's verdict is "race" when at least one finding counts; its
location is the counting findings' locations (score.py then checks them against the gold line),
its consequence and confidence those of the first counting finding. A finding outside the code
under study counts for nothing and is listed with what it hits among the known sites of the same
source (SAME below)."""
import json, os, re, sys, glob

key = json.load(open(sys.argv[1]))
raw, out = sys.argv[2], sys.argv[3]
TOP = int(sys.argv[4]) if len(sys.argv) > 4 else 5
os.makedirs(out, exist_ok=True)
SLACK = 10
# items whose sites are present, unchanged, in another item's condition-3 tree
SAME = {"21631d": ["c06ed6"], "c06ed6": ["21631d"], "fc1e24": ["c06ed6"],
        "1bff96": ["c869a1"], "c869a1": ["1bff96"], "65f689": ["10a10b"], "10a10b": ["65f689"]}


def locs(text, pid):
    text = (text or "").replace(f"/t/{pid}/", "")
    return [(re.sub(r"^(\./)+", "", m.group(1)), int(m.group(2)))
            for m in re.finditer(r"([\w./+-]+):(\d+)", text)]


def same_file(f, part):
    return f == part or part.endswith("/" + f) or f.endswith("/" + part)


def in_component(f, line, item):
    for p in key[item].get("excerpt_parts", []):
        if same_file(f, p["file"]) and any(lo - SLACK <= line <= hi + SLACK for lo, hi in p["ranges"]):
            return True
    return False


def at_gold(f, line, item):
    for g in key[item].get("locations", []):
        gf, gl = g.rsplit(":", 1)
        if os.path.basename(gf) == os.path.basename(f) and abs(int(re.match(r"\d+", gl).group(0)) - line) <= SLACK:
            return True
    return False


for path in sorted(glob.glob(os.path.join(raw, "*.json"))):
    data = json.load(open(path))
    conv = {}
    for pid, ans in data.items():
        finds = (ans.get("findings") or [])[:TOP]
        hits = []
        print(f"{os.path.basename(path)} {pid} {key[pid]['project']} / {key[pid]['component']} "
              f"({key[pid]['kind']}): {len(finds)} findings")
        for n, f in enumerate(finds, 1):
            ls = locs(f.get("location"), pid)
            mine = any(in_component(a, b, pid) for a, b in ls)
            gold = any(at_gold(a, b, pid) for a, b in ls)
            other = [o for o in SAME.get(pid, []) if any(at_gold(a, b, o) or in_component(a, b, o) for a, b in ls)]
            tag = ("own item, gold line" if gold else "own item, code under study") if mine else "outside the code under study"
            if other:
                tag += "; also " + ", ".join(f"{o} ({key[o]['kind']}, {'gold line' if any(at_gold(a, b, o) for a, b in ls) else 'code under study'})" for o in other)
            print(f"  {n}. {f.get('location')} [{f.get('consequence')}, conf {f.get('confidence')}] -> {tag}")
            if mine:
                hits.append(f)
        if hits:
            conv[pid] = {"verdict": "race", "confidence": hits[0].get("confidence"),
                         "location": "; ".join(h.get("location") or "" for h in hits),
                         "consequence": hits[0].get("consequence")}
        else:
            conv[pid] = {"verdict": "no-race", "confidence": None, "location": None, "consequence": "none"}
    with open(os.path.join(out, os.path.basename(path)), "w") as fh:
        json.dump(conv, fh, indent=1)
