/* 23: 16 with the probe and the receive of every receiving thread inside one critical section
   of a mutex (negative for the probe split): no other thread can receive between a probe and
   its receive. Rank 1 sends 8 bytes, then 400. Expected: no truncation and no probe split at any
   level; at L1+SP-T, which thread gets which message, without consequence. */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static void *receiver(void *arg) {
    if ((long)arg == 2) idiom_delay_ms(100);
    MPI_Status st; int count;
    pthread_mutex_lock(&mu);
    MPI_Probe(MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &st);
    MPI_Get_count(&st, MPI_INT, &count);
    int *buf = malloc(sizeof(int) * count);
    MPI_Recv(buf, count, MPI_INT, st.MPI_SOURCE, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    pthread_mutex_unlock(&mu);
    free(buf);
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
        pthread_create(&a, NULL, receiver, (void *)1L);
        pthread_create(&b, NULL, receiver, (void *)2L);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    } else if (rank == 1) {
        int small[2] = {1, 2}, big[100] = {0};
        MPI_Send(small, 2, MPI_INT, 0, TAG, MPI_COMM_WORLD);
        MPI_Send(big, 100, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
