# Blind LLM benchmark: message races from reading code (29 Oct 2026)

Judges must never see this directory. It holds the answer key, the batching proposal, and the
tools that built the packets.

## Layout

- Packets (what judges see): `~/llm-bench/packets/<id>.md` on focs, 36 files. The ids
  are random 6-hex strings; nothing in a path or file name names a project, version or answer.
- Source trees (the "repository" condition): `~/llm-bench/trees/<id>/` on focs, one
  per packet, 3.3 GB in all. No `.git`, no patch files: a tree is the project's source at the
  item's commit with the item's change already applied, plus a `driver/` directory where the run
  needs a written driver or an input file.
- `key.json`: per id the project, component, version or commit, label (positive/negative), kind
  (documented / new / latent / fixed / screened-safe), true location(s) as file:line in that
  item's tree, consequence, conditions, pair id, whether the bug was public before mid-2026,
  every comment strip, the excerpt's line count, window and ranges, the tree's size, the number
  of excerpt lines that differ from the pair partner, and Stretto's result on the same item with
  its data file.
- `batches.json`: 8 batches of at most 5 packets, no two from the same project (so the two
  members of a pair never share a batch), random order, seed 20261029.
- `tools/`: `scripts/stage.sh` (builds the trees from the archives and clones on a-p13),
  `scripts/spec.py` (the per-item selection), `scripts/gen.py` (the excerpt rule and packet
  template), `scripts/key_src.py` (key and batches), `scripts/summ.py` (counts from Stretto's
  CSVs), `drivers/` (the sanitised copies of the written drivers), `patches/` (the changes of the
  fixed variants as applied), `work/` (spec.json, meta.json, tree sizes, pair diff counts).
  Everything ran on a-p13 in the container `llm-prep` (scr-dev:24.04, cpus 0-3,40-43, 24 GB),
  work directory `~/llm-bench` (mounted at /w, the home read-only at /h).

## Items: 36

| kind | count | positive / negative |
|---|---|---|
| documented (historical, real) | 8 | positive |
| new (found Oct 2026) | 3 | positive |
| latent (reached through a written driver) | 4 | positive |
| fixed (the pair partner of each positive) | 15 | negative |
| screened-safe (suspicious site shown safe) | 6 | negative |

15 positives and 21 negatives. A balanced 30 (15 + 15) is the 15 pairs alone; the 6 screened-safe
items can be added or dropped as a group.

## Selection rule

- Positives: every bug the coordinator listed, at the version with the bug, with the driver,
  options and process count of Stretto's own campaign (so the key can carry Stretto's result on
  the same configuration).
- Fixed negatives: for every positive, the version or variant Stretto ran as "the fix" in the
  same campaign, with the same driver and options. All 15 positives have one.
- Screened-safe negatives: sites screened as suspicious and then shown safe by tracing and replay
  or by reading the ordering: hypre 3.2.0 `hypre_LocateAssumedPartition` via MGR, dune
  `IndicesSyncer::sync` via pamgtest, PETSc 3.26 `MatGetRowMaxAbs_MPISBAIJ` (the 300 cross-tag
  benign replays), MFEM 4.10 `ParNCMesh::Refine` via ex6p, deal.II 9.8.0 RemotePointEvaluation
  via `mapping_01`, and Trilinos Zoltan `Zoltan_Comm_Invert_Map` (26 Oct screen, by reading).

## Excerpt rule (the same text is in every packet)

