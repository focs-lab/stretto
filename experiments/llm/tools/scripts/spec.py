#!/usr/bin/env python3
"""Item spec of the blind benchmark: writes spec.json for gen.py.  usage: spec.py out.json"""
import json, sys

OMPI = 'Open MPI 4.1.6 (Ubuntu 24.04 package)'
MPICH = 'MPICH 4.2.0 (Ubuntu 24.04 package)'
items = []

def item(**kw):
    items.append(kw)

def fn(rx, **kw):
    d = {'fn': rx}; d.update(kw); return d

def lines(rx):
    return {'lines': rx}

# ---------------------------------------------------------------- HYPRE ParaSails (old releases)
def parasails(pid, prefix):
    P = prefix + 'distributed_ls/ParaSails/ParaSails.c'
    item(id=pid, lang='c',
         program=('HYPRE, a library of parallel linear solvers and preconditioners (C). The run solves a '
                  'linear system by PCG with the ParaSails sparse approximate inverse preconditioner.'),
         driver='HYPRE\'s test driver `ij` (`' + prefix + 'test/ij.c`), unmodified',
         options='mpirun -np 4 ./ij -solver 8 -pout 0', procs=4, mpi=MPICH,
         input='the driver\'s built-in default problem (a 3D Laplacian)',
         parts=[{'file': P, 'sel': [
             lines(r'^#define ROW_\w*TAG'),
             fn(r'^HYPRE_Int FindNumReplies\('),
             fn(r'^static void SendRequests\('),
             fn(r'^static void ReceiveRequest\('),
             fn(r'^static void SendReplyPrunedRows\('),
             fn(r'^static void ReceiveReplyPrunedRows\('),
             fn(r'^static void SendReplyStoredRows\('),
             fn(r'^static void ReceiveReplyStoredRows\('),
             fn(r'^static void ExchangePrunedRows\('),
             fn(r'^static void ExchangeStoredRows\('),
             fn(r'^void ParaSailsSetupPattern\('),
             fn(r'^HYPRE_Int ParaSailsSetupValues\('),
         ]}])
parasails('a55842', '')
parasails('520bef', 'src/')

# ---------------------------------------------------------------- NPB MG
def npbmg(pid, d):
    F = d + '/MG/mg.f'
    item(id=pid, lang='fortran',
         program=('The NAS Parallel Benchmarks\' MG kernel, MPI version (Fortran 77): V-cycle multigrid on '
                  'a periodic 3D grid, with halo exchanges between neighbouring processes at every grid level.'),
         driver='the benchmark itself, built with `make MG NPROCS=16 CLASS=S` in `' + d + '`',
         options='mpirun -np 16 ./mg.S.16', procs=16, mpi=MPICH,
         input='class S (32^3 grid, 4 iterations)',
         parts=[{'file': F, 'sel': [
             fn(r'^\s+program mg_mpi'),
             lines(r'^\s+msg_type\(.*\)\s*='),
             fn(r'^\s+subroutine mg3P\('),
             fn(r'^\s+subroutine psinv\('),
             fn(r'^\s+subroutine resid\('),
             fn(r'^\s+subroutine rprj3\('),
             fn(r'^\s+subroutine interp\('),
             fn(r'^\s+subroutine zran3\('),
             fn(r'^\s+subroutine comm3\('),
             fn(r'^\s+subroutine comm3_ex\('),
             fn(r'^\s+subroutine ready\('),
             fn(r'^\s+subroutine give3\('),
             fn(r'^\s+subroutine take3\('),
             fn(r'^\s+subroutine give3_ex\('),
             fn(r'^\s+subroutine take3_ex\('),
         ]}])
npbmg('0d199a', 'NPB2.4-MPI')
npbmg('ccd0e2', 'NPB3.3-MPI')

