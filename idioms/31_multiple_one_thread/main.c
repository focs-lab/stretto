/* 31: idiom 01 (master-worker with MPI_ANY_SOURCE) initialised with MPI_THREAD_MULTIPLE, one
   calling thread per rank. The tracer posts under its issue lock and records iseq; the
   analysis sees one chain per rank. Expected: the report of idiom 01. */
#include "../common.h"
#define TAG 7
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    if (rank == 0) {
        for (int i = 0; i < 2; i++) {
            int result, work = 100 + i;
            MPI_Status st;
            MPI_Recv(&result, 1, MPI_INT, MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &st);
            MPI_Send(&work, 1, MPI_INT, st.MPI_SOURCE, TAG, MPI_COMM_WORLD);
        }
    } else {
        int result = rank * 10, work;
        if (rank == 2) idiom_delay_ms(100);   /* rank 1 wins the observed run */
        MPI_Send(&result, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD);
        MPI_Recv(&work, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    }
    MPI_Finalize();
    return 0;
}
