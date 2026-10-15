# 01 master-worker with ANY_SOURCE

Ranks: 3. Master (0) does `Recv(ANY_SOURCE)` twice and answers `st.MPI_SOURCE`. Workers send a
result, then receive work. Rank 2 is delayed so the observed run is `r0 <- rank1`, `r1 <- rank2`.

Alternative matching: `r0 <- rank2`, `r1 <- rank1` (a single swap).

Expected reports:
- L1: swap feasible at every B; class **1f benign**; continuation of rank 0 after `r0` is
  **metadata-dependent** (the destination of the next send is `MPI_SOURCE`), so the trace alone
  cannot certify what follows: this is the L1/L2 boundary probe.
- L1+SP (single-path assumption, i.e. the recorded call sequence with `dst = 1` frozen): at B = 0 the
  explorer reports a **spurious deadlock** (rank 0 would send work to rank 1, which is still blocked
  in its own send). The real program sends to `MPI_SOURCE = 2` and completes. This is the unsoundness
  of the single-path assumption on metadata-dependent code: MOPPER's setting cannot be applied here.
- L2 (DSL twin with the `MPI_SOURCE` dependency): both continuations are symmetric; no bug.

Gate role: shows the boundary; contributes nothing to criteria 1–3 by itself.
