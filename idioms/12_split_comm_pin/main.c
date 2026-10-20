/* 12: master-worker on a sub-communicator whose ranks differ from the world ranks.
   Ranks 1, 2 and 3 form a communicator (rank 0 of the world stays out); its rank 0
   (world rank 1) receives from MPI_ANY_SOURCE twice. World rank 3 is delayed, so the
   observed run is r0 <- world 2, r1 <- world 3. The alternative pins r0 to world rank 3,
   which the replay library must translate into communicator rank 2.
   Expected: L1 reports the alternative as benign (1f) with STRETTO_PIN=1:0:3; the replay
   applies the pin and finishes. */
#include "../common.h"
#define TAG 12
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 4, rank);
    MPI_Comm sub;
    MPI_Comm_split(MPI_COMM_WORLD, rank == 0 ? MPI_UNDEFINED : 1, rank, &sub);
    if (rank == 0) { MPI_Finalize(); return 0; }
    int srank;
    MPI_Comm_rank(sub, &srank);
    if (srank == 0) {
        for (int i = 0; i < 2; i++) {
            int v; MPI_Status st;
            MPI_Recv(&v, 1, MPI_INT, MPI_ANY_SOURCE, TAG, sub, &st);
            MPI_Send(&v, 1, MPI_INT, st.MPI_SOURCE, TAG, sub);
        }
    } else {
        int v = rank, back;
        if (rank == 3) idiom_delay_ms(100);
        MPI_Send(&v, 1, MPI_INT, 0, TAG, sub);
        MPI_Recv(&back, 1, MPI_INT, 0, TAG, sub, MPI_STATUS_IGNORE);
    }
    MPI_Comm_free(&sub);
    MPI_Finalize();
    return 0;
}
