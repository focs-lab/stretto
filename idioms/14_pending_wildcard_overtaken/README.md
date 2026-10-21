# 14 pending wildcard overtaken by a later receive

Ranks: 4. Rank 0: `r = Irecv(ANY, T)`, `r3 = Recv(ANY, T)`, `Send x -> 2`, `Wait r`,
`r4 = Recv(ANY, T)`. Rank 1 sends `s1` (T). Rank 2 receives `x`, then sends `sC` (T). Rank 3
sends `s3` (T) after a delay.
Observed: `r <- s1`, `r3 <- s3`, `r4 <- sC`.

This is the paper's example of why happens-before concurrency cannot decide an alternative
(section "The constraint graph"; `stretto.hb`).

Alternatives:
- `r <- s3`: feasible (`r3` is then left without a message).
- `r <- sC`: **infeasible**. `r` is posted before `r3` and accepts `s3`, so `r` is matched
  before `r3` completes; `sC` is sent only after `x`, which follows `r3`. Selection at the
  completion clock accepts it (`sC` is concurrent with `Wait r`); selection at the posting
  clock rejects it, since `sC` follows the posting of `r`.
- `r3 <- sC`: infeasible, `sC` causally follows `r3`.

Expected reports:
- L1: `r <- s3` feasible, class **1f**; `r <- sC` rejected with a constraint cycle.

Replay: `STRETTO_PIN=0:0:2,0:1:3` matches both pins and finishes: with its source rewritten to
rank 2, `r` no longer accepts `s3`, so `r3` takes it while `r` is pending. The replay's own trace
keeps `r`'s original envelope and has a constraint cycle (`stretto.confirm.unpinned_violation`):
no run of the program does this. The control `STRETTO_PIN=0:0:1,0:1:3` finishes as observed.