# ---------------------------------------------------------------- deal.II RemotePointEvaluation
def dealii_rpe(pid, mpi, cache_nth, mapping_nth, rpe_nth=(0,), after={}):
    rpe = [fn(r'RemotePointEvaluation<dim, spacedim>::evaluate_and_process\(', nth=k) for k in rpe_nth]
    item(id=pid, lang='cpp',
         program=('deal.II, a C++ finite element library. The run is one of its own tests: a distributed '
                  'triangulation, a finite element vector, and the values of that vector evaluated at a set '
                  'of points owned by other processes, through a cached Utilities::MPI::RemotePointEvaluation.'),
         driver='deal.II\'s test `tests/remote_point_evaluation/mapping_01.cc`, built as a program against the library (Release, MPI and p4est)',
         options='mpirun -np 4 ./mapping_01', procs=4, mpi=mpi,
         input='none (the test builds its mesh and points); its output is compared with `mapping_01.mpirun=4.output`',
         parts=[
             {'file': 'include/deal.II/base/mpi_tags.h', 'sel': [lines(r'^\s+remote_point_evaluation,')]},
             {'file': 'include/deal.II/base/mpi_remote_point_evaluation.h', 'sel': rpe},
             {'file': 'include/deal.II/numerics/vector_tools_evaluate.h', 'sel': [
                 fn(r'^\s*(value_type> )?(evaluate_at_points|point_values)\(', nth=mapping_nth, **after),
                 fn(r'^\s*(value_type> )?(evaluate_at_points|point_values)\(', nth=cache_nth, **after)]},
             {'file': 'tests/remote_point_evaluation/mapping_01.cc', 'sel': [{'range': [1, 10**6]}]},
         ])
dealii_rpe('9e1162', MPICH, 3, 2)
dealii_rpe('55816f', MPICH, 3, 2)
dealii_rpe('e3cf4d', OMPI, 1, 0, rpe_nth=(0, 1), after={'after': r'^#ifndef DOXYGEN'})

# ---------------------------------------------------------------- deal.II ConsensusAlgorithm_NBX, 2019
def dealii_nbx(pid):
    item(id=pid, lang='cpp',
         program=('deal.II, a C++ finite element library: its consensus algorithm Utilities::MPI::'
                  'ConsensusAlgorithm_NBX, by which each process finds out which processes want something '
                  'from it, used for example to find the owners of indices.'),
         driver='a program written for this run, `driver/nbx_calls.cc`, linked against the library (Release, MPI)',
         options='mpirun -np 4 ./nbx_calls 3', procs=4, mpi=OMPI,
         parts=[
             {'file': 'include/deal.II/base/mpi.h', 'sel': [lines(r'static const unsigned int tag_(request|delivery)\s*=\s*1[23];')]},
             {'file': 'source/base/mpi.cc', 'sel': [
                 fn(r'ConsensusAlgorithm_NBX<T1, T2>::run\(\)'),
                 fn(r'ConsensusAlgorithm_NBX<T1, T2>::check_own_state\(\)'),
                 fn(r'ConsensusAlgorithm_NBX<T1, T2>::signal_finish\(\)'),
                 fn(r'ConsensusAlgorithm_NBX<T1, T2>::check_global_state\(\)'),
                 fn(r'ConsensusAlgorithm_NBX<T1, T2>::process_requests\(\)'),
                 fn(r'ConsensusAlgorithm_NBX<T1, T2>::start_communication\(\)'),
                 fn(r'ConsensusAlgorithm_NBX<T1, T2>::clean_up_and_end_communication\(\)')]},
             {'file': 'driver/nbx_calls.cc', 'sel': [{'range': [1, 10**6]}]},
         ])
dealii_nbx('8dba4d')
dealii_nbx('ec7c94')

# ---------------------------------------------------------------- Cabana
def cabana(pid):
    item(id=pid, lang='cpp',
         program=('Cabana, a performance-portable C++ library for particle methods (Kokkos and MPI). The '
                  'run is one of its unit tests of Cabana::CommunicationPlan, which builds a plan of '
                  'point-to-point messages from the ids each process imports.'),
         driver='Cabana\'s unit test `Cabana_Core_Test_CommunicationPlan_MPI_SERIAL` (`core/unit_test/tstCommunicationPlan.hpp`), Kokkos Serial back end',
         options='mpirun -np 3 ./Cabana_Core_Test_CommunicationPlan_MPI_SERIAL --gtest_filter=CommPlan.Test10NoTopo',
         procs=3, mpi=OMPI,
         parts=[
             {'file': 'core/src/Cabana_CommunicationPlan.hpp', 'sel': [
                 fn(r'^\s+auto createWithoutTopology\( ExecutionSpace exec_space, Import,'),
                 fn(r'^\s+auto createWithoutTopology\( Import,')]},
             {'file': 'core/unit_test/tstCommunicationPlan.hpp', 'sel': [
                 fn(r'^\s+createFromImports\( const ViewType& element_import_ranks,'),
                 fn(r'^void test10\( const bool use_topology \)'),
                 lines(r'^TEST\( CommPlan, Test10NoTopo \)')]},
         ])
