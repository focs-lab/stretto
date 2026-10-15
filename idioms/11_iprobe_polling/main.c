/* 11: master polls MPI_Iprobe(MPI_ANY_SOURCE) in a busy loop, then receives from the
   probed source. Rank 2 is delayed, so the observed run probes rank 1's message first and
   spins for a while before it. Tests that a busy loop yields a bounded trace and that the
   probe alternative (rank 2 first) is reported with a pin that a replay can apply. */
#include "../common.h"
#define TAG 11
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    if (rank == 0) {
        for (int k = 0; k < 2; k++) {
            int flag = 0, v;
            MPI_Status st;
            while (!flag)
                MPI_Iprobe(MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &flag, &st);
            MPI_Recv(&v, 1, MPI_INT, st.MPI_SOURCE, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        }
    } else {
        int v = rank;
        idiom_delay_ms(rank == 1 ? 50 : 200);
        MPI_Send(&v, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
