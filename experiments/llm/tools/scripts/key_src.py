#!/usr/bin/env python3
"""Assemble key.json and batches.json of the blind benchmark.
usage: key_src.py meta.json sizes.txt pairdiff.txt out_dir"""
import json, sys, random

meta = json.load(open(sys.argv[1]))
sizes = dict(l.split()[::-1] for l in open(sys.argv[2]) if l.strip())   # "<MB> <id>"
pdiff = dict((l.split()[0], int(l.split()[1])) for l in open(sys.argv[3]) if l.strip())
out = sys.argv[4]

E = 'repository docs/results.md'   # committed tables
K = {}

def put(i, **kw):
    K[i] = kw

DRV = ('driver comments neutralised in every copy of the written driver (both members of a pair): '
       'header rewritten without the purpose of the run, the experiment and the mechanism')

# ---------------------------------------------------------------- documented
put('a55842', project='HYPRE', component='ParaSails', version='2.10.1 (release archive)', label='positive',
    kind='documented', pair='520bef',
    locations=['distributed_ls/ParaSails/ParaSails.c:166'], consequence='hang',
    conditions=('-pout 0 (no statistics Allreduce between the exchanges); a pruned-rows request probe '
                '(tag 222) takes a neighbour\'s stored-rows request of the next exchange (same tag)'),
    public_before_mid_2026='yes (HYPRE 2.11.0 CHANGELOG 2016; NINJA paper, PPoPP 2017)',
    strips=[], stretto={
        'predicted_from_clean_traces': 'yes: 18 of 20 traced runs at 4 ranks (29 cross alternatives), 20 of 20 at 8 (280)',
        'confirmed_by_replay': 'yes: 29/29 and 280/280 cross replays hang, controls finish',
        'data': 'experiments/results/hypre/predict.csv; ' + E + ' E5c'})
put('520bef', project='HYPRE', component='ParaSails', version='2.11.0 (release archive; fix bc3158ee)', label='negative',
    kind='fixed', pair='a55842', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='yes (fix public since 2016)',
    strips=['tree only: CHANGELOG line "- Fixed an MPI bug that was causing Parasails to hang occasionally" removed'],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 20+20 traced runs (4, 8 ranks)',
             'confirmed_by_replay': 'no race: 40+220 same-exchange replays finish with the control residual',
             'data': 'experiments/results/hypre/predict-2.11.0.csv; ' + E + ' E5c'})
put('0d199a', project='NPB', component='MG', version='NPB 2.4.1 (MPI)', label='positive', kind='documented',
    pair='ccd0e2', locations=['NPB2.4-MPI/MG/mg.f:1164-1165'], consequence='wrong-result',
    conditions='the MPI_ANY_SOURCE irecv of ready() takes a halo message of another level k (tag independent of k)',
    public_before_mid_2026='yes (NPB 3.2.1 Changes.log)', strips=[],
    stretto={'predicted_from_clean_traces': 'yes: 3 of 3 traced runs at 16 ranks, 480 alternatives',
             'confirmed_by_replay': 'yes: 384 of 480 replays change the residual norm beyond the relative check',
             'data': 'experiments/results/npbmg/predict-2.4.1.csv; ' + E + ' E5d'})
put('ccd0e2', project='NPB', component='MG', version='NPB 3.3.1 (MPI)', label='negative', kind='fixed',
    pair='0d199a', locations=[], consequence='none', conditions='-', public_before_mid_2026='yes',
    strips=['tree only: Changes.log lines 171-176 (the paragraph on MPI_ANY_SOURCE in ready()) removed'],
    stretto={'predicted_from_clean_traces': 'no: 0 alternatives in 3 traced runs at 16 ranks',
             'confirmed_by_replay': 'n/a (nothing to replay)',
             'data': 'experiments/results/npbmg/predict-3.3.1.csv; ' + E + ' E5d'})