cabana('8df9ee')
cabana('a7d8e5')

# ---------------------------------------------------------------- stempy
def stempy(pid):
    item(id=pid, lang='cpp',
         program=('stempy, a toolkit for 4D scanning transmission electron microscopy data (C++ with Python '
                  'bindings). Its MPI electron counting ends by gathering every worker\'s sparse events on '
                  'rank 0 (`gatherEvents`).'),
         driver=('a harness written for this run, `driver/harness.cpp`, that includes stempy\'s own functions '
                 'from `stempy/electron_mpi.cpp` (serializeEvents through gatherBlocks, verbatim) and calls '
                 '`gatherEvents` on synthetic events; BigMPI and cereal as stempy uses them'),
         options='mpirun -np 4 ./harness 256 12 5', procs=4, mpi=OMPI,
         parts=[
             {'file': 'stempy/electron_mpi.cpp', 'sel': [
                 fn(r'^void serializeEvents\('),
                 fn(r'^void receivePartialMap\('),
                 fn(r'^void sendPartialMap\('),
                 fn(r'^void gatherEvents\(')]},
             {'file': 'driver/harness.cpp', 'sel': [{'range': [1, 10**6]}]},
         ])
stempy('377dc2')
stempy('245580')

# ---------------------------------------------------------------- MFEM 3.3.2 / 3.4
def mfem33(pid):
    item(id=pid, lang='cpp',
         program=('MFEM, a C++ finite element library, in its parallel (MPI) build with HYPRE 2.11.2 and '
                  'METIS 4. The run builds two parallel meshes, one after the other, and a finite element '
                  'space on each.'),
         driver='a program written for this run, `driver/twomesh.cpp`, linked against the library',
         options='mpirun -np 4 ./twomesh', procs=4, mpi=OMPI,
         parts=[
             {'file': 'general/communication.cpp', 'sel': [fn(r'^void GroupTopology::Create\(')]},
             {'file': 'mesh/pmesh.cpp', 'sel': [fn(r'^ParMesh::ParMesh\(MPI_Comm comm, Mesh &mesh,')]},
             {'file': 'driver/twomesh.cpp', 'sel': [{'range': [1, 10**6]}]},
         ], marks=[r'gtopo\.Create'])
mfem33('ab5082')
mfem33('1c72c6')

# ---------------------------------------------------------------- LAMMPS
def lammps(pid):
    item(id=pid, lang='cpp',
         program=('LAMMPS, a classical molecular dynamics code (C++). The run displaces every atom at '
                  'random, which migrates atoms between the processes\' subdomains, and then runs 10 steps.'),
         driver='LAMMPS itself (`src/lmp_mpi`, `make mpi`, no packages) with the input `driver/in.displace` written for this run',
         options='mpirun -np 4 ./lmp_mpi -in in.displace -log none', procs=4, mpi=OMPI,
         parts=[
             {'file': 'src/irregular.cpp', 'sel': [
                 fn(r'^void Irregular::migrate_atoms\('),
                 fn(r'^int Irregular::create_atom\('),
                 fn(r'^void Irregular::exchange_atom\(')]},
             {'file': 'src/displace_atoms.cpp', 'sel': [fn(r'^void DisplaceAtoms::command\(')]},
             {'file': 'driver/in.displace', 'lang': 'text', 'sel': [{'range': [1, 10**6]}]},
         ], marks=[r'migrate_atoms\('])
lammps('12506a')
lammps('50e789')

