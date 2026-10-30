/* 42: 41 with each send inside the critical section that sets or reads the flag (negative):
   thread A sends and sets the flag in one critical section; thread B polls the flag in critical
   sections of the same mutex and sends in the one that finds it set. Both critical sections that
   contain a send could run in either order as far as the mutex goes, but B's depends on what it
   read: B enters it right after leaving an empty one (a polling loop), so its order is kept
   (docs/threads.md, section 13). Expected: nothing at any level. */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static int a_done = 0;
static void *first(void *arg) {
    (void)arg;
    int v = 1;
    idiom_delay_ms(50);
    pthread_mutex_lock(&mu);
    MPI_Send(&v, 1, MPI_INT, 1, TAG, MPI_COMM_WORLD);
    a_done = 1;
    pthread_mutex_unlock(&mu);
    return NULL;
}
static void *second(void *arg) {
    (void)arg;
    int v = 2, done = 0;
    while (!done) {
        pthread_mutex_lock(&mu);
        if (a_done) { MPI_Send(&v, 1, MPI_INT, 1, TAG, MPI_COMM_WORLD); done = 1; }
        pthread_mutex_unlock(&mu);
        if (!done) idiom_delay_ms(10);
    }
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
