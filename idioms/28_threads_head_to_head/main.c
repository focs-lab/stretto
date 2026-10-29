/* 28: head-to-head exchange of 1 MiB across threads (negative for 2-buffering). On ranks 0 and
   1, thread A sends to the other rank and thread B receives from it. A blocking send blocks its
   thread only (MPI 4.1 section 12.6.1), so this cannot deadlock whatever the buffering, unlike
   idiom 03's single-threaded form. Expected: no 2-buffering, also at B = 0. */
#include "../common.h"
#include <pthread.h>
#define N (1 << 18)
static int peer;
static void *sender(void *arg) {
    (void)arg;
    int *buf = calloc(N, sizeof(int));
    MPI_Send(buf, N, MPI_INT, peer, 0, MPI_COMM_WORLD);
    free(buf);
    return NULL;
}
static void *receiver(void *arg) {
    (void)arg;
    int *buf = malloc(N * sizeof(int));
    MPI_Recv(buf, N, MPI_INT, peer, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    free(buf);
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank < 2) {
        peer = 1 - rank;
        pthread_t a, b;
        pthread_create(&a, NULL, sender, NULL);
        pthread_create(&b, NULL, receiver, NULL);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    }
    MPI_Finalize();
    return 0;
}
