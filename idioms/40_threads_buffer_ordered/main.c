/* 40: 39 without the race. The second thread waits until the main thread has completed its
   receive into buf (a flag under a mutex and a condition variable) before it sends buf, so
   the receive's completion happens before the send's call in every schedule. Expected: no
   3-race (traced with STRETTO_BUFS=1). */
#include "../common.h"
#include <pthread.h>
#define N 1000
static int buf[N], received;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static void *sender(void *arg) {
    (void)arg;
    MPI_Request req;
    pthread_mutex_lock(&mu);
    while (!received) pthread_cond_wait(&cv, &mu);
    pthread_mutex_unlock(&mu);
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
        pthread_mutex_lock(&mu);
        received = 1;
        pthread_cond_signal(&cv);
        pthread_mutex_unlock(&mu);
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