put('9e1162', project='deal.II', component='RemotePointEvaluation', version='9.3.0-rc1 (release archive)',
    label='positive', kind='documented', pair='55816f',
    locations=['include/deal.II/base/mpi_remote_point_evaluation.h:328'], consequence='wrong-result',
    conditions='consecutive evaluate_and_process calls on one cached object; a probe takes the next call\'s message',
    public_before_mid_2026='yes (PR 12363, 2021)', strips=[],
    stretto={'predicted_from_clean_traces': 'yes: 20 of 20 traced runs at 4 ranks, 1440 cross alternatives',
             'confirmed_by_replay': 'yes: 1439 of 1440 cross replays change the output',
             'data': 'experiments/results/dealii/predict-9.3.0-rc1.csv; ' + E + ' E5e'})
put('55816f', project='deal.II', component='RemotePointEvaluation', version='9.3.0 (release archive)',
    label='negative', kind='fixed', pair='9e1162', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='yes', strips=[],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 20 traced runs',
             'confirmed_by_replay': 'no race: 1920 same-call replays leave the output unchanged',
             'data': 'experiments/results/dealii/predict-9.3.0.csv; ' + E + ' E5e'})
put('8dba4d', project='deal.II', component='ConsensusAlgorithm_NBX', version='master 2c1b27fb (Oct 2019)',
    label='positive', kind='documented', pair='ec7c94', locations=['source/base/mpi.cc:979'],
    consequence='wrong-result', conditions=('back-to-back run() calls on one communicator with the same tags; a '
                                            'rank still probing after its Ibarrier takes the next call\'s request '
                                            '(the original report: segfault in process_request at 1792 ranks)'),
    public_before_mid_2026='yes (issue 8929, 2019)', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'yes: 10 of 10 traced runs at 4 ranks (240 cross flag flips), 10 of 10 at 8 (1120)',
             'confirmed_by_replay': 'yes: every cross replay prints wrong answers; 186/240 and 728/1120 with a correct control',
             'data': 'experiments/results/dealii8929/predict-2c1b27fb.csv; ' + E + ' E5g'})
put('ec7c94', project='deal.II', component='ConsensusAlgorithm_NBX', version='master f9ea3396 (Nov 2019, PR 8997)',
    label='negative', kind='fixed', pair='8dba4d', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='yes', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 10+10 traced runs',
             'confirmed_by_replay': 'n/a', 'data': 'experiments/results/dealii8929/predict-f9ea3396.csv; ' + E + ' E5g'})
put('8df9ee', project='Cabana', component='CommunicationPlan', version='fbbcc37f (parent of PR 835 merge)',
    label='positive', kind='documented', pair='a7d8e5', locations=['core/src/Cabana_CommunicationPlan.hpp:1268'],
    consequence='hang', conditions='a round-1 wildcard count receive takes a round-2 id of a faster rank (same tag)',
    public_before_mid_2026='yes (PR 835, Aug 2025)', strips=[],
    stretto={'predicted_from_clean_traces': 'yes: 15 of 15 traced runs at 3 ranks (L1+SP explorer findings)',
             'confirmed_by_replay': 'yes: 160/160 error findings replayed TIMEOUT; 138 judged runs of the program',
             'data': 'experiments/results/cabana/predict-fbbcc37f.csv; ' + E + ' E5g'})
put('a7d8e5', project='Cabana', component='CommunicationPlan', version='38170631 (PR 835 merge)',
    label='negative', kind='fixed', pair='8df9ee', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='yes',
    strips=['core/src/Cabana_CommunicationPlan.hpp (tree and excerpt): comment blocks added by the fix removed: '
            '2x "A barrier is needed because of the use of wildcard receives. This avoids successive calls '
            'interfering with each other.", 2x "Increment the mpi_tag for this round of messages ...", '
            '1x "No barrier is needed because all ranks know who they are receiving and sending to.", '
            '1x "No barrier is needed because all ranks know who they are receiving from and sending to." (with its blank line)'],
    stretto={'predicted_from_clean_traces': 'no: 0 of 15 traced runs (only round-1 reorderings, 1f/L1)',
             'confirmed_by_replay': 'n/a', 'data': 'experiments/results/cabana/predict-38170631.csv; ' + E + ' E5g'})
