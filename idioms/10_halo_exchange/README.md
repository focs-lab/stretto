# 10 halo exchange (negative control)

Ranks: any ≥ 2 (default 4). Ring exchange with `Irecv`/`Isend`/`Waitall` and an `Allreduce` per
iteration, three iterations. No wildcards, so w = 0.

Expected reports: **none**. The observed matching must be feasible at B = 0 (receives are posted
before the sends complete), and no alternative matching exists.