# ---------------------------------------------------------------- HYPRE 3.2.0 ParaSails, two levels
def parasails3(pid, tags=r'^#define (ROW_\w*TAG\w*|PRUNED_REQ_TAG)'):
    P = 'src/distributed_ls/ParaSails/ParaSails.c'
    item(id=pid, lang='c',
         program=('HYPRE, a library of parallel linear solvers and preconditioners (C). The run solves a '
                  '2D Laplacian by PCG with the ParaSails sparse approximate inverse preconditioner, whose '
                  'sparsity pattern is built over two levels.'),
         driver=('HYPRE\'s example `src/examples/ex5.c` with one option added, `-sai_levels`, that sets '
                 'ParaSails\' number of levels (nlevels of HYPRE_ParaSailsSetParams, otherwise 1): '
                 '`src/examples/ex5_levels.c`'),
         options='mpirun -np 4 ./ex5_levels -solver 8 -sai_levels 2 -n 33', procs=4, mpi=OMPI,
         parts=[{'file': P, 'sel': [
             lines(tags),
             fn(r'^HYPRE_Int FindNumReplies\('),
             fn(r'^static void SendRequests\('),
             fn(r'^static void ReceiveRequest\('),
             fn(r'^static void SendReplyPrunedRows\('),
             fn(r'^static void ReceiveReplyPrunedRows\('),
             fn(r'^static void SendReplyStoredRows\('),
             fn(r'^static void ReceiveReplyStoredRows\('),
             fn(r'^static void ExchangePrunedRows\('),
             fn(r'^static void ExchangeStoredRows\('),
             fn(r'^void ParaSailsSetupPattern\('),
             fn(r'^HYPRE_Int ParaSailsSetupValues\('),
         ]}])
parasails3('21631d')
parasails3('fc1e24')

# ---------------------------------------------------------------- SuperLU_DIST 3D solve
def slu(pid):
    S = 'SRC/double/pdgstrs3d.c'
    item(id=pid, lang='c',
         program=('SuperLU_DIST, a distributed-memory sparse direct solver (C). The run factors and solves '
                  'a sparse system on a 3D process grid with the default 3D triangular solve '
                  '(pdgstrs3d_newsolve, tree communication).'),
         driver='SuperLU_DIST\'s example `EXAMPLE/pddrive3d.c`, unmodified (CMake defaults without ParMETIS, internal BLAS, double precision; OMP_NUM_THREADS=1)',
         options='mpirun -np 3 ./pddrive3d -r 1 -c 3 -d 1 blk12x6.mtx', procs=3, mpi=OMPI,
         input=('`driver/blk12x6.mtx`, a block-diagonal matrix of 864 rows: six independent 12x12 '
                'five-point grid operators (its elimination tree is a forest)'),
         parts=[
             {'file': 'SRC/include/superlu_defs.h', 'sel': [lines(r'^static const int (BC|RD)_(L|U)=')]},
             {'file': S, 'sel': [
                 fn(r'^void dForwardSolve3d_newsolve_reusepdgstrs\(', end=r'^\} /\* dForwardSolve3d_newsolve_reusepdgstrs \*/'),
                 fn(r'^void dBackSolve3d_newsolve_reusepdgstrs\(', end=r'^\} /\* dBackSolve3d_newsolve_reusepdgstrs \*/'),
                 fn(r'^\s*int_t pdgsTrForwardSolve3d_newsolve\('),
                 fn(r'^\s*int_t pdgsTrBackSolve3d_newsolve\('),
                 fn(r'^pdgstrs3d_newsolve \(')]},
         ], marks=[r'Solve3d_newsolve(_reusepdgstrs)?\(', r'MPI_ANY'])
slu('77ec6a')
slu('86c6b6')

# ---------------------------------------------------------------- Quantum ESPRESSO elph_prt
def qe(pid):
    item(id=pid, lang='fortran',
         program=('Quantum ESPRESSO, plane-wave electronic-structure codes (Fortran). The run is a phonon '
                  'calculation (ph.x) that also prints the electron-phonon vertex at one k point '
                  '(electron_phonon=\'prt\'), for spin-polarised (LSDA) nickel, with the k points '
                  'distributed over three pools.'),
         driver=('pw.x and ph.x, built with CMake (no OpenMP, no ScaLAPACK); first `pw.x -nk 3 -in ni.scf.in` '
                 '(the test-suite\'s Ni case, LSDA), then ph.x with `driver/ph.in` (phonon at X, '
                 'fildvscf, electron_phonon=\'prt\', kx,ky,kz = -0.375, 1.125, 0.375)'),
         options='mpirun -np 3 ./ph.x -nk 3 -in ph.in', procs=3, mpi=MPICH,
         input='`driver/ni.scf.in`, `driver/ph.in`, the pseudopotential Ni.pbe-nd-rrkjus.UPF',
         parts=[
             {'file': 'PHonon/PH/elphon.f90', 'sel': [fn(r'^SUBROUTINE elph_prt\(\)')]},
             {'file': 'PHonon/PH/do_phonon.f90', 'sel': [fn(r'^SUBROUTINE do_phonon\(')]},
             {'file': 'PHonon/PH/phonon.f90', 'sel': [fn(r'^PROGRAM phonon')]},
             {'file': 'driver/ph.in', 'lang': 'text', 'sel': [{'range': [1, 10**6]}]},
         ])
