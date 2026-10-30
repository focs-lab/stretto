#!/usr/bin/env bash
# Name the call sites of a trace: one line "rank site function" per site record, from the
# object's symbol table (gdb's "info symbol"; no debug information needed).
#   experiments/slate/sites.sh TRACE_DIR > sites.txt
set -eu
for f in "$1"/rank-*.jsonl; do
  r=$(basename "$f" .jsonl); r=${r#rank-}
  grep '"op":"site"' "$f" | sed -E 's/.*"id":([0-9]+),"obj":"([^"]*)","addr":([0-9]+),"base":([0-9]+).*/\1 \2 \3 \4/' |
  while read -r id obj addr base; do
    off=$(printf '0x%x' $((addr - base - 1)))   # the return address minus one: inside the call
    name=$(gdb -batch -ex "info symbol $off" "$obj" 2>/dev/null | head -1 | sed -E 's/ in section.*//; s/ \+ [0-9]+$//' | c++filt)
    echo "$r $id ${name:-?}"
  done
done
