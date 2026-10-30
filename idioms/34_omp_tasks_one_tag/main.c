/* 34: two OpenMP tasks that no dependence orders receive from one process on one tag, the shape
   of SLATE's CALU tag collision (experiments/slate). Rank 0: one thread of a parallel region
   creates task A, MPI_Recv of 8 bytes from rank 1 with tag 5, and task B, which waits 100 ms and
   receives 400 bytes from rank 1 with tag 5. Rank 1 sends 8 bytes, then 400. Observed: A takes
   the 8 bytes. Expected: an L1 concurrent swap of the two tasks' receives, with A's receive
   taking the 400 bytes into its 8-byte buffer (1a, truncation). */
#include "../common.h"
#include <omp.h>
#define TAG 5
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        char small[8], big[400];
#pragma omp parallel num_threads(2)
#pragma omp single
        {
#pragma omp task shared(small)
            MPI_Recv(small, 8, MPI_CHAR, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
#pragma omp task shared(big)
            {
                idiom_delay_ms(100);
                MPI_Recv(big, 400, MPI_CHAR, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
            }
        }
    } else if (rank == 1) {
        char buf[400] = {0};
        MPI_Send(buf, 8, MPI_CHAR, 0, TAG, MPI_COMM_WORLD);
        MPI_Send(buf, 400, MPI_CHAR, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