qe('d9668c')
qe('fd3e23')

# ---------------------------------------------------------------- OpenFOAM exchange(Map)
def foam(pid, opts):
    PE = 'src/OpenFOAM/db/IOstreams/Pstreams/PstreamExchange.txx'
    item(id=pid, lang='cpp',
         program=('OpenFOAM (the openfoam.com code base), a C++ CFD toolbox: its parallel stream layer '
                  'Pstream, here Pstream::exchange on Map containers (a sparse exchange of per-process '
                  'lists, whose sizes are exchanged first).'),
         driver='a program written for this run, `driver/nbxExchange/nbxExchange.C`, built with wmake against libOpenFOAM and the MPI Pstream library',
         options=opts, procs=4, mpi=OMPI,
         parts=[
             {'file': 'src/Pstream/mpi/UPstreamWrapping.txx', 'sel': [
                 fn(r'^void Foam::PstreamDetail::allToAllConsensus$', nth=1)]},
             {'file': 'src/Pstream/mpi/UPstreamAllToAll.C', 'sel': [
                 {'block': [r'^#define Pstream_CommonRoutines\(Native, TaggedType\)', r'^\}\s*$'], 'nth': 1}]},
             {'file': PE, 'sel': [
                 fn(r'^void Foam::Pstream::exchangeSizes$', nth=2),
                 fn(r'^void Foam::Pstream::exchangeSizes$', nth=3),
                 fn(r'^void Foam::Pstream::exchange$', nth=2),
                 fn(r'^void Foam::Pstream::exchange$', nth=3),
                 fn(r'^void exchangeContainer$', nth=1)]},
             {'file': 'driver/nbxExchange/nbxExchange.C', 'sel': [{'range': [1, 10**6]}]},
         ])
foam('8d0015', 'mpirun -np 4 nbxExchange -parallel -calls 3 -pattern ring')
foam('dcaf36', 'mpirun -np 4 nbxExchange -parallel -calls 3 -pattern ring -opt-switch nbx.tuning=1')

# ---------------------------------------------------------------- libsc sc_notify
def libsc(pid, kind):
    common = {'file': 'src/sc_mpi.h', 'sel': [{'block': [r'^typedef enum', r'sc_tag_t;'], 'nth': 0}]}
    if kind == 'nbx':
        sel = [fn(r'^sc_notify_payload_nbx \('), fn(r'^sc_notify_payload \(')]
        opts = 'mpirun -np 6 ./notify2 nbx 2'
        prog = ('libsc, the support library of p4est (C). Its notification service sc_notify tells each '
                'process which processes will send to it, with a payload; the run selects the algorithm '
                'type SC_NOTIFY_NBX.')
    else:
        sel = [fn(r'^sc_notify_recursive_nary \('), fn(r'^sc_notify_payload_nary \('),
               fn(r'^sc_notify_payload \(')]
        opts = 'mpirun -np 6 ./notify2 nary 2 3 2 2'
        prog = ('libsc, the support library of p4est (C). Its notification service sc_notify tells each '
                'process which processes will send to it, with a payload; the run selects the algorithm '
                'type SC_NOTIFY_NARY with widths 3 2 2.')
    item(id=pid, lang='c', program=prog,
         driver='a program written for this run, `driver/notify2.c`, over libsc\'s public API (libsc built with CMake, MPI on, Release)',
         options=opts, procs=6, mpi=OMPI,
         parts=[common, {'file': 'src/sc_notify.c', 'sel': sel},
                {'file': 'driver/notify2.c', 'sel': [{'range': [1, 10**6]}]}])
libsc('1bff96', 'nbx')
libsc('65f689', 'nbx')
libsc('c869a1', 'nary')
libsc('10a10b', 'nary')

