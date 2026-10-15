/* 09: availability chain through non-overtaking. Rank 1 sends twice (tag X);
   rank 2 sends once (tag Y). Rank 0: Recv(ANY, X); Recv(ANY, ANY_TAG); Recv(1, X).
   Observed: rA <- s1, rB <- s2 (rank 2), rC <- s1'. Alternative: rB <- s1'
   (FIFO allows it since s1 was consumed by rA); then rC starves and s2 is an orphan. */
#include "../common.h"
#define TAG_X 31
#define TAG_Y 32
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    if (rank == 0) {
        int a, b, c;
        MPI_Status st;
        MPI_Recv(&a, 1, MPI_INT, MPI_ANY_SOURCE, TAG_X, MPI_COMM_WORLD, &st);
        MPI_Recv(&b, 1, MPI_INT, MPI_ANY_SOURCE, MPI_ANY_TAG, MPI_COMM_WORLD, &st);
        MPI_Recv(&c, 1, MPI_INT, 1, TAG_X, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    } else if (rank == 1) {
        int v = 1, w = 11;
        MPI_Send(&v, 1, MPI_INT, 0, TAG_X, MPI_COMM_WORLD);
        idiom_delay_ms(150);                   /* observed: rank 2's message arrives first */
        MPI_Send(&w, 1, MPI_INT, 0, TAG_X, MPI_COMM_WORLD);
    } else {
        int y = 2;
        idiom_delay_ms(50);
        MPI_Send(&y, 1, MPI_INT, 0, TAG_Y, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
