/* 05: the alternative sender's message is larger than the receive buffer:
   MPI_ERR_TRUNCATE under the alternative matching. Trace-only, metadata only. */
#include "../common.h"
#define TAG 5
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    if (rank == 0) {
        int small[4], big[8];
        MPI_Status st;
        MPI_Recv(small, 4, MPI_INT, MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &st);
        MPI_Recv(big, 8, MPI_INT, 2, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    } else if (rank == 1) {
        int v[4] = {1, 1, 1, 1};
        MPI_Send(v, 4, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    } else {
        int v[8] = {2, 2, 2, 2, 2, 2, 2, 2};
        idiom_delay_ms(100);                  /* observed: wildcard takes rank 1 */
        MPI_Send(v, 8, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
