/* 24: 15 with a communicator per thread, the standard's advice (MPI 4.1 section 12.6.1;
   negative): each thread of rank 0 receives on its own duplicate of MPI_COMM_WORLD, and rank 1
   sends each message on the matching one. Expected: nothing. */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static MPI_Comm comms[2];
static void *recv_one(void *arg) {
    int want = (int)(long)arg, msg[2];
    if (want == 2) idiom_delay_ms(100);
    MPI_Recv(msg, 2, MPI_INT, 1, TAG, comms[want - 1], MPI_STATUS_IGNORE);
    if (msg[0] != want) printf("thread %d: wrong header %d\n", want, msg[0]);
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    MPI_Comm_dup(MPI_COMM_WORLD, &comms[0]);
    MPI_Comm_dup(MPI_COMM_WORLD, &comms[1]);
    if (rank == 0) {
        pthread_t a, b;
        pthread_create(&a, NULL, recv_one, (void *)1L);
        pthread_create(&b, NULL, recv_one, (void *)2L);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    } else if (rank == 1) {
        int m1[2] = {1, 10}, m2[2] = {2, 20};
        MPI_Send(m1, 2, MPI_INT, 0, TAG, comms[0]);
        MPI_Send(m2, 2, MPI_INT, 0, TAG, comms[1]);
    }
    MPI_Comm_free(&comms[0]);
    MPI_Comm_free(&comms[1]);
    MPI_Finalize();
    return 0;
}
