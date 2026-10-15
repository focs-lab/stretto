# 03 head-to-head Send / Recv

Ranks: 2. Both ranks `Send` to the other, then `Recv`. The observed run completes only because the
implementation buffered the message (eager protocol). Under rendezvous (B < message size) both
sends block and the program deadlocks.

Expected reports:
- family ② deadlock, **B-tagged**: "observed matching infeasible for B < 400 bytes" (with the
  default count of 100 ints). No re-matching involved; this is the buffering-parameter result.

Replay: `scripts/run_idiom.sh 03 2` then rerun with a large count (e.g. `main 100000`) or a small
eager limit (`--mca btl_vader_eager_limit 512 --mca btl_self_eager_limit 512`); the run hangs under
`timeout`, confirming the prediction.
