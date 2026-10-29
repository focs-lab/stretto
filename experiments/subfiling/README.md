# HDF5 Subfiling VFD: the wrong-worker race (known bug, several threads)

A fixed bug of HDF5's Subfiling virtual file driver, used as a known answer for the analysis of
several threads ([docs/threads.md](../../docs/threads.md), section 10). An I/O concentrator (IOC)
receives the header of each write request on its main thread (`MPI_Iprobe(MPI_ANY_SOURCE,
MPI_ANY_TAG)`, then `MPI_Recv`) and queues the request for a pool of worker threads. A worker sends
the client an ACK and receives the data with `MPI_Recv(client, WRITE_INDEP_DATA)`; the client
`MPI_Isend`s the data once it has the ACK and goes on to its next request. Two write requests of
one client to one IOC are then two worker threads receiving with one envelope, and either may take
either data message: the data lands at the other request's offset, or, when the sizes differ, a
worker's receive is truncated.

| version | commit | what |
|---|---|---|
| `pre` | 9975eaeb2 (1 Oct 2021) | the subfiling branch before the fix: one data tag for every write |
| `fix` | ffc254983 (17 Nov 2021) | "Code to issue unique tags for each write request via the ACK message": each worker sends its own tag in the ACK and receives with it |

Both commits are on the subfiling branch that PR #1464 merged (`git fetch origin
pull/1464/head`); PR #1883 squashed that history into develop as 27bb358 (22 Jul 2022), whose
message quotes the fix. The parent of 27bb358 on develop has no Subfiling VFD.

`run.sh` builds `testpar/t_subfiling_vfd` at both commits (CMake, `HDF5_BUILD_SUBFILING=ON`, in the
image of `Dockerfile`, which adds cmake and libtool to the experiment image), runs it traced on 4
ranks (one IOC, on rank 0, with its worker threads) and counts the findings; `results.csv` is its
output of 29 Oct 2026 (a-p13, Open MPI 4.1.6, 5 runs per version).

Result: every clean trace of `pre` predicts at L1 that one client's two data messages go to each
other's worker (concurrent swaps and steals between the workers' receives of tag
`WRITE_INDEP_DATA`, 0xd8da02; 4 to 6 per trace in the table, 1f: messages of one size); one of
five earlier runs, not in the table, also had two truncations (1a: an 800-byte write taken by a
worker that expects 96 bytes). No trace of `fix` has an alternative at a receive (one has an
observed `2-buffering`, not examined). The untraced test passed 20 of 20 runs of `pre` (it does
not show the race at this size). Replays: below. Both versions also have probe alternatives at
the IOC's `Iprobe(ANY_SOURCE, ANY_TAG)` (which header the main thread sees first; no consequence follows)
and an observed `2-thread-finalize` (the IOC thread's records are not ordered before
`MPI_Finalize` by any recorded synchronisation; not examined).

## Replays (30 Oct 2026)

`replay.sh` forces the predicted alternatives with chain pins and order gates
([docs/threads.md](../../docs/threads.md), section 11). Its input was a new set of traces made
with run.sh's commands in the same container as the replays (5 per version, Open MPI 4.1.6,
a-p13; the traces of `results.csv` came from another container, in which the test starts another
number of I/O concentrator threads, and chain names are not comparable across the two): every
`pre` trace predicts 5 to 8 L1 alternatives at the data receives (a 1a truncation, an 800-byte
write into a 96-byte receive, in 3 of 5), no `fix` trace has one, and all 10 runs passed. The
`fix` traces are the negative: there is nothing to replay.

`replay.csv`: 20 alternatives (4 per `pre` trace, all 1f), each replayed twice with its control
(the `commit` column says unknown: the container has no git; the code was the branch's
`confirm: Replay alternatives of several threads`).

| | finished | SIGSEGV | SIGABRT | other error | pin misapplied | gate timed out | pins matched | sync differs | wrong data read back |
|---|---|---|---|---|---|---|---|---|---|
| replays (40) | 12 | 4 | 9 | 1 | 12 | 2 | 0 | 38 | 0 |
| controls (40) | 31 | 0 | 1 | 0 | 6 | 2 | 0 | 40 | 0 |

No replay is a confirmation: no replay took its pinned messages, and every control synchronised
its threads in another order than the trace. The cause is the I/O concentrator's pool of worker
threads: which worker takes which request is decided by the pool's mutex and condition
variable, and differs from run to run (the workers' operation counts differ across the five
traced runs, with the same chain names), so a worker's k-th receive names another request in a
replay than in the trace. The gates force MPI operations only; forcing the pool's hand-out would
need the synchronisation order itself replayed, which the replay does not do (L1+SP-T's lock
gates, or a pool-aware naming of a receive by the request it serves). Crashes are more frequent
in replays (14 of 40) than in controls (1 of 40), which suggests that the gated orders reach the
race, but a crash of a replay that did not follow its pins is not attributed to the alternative.
A first run of 30 Oct gated every pair of concurrent operations instead of the pairs the
alternative reorders (13 rows, not kept): gates between workers of different pools never opened
(6 of 13 replays and 7 of 13 controls timed out), for the same reason.