put('377dc2', project='stempy', component='gatherEvents', version='3.3.5 (release archive)', label='positive',
    kind='documented', pair='245580', locations=['stempy/electron_mpi.cpp:50', 'stempy/electron_mpi.cpp:43'],
    consequence='crash', conditions='the map receive (or size receive) takes another worker\'s message; cereal throws "Failed to read"',
    public_before_mid_2026='yes (issue 292, 2023)', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'yes: 15 of 15 traced runs at 4 ranks (45 cross alternatives)',
             'confirmed_by_replay': 'yes: 45/45 cross replays abort (APP-ERROR:3, cereal exception), controls correct',
             'data': 'experiments/results/stempy/predict-3.3.5-np4.csv; ' + E + ' E5g'})
put('245580', project='stempy', component='gatherEvents', version='3.3.6 (release archive)', label='negative',
    kind='fixed', pair='377dc2', locations=[], consequence='none', conditions='-', public_before_mid_2026='yes',
    strips=['stempy/electron_mpi.cpp (tree and excerpt): the fix\'s comment "// First get msg size using the source rank" '
            'restored to the earlier "// First get the message size"', DRV],
    stretto={'predicted_from_clean_traces': 'no: 0 of 15 traced runs', 'confirmed_by_replay': 'n/a',
             'data': 'experiments/results/stempy/predict-3.3.6-np4.csv; ' + E + ' E5g'})
put('ab5082', project='MFEM', component='GroupTopology::Create', version='3.3.2 (release archive)', label='positive',
    kind='documented', pair='1c72c6', locations=['general/communication.cpp:185'], consequence='crash',
    conditions='two ParMesh constructions back to back; a receive takes a group row of the next mesh (MPI_ERR_TRUNCATE or abort)',
    public_before_mid_2026='yes (c52c6243b, 2018)', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'yes: 14 of 17 clean traced runs at 4 ranks (38 cross alternatives)',
             'confirmed_by_replay': 'yes: 38/38 cross replays MPI_ERR_TRUNCATE',
             'data': 'experiments/results/mfem332/predict-3.3.2.csv; ' + E + ' E5g'})
put('1c72c6', project='MFEM', component='GroupTopology::Create', version='3.4 (release archive)', label='negative',
    kind='fixed', pair='ab5082', locations=[], consequence='none', conditions='-', public_before_mid_2026='yes',
    strips=[DRV], stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 20+20 traced runs',
                           'confirmed_by_replay': 'n/a', 'data': 'experiments/results/mfem332/predict-3.4.csv; ' + E + ' E5g'})
put('12506a', project='LAMMPS', component='Irregular::create_atom', version='3a2da51a (parent of 411c069b, 2017)',
    label='positive', kind='documented', pair='50e789',
    locations=['src/irregular.cpp:398', 'src/irregular.cpp:409'], consequence='hang',
    conditions='deadlock when the 4-byte count sends are not buffered (no eager protocol); with default eager limits the runs finish',
    public_before_mid_2026='yes (411c069b, 2017)', strips=['driver/in.displace: first comment line no longer names the experiment'],
    stretto={'predicted_from_clean_traces': 'yes: 20 of 20 traced runs at 4 ranks, 2-buffering finding B < 4 at irregular.cpp:398/409',
             'confirmed_by_replay': 'yes (forced-rendezvous reruns): 10/10 hang below the bound, 10/10 finish at it and with defaults',
             'data': 'experiments/results/lammps/predict-3a2da51a.csv; ' + E + ' E5g'})
put('50e789', project='LAMMPS', component='Irregular::create_atom', version='411c069b (2017)', label='negative',
    kind='fixed', pair='12506a', locations=[], consequence='none', conditions='-', public_before_mid_2026='yes',
    strips=['src/irregular.cpp (tree and excerpt): the fix\'s trailing comments "// Use non-blocking send to avoid possible deadlock" '
            'and "// the MPI_Barrier below marks completion" removed (both copies of the change)',
            'driver/in.displace: first comment line no longer names the experiment'],
    stretto={'predicted_from_clean_traces': 'no: 0 findings in 20 traced runs', 'confirmed_by_replay': 'n/a',
             'data': 'experiments/results/lammps/predict-411c069b.csv; ' + E + ' E5g'})
