/* 07: buffer race on a nonblocking send (family 3). The write to buf[0]
   happens inside the request span [Isend, Wait]. Traced only in WP0. */
#include "../common.h"
#define TAG 8
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 2, rank);
    int buf[16];
    if (rank == 0) {
        MPI_Request req;
        memset(buf, 0, sizeof buf);
        MPI_Isend(buf, 16, MPI_INT, 1, TAG, MPI_COMM_WORLD, &req);
        buf[0] = 1;                            /* BUG: buffer owned by MPI here */
        MPI_Wait(&req, MPI_STATUS_IGNORE);
    } else {
        MPI_Recv(buf, 16, MPI_INT, 0, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    }
    MPI_Finalize();
    return 0;
}
