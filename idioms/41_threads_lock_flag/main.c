/* 41: 20 with the order fixed by a flag under a mutex (negative): thread A sends, then sets
   the flag in a critical section; thread B polls the flag in critical sections of the same
   mutex and sends once it is set. The critical sections contain no MPI call, so their order
   is kept (docs/threads.md, section 13): the sends are ordered. Expected: nothing at any level. */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static int a_done = 0;
static void *first(void *arg) {
    (void)arg;
    int v = 1;
    idiom_delay_ms(50);
    MPI_Send(&v, 1, MPI_INT, 1, TAG, MPI_COMM_WORLD);
    pthread_mutex_lock(&mu); a_done = 1; pthread_mutex_unlock(&mu);
    return NULL;
}
static void *second(void *arg) {
    (void)arg;
    int v = 2, done = 0;
    while (!done) { pthread_mutex_lock(&mu); done = a_done; pthread_mutex_unlock(&mu); if (!done) idiom_delay_ms(10); }
    MPI_Send(&v, 1, MPI_INT, 1, TAG, MPI_COMM_WORLD);
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
        pthread_create(&b, NULL, second, NULL);
        pthread_create(&a, NULL, first, NULL);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    } else if (rank == 1) {
        int x, y;
        MPI_Recv(&x, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        MPI_Recv(&y, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        if (x != 1) printf("wrong order: %d before %d\n", x, y);
    }
    MPI_Finalize();
    return 0;
}