# ---------------------------------------------------------------- new
put('21631d', project='HYPRE', component='ParaSails (pattern levels)', version='3.2.0 (release archive) + ex5_levels driver',
    label='positive', kind='new', pair='fc1e24', locations=['src/distributed_ls/ParaSails/ParaSails.c:153'],
    consequence='hang', conditions='nlevels >= 2; a probe of level L takes a neighbour\'s request of level L+1 (same tag 221)',
    public_before_mid_2026='no (found Oct 2026, not reported)', strips=[],
    stretto={'predicted_from_clean_traces': 'yes: 10 of 10 traced runs at 4 ranks, 2 levels (20 cross alternatives); 0 at 1 level',
             'confirmed_by_replay': 'yes: 20/20 cross replays hang, controls finish',
             'data': 'experiments/results/hypre3/predict.csv; ' + E + ' E5f'})
put('fc1e24', project='HYPRE', component='ParaSails (pattern levels)', version='3.2.0 + even levels\' requests under tag 227 (experiments/hypre3/run.sh VARIANT=fix)',
    label='negative', kind='fixed', pair='21631d', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='no', strips=[],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 10 traced runs (4 ranks, 2 levels)',
             'confirmed_by_replay': 'no race: 240 same and 880 other replays finish',
             'data': 'experiments/results/hypre3/predict-fix.csv; ' + E + ' E5f'})
put('77ec6a', project='SuperLU_DIST', component='pdgstrs3d_newsolve', version='9f7c819 (= 9.3.1)', label='positive',
    kind='new', pair='86c6b6', locations=['SRC/double/pdgstrs3d.c:3903-3904'], consequence='hang',
    conditions='reducible matrix (forest), 1x3 grid; the L-solve loop takes an early BC_U/RD_U message, counts and drops it (hang, or wrong x)',
    public_before_mid_2026='no (found Oct 2026, not reported)', strips=[],
    stretto={'predicted_from_clean_traces': 'yes: 10 of 10 traced runs (blk12x6, 1x3x1, Open MPI), 3-7 cross alternatives each',
             'confirmed_by_replay': 'yes: 10/10 sampled cross replays hang, controls correct',
             'data': 'experiments/results/superlu3d/predict-9f7c819-openmpi-blk12x6-1x3x1.csv; ' + E + ' E5h'})
put('86c6b6', project='SuperLU_DIST', component='pdgstrs3d_newsolve', version='9f7c819 + barrier.patch (barrier at the end of the L-solve)',
    label='negative', kind='fixed', pair='77ec6a', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='no',
    strips=['experiments/superlu3d/barrier.patch applied without its 6-line explanatory comment (3 precisions), tree and excerpt'],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 10 traced runs', 'confirmed_by_replay': 'n/a',
             'data': 'experiments/results/superlu3d/predict-9f7c819-barrier-openmpi-blk12x6-1x3x1.csv; ' + E + ' E5h'})
put('d9668c', project='Quantum ESPRESSO', component='PHonon elph_prt', version='develop 615e40c (26 Oct 2026)',
    label='positive', kind='new', pair='fd3e23',
    locations=['PHonon/PH/elphon.f90:1509', 'PHonon/PH/elphon.f90:1510', 'PHonon/PH/elphon.f90:1512'],
    consequence='wrong-result', conditions='LSDA, -nk 3, k point whose two spins are in two non-ionode pools; spin 1\'s receives take spin 2\'s messages',
    public_before_mid_2026='no (code from Apr 2026; found Oct 2026, not reported)', strips=[],
    stretto={'predicted_from_clean_traces': 'yes: 6 of 6 clean traced runs (of 10; 4 were wrong on their own), 3 cross alternatives each',
             'confirmed_by_replay': 'yes: 18/18 cross replays print a wrong table',
             'data': 'experiments/results/qeelph/predict-615e40c.csv; ' + E + ' E5h'})
put('fd3e23', project='Quantum ESPRESSO', component='PHonon elph_prt', version='615e40c + spin-tags.patch',
    label='negative', kind='fixed', pair='d9668c', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='no', strips=['experiments/qeelph/spin-tags.patch applied, then its 2 comment lines removed (tree and excerpt)'],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 10 traced runs', 'confirmed_by_replay': 'n/a',
             'data': 'experiments/results/qeelph/predict-615e40c-spintags.csv; ' + E + ' E5h'})
