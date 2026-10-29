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
observed `2-buffering`, not examined). The untraced test passed 20
of 20 runs of `pre` (it does not show the race at this size). Not replayed: the pin library has no
pins for several threads yet. Both versions also have probe alternatives at the IOC's
`Iprobe(ANY_SOURCE, ANY_TAG)` (which header the main thread sees first; no consequence follows)
and an observed `2-thread-finalize` (the IOC thread's records are not ordered before
`MPI_Finalize` by any recorded synchronisation; not examined).
