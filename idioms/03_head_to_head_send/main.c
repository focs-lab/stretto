/* 03: head-to-head blocking sends. Deadlocks iff the eager threshold B is
   smaller than the message. argv[1] = element count (default 100 ints = 400 B). */
#include "../common.h"
#define TAG 3
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 2, rank);
    int n = argc > 1 ? atoi(argv[1]) : 100;
    int *out = malloc(sizeof(int) * n), *in = malloc(sizeof(int) * n);
    memset(out, rank, sizeof(int) * n);
    int other = 1 - rank;
    MPI_Send(out, n, MPI_INT, other, TAG, MPI_COMM_WORLD);
    MPI_Recv(in, n, MPI_INT, other, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    free(out); free(in);
    MPI_Finalize();
    return 0;
}
