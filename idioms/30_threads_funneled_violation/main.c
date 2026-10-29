/* 30: MPI_THREAD_FUNNELED, and a worker thread calls MPI (MPI 4.1 section 12.2.1: only the main
   thread may). The main thread joins the worker before its own calls, so the run is harmless.
   Expected: observed 2-thread-level. */
#include "../common.h"
#include <pthread.h>
static void *worker(void *arg) {
    (void)arg;
    int v = 1;
    MPI_Send(&v, 1, MPI_INT, 1, 0, MPI_COMM_WORLD);
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_FUNNELED);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        pthread_t t;
        pthread_create(&t, NULL, worker, NULL);
        pthread_join(t, NULL);
    } else if (rank == 1) {
        int v;
        MPI_Recv(&v, 1, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    }
    MPI_Finalize();
    return 0;
}
