/* 29: MPI_Finalize before the threads' MPI calls are ordered before it (MPI 4.1 section
   12.6.2: finalize only after all threads have completed their MPI calls). A thread of rank 0
   sends to rank 1; the main thread sleeps and finalizes, and joins the thread only after.
   The send did return before the finalize, but nothing orders it before. Expected: observed
   2-thread-finalize. */
#include "../common.h"
#include <pthread.h>
static void *sender(void *arg) {
    (void)arg;
    int v = 1;
    MPI_Send(&v, 1, MPI_INT, 1, 0, MPI_COMM_WORLD);
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    pthread_t t;
    if (rank == 0) pthread_create(&t, NULL, sender, NULL);
    else if (rank == 1) { int v; MPI_Recv(&v, 1, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE); }
    if (rank == 0) idiom_delay_ms(200);
    MPI_Finalize();
    if (rank == 0) pthread_join(t, NULL);
    return 0;
}
