# 08 cross-purpose match through ANY_TAG

Ranks: 3. Rank 0: `Recv(ANY_SOURCE, ANY_TAG)` meant for DATA, then `Recv(2, TAG_CTRL)`. Rank 1
sends DATA, rank 2 (delayed) sends CTRL.

Alternative: the wildcard consumes CTRL from rank 2.

Expected reports:
- L1: re-match `r0 <- rank2` feasible; class **1b cross-purpose** (the alternative message carries a
  different tag than the observed one: another logical channel).
- L1+SP: **1c orphan** (DATA from rank 1) and **1d deadlock** (`Recv(2, TAG_CTRL)` starves).

Replay: `REMATCH_PIN=0:0:2`; hangs under `timeout`.

Gate role: criteria 1 and 2; the shape of the ddcMD bug.
