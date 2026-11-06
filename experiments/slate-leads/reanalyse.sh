#!/usr/bin/env bash
# Analyse the sweep's kept traces of the lead cells again with this repository's analysis
# (LEADS.md, leads F, C, K; the fix of commit "analysis: Make no observed finding of the
# overflow chain"). Each CELL_DIR holds the sweep's trace.tar.xz; one row per cell goes to stdout:
#   experiments/slate-leads/reanalyse.sh CELL_DIR... > reanalysed.csv
set -u
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
export PYTHONPATH=$REPO/analysis
commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT")
echo "cell,status,overflow_chains,classes,commit"
for d in "$@"; do
  tmp=$(mktemp -d); tar -C "$tmp" -xJf "$d/trace.tar.xz"
  timeout 3600 python3 -m stretto analyze --json "$tmp/trace" > "$tmp/report.json" 2> /dev/null
  python3 -I - "$(basename "$d")" "$tmp/report.json" "$commit" <<'EOF'
import collections, json, sys
cell, path, commit = sys.argv[1:]
try:
    d = json.load(open(path))
except ValueError:
    print(f"{cell},none,,,{commit}"); sys.exit()
merged = int(any("beyond the tracer's chain table" in n for n in d.get("notes", [])))
c = collections.Counter(f"{f['cls']}@{f['level']}" for f in d.get("findings", []))
print(f"{cell},{d.get('status', '')},{merged},{'+'.join(f'{k}:{v}' for k, v in sorted(c.items())) or '-'},{commit}")
EOF
  rm -rf "$tmp"
done
