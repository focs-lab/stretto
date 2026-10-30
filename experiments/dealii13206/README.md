# deal.II 9.3.0 `FETools::extrapolate`: one send buffer for every `MPI_Isend`

`ExtrapolateImplementation::send_cells` (`fe_tools_extrapolate.templates.h:1146-1180` in
9.3.0) allocates a send buffer per cell to send, but never advances its iterator over them: each
cell's data is packed into the buffer of the previous `MPI_Isend`, which is still pending. Fixed
upstream by PR 13206 (b3b2b97, 9 Jan 2022, one line, `++buffer`), whose description says the
bug went unnoticed because MPI copies small messages in the call. Case #33 of the bug-class
survey of 29 Oct 2026.

Driver: deal.II's own test `mpi/fe_tools_extrapolate_03` (3 ranks, p4est, deal.II's
distributed vectors), built against 9.3.0 and against the fix (`run.sh build`).

| | as released | with the fix |
|---|---:|---:|
| traced runs with the guard at MPICH's defaults: 3-local findings | 48 in each of 10 (writes to the buffers of pending 40-, 96- and 248-byte `Isend`s, `B < 40`, `B < 96`, `B < 248`) | 0 in each of 10 |
| untraced runs that fail the test's check, at the defaults (eager up to 650 633 bytes) | 0 of 10 | 0 of 10 |
| every message by rendezvous (`UCX_RNDV_THRESH=1`) | 10 of 10 | 0 of 10 |
| threshold 247, just below the largest misused message | 10 of 10 | 0 of 10 |
| threshold 248, at it | 0 of 10 | 0 of 10 |

The failing runs abort on the test's own assertion (`out_ghosted.l2_norm() < 1e-8`, error after
extrapolation 0.0949 instead of 0 in one run looked at). The report predicts each change below
the message's size, in every execution with the observed matching for the writes the guard
caught and in some for those found by checksum, and none at or above it: the runs with the
threshold forced change exactly there. `thresholds.csv` replays each finding just below and at its own message size under the
tracer with message checksums (`experiments/guard/thresholds.py`).

Tables: `experiments/results/dealii13206/` (`predict.csv`, `replay.csv`, `thresholds.csv`), from
`run.sh all` on 30 Oct 2026 in the experiment image (4 processors, MPICH 4.2.0 over UCX).
