# 11 Iprobe polling loop

Ranks: 3. Rank 0 spins on `MPI_Iprobe(MPI_ANY_SOURCE)` until a message is visible, then
receives from `status.MPI_SOURCE`, twice. Ranks 1 and 2 each send once; rank 1 sends after
50 ms, rank 2 after 200 ms.

Expected:
- The trace stays small: the failed polls of each spin are one record plus a `pollrepeat`
  count, not one record per iteration.
- `probe-alt` (L1): the first successful wildcard probe could observe rank 2's message instead
  of rank 1's; the finding carries `REMATCH_PIN_PROBE=0:0:2`.
- The probe ordinal counts successful probes only, so the pin refers to the same probe in the
  replay however long each loop spins. Replaying with the pin makes rank 0 receive from rank 2
  first, and the run still completes: the alternative is realisable and benign.
