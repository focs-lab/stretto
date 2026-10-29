/* 15: two threads of rank 0 receive from rank 1 on one tag; each expects its own header.
   The receives are concurrent (no synchronisation orders them), so either thread can take
   either message (MPI 4.1 section 4.5, Order). Observed: thread B starts late, A takes the first
   message. Expected: L1 alternative, a concurrent swap of the two receives; consequence a wrong
   result ("wrong header"), stated by a replay only. */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static void *recv_one(void *arg) {
    int want = (int)(long)arg, msg[2];
    if (want == 2) idiom_delay_ms(100);
    MPI_Recv(msg, 2, MPI_INT, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    if (msg[0] != want) printf("thread %d: wrong header %d\n", want, msg[0]);
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
        pthread_create(&a, NULL, recv_one, (void *)1L);
        pthread_create(&b, NULL, recv_one, (void *)2L);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    } else if (rank == 1) {
        int m1[2] = {1, 10}, m2[2] = {2, 20};
        MPI_Send(m1, 2, MPI_INT, 0, TAG, MPI_COMM_WORLD);
        MPI_Send(m2, 2, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
