/* 35: idiom 34 with the tasks ordered by their dependences (negative): A has depend(out: x),
   B depend(in: x), so B starts after A has ended. A third task C, created between them with
   depend(in: y) on another address, receives on another tag and orders nothing. Expected: no
   finding. */
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
        char small[8], big[400], other[4];
        int x = 0, y = 0;
#pragma omp parallel num_threads(2)
#pragma omp single
        {
#pragma omp task shared(small) depend(out: x)
            MPI_Recv(small, 8, MPI_CHAR, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
#pragma omp task shared(other) depend(in: y)
            MPI_Recv(other, 4, MPI_CHAR, 1, TAG + 1, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
#pragma omp task shared(big) depend(in: x)
            MPI_Recv(big, 400, MPI_CHAR, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        }
        (void)x; (void)y;
    } else if (rank == 1) {
        char buf[400] = {0};
        MPI_Send(buf, 8, MPI_CHAR, 0, TAG, MPI_COMM_WORLD);
        MPI_Send(buf, 400, MPI_CHAR, 0, TAG, MPI_COMM_WORLD);
        MPI_Send(buf, 4, MPI_CHAR, 0, TAG + 1, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
