/* 02: Waitany loop. Two Irecv from fixed sources; the Waitany index decides
   who gets the next work item. No wildcard receive: the nondeterminism is in
   completion order, an atom distinct from matching. */
#include "../common.h"
#define TAG 7
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    if (rank == 0) {
        int res[2], work = 1, idx;
        MPI_Request req[2];
        MPI_Irecv(&res[0], 1, MPI_INT, 1, TAG, MPI_COMM_WORLD, &req[0]);
        MPI_Irecv(&res[1], 1, MPI_INT, 2, TAG, MPI_COMM_WORLD, &req[1]);
        MPI_Waitany(2, req, &idx, MPI_STATUS_IGNORE);
        MPI_Send(&work, 1, MPI_INT, idx + 1, TAG, MPI_COMM_WORLD);  /* first finisher gets work */
        MPI_Waitany(2, req, &idx, MPI_STATUS_IGNORE);
        MPI_Send(&work, 1, MPI_INT, idx + 1, TAG, MPI_COMM_WORLD);
    } else {
        int result = rank, work;
        if (rank == 2) idiom_delay_ms(100);
        MPI_Send(&result, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD);
        MPI_Recv(&work, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    }
    MPI_Finalize();
    return 0;
}
