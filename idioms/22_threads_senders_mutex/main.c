/* 22: 20 with each send inside a critical section of one mutex. The mutex serialises the
   sends but does not order them: either thread may take it first. Observed: thread B starts
   late. Expected: L1 nothing (the lock order of the trace is kept); at L1+L, the race of 20
   (which thread's message rank 1 receives first). */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static void *send_one(void *arg) {
    int v = (int)(long)arg;
    if (v == 2) idiom_delay_ms(100);
    pthread_mutex_lock(&mu);
    MPI_Send(&v, 1, MPI_INT, 1, TAG, MPI_COMM_WORLD);
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
        pthread_create(&a, NULL, send_one, (void *)1L);
        pthread_create(&b, NULL, send_one, (void *)2L);
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
