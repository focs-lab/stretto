/* 04: a wildcard receive followed by a specific receive. If the wildcard takes
   rank 2's message, the specific Recv(2) starves and rank 1's send is an orphan. */
#include "../common.h"
#define TAG 4
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    if (rank == 0) {
        int a, b;
        MPI_Status st;
        MPI_Recv(&a, 1, MPI_INT, MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &st);
        MPI_Recv(&b, 1, MPI_INT, 2, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    } else {
        int v = rank;
        if (rank == 2) idiom_delay_ms(100);   /* observed: wildcard takes rank 1 */
        MPI_Send(&v, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