# ---------------------------------------------------------------- Swift/T ADLB
def swift(pid):
    L = 'lb/code/src/'
    item(id=pid, lang='c',
         program=('ADLB, the load balancer and distributed data store of Swift/T (C, MPI): servers hold '
                  'tasks and data, workers put and get tasks; tasks may wait on container subscripts, and '
                  'servers synchronise with each other to subscribe and notify.'),
         driver='a program written for this run, `driver/adlbsub.c`, over ADLB\'s C API (c-utils and lb built with autotools, -O2)',
         options='mpirun -np 4 ./adlbsub 40 48', procs=4, mpi=OMPI,
         input='4 ranks: 2 ADLB servers and 2 workers; 40 tasks waiting on 48-byte subscripts',
         parts=[
             {'file': L + 'messaging.h', 'sel': [
                 {'block': [r'^typedef enum', r'adlb_tag;'], 'nth': 1},
                 lines(r'SYNC_DATA_SIZE|PACKED_SUBSCRIBE_INLINE_BYTES|PACKED_SYNC_SIZE')]},
             {'file': L + 'handlers.h', 'sel': [fn(r'^xlb_handle\(adlb_tag tag, int caller\)')]},
             {'file': L + 'server.c', 'sel': [
                 fn(r'^ADLB_Server\(long max_memory\)'),
                 fn(r'^serve_several\(\)'),
                 fn(r'^xlb_poll\(int source, MPI_Status\* req_status\)'),
                 fn(r'^xlb_handle_pending\(MPI_Status\* status\)'),
                 fn(r'^xlb_serve_one\(int source\)'),
                 fn(r'^xlb_serve_server\(int source\)')]},
             {'file': L + 'sync.h', 'sel': [fn(r'^static inline adlb_code xlb_check_sync_msgs\(int \*caller\)$')]},
             {'file': L + 'sync.c', 'sel': [
                 fn(r'^xlb_sync_init\(void\)'),
                 fn(r'^xlb_sync\(int target\)'),
                 fn(r'^xlb_sync2\('),
                 fn(r'^send_subscribe_sync\('),
                 fn(r'^xlb_sync_subscribe\('),
                 fn(r'^xlb_sync_notify\('),
                 fn(r'^static xlb_sync_recv \*xlb_next_sync_msg\(void\)', nth=1),
                 fn(r'^static adlb_code xlb_sync_msg_done\(void\)', nth=1),
                 fn(r'^static adlb_code msg_from_other_server\(', nth=1),
                 fn(r'^adlb_code xlb_handle_next_sync_msg\(int caller\)'),
                 fn(r'^static adlb_code xlb_handle_subscribe_sync\(int rank,', nth=1),
                 fn(r'^adlb_code xlb_handle_notify_sync\(int rank,')]},
             {'file': 'driver/adlbsub.c', 'sel': [{'range': [1, 10**6]}]},
         ])
swift('61a354')
swift('64c6e0')

# ---------------------------------------------------------------- screened sites
item(id='c06ed6', lang='c',
     program=('HYPRE, a library of parallel linear solvers and preconditioners (C). The run solves a 3D '
              'Laplacian with the MGR (multigrid reduction) solver, whose set-up extracts sub-blocks of the '
              'matrix and builds an assumed partition for each.'),
     driver='HYPRE\'s test driver `src/test/ij.c`, unmodified',
     options='mpirun -np 8 ./ij -laplacian -n 13 13 13 -P 8 1 1 -solver 70 -mgr_bsize 2 -mgr_nlevels 1 -mgr_interp_type 4',
     procs=8, mpi=OMPI,
     parts=[{'file': 'src/parcsr_mv/par_csr_assumed_part.c', 'sel': [
         fn(r'^hypre_LocateAssumedPartition\('),
         fn(r'^hypre_AssumedPartitionCreate\('),
         fn(r'^hypre_ParCSRMatrixCreateAssumedPartition\(')]}])

item(id='175dd3', lang='cpp',
     program=('DUNE (dune-common and dune-istl), C++ numerics libraries. The run is dune-istl\'s parallel '
              'algebraic multigrid test: an AMG hierarchy is built by aggregation, and each coarsening step '
              'synchronises the remote index information of the coarse level (IndicesSyncer).'),
     driver='dune-istl\'s test `dune/istl/paamg/test/parallelamgtest.cc` (pamgtest), unmodified (Release, no ParMETIS or SuperLU)',
     options='mpirun -np 4 ./pamgtest 100', procs=4, mpi=OMPI,
     parts=[
         {'file': 'dune-common/dune/common/parallel/indicessyncer.hh', 'sel': [
             fn(r'^  void IndicesSyncer<T>::sync\(T1& numberer, bool useFixedOrder\)'),
             fn(r'^  void IndicesSyncer<T>::packAndSend\('),
             fn(r'^  void IndicesSyncer<T>::recvAndUnpack\('),
             lines(r'\b345\b')]},
         {'file': 'dune-istl/dune/istl/paamg/indicescoarsener.hh', 'sel': [
             fn(r'^    void ParallelIndicesCoarsener<T,E>::buildCoarseRemoteIndices\('),
             fn(r'^    ParallelIndicesCoarsener<T,E>::coarsen\(ParallelInformation& fineInfo,')]},
     ])

