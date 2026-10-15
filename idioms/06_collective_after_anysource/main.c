/* 06: collective participation depends on MPI_SOURCE. Observed: source 1,
   rank 0 drains rank 2's message and everyone reaches Bcast. Alternative:
   source 2, rank 0 skips the Bcast and sends an ack that nobody receives;
   ranks 1 and 2 hang in Bcast (at every B). */
#include "../common.h"
#define TAG 6
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    int x = 42;
    if (rank == 0) {
        int v;
        MPI_Status st;
        MPI_Recv(&v, 1, MPI_INT, MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &st);
        if (st.MPI_SOURCE == 1) {
            MPI_Recv(&v, 1, MPI_INT, 2, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE); /* drain rank 2 */
            MPI_Bcast(&x, 1, MPI_INT, 0, MPI_COMM_WORLD);
        } else {
            int ack = 0;
            MPI_Send(&ack, 1, MPI_INT, st.MPI_SOURCE, TAG, MPI_COMM_WORLD);
        }
    } else {
        int v = rank;
        if (rank == 2) idiom_delay_ms(100);
        MPI_Send(&v, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD);
        MPI_Bcast(&x, 1, MPI_INT, 0, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
