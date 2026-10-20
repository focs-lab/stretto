# 12 master-worker on a sub-communicator

Ranks: 4. World ranks 1, 2 and 3 form a communicator; its rank 0 (world rank 1) does
`Recv(ANY_SOURCE)` twice and answers `st.MPI_SOURCE`. World rank 3 is delayed, so the
observed run is `r0 <- world 2`, `r1 <- world 3`.

Alternative matching: `r0 <- world 3`, `r1 <- world 2`.

Expected reports:
- L1: the alternative is feasible at every B, class **1f benign**, continuation metadata-
  dependent (the reply goes to `MPI_SOURCE`), pin `STRETTO_PIN=1:0:3`.
- Replay: the pin names the sender's *world* rank; the replay library translates it into the
  communicator (rank 2 there), reports `applied widx=0 src=2 (world 3)`, and the run
  finishes. Before the translation existed, the pin would have named communicator rank 3,
  which does not exist, and the run would have failed or hung for the wrong reason.
