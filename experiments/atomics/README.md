# Synchronisation through atomics on the real cases

Step 3.1 of the plan for several threads ([docs/threads.md](../../docs/threads.md), section 17):
the tracer records the atomics, fences and volatile accesses of code compiled with clang 18's
ThreadSanitizer instrumentation and linked against `tracer/bin/libstretto-tsan.so`. These are
the runs that measure what it changes on the cases where unrecorded synchronisation made the
analysis over-predict, and that the confirmed races stay predicted. a-p13, 1 Nov 2026, CPUs
8-15,48-55, Open MPI 4.1.6, the image of [Dockerfile](Dockerfile) (the DLA-Future image plus clang
18 and what HDF5, Uintah and PaRSEC add; no ThreadSanitizer runtime installed).

Each application's own script builds the instrumented variant with `TSAN=1`:
[subfiling/run.sh](../subfiling/run.sh) (HDF5: atomics and volatile accesses),
[uintah/build.sh](../uintah/build.sh) (atomics, and the volatile thread states of
`UnifiedScheduler.cc`), [dlaf/build.sh](../dlaf/build.sh) (pika, DLA-Future and the driver:
atomics), [parsec/build.sh](../parsec/build.sh) (atomics). SLATE is not instrumented: its threads
are OpenMP tasks of gcc's libgomp, which clang does not target (its OpenMP is libomp's, not in the
images), and its synchronisation is OpenMP's and locks', which the tracer already follows.
[overhead.sh](overhead.sh) times the tracer with and without the recording.

## Over-predictions (b of the step's acceptance)

Before and after in one session; "before" is the same build traced with `STRETTO_ATOMICS=0`
(DLA-Future) or the gcc build (HDF5, Uintah).

| case | before | with the atomics recorded | details |
|---|---|---|---|
| HDF5 Subfiling `pre`, 13 traces | 60 data-receive alternatives, 56 across two files' pools | 5, 0 across pools (the real race, 2 traces) | [subfiling](../subfiling/README.md), `results-atomics.csv` |
| HDF5 Subfiling `fix`, 13 traces | (0 on 30 Oct) | 0 | the same |
| DLA-Future, 40 traces with several workers | observed `2-thread-collective` up to 15, `2-thread-wait` 2 to 6 per trace | 0 and 0 in all | [dlaf](../dlaf/README.md), `traced-atomics*.csv` |
| Uintah ARCHES nightly, 3 traces | 12 concurrent collective pairs and 2 `2-thread-collective` per trace | 0 and 0 | [uintah](../uintah/README.md) |

What escapes: Uintah's two `2-thread-wait`s per trace (a lock-free pool that claims a request
with relaxed atomics, which order nothing), and DLA-Future's `waitany-order` warnings
(completion order of pika's `Testsome` polls, not synchronisation).

## The real races stay (c)

| case | plain traces predicting the race | patched and controls |
|---|---|---|
| DLA-Future `matrix::copy`, pika instrumented | 15 of 15 (5 to 40 alternatives; 39 to 210 without the atomics) | 0 of 35 |
| PaRSEC reshapes, PaRSEC instrumented | 10 of 10 (486 to 764; 15 to 20 on 30 Oct) | 0 of 30 |
| HDF5 Subfiling within one pool | 2 of 13 traces (as with gcc: 2 of 13) | 0 of 13 `fix` |
| SLATE CALU (not instrumented: the tracer change alone) | 10 of 10, a subset of the 42 of 30 Oct (20 to 109, all in `permuteRows`; one right factor) | 0 of 10 patched |

## Replays of an instrumented program (2 Nov 2026)

[docs/threads.md](../../docs/threads.md) section 19.2. Lock gates order lock acquisitions, not
atomics. PaRSEC's instrumented build, forced replays of the reshape race
([parsec](../parsec/README.md), `replay-atomics*.csv`; 5 traces per setting, 3 alternatives
each, twice with their controls):

| traced and replayed | replays: every pin, check failed | controls right, every pin | synchronisation as the trace (replays, controls) | confirmed |
|---|---|---|---|---|
| atomics recorded | 27 of 30 | 30 of 30 | 0 of 30, 0 of 30 | 0 |
| `STRETTO_ATOMICS=0`, the same binaries | 30 of 30 | 30 of 30 | 30 of 30, 30 of 30 | 30 |

With the atomics recorded every pinned receive of every replay and control has one `Sendrecv` of
another thread more or less before it than in the trace (202 of 202 differences are of two
operations): which release an atomic acquire of PaRSEC's scheduler reads varies between runs,
and with it the happens-before. The race is realised (pins and outcome) but judged
inconclusive. Not fixed: ordering the atomics in a replay needs the order of every atomic
operation per location, of which the tracer keeps no log (one clock per location, section 17.1),
and judging the synchronisation without the atomic edges needs a third half of every clock in
the tracer (as `hafter` keeps one without the soft lock edges). A confirmation can use a trace
recorded with `STRETTO_ATOMICS=0` (30 of 30 above), whose analysis predicts from the coarser
happens-before (15 to 23 alternatives per trace instead of 718 to 765). DLA-Future's replays stay
unreplayable for another reason (section 16): pika's work stealing gives a worker other work in
every run (P-T5), which no gate on locks or MPI calls changes.

## Cost (e)

[overhead.sh](overhead.sh), wall time of `mpirun`, 5 runs per mode, interleaved, on otherwise idle
processors ([overhead-dlaf-2048.csv](overhead-dlaf-2048.csv), [overhead-dlaf.csv](overhead-dlaf.csv),
[overhead-hdf5.csv](overhead-hdf5.csv)); mean (min-max) seconds:

| application | untraced | traced, `STRETTO_ATOMICS=0` | traced, atomics recorded |
|---|---|---|---|
| DLA-Future 2x1, 10 copies of 2048x2048 (pika instrumented) | 0.93 (0.91-0.96) | 1.08 (1.05-1.13) | 1.21 (1.18-1.25) |
| DLA-Future 2x1, 1 copy of 512x512 | 0.65 (0.61-0.73) | 0.65 (0.63-0.67) | 0.66 (0.64-0.67) |
| HDF5 `t_subfiling_vfd`, instrumented build | 1.27 (1.20-1.39) | 1.27 (1.24-1.31) | 1.28 (1.24-1.30) |
| HDF5 `t_subfiling_vfd`, gcc build | 1.18 (1.13-1.27) | 1.21 (1.17-1.26), the tracer as before | |

Recording pika's atomics (about 92 000 acquires and 42 000 releases per rank and copy of
512x512) adds 12 % to a traced run of the larger copy; HDF5's few atomics cost nothing
measurable. The instrumented HDF5 build pays 7 % untraced for the calls at every memory access
that the volatile flags need.
