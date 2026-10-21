# 07 buffer reuse inside a request span (family ③)

Ranks: 2. Rank 0 writes to the send buffer between `Isend` and `Wait`.

WP0 status: **traced only**. The trace shows the request span `[isend, wait]`; the memory access is
invisible to PMPI (see `docs/analysis.md`, section G). Analysis needs access instrumentation (WP2).

Expected reports in WP0: none from Stretto. MUST reports this on the observed run.

With the buffer guard (`STRETTO_GUARD=1 scripts/run_idiom.sh 07`): an observed **3-local**, the
send buffer changed before its completion. The buffer is on the stack, whose pages the guard
never protects, so the send buffer's checksum at the `Wait` is what catches the write, not the
access itself.
