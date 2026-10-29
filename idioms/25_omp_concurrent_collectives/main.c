/* 25: two OpenMP sections of every rank each call MPI_Bcast on MPI_COMM_WORLD. Collectives on
   one communicator must be ordered by the threads' synchronisation (MPI 4.1 section 12.6.2):
   the program is erroneous whatever its run does. The second section starts late so that the
   observed run matches the collectives consistently. Expected: observed 2-thread-collective. */
#include "../common.h"
#include <omp.h>
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    int a = rank == 0 ? 1 : 0, b = rank == 0 ? 2 : 0;
#pragma omp parallel sections num_threads(2)
    {
#pragma omp section
        MPI_Bcast(&a, 1, MPI_INT, 0, MPI_COMM_WORLD);
#pragma omp section
        { idiom_delay_ms(200); MPI_Bcast(&b, 1, MPI_INT, 0, MPI_COMM_WORLD); }
    }
    if (a != 1 || b != 2) printf("rank %d: broadcasts mixed up (%d, %d)\n", rank, a, b);
    MPI_Finalize();
    return 0;
}
