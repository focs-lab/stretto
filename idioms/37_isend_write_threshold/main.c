/* 37: a write to a pending send buffer whose harm depends on the protocol (family 3).
   Rank 0 sends N ints with MPI_Isend, writes the last one, and only then sends rank 1 a token;
   rank 1 posts the data receive after the token. A message copied at the call (eager) carries
   the old value; one sent by rendezvous is read after the match, which the token puts after
   the write, so it carries the new one. Rank 1 says which it got. */
#include "../common.h"
#define N 1000
#define DATA 1
#define TOKEN 2
int main(int argc, char **argv) {
    int rank, np, token = 0;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 2, rank);
    int *buf = malloc(N * sizeof(int));
    if (rank == 0) {
        MPI_Request req;
        for (int i = 0; i < N; i++) buf[i] = i;
        MPI_Isend(buf, N, MPI_INT, 1, DATA, MPI_COMM_WORLD, &req);
        buf[N - 1] = -1;                       /* BUG: the buffer is MPI's until the Wait */
        MPI_Send(&token, 1, MPI_INT, 1, TOKEN, MPI_COMM_WORLD);
        MPI_Wait(&req, MPI_STATUS_IGNORE);
    } else {
        MPI_Recv(&token, 1, MPI_INT, 0, TOKEN, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        MPI_Recv(buf, N, MPI_INT, 0, DATA, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        printf("rank 1: last element %s\n", buf[N - 1] == N - 1 ? "as at the call" : "written after the call");
    }
    free(buf);
    MPI_Finalize();
    return 0;
}
