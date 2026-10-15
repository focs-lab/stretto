# 02 Waitany loop

Ranks: 3. Rank 0 posts `Irecv` from 1 and from 2, then `Waitany` twice; the completed index picks the
destination of the next send. Rank 2 is delayed so the observed order is index 0 then 1.

Alternative: the first `Waitany` completes index 1. The matching is unchanged (sources are fixed);
only the completion order differs. Rematch models `Waitany` as a choice among requests whose sends
are available.

Expected reports:
- L1: alternative completion order feasible at every B; class **1f benign**; continuation of rank 0
  is **metadata-dependent** (the send destination is the completed index). L1/L2 boundary probe.
- L2 (DSL twin): symmetric, no bug.
