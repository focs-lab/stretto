# Latent bugs

Three races found by the bug hunts of October 2026 in current code that no shipped program
reaches: each is reached only by a driver written for the hunt over the library's public API.
They are real (forced replays show the wrong results, and a small fix removes both the
prediction and the failure) but they count apart from the real bugs of the evaluation
([docs/findings.md](../../docs/findings.md), "Latent bugs"), and they are not tables of the
paper (`experiments/tables.py` does not read them).

| Case | Code | Race | Driver | Fix |
|---|---|---|---|---|
| `openfoam` | OpenFOAM ESI develop 68a81857c | `Pstream::exchange(Map)`: NBX with a constant tag; a failed `Iprobe` of one call takes the next call's message | `nbxExchange/nbxExchange.C`: calls in a row, data checked | none written; the existing `nbx.tuning=1` barrier removes it |
| `libsc` | libsc develop 6928417 | `sc_notify_payload_nbx` (fixed tag) and `sc_notify_recursive_nary` (per-level tags) mix consecutive calls; `sc_notify_payloadv_nbx` sends one tag and probes another (a deterministic hang, not a race) | `notify2.c`: two `sc_notify` calls in a row, senders and payloads checked | `fix.diff`: barriers after the loops, the right tag in payloadv |
| `swiftt` | Swift/T 4b3882d (ADLB) | `xlb_poll`'s `Iprobe(ANY_SOURCE, ANY_TAG)` can take a `SYNC_SUB` message, which has no handler: the server fails with "invalid tag" | `adlbsub.c`: data-dependent tasks on long container subscripts across two servers | `fix.diff`: `xlb_poll` leaves `SYNC_SUB` to the sync handler |

Why no shipped caller reaches them, and the sites the hunts showed safe, are in the findings list.
Each `run.sh` describes its mechanism, its steps and its settings in its header.

## Results

Compact tables, one row per version and process count, in `experiments/results/latent/`:

- `<case>-predict.csv`: traced runs, clean ones, clean ones predicting an alternative of `kind`,
  those alternatives, the ones replayed, and of the replays: wrong (bad output or not finished),
  pins matched, valid, control correct, and confirmed (wrong, matched, valid, control correct).
  `kind` is `cross` (a probe of one call that would take a message of another call; a flag flip
  of a failed poll included), `syncsub` (Swift/T: a probe that would take `SYNC_SUB`) or `other`
  (Swift/T: the other probe alternatives, a control sample). Versions prefixed `fix-` ran with
  `fix.diff`; `-tuning1` with `nbx.tuning=1`; `nbxmin2` is OpenFOAM's shipped Test-parallel-chunks
  with `nbx.min=2`, and `*-pbufs1` the shipped snappyHexMesh tutorials with `pbufs.tuning=1`.
- `<case>-stress.csv`: untraced runs, plain and with random send and receive delays, and how many
  failed.

`source` is the upstream code, `commit` the Stretto commit the rows ran at (d0101cd for all of
them, 29 Oct 2026). Rows with an empty commit were counted by the hunts' shell loops, not by
`history_stress.py`: libsc's payloadv hang (`nbxv`) and Swift/T's runs with all four ranks on one
processor. The `stress` steps now record both with `history_stress.py`. The per-run tables,
traces and outputs of failed runs are not in the repository; a rerun writes them to
`experiments/results/latent/<case>/` (ignored by git).

## Rerunning

Each case runs inside the experiment container from the repository root, with the tracer and the
replay library built there (`make -C tracer && make -C replay`), and an explicit processor set and
job count, for example:

    MACHINE=a-p13 NAME=lat-libsc CPUS=0-5 DETACH=0 experiments/container/run.sh \
        env JOBS=1 MAKE_JOBS=6 experiments/latent/libsc/run.sh all

`all` fetches the code at its commit (git checks it), builds it with and without the fix, runs
the predictions and the stress runs, and rewrites the compact tables (`summary`). OpenFOAM needs
flex and bison: build `experiments/latent/openfoam/Dockerfile` (`IMAGE=stretto-foam:24.04`). Its
snappyHexMesh steps (`shm-build`, `shm`) are not in `all`: the libraries take hours to build and
an analysis of one tutorial trace about ten minutes.

The scripts were assembled on 29 Oct 2026 from the hunts' own scripts, with the same settings and
seeds; the rows above come from the hunts' runs, not from these scripts.
