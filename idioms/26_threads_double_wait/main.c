/* 26: one request in two concurrent completion calls (MPI 4.1 section 12.6.2: erroneous).
   Rank 0 posts an Irecv; thread A blocks in MPI_Wait on it while thread B tests the same
   request once (rank 1 sends late, so the test returns false and the run is harmless).
   Expected: observed 2-thread-wait. */
#include "../common.h"
#include <pthread.h>
static MPI_Request req;
static void *waiter(void *arg) {
    (void)arg;
    MPI_Request r = req;
    MPI_Wait(&r, MPI_STATUS_IGNORE);
    return NULL;
}
static void *tester(void *arg) {
    (void)arg;
    MPI_Request r = req; int flag;
    idiom_delay_ms(50);
    MPI_Test(&r, &flag, MPI_STATUS_IGNORE);
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np, v = 0;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        MPI_Irecv(&v, 1, MPI_INT, 1, 0, MPI_COMM_WORLD, &req);
        pthread_t a, b;
        pthread_create(&a, NULL, waiter, NULL);
        pthread_create(&b, NULL, tester, NULL);
        pthread_join(b, NULL);
        pthread_join(a, NULL);
    } else if (rank == 1) {
        v = 1;
        idiom_delay_ms(300);
        MPI_Send(&v, 1, MPI_INT, 0, 0, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