# ---------------------------------------------------------------- latent
put('8d0015', project='OpenFOAM', component='Pstream::exchange(Map) / allToAllConsensus NBX', version='ESI develop 68a81857c (18 Oct 2026)',
    label='positive', kind='latent', pair='dcaf36', locations=['src/Pstream/mpi/UPstreamWrapping.txx:991-993'],
    consequence='wrong-result', conditions='consecutive exchange(Map) calls (same tag), nbx.tuning=0 (default); a rank still probing takes the next call\'s Issend',
    public_before_mid_2026='no (not reported; the routine\'s docs say the tag should be unique; issue 3238 of 2024 was a related tag clash elsewhere)',
    strips=[], stretto={'predicted_from_clean_traces': 'yes: 10 of 10 traced runs at 4 ranks, 8 cross alternatives each',
                        'confirmed_by_replay': 'yes: 80/80 cross replays print wrong answers, controls correct',
                        'data': 'track notes nbx-hunt (results-apollo/foam/predict-ring.csv, not committed)'})
put('dcaf36', project='OpenFOAM', component='Pstream::exchange(Map) / allToAllConsensus NBX', version='68a81857c, run with -opt-switch nbx.tuning=1 (initial MPI_Barrier)',
    label='negative', kind='fixed', pair='8d0015', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='no', strips=['none: the source is identical to 8d0015; only the command line differs'],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 10 traced runs at 4 ranks',
             'confirmed_by_replay': 'n/a', 'data': 'track notes nbx-hunt (results-apollo/foam/predict-ring-tuning1.csv)'})
put('1bff96', project='libsc', component='sc_notify_payload_nbx', version='develop 6928417',
    label='positive', kind='latent', pair='65f689', locations=['src/sc_notify.c:1960'], consequence='wrong-result',
    conditions='type NBX (opt-in), consecutive calls on one communicator with nothing between; a probe still in the loop takes the next call\'s Issend',
    public_before_mid_2026='no (not reported)', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'yes: 6 of 6 clean traced runs at 6 ranks (of 10; 4 wrong on their own), 16 cross each',
             'confirmed_by_replay': 'yes: 96/96 cross replays print wrong senders/payloads; 71 with a correct control',
             'data': 'track notes probe-hunt (a-p13 ~/probe-hunt/work/libsc/results/predict-nbx-np6.csv)'})
put('65f689', project='libsc', component='sc_notify_payload_nbx', version='6928417 + probe-hunt fix.diff (MPI_Barrier after the NBX loops; NARY barrier; NBXV tag)',
    label='negative', kind='fixed', pair='1bff96', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='no', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 10 traced runs at 6 ranks (30 over 4/6/8)',
             'confirmed_by_replay': 'n/a', 'data': 'track notes probe-hunt (predict-fix-nbx-np6.csv)'})
put('c869a1', project='libsc', component='sc_notify_recursive_nary', version='develop 6928417',
    label='positive', kind='latent', pair='10a10b', locations=['src/sc_notify.c:1369'], consequence='crash',
    conditions='type NARY widths 3 2 2, 6 ranks, consecutive calls; a level probe takes the next call\'s message (wrong senders, then abort in sc_finalize or SEGV)',
    public_before_mid_2026='no (not reported)', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'yes: 10 of 10 traced runs at 6 ranks, 6 cross each',
             'confirmed_by_replay': 'yes: 60/60 cross replays fail (SIGNAL 6), controls correct',
             'data': 'track notes probe-hunt (predict-nary322-np6.csv)'})
put('10a10b', project='libsc', component='sc_notify_recursive_nary', version='6928417 + probe-hunt fix.diff',
    label='negative', kind='fixed', pair='c869a1', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='no', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'no: 0 cross alternatives in 10 traced runs at 6 ranks',
             'confirmed_by_replay': 'n/a', 'data': 'track notes probe-hunt (predict-fix-nary322-np6.csv)'})