1. The functions that contain the wildcard receive/probe/poll sites of the code under study.
2. Their callers, up to two levels. A function that only forwards its arguments (Cabana's
   3-argument `createWithoutTopology`, the test's `createFromImports`, OpenFOAM's macro wrapper
   `UPstream::allToAllConsensus`, deal.II 9.8.0's forwarding `evaluate_and_process`) is shown
   but not counted as a level.
3. The functions of the same files that these call and that make the sends and collectives of
   the same exchanges (ParaSails' Send*/FindNumReplies, NPB's give3/take3, LAMMPS'
   exchange_atom, OpenFOAM's exchangeContainer), and, where the matching sends are made
   elsewhere, the sender side (Swift/T: xlb_sync, xlb_sync2, send_subscribe_sync and its two
   callers).
4. The definitions of the tags and macros used at the sites.
5. The driver's own code when the driver is a test or a program written for the run: the whole
   file if it has at most 250 lines (all of ours, deal.II's mapping_01.cc, the LAMMPS input,
   QE's ph.in), otherwise the function that makes the calls (Cabana's `test10` and its TEST
   line). Project example and application drivers (ij, ex5, ex2, ex6p, pddrive3d, mg, ph.x,
   lmp_mpi, zdrive, pamgtest) are not shown.
6. Each function with the comment block directly above it (C++ template and return-type lines
   included). A function longer than 300 lines is windowed: its first 16 and last 4 lines plus
   20 lines on each side of every MPI call and of every call of another excerpt function (10 or
   5 if the packet would otherwise pass 1500 lines; no packet needed that). Windowed functions:
   the MFEM 3.3.2/3.4 ParMesh constructor, SuperLU's dForwardSolve3d/dBackSolve3d and
   pdgstrs3d_newsolve, and QE's elph_prt (333 lines). Everything else is shown whole.
7. For a pair, both members use the same selectors (functions found by name), so the fixed
   excerpt shows the same functions; the excerpts then differ only by what differs in those
   functions between the two trees (`pair_excerpt_changed_lines` in the key).

Excerpt sizes (lines of code shown): 155 to 1472; median 509. The largest are NPB MG
(1472, 1464) and SuperLU (1234 each).

## Judgement calls

- **No versions in packets.** Packets name the project and component (the code shows them
  anyway) but not the version, commit or release, which would point a model that remembers a
  changelog at the answer. The trees still carry their version files.
- **Release pairs differ beyond the fix.** Where the fixed version is a later release, the
  excerpt shows that release's functions, which carry unrelated changes too: NPB 2.4.1 / 3.3.1
  (279 changed excerpt lines), MFEM 3.3.2 / 3.4 (198: the fix rewrote GroupTopology::Create;
  the ParMesh windows differ too), deal.II 9.3.0-rc1 / 9.3.0 (40: the mutex plus the release's
  rename of `VectorTools::evaluate_at_points` to `point_values`, in the test as well), deal.II
  2c1b27fb / f9ea3396 (34: the mutex plus a rewrite of the Waitall code in
  clean_up_and_end_communication), HYPRE 2.10.1 / 2.11.0 (25: the fix only, it threads a tag
  argument through). The other pairs differ by the fix alone (0 to 13 lines). I kept the real
  fixed versions rather than synthesising "buggy + fix commit", because Stretto's results are on
  those versions.
- **Comment strips** (every one is listed per item in the key): comments that a fix added to
  explain itself were removed from the fixed tree and excerpt (Cabana 6 blocks, LAMMPS 2
  trailing comments, stempy 1 comment restored to its earlier wording, SuperLU and QE patch
  comments not applied, Swift/T 2 lines); release notes that name the fix were removed from the
  fixed trees (HYPRE 2.11.0 CHANGELOG line, NPB 3.3.1 Changes.log paragraph). Left in place:
  deal.II's news entries in f9ea3396 and 9.3.0 (they announce CollectiveMutex as a new class,
  not the race), and upstream comments in the buggy code itself (e.g. SuperLU's "MUST have this
  barrier, otherwise the code hang" next to the GPU-only barriers, and its commented-out
  `// MPI_Barrier( grid->comm )` at the site of the fix).
- **Written drivers sanitised.** Our drivers' header comments described the purpose of the run,
  the experiment, the mechanism or "the control"; each was rewritten to a neutral description
  and usage (notify2.c, adlbsub.c, nbx_calls.cc, twomesh.cpp, harness.cpp, in.displace first
  line); code is unchanged. Both members of a pair get the same copy. Line numbers in those
  drivers therefore differ from the originals in the track directories. Residual hint: positives
  and their fixed partners run written drivers, the screened-safe negatives do not; a judge could
  learn "written driver = suspicious". Pairs keep this balanced within the positives' projects.
- **Two-level limit kept literally.** For two screened-safe items the code that makes them safe
  lies above the second caller level and is therefore not in the excerpt: hypre MGR (the
  Scan + Bcast in hypre_MGRGetSubBlock, 3-4 levels up) and dune (the two Allreduce in
  MatrixHierarchy::build, 3 levels up). Their packets show the site and its near callers only;
  the repository condition has the rest. Marked in the key's `note`.
- **Drivers chosen** where the coordinator offered a choice: HYPRE 3.2.0 levels through hypre's
  ex5 with the `-sai_levels` option (hypre-only tree) rather than PETSc ex2; SuperLU on the
  generated block-diagonal matrix blk12x6 (1x3x1), described as such in the packet, rather than
  NOS2; libsc NBX at 6 ranks with 2 calls and NARY 3 2 2 at 6 ranks, OpenFOAM ring at 4 ranks with
  3 calls (the settings with recorded Stretto results).
- **The OpenFOAM negative is a configuration**: the same tree as its positive, run with
  `-opt-switch nbx.tuning=1`, which turns on the existing initial MPI_Barrier of the NBX routine.
  The packets differ only in the command line.
- **libsc fixed trees** carry the whole probe-hunt fix.diff (barrier after the NBX loop, barrier
  after the NARY recursion, and the NBXV tag correction); each excerpt shows only its own change.
- **Swift/T fixed variant** keeps the race in the matching (the Iprobe can still see SYNC_SUB) but
  not in the behaviour; it is labelled negative because the question asks about behaviour.
- **MueLu replaced by Zoltan.** The 26 Oct screen named Tpetra computeReceives, MueLu ConstructGIDs,
  Zoltan Comm_Invert_Map and the Zoltan2 directory together (Reduce + Scatter before the sends).
  MueLu's tree needs most of Trilinos (Teuchos, Tpetra, Xpetra, Kokkos, KokkosKernels, Belos,
  Ifpack2, ...); Zoltan builds alone, so the item is Zoltan_Comm_Invert_Map with the tree
  `packages/zoltan` of Trilinos 6b95a944 (the whole Trilinos clone is 1.1 GB). Its driver
  (zdrive, ch_simple, RCB, 4 ranks) is a plausible path through Zoltan_Comm_Create that nobody
  ran; Stretto has no result on it ("not run").
