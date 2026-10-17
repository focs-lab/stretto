# WP0 gate report (bootstrap run, 2026-09-05)

> Superseded by `gate.md` (benchmark-level criteria, 2026-09-07). Kept as the idiom-level record.

Status: **infrastructure complete; gate (b) passed on the idiom set; gate (a) first pass done,
taint-level classification pending.** The three criteria of proposal §15 are restated with the
bug taxonomy of `analysis.md` section B.

## (b) Hand-check of the mini-model on the idioms

All ten idioms traced on Open MPI 4.1.6 and MPICH 4.2.0 (`scripts/run_all.sh`,
`MPI=mpich scripts/run_idiom.sh NN`), analysed by `rematch`, and confirmed by forced replay
where a prediction was made. Ground truth by the exhaustive explorer over the DSL twins; 47
unit tests pass (`analysis/tests`).

| idiom | wildcard | L1 finding (trace only) | L1+SP finding (single-path assumed) | replay | invisible to an observational tool on this trace? |
|---|---|---|---|---|---|
| 01 master–worker | ANY_SOURCE | 1f benign, metadata-dependent continuation | **spurious** 1d at B=0 (SP is unsound here) | n/a | yes, and correctly benign at L2 |
| 02 Waitany loop | Waitany | waitany-order, metadata-dependent | none | n/a | yes, benign at L2 |
| 03 head-to-head | none | 2-buffering: deadlock for eager threshold < 400 B | — | hang with 400 KB message or eager limit 256 B; finishes at default | yes (MUST reports it only if it happens) |
| 04 orphan send | ANY_SOURCE | 1f | 1c orphan + 1d deadlock | **hang** | yes |
| 05 count mismatch | ANY_SOURCE | **1a** (32 B into 16 B) | 1a truncation + 1c orphan | **MPI_ERR_TRUNCATE abort** | yes |
| 06 collective after ANY_SOURCE | ANY_SOURCE | 1f, continuation undetermined | 1d (wrong mechanism) | hang | yes; real mechanism only at L2 |
| 07 buffer reuse | none | none (family ③ needs access events) | — | — | no: MUST sees it on the observed run |
| 08 ANY_TAG cross-purpose | both | **1b** (tag 22 for a receive that got 11) | 1c + 1d | **hang** | yes |
| 09 FIFO chain | ANY_SOURCE, both | **1b**; the 2 infeasible alternatives rejected (FIFO, horizon) | 1c + 1d | **hang** | yes |
| 10 halo exchange | none | none | — | — | control: no report |

Gate criteria:

1. **≥3 of 10 idioms yield a prediction invisible to an observational tool on the same trace and
   confirmable by a hand-constructed (here: replayed) schedule** — met: 03, 04, 05, 08, 09
   (five confirmed by replay), plus 06 confirmed as a hang.
2. **≥1 of them is family ① (message race), not family ③** — met: 05 (1a), 08 (1b), 09 (1b) are
   trace-only message-race findings; 04, 08, 09 add orphan/deadlock consequences.
3. **≥1 requires going beyond single-path** — met in the strong form: 01 and 06 show that the
   single-path assumption (MOPPER's input) is *unsound* on metadata-dependent code (01 yields a
   deadlock the program cannot reach), and the L2 twins with the `MPI_SOURCE` dependency give
   the right answer. 02 shows the same for `Waitany`.

Caveat: these are idioms written to probe the model, not benchmark programs. The next step is
to run the same pipeline on the MBI / MPI-CorrBench programs that use wildcards (fetched under
`study/apps`) and count predictions that MUST does not make on the observed run.

## (a) Payload-obliviousness bet, first pass

`study/sites.csv` classifies every wildcard-source / probe / `Waitany` site in the three ECP
proxy apps that use `MPI_ANY_SOURCE` (by reading the code; no taint pass yet):

| app | wildcard-source sites | metadata-only or none | payload |
|---|---|---|---|
| AMG (hypre) | 3 (1 `Recv`, 2 `Iprobe`) + 2 `Probe(0)` + 1 `Testall` loop | 5 | 1 (`par_csr_assumed_part.c:274`: loop bound accumulated from payload, but invariant under re-matching) |
| MACSio | 1 (`memfile.c:190`, in `src-stuff`) | 1 | 0 |
| Nekbone | 3 wrappers, 10 call sites, all with a tag protocol that makes the sender unique | 13 | 0 |

Reading:

- The bet holds on this corpus: every site's *communication pattern* depends on metadata
  (`MPI_SOURCE`, `Get_count`, probe flags) or on nothing. The one payload dependence (AMG's
  assumed-partition loop) controls a receive count whose value is invariant under re-matching:
  a property the L2 skeleton must be able to express ("loop over incoming messages until a
  payload-derived total is reached" ≡ "receive all messages of this tag").
- Two patterns not in the proposal's model show up and must be added: (i) **probe-driven loops**
  (AMG `exchange_data.c`): the number of MPI calls depends on `Iprobe`/`Testall` flag timing,
  a nondeterminism atom beyond matching; (ii) **wildcard source with a unique tag** (Nekbone):
  the wildcard is syntactic only; the analysis should recognise it as deterministic (no
  candidates), which `matching.candidates` already does by construction when only one send
  carries the tag.
- Nekbone's `gp2` tree reduction (`comm_mpi.f:994`) receives its children with one shared tag:
  the call pattern is fixed but the floating-point accumulation order follows the match. This is
  the "different numerical results run to run" consequence of §4 in the proposal, seen in a real
  proxy app, and a class-1f finding with a numerical rather than a control-flow effect.
- The corpus is small (3 apps, 19 sites). The MBI / MPI-CorrBench programs with wildcards are
  fetched and are the next classification target; a taint pass (LLVM 18, system) replaces manual
  reading in WP1. NEKbone is Fortran: manual only.

Decision: **continue** (PLDI trajectory per §15: L2 covers the observed patterns). Re-evaluate
after the MBI pass and after `related-work/first-races.md` is filled.

## Environment notes

- System toolchain only; both Open MPI 4.1.6 and MPICH 4.2.0 verified (traces, analysis, replay).
- Ubuntu's MPICH is PMIx-based; its hydra launcher starts singletons. `mpirun.openmpi` launches
  MPICH binaries correctly and is used for both (`scripts/env.sh`).
- MPI-BugBench: no public repository found under github.com/tudasc or the RWTH GitLab; MBI
  (gitlab.com/MpiBugsInitiative) and MPI-CorrBench (github.com/tudasc) are fetched instead.
