# 05 count mismatch under re-match

Ranks: 3. Rank 0 receives 4 ints from `ANY_SOURCE`, then 8 ints from rank 2. Rank 1 sends 4 ints,
rank 2 (delayed) sends 8 ints. Observed run is correct.

Alternative: the wildcard takes rank 2's 8-int message into a 4-int buffer.

Expected reports:
- L1: re-match `r0 <- rank2` feasible; class **1a count mismatch** (32 bytes into a 16-byte
  receive). Trace-only, metadata-only, sound.
- L1+SP: additionally **1c orphan** (rank 1's send) and **1d deadlock**: after the truncation (modelled
  as under `MPI_ERRORS_RETURN`) rank 0 continues to `Recv(2)`, whose message was consumed, and starves.

Replay: `REMATCH_PIN=0:0:2`; Open MPI aborts with `MPI_ERR_TRUNCATE`.

Gate role: criteria 1 and 2.