put('61a354', project='Swift/T', component='ADLB xlb_poll / server sync', version='4b3882d',
    label='positive', kind='latent', pair='64c6e0', locations=['lb/code/src/server.c:376'], consequence='crash',
    conditions=('2+ servers, subscripts longer than 32 bytes: SYNC_REQUEST goes to the pre-posted sync Irecv, the '
                'following SYNC_SUB is taken by xlb_poll\'s Iprobe(ANY, ANY) when both arrive between the Test and the Iprobe; '
                '"invalid tag: 40", ADLB_Server returns an error'),
    public_before_mid_2026='no (not reported)', strips=[DRV],
    stretto={'predicted_from_clean_traces': 'yes, after dropping the trace\'s MPI_Cancel records (hp_strip.py; otherwise "degraded:unmodelled"): 10 of 10 traced runs at 4 ranks, 190 syncsub alternatives',
             'confirmed_by_replay': 'yes: 20/20 sampled replays end in the server error, controls OK; untraced stress 0/160',
             'data': 'track notes probe-hunt (a-p13 ~/probe-hunt/work/swift/results/predict-adlb-np4.csv)'})
put('64c6e0', project='Swift/T', component='ADLB xlb_poll / server sync', version='4b3882d + probe-hunt swift fix.diff (xlb_poll ignores SYNC_SUB)',
    label='negative', kind='fixed', pair='61a354', locations=[], consequence='none', conditions='-',
    public_before_mid_2026='no', strips=['lb/code/src/server.c (tree and excerpt): the fix\'s 2 comment lines removed', DRV],
    stretto={'predicted_from_clean_traces': 'alternatives remain (64 syncsub in 5 traced runs): the probe can still see the message',
             'confirmed_by_replay': 'no race: 10/10 sampled replays finish OK, controls OK',
             'data': 'track notes probe-hunt (predict-fix-adlb-np4.csv)'})
# ---------------------------------------------------------------- screened safe
put('c06ed6', project='HYPRE', component='hypre_LocateAssumedPartition via MGR', version='3.2.0 (release archive)',
    label='negative', kind='screened-safe', pair=None, locations=[], consequence='none', conditions='-',
    public_before_mid_2026='n/a', strips=[],
    note=('the ordering that makes it safe (MPI_Scan then MPI_Bcast from rank p-1 inside hypre_MGRGetSubBlock, '
          'par_mgr.c) is 3-4 caller levels above the site and outside the excerpt'),
    stretto={'predicted_from_clean_traces': 'no: 2 traced runs at 8 ranks, 31 receive alternatives at par_csr_assumed_part.c:259, all within one call; 0 cross',
             'confirmed_by_replay': 'not run', 'data': 'track notes bughunt-new (notes.md, shortlist.md "Traced and found safe")'})
put('175dd3', project='DUNE', component='IndicesSyncer::sync', version='dune-common 96774ca, dune-istl 19f47aa',
    label='negative', kind='screened-safe', pair=None, locations=[], consequence='none', conditions='-',
    public_before_mid_2026='n/a', strips=[],
    note=('safe because MatrixHierarchy::build (matrixhierarchy.hh:561, 668) runs two MPI_Allreduce between two levels\' '
          'syncs; that function is 3 caller levels above the probe and outside the excerpt'),
    stretto={'predicted_from_clean_traces': 'no: 10 traced runs (N=100, 4 ranks), 6 same-sync probe alternatives per run at tag 345 (and 6 of other kinds); 0 cross',
             'confirmed_by_replay': 'no race: 10/10 sampled replays correct, controls correct',
             'data': 'track notes probe-hunt (a-p13 ~/probe-hunt/work/dune/results/predict-n100-np4.csv); the command-line argument "100" is how the track notes state N, not checked against the log'})
put('3b67af', project='PETSc', component='MatGetRowMaxAbs_MPISBAIJ', version='3.26.0 (release archive)',
    label='negative', kind='screened-safe', pair=None, locations=[], consequence='none', conditions='-',
    public_before_mid_2026='n/a', strips=[],
    stretto={'predicted_from_clean_traces': 'alternatives yes, race no: 5 traced runs at 4 ranks, 4 cross-tag + 12 same alternatives each',
             'confirmed_by_replay': 'no race: 28/28 replays print the control\'s output (480/480 over all PETSc drivers)',
             'data': 'experiments/results/petsc/predict.csv (driver ex2-sbaij-rowmax); experiments/RUNBOOK.md "PETSc 3.26.0\'s own wildcard sites"'})
