# 06 collective participation depends on MPI_SOURCE

Ranks: 3. Rank 0 receives from `ANY_SOURCE`; if the source is 1 it drains rank 2's message and joins a
`Bcast`, otherwise it sends an ack to the source and never calls `Bcast`. Ranks 1 and 2 send, then `Bcast`. Rank 2 is delayed.

Alternative: source 2. Rank 0 sends an ack rank 2 never receives and never calls `Bcast`;
ranks 1 and 2 hang in `Bcast`.

Expected reports:
- L1: re-match `r0 <- rank2` feasible; continuation of rank 0 **undetermined** (horizon). Nothing
  more can be said from the trace alone.
- L1+SP: the single-path assumption is *wrong* here (the call sequence changes). The explorer over the
  frozen trace still reports a deadlock (rank 1's send is never drained, rank 0 waits in `Recv(2)`), but
  for the wrong reason; the L2 twin gives the real mechanism.
- L2 (DSL twin with the `MPI_SOURCE` branch): **1e divergent skeleton**, **1d deadlock** in `Bcast`,
  **1c orphan** ack.

Gate role: criterion 3 (needs a non-trivial skeleton: beyond single-path).

Replay: `STRETTO_PIN=0:0:2`; hangs under `timeout`.
