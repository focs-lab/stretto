/* 17: as 16, with one message per source: ranks 1 and 2 each send one message on the tag.
   Observed: A probes and takes rank 1's message, B (late) takes rank 2's. Expected: L1 probe
   split, B takes rank 1's message after A's probe; A's receive, from rank 1, then has nothing to
   take: 1d contained deadlock of A's chain, and the process hangs at the join. */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static void *prober(void *arg) {
    (void)arg;
    MPI_Status st; int v;
    MPI_Probe(MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &st);
    MPI_Recv(&v, 1, MPI_INT, st.MPI_SOURCE, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    return NULL;
}
static void *receiver(void *arg) {
    (void)arg;
    int v;
    idiom_delay_ms(100);
    MPI_Recv(&v, 1, MPI_INT, MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 3, rank);
    if (rank == 0) {
        pthread_t a, b;
        pthread_create(&a, NULL, prober, NULL);
        pthread_create(&b, NULL, receiver, NULL);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    } else if (rank == 1 || rank == 2) {
        int v = rank;
        if (rank == 2) idiom_delay_ms(30);       /* the prober sees rank 1 first */
        MPI_Send(&v, 1, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
