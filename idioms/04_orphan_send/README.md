# 04 orphan send

Ranks: 3. Rank 0: `Recv(ANY_SOURCE)` then `Recv(2)`. Ranks 1 and 2 each send once. Rank 2 is delayed
so the observed run is wildcard <- 1, specific <- 2, and the run completes.

Alternative: wildcard <- 2. Then `Recv(2)` has no message, rank 1's send is never consumed.

Expected reports:
- L1: re-match `r0 <- rank2` feasible at every B; the continuation of rank 0 is cut at the horizon.
- L1+SP: with the single-path assumption the whole run is determined: **1c orphan send** (rank 1)
  and **1d deadlock** (rank 0 blocks in `Recv(2)`). Both are confirmed by the explorer.

Replay: `STRETTO_PIN=0:0:2` pins rank 0's first wildcard to source 2; the run hangs under `timeout`.

Gate role: criterion 1 (invisible to MUST on the observed trace) and criterion 2 (family ①).
