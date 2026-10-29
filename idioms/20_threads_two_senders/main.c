/* 20: two threads of rank 0 send to rank 1 on one tag, unsynchronised; rank 1 receives twice
   and expects thread A's message first. Sends of two threads are not ordered for
   non-overtaking (MPI 4.1 section 4.5). Observed: thread B starts late. Expected: L1
   alternative on the sender side (B's message first); consequence "wrong order" (replay). */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static void *send_one(void *arg) {
    int v = (int)(long)arg;
    if (v == 2) idiom_delay_ms(100);
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
