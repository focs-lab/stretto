/* 27: MPI-CorrBench's deadlock_probe shape. Thread A of rank 0 holds a mutex across a receive
   of tag 2 from rank 1; thread B takes the same mutex to send tag 1 to rank 1, which answers
   tag 1 with tag 2. If A takes the mutex first, A waits for rank 1, rank 1 for B, B for the
   mutex: a deadlock. Observed: A starts late, B goes first, the run finishes. Expected: L1
   nothing (the lock order of the trace is kept); L1+L: the deadlock (2-lock-order). */
#include "../common.h"
#include <pthread.h>
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static void *thread_a(void *arg) {
    (void)arg;
    int v;
    idiom_delay_ms(100);
    pthread_mutex_lock(&mu);
    MPI_Recv(&v, 1, MPI_INT, 1, 2, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    pthread_mutex_unlock(&mu);
    return NULL;
}
static void *thread_b(void *arg) {
    (void)arg;
    int v = 1;
    pthread_mutex_lock(&mu);
    MPI_Send(&v, 1, MPI_INT, 1, 1, MPI_COMM_WORLD);
    pthread_mutex_unlock(&mu);
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        pthread_t a, b;
        pthread_create(&a, NULL, thread_a, NULL);
        pthread_create(&b, NULL, thread_b, NULL);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    } else if (rank == 1) {
        int v;
        MPI_Recv(&v, 1, MPI_INT, 0, 1, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        MPI_Send(&v, 1, MPI_INT, 0, 2, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
