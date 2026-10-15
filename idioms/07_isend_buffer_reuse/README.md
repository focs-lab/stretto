# 07 buffer reuse inside a request span (family ③)

Ranks: 2. Rank 0 writes to the send buffer between `Isend` and `Wait`.

WP0 status: **traced only**. The trace shows the request span `[isend, wait]`; the memory access is
invisible to PMPI (see `docs/analysis.md`, section G). Analysis needs access instrumentation (WP2).

Expected reports in WP0: none from Rematch. MUST reports this on the observed run.
