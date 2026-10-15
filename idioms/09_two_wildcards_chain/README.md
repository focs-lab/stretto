# 09 availability chain through non-overtaking

Ranks: 3. Rank 0: `rA = Recv(ANY, X)`, `rB = Recv(ANY, ANY_TAG)`, `rC = Recv(1, X)`. Rank 1 sends
`s1` (X), waits, sends `s1'` (X). Rank 2 sends `s2` (Y) in between.
Observed: `rA <- s1`, `rB <- s2`, `rC <- s1'`.

Alternatives:
- `rB <- s1'`: allowed by non-overtaking only because `s1` was already consumed by `rA`. Then `rC`
  starves and `s2` is an orphan.
- `rA <- s2`: **infeasible** (tag Y is not X). The checker must reject it.
- `rB <- s1` : **infeasible** (`s1` must be consumed before `s1'` and `rA` needs an X message that
  only `s1` can provide before `rB` is posted). Negative test for availability + FIFO.

Expected reports:
- L1: `rB <- s1'` feasible; class **1b cross-purpose** (tag X instead of Y).
- L1+SP: **1c orphan** (`s2`) and **1d deadlock** (`rC`).
- Exactly one feasible alternative; the two infeasible ones are silently rejected.

Replay: `REMATCH_PIN=0:1:1` pins rank 0's second wildcard to source 1; hangs under `timeout`.
