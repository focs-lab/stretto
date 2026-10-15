/* 10: negative control. 1-D ring halo exchange with nonblocking p2p and an
   Allreduce per iteration. No wildcards (w = 0): Rematch must report nothing. */
#include "../common.h"
#define TAG_L 41
#define TAG_R 42
#define N 64
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    if (np < 2) { if (rank == 0) fprintf(stderr, "needs >= 2 ranks\n"); MPI_Abort(MPI_COMM_WORLD, 1); }
    int left = (rank + np - 1) % np, right = (rank + 1) % np;
    double u[N + 2], hl, hr, local, global;
    for (int i = 0; i < N + 2; i++) u[i] = rank;
    for (int it = 0; it < 3; it++) {
        MPI_Request req[4];
        MPI_Irecv(&hl, 1, MPI_DOUBLE, left, TAG_R, MPI_COMM_WORLD, &req[0]);
        MPI_Irecv(&hr, 1, MPI_DOUBLE, right, TAG_L, MPI_COMM_WORLD, &req[1]);
        MPI_Isend(&u[1], 1, MPI_DOUBLE, left, TAG_L, MPI_COMM_WORLD, &req[2]);
        MPI_Isend(&u[N], 1, MPI_DOUBLE, right, TAG_R, MPI_COMM_WORLD, &req[3]);
        MPI_Waitall(4, req, MPI_STATUSES_IGNORE);
        u[0] = hl; u[N + 1] = hr;
        local = 0; for (int i = 1; i <= N; i++) local += u[i];
        MPI_Allreduce(&local, &global, 1, MPI_DOUBLE, MPI_SUM, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
