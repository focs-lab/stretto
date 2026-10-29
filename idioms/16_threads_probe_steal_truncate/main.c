/* 16: the MPI_Probe hazard of MPI 4.1 section 4.8.1. Thread A probes, sizes its buffer by the
   probed message and receives with the probed source and tag; thread B receives on the same tag
   concurrently. Rank 1 sends 8 bytes, then 400. Observed: B starts late, so A takes the 8-byte
   message it probed and B the other. Expected: L1 probe split, B takes the probed message after
   A's probe; A's receive then takes the 400-byte message into its 8-byte buffer: 1a truncate. */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static void *prober(void *arg) {
    (void)arg;
    MPI_Status st; int count;
    MPI_Probe(MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &st);
    MPI_Get_count(&st, MPI_INT, &count);
    int *buf = malloc(sizeof(int) * count);
    MPI_Recv(buf, count, MPI_INT, st.MPI_SOURCE, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    free(buf);
    return NULL;
}
static void *receiver(void *arg) {
    (void)arg;
    int buf[100];
    idiom_delay_ms(100);
    MPI_Recv(buf, 100, MPI_INT, MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
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
        pthread_create(&a, NULL, prober, NULL);
        pthread_create(&b, NULL, receiver, NULL);
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
