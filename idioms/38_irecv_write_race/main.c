/* 38: a write to a pending receive buffer (family 3). Rank 1 sends N ints with MPI_Isend and
   both ranks meet at a barrier; rank 0 then posts MPI_Irecv and writes the first element
   before MPI_Wait. The message may be in place when the posting returns (an eager message that
   has arrived, or one a rendezvous fetches at once), and then the write corrupts it, or arrive
   later and overwrite the write: neither is ordered. Rank 0 says which happened. */
#include "../common.h"
#define N 1000
#define DATA 1
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 2, rank);
    int *buf = malloc(N * sizeof(int));
    MPI_Request req;
    if (rank == 0) {
        MPI_Barrier(MPI_COMM_WORLD);
        MPI_Irecv(buf, N, MPI_INT, 1, DATA, MPI_COMM_WORLD, &req);
        buf[0] = -1;                           /* BUG: the buffer is MPI's until the Wait */
        MPI_Wait(&req, MPI_STATUS_IGNORE);
        printf("rank 0: first element %s\n", buf[0] == 0 ? "from the message" : "the program's write");
    } else {
        for (int i = 0; i < N; i++) buf[i] = i;
        MPI_Isend(buf, N, MPI_INT, 0, DATA, MPI_COMM_WORLD, &req);
        MPI_Barrier(MPI_COMM_WORLD);
        MPI_Wait(&req, MPI_STATUS_IGNORE);
    }
    free(buf);
    MPI_Finalize();
    return 0;
}