- **MFEM 4.10 component**: of MFEM 4.10's screened sites the item uses ParNCMesh::Refine's
  VarMessage probes (ex6p's refinement loop), not the whole set (RedistributeElements,
  RecvRebalanceDofs, BuildParallelConformingInterpolation), to stay within the size cap with the
  same rule.
- **deal.II 9.8.0** uses the same test and component as the 9.3.0 pair (RemotePointEvaluation,
  mapping_01): the current release, with the CollectiveMutex.
- **dolfinx `nbx_consensus_rounds` not included.** The coordinator listed it as screened safe, but
  the 26 Oct shortlist lists it under "out of Stretto's reach: real hazards, not traced", and my
  reading of `cpp/dolfinx/common/MPI.cpp:182-290` (a7b1840) does not show it safe: after its
  Ibarrier completes a rank cancels its still-posted `MPI_Irecv(MPI_ANY_SOURCE, tag)` listener and,
  if the cancel fails, records the match as a source of this call. A faster rank's Issend of the
  next call with the same tag can match that listener before the cancel; the edge would be counted
  in the wrong call. That is a guess from reading, not checked by a run. Without ground truth it
  is neither a positive nor a negative here.
- Stretto results for the latent items, hypre MGR and dune come from the track directories (not
  committed): the key names the CSV on a-p13 or the notes file.

## Trees

Whole projects except Trilinos (only `packages/zoltan`). Sizes in the key; the largest are QE
(286 MB each), deal.II 9.8.0 (267), LAMMPS (258 each), OpenFOAM (243 each, the git tree of
68a81857c), Zoltan (220). What a tree does not contain: the third-party code the builds used
(Kokkos and googletest for Cabana; BigMPI and cereal for stempy; METIS and HYPRE for MFEM; p4est
for deal.II; QE's devicexlib, wannier90 and libmbd submodule archives and the Ni pseudopotential;
c-utils is in the Swift/T tree). deal.II trees have only the tests the campaigns fetched
(`tests/remote_point_evaluation`, `base`, `mpi`, `tests.h`); the 2019 deal.II trees have no tests.

## Not done

- No LLM judging (by design).
- No new Stretto runs: the Zoltan item has no Stretto result.

## Results (29 Oct 2026)

Judges were Claude Code subagents (Opus 5.5, Sonnet 5.5) given the fixed prompts in
`prompt-excerpt.txt` and `prompt-repo.txt`. Every judge's tool calls were audited from its
transcript: excerpt judges read only their packets; repository judges ran only read-only commands
inside their own tree. Answers are in `answers/`, scored by `tools/score.py` (a location counts
when it names the gold file within 10 lines).

Excerpt condition, 36 items (15 bugs, 21 without), 8 batches:

| run | precision | recall | new 3 | latent 4 | fixed 15 | screened-safe 6 |
|---|---|---|---|---|---|---|
| Opus, rep 1 | 0.80 | 0.80 | 3 | 4 | 14 | 4 |
| Opus, rep 2 | 0.87 | 0.87 | 3 | 4 | 15 | 4 |
| Sonnet, rep 1 | 0.79 | 0.73 | 2 | 4 | 14 | 4 |

Every positive a judge called a race had the right line. All runs missed NPB MG and LAMMPS (a
buffering deadlock) and called hypre's MGR site and dune-common's sync races: the order that
makes those safe is in code outside the excerpt.

Repository condition, 20 items (the new, latent and their fixed versions, and the screened-safe
sites), Opus, one judge per item with the whole tree and only the run description: 18 of 20
right (precision 0.86, recall 0.86). It found all three new bugs at the right line, told buggy
from fixed in 6 of 7 pairs, judged hypre MGR and dune-common safe, missed OpenFOAM's
`exchange(Map)` and called MFEM 4.10 a race.

Caveat for both conditions: the run descriptions name the driver and options under which each
bug occurs (for example ParaSails with two levels, `-nk 3`, a block-diagonal matrix), which the
hunts chose knowing the bugs. They are identical for a bug and its fixed version, so they do not
give the answer, but they say where to look. A judge given only a code base and no run is not
measured here.
