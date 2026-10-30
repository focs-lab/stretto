/* 39: a buffer race between two threads (family 3). On rank 0 the main thread receives N ints
   into buf with MPI_Irecv and MPI_Wait, while a second thread, started before, sends the same
   buf to rank 1 with MPI_Isend after a delay. Nothing orders the two: in another schedule the
   send reads buf while the receive writes it. Observed: the send starts after the receive has
   completed. Expected: an L1 3-race at every threshold (traced with STRETTO_BUFS=1). */
#include "../common.h"
#include <pthread.h>
#define N 1000
static int buf[N];
static void *sender(void *arg) {
    (void)arg;
    MPI_Request req;
    idiom_delay_ms(200);
    MPI_Isend(buf, N, MPI_INT, 1, 2, MPI_COMM_WORLD, &req);
    MPI_Wait(&req, MPI_STATUS_IGNORE);
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 2, rank);
    if (rank == 0) {
        pthread_t th;
        MPI_Request req;
        pthread_create(&th, NULL, sender, NULL);
        MPI_Irecv(buf, N, MPI_INT, 1, 1, MPI_COMM_WORLD, &req);
        MPI_Wait(&req, MPI_STATUS_IGNORE);
        pthread_join(th, NULL);
    } else {
        int in[N];
        for (int i = 0; i < N; i++) buf[i] = i;
        MPI_Send(buf, N, MPI_INT, 0, 1, MPI_COMM_WORLD);
        MPI_Recv(in, N, MPI_INT, 0, 2, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    }
    MPI_Finalize();
    return 0;
}
