/* 08: two logical channels (DATA, CTRL) share one ANY_SOURCE/ANY_TAG receive.
   ddcMD shape: the wildcard can consume the control message meant for a
   later receive. */
#include "../common.h"
#define TAG_DATA 11
#define TAG_CTRL 22
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    if (rank == 0) {
        int data, ctrl;
        MPI_Status st;
        MPI_Recv(&data, 1, MPI_INT, MPI_ANY_SOURCE, MPI_ANY_TAG, MPI_COMM_WORLD, &st);
        MPI_Recv(&ctrl, 1, MPI_INT, 2, TAG_CTRL, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    } else if (rank == 1) {
        int data = 123;
        MPI_Send(&data, 1, MPI_INT, 0, TAG_DATA, MPI_COMM_WORLD);
    } else {
        int ctrl = -1;
        idiom_delay_ms(100);                   /* observed: wildcard takes DATA from 1 */
        MPI_Send(&ctrl, 1, MPI_INT, 0, TAG_CTRL, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
