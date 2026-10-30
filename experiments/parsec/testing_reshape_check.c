/*
 * Driver for reshape_check.jdf: local reshapes of every tile, run REPS times (environment,
 * default 5). Options as the other reshape tests (common.h): -N size, -t tile, -c cores,
 * -m 1 for MPI_THREAD_MULTIPLE, PaRSEC options after "--" (for example
 * "-- --mca runtime_vpmap rr:2:2:4" for two virtual processes of two threads each).
 * Rank 0 prints per repetition the reshaped copies' wrong elements (summed over ranks) and
 * whether the matrix differs from its initial values afterwards.
 */
#include <stdlib.h>
#include <string.h>

#if defined(PARSEC_HAVE_MPI)
#include <mpi.h>
#endif

#include "parsec/data_dist/matrix/two_dim_rectangle_cyclic.h"
#include "common.h"
#include "reshape_check.h"

static int set_check_value(parsec_execution_stream_t *es, const parsec_tiled_matrix_t *descA,
                           void *_A, parsec_matrix_uplo_t uplo, int m, int n, void *args)
{
    int *A = (int *)_A;
    for(int j = 0; j < descA->nb; j++)
        for(int i = 0; i < descA->mb; i++)
            A[j * descA->mb + i] = (m * 100 + n) * 1024 + i * 32 + j;
    (void)es; (void)uplo; (void)args;
    return 0;
}

int main(int argc, char *argv[])
{
    parsec_context_t* parsec;
    int rank, nodes, ch;
    int ret = 0, cret;
    parsec_matrix_block_cyclic_t dcA, dcA_check;
    parsec_taskpool_t *tp;
    int m = 0, M = 8, N = 8, MB = 4, NB = 4, P = 1, KP = 1, KQ = 1, cores = -1;
    int reps = getenv("REPS") ? atoi(getenv("REPS")) : 5, bad_reps = 0;

    DO_INIT();
    DO_INI_DATATYPES();
    if( rank == 0 ) {
        int prov;
        MPI_Query_thread(&prov);
        printf("provided %d vps %d comm_mt %d\n", prov, parsec->nb_vp,
               (parsec->flags & PARSEC_CONTEXT_FLAG_COMM_MT) ? 1 : 0);
    }

    parsec_matrix_block_cyclic_init(&dcA, PARSEC_MATRIX_INTEGER, PARSEC_MATRIX_TILE,
                                    rank, MB, NB, M, N, 0, 0, M, N, P, nodes/P, KP, KQ, 0, 0);
    dcA.mat = parsec_data_allocate((size_t)dcA.super.nb_local_tiles * (size_t)dcA.super.bsiz *
                                   (size_t)parsec_datadist_getsizeoftype(dcA.super.mtype));
    parsec_data_collection_set_key((parsec_data_collection_t*)&dcA, "dcA");
    parsec_matrix_block_cyclic_init(&dcA_check, PARSEC_MATRIX_INTEGER, PARSEC_MATRIX_TILE,
                                    rank, MB, NB, M, N, 0, 0, M, N, P, nodes/P, KP, KQ, 0, 0);
    dcA_check.mat = parsec_data_allocate((size_t)dcA_check.super.nb_local_tiles *
                                         (size_t)dcA_check.super.bsiz *
                                         (size_t)parsec_datadist_getsizeoftype(dcA_check.super.mtype));
    parsec_data_collection_set_key((parsec_data_collection_t*)&dcA_check, "dcA_check");

    for(int rep = 0; rep < reps; rep++) {
        int32_t errors = 0;
        parsec_apply(parsec, PARSEC_MATRIX_FULL, (parsec_tiled_matrix_t *)&dcA,
                     (parsec_tiled_matrix_unary_op_t)set_check_value, NULL);
        parsec_apply(parsec, PARSEC_MATRIX_FULL, (parsec_tiled_matrix_t *)&dcA_check,
                     (parsec_tiled_matrix_unary_op_t)set_check_value, NULL);
        parsec_reshape_check_taskpool_t *ctp =
            parsec_reshape_check_new((parsec_tiled_matrix_t *)&dcA, &errors);
        ctp->arenas_datatypes[PARSEC_reshape_check_DEFAULT_ADT_IDX]    = adt_default;
        ctp->arenas_datatypes[PARSEC_reshape_check_LOWER_TILE_ADT_IDX] = adt_lower;
        DO_RUN(ctp);
        cret = check_matrix_equal(dcA, dcA_check);
        int sum = errors;
        MPI_Allreduce(MPI_IN_PLACE, &sum, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD);
        if( rank == 0 )
            printf("rep %d copy errors %d matrix %s %s\n", rep, sum, cret ? "differs" : "equal",
                   (sum || cret) ? "WRONG" : "ok");
        bad_reps += (sum || cret) ? 1 : 0;
        ret |= cret;
        BARRIER;
    }
    if( rank == 0 )
        printf("summary reps %d wrong %d\n", reps, bad_reps);

    parsec_data_free(dcA.mat);
    parsec_tiled_matrix_destroy((parsec_tiled_matrix_t*)&dcA);
    parsec_data_free(dcA_check.mat);
    parsec_tiled_matrix_destroy((parsec_tiled_matrix_t*)&dcA_check);
    DO_FINI_DATATYPES();
    parsec_fini(&parsec);
#if defined(PARSEC_HAVE_MPI)
    MPI_Finalize();
#endif
    return ret;
}
