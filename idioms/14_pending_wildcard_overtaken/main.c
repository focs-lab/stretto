/* 14: a pending wildcard receive that a later receive overtakes. Rank 0 posts Irecv r(ANY),
   then Recv r3(ANY), which takes rank 3's message, sends x to rank 2 and waits for r, which
   takes rank 1's message; a last Recv(ANY) takes sC, which rank 2 sends after receiving x.
   r <- sC is infeasible: r is posted before r3 and accepts rank 3's message, so r is matched
   before r3 completes, and sC is sent only after r3 completes. Happens-before at r's
   completion (its wait) finds sC concurrent with it and selects the steal; at r's posting it
   does not. Pinning r to rank 2 lets r3 take rank 3's message while r is pending, so the
   pinned run matches every pin although no run of this program does. */
#include "../common.h"
#define TAG_T 41
#define TAG_U 42
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 4, rank);
    if (rank == 0) {
        int a, b, c, x = 7;
        MPI_Request r;
        MPI_Irecv(&a, 1, MPI_INT, MPI_ANY_SOURCE, TAG_T, MPI_COMM_WORLD, &r);
        MPI_Recv(&b, 1, MPI_INT, MPI_ANY_SOURCE, TAG_T, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        MPI_Send(&x, 1, MPI_INT, 2, TAG_U, MPI_COMM_WORLD);
        MPI_Wait(&r, MPI_STATUS_IGNORE);
        MPI_Recv(&c, 1, MPI_INT, MPI_ANY_SOURCE, TAG_T, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    } else if (rank == 1) {
        int v = 1;
        MPI_Send(&v, 1, MPI_INT, 0, TAG_T, MPI_COMM_WORLD);
    } else if (rank == 2) {
        int x, v = 2;
        MPI_Recv(&x, 1, MPI_INT, 0, TAG_U, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        MPI_Send(&v, 1, MPI_INT, 0, TAG_T, MPI_COMM_WORLD);
    } else {
        int v = 3;
        idiom_delay_ms(100);                   /* observed: rank 1's message reaches r first */
        MPI_Send(&v, 1, MPI_INT, 0, TAG_T, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
