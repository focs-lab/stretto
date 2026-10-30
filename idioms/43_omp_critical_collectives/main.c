/* 43: MPI-CorrBench's two_collectives_9 shape. Each of two OpenMP threads of every rank calls
   MPI_Bcast of an int and then of a float array inside critical constructs. The critical
   construct serialises the broadcasts but does not order them: a rank whose threads enter the
   critical sections in another order than the root's pairs a broadcast of ints with one of
   floats (a type signature mismatch, MPI 4.1 section 12.6.2). The second thread starts late, so
   the observed run orders them alike on every rank. Expected: L1 nothing (the lock order of the
   trace is kept); L1+L 2-thread-collective. */
#include "../common.h"
#include <omp.h>
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
#pragma omp parallel num_threads(2)
    {
        int ia[4] = {rank, rank, rank, rank};
        float fa[4] = {0.5f, 0.5f, 0.5f, 0.5f};
        if (omp_get_thread_num() == 1) idiom_delay_ms(200);
#pragma omp critical
        MPI_Bcast(ia, 4, MPI_INT, 0, MPI_COMM_WORLD);
#pragma omp critical
        MPI_Bcast(fa, 4, MPI_FLOAT, 0, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