put('8fa246', project='MFEM', component='ParNCMesh::Refine (VarMessage probes)', version='4.10 (release archive)',
    label='negative', kind='screened-safe', pair=None, locations=[], consequence='none', conditions='-',
    public_before_mid_2026='n/a', strips=[],
    stretto={'predicted_from_clean_traces': 'no: 5 traced runs at 4 ranks (run 1: 3197 probe alternatives), all within one exchange',
             'confirmed_by_replay': 'no race: 4/4 sampled replays print the control\'s output (150 of 160 over all MFEM drivers, 10 inconclusive)',
             'data': 'experiments/results/mfem/predict.csv (driver ex6p); experiments/RUNBOOK.md "MFEM 4.10"'})
put('e3cf4d', project='deal.II', component='RemotePointEvaluation', version='9.8.0 (release archive)',
    label='negative', kind='screened-safe', pair=None, locations=[], consequence='none', conditions='-',
    public_before_mid_2026='n/a', strips=[],
    note='the current release of the component of 9e1162/55816f, with the CollectiveMutex; same test',
    stretto={'predicted_from_clean_traces': 'no: 5 traced runs at 4 ranks (run 1: 562 probe alternatives), all within one exchange',
             'confirmed_by_replay': 'no race: 20/20 sampled replays print the control\'s output',
             'data': 'experiments/results/dealii98/predict.csv (mapping_01); experiments/RUNBOOK.md "deal.II 9.8.0"'})
put('224d88', project='Trilinos/Zoltan', component='Zoltan_Comm_Invert_Map', version='Trilinos 6b95a944 (23 Oct 2026), packages/zoltan only',
    label='negative', kind='screened-safe', pair=None, locations=[], consequence='none', conditions='-',
    public_before_mid_2026='n/a', strips=[],
    note=('screened safe by reading only (26 Oct screen, trilinos.md: counts with Reduce+Scatter before any send; each call '
          'also starts with an MPI_Allreduce); not traced; the stated driver is a plausible path, not one that was run'),
    stretto={'predicted_from_clean_traces': 'not run', 'confirmed_by_replay': 'not run', 'data': '-'})

for i, k in K.items():
    m = meta[i]
    k['excerpt_lines'] = m['excerpt_lines']
    k['excerpt_window'] = m['window']
    k['excerpt_parts'] = m['parts']
    k['tree'] = '~/llm-bench/trees/%s (%s MB)' % (i, sizes.get(i, '?'))
    if k.get('pair') and i in pdiff:
        k['pair_excerpt_changed_lines'] = pdiff[i]
assert set(K) == set(meta), set(meta) ^ set(K)
json.dump(K, open(out + '/key.json', 'w'), indent=1, ensure_ascii=False)

# batches: groups of at most 5, no two packets of one project, random order
rng = random.Random(20261029)
ids = list(K); rng.shuffle(ids)
nb = 8
for attempt in range(10000):
    rng.shuffle(ids)
    batches = [[] for _ in range(nb)]
    ok = True
    for i in sorted(ids, key=lambda x: -sum(K[y]['project'] == K[x]['project'] for y in ids)):
        cand = [b for b in batches if len(b) < 5 and all(K[j]['project'] != K[i]['project'] for j in b)]
        if not cand:
            ok = False; break
        rng.choice(cand).append(i)
    if ok:
        break
for b in batches:
    rng.shuffle(b)
rng.shuffle(batches)
json.dump({'rule': 'groups of at most 5 packets, no two from the same project, random order (seed 20261029)',
           'batches': batches}, open(out + '/batches.json', 'w'), indent=1)
from collections import Counter
print(len(K), 'items', dict(Counter(k['kind'] for k in K.values())), dict(Counter(k['label'] for k in K.values())))
print([len(b) for b in batches])