item(id='3b67af', lang='c',
     program=('PETSc, a library of parallel solvers (C). The run solves a 2D Laplacian with KSP, the matrix '
              'stored in the symmetric block format SBAIJ, and the Jacobi preconditioner built from the '
              'largest absolute value of each row.'),
     driver='PETSc\'s KSP tutorial `src/ksp/ksp/tutorials/ex2.c`, unmodified',
     options='mpirun -np 4 ./ex2 -m 32 -n 32 -mat_type sbaij -mat_ignore_lower_triangular -pc_type jacobi -pc_jacobi_type rowmax',
     procs=4, mpi=OMPI,
     parts=[
         {'file': 'src/mat/impls/sbaij/mpi/mpisbaij.c', 'sel': [fn(r'^static PetscErrorCode MatGetRowMaxAbs_MPISBAIJ\(Mat A')]},
         {'file': 'src/mat/interface/matrix.c', 'sel': [fn(r'^PetscErrorCode MatGetRowMaxAbs\(')]},
         {'file': 'src/ksp/pc/impls/jacobi/jacobi.c', 'sel': [fn(r'^static PetscErrorCode PCSetUp_Jacobi\(')]},
     ])

item(id='8fa246', lang='cpp',
     program=('MFEM, a C++ finite element library, parallel build with HYPRE 3.2.0 and METIS 4. The run is an '
              'adaptive refinement example: solve, estimate the error, refine the mesh non-conformingly '
              '(and rebalance it), repeated until a size limit.'),
     driver='MFEM\'s example `examples/ex6p.cpp`, unmodified',
     options='mpirun -np 4 ./ex6p -m ../data/star.mesh -no-vis -md 4000', procs=4, mpi=OMPI,
     parts=[
         {'file': 'general/communication.hpp', 'sel': [
             fn(r'^   static void Probe\(int &rank, int &size, MPI_Comm comm\)'),
             fn(r'^   static void IsendAll\(MapT& rank_msg, MPI_Comm comm\)'),
             fn(r'^   static void WaitAllSent\(MapT& rank_msg\)')]},
         {'file': 'mesh/pncmesh.hpp', 'sel': [lines(r'VarMessage<')]},
         {'file': 'mesh/pncmesh.cpp', 'sel': [fn(r'^void ParNCMesh::Refine\(const Array<Refinement> &refinements\)')]},
         {'file': 'mesh/pmesh.cpp', 'sel': [fn(r'^void ParMesh::NonconformingRefinement\(')]},
         {'file': 'mesh/mesh.cpp', 'sel': [fn(r'^void Mesh::GeneralRefinement\(const Array<Refinement> &refinements,')]},
     ])

item(id='224d88', lang='c',
     program=('Zoltan (Trilinos), a C library for parallel partitioning and data migration. The run '
              'partitions a small graph with recursive coordinate bisection (RCB) and migrates the objects; '
              'every data movement builds a communication plan with Zoltan_Comm_Create.'),
     driver='Zoltan\'s test driver zdrive (`src/driver`), with the test input `test/ch_simple/zdrive.inp.rcb`',
     options='mpirun -np 4 ./zdrive.exe zdrive.inp.rcb', procs=4, mpi=OMPI,
     parts=[
         {'file': 'packages/zoltan/src/Utilities/Communication/comm_invert_map.c', 'sel': [fn(r'^int\s+Zoltan_Comm_Invert_Map\(')]},
         {'file': 'packages/zoltan/src/Utilities/Communication/comm_create.c', 'sel': [fn(r'^int Zoltan_Comm_Create\(')]},
         {'file': 'packages/zoltan/src/rcb/shared.c', 'sel': [
             fn(r'^int Zoltan_RB_Send_Outgoing\('), fn(r'^int Zoltan_RB_Send_To_Part\(')]},
     ], marks=[r'Zoltan_Comm_Create\('])

json.dump(items, open(sys.argv[1], 'w'), indent=1)
print(len(items), 'items')
