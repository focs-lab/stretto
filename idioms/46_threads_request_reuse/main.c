/* 46: request handles that the library hands from one thread to another (a regression of the
   tracer, 30 Oct 2026). Rank 0 runs two threads; each posts an MPI_Isend into a variable of its
   own, copies the handle into an array and completes it with MPI_Waitall on the array, as
   SLATE's tileIbcastToSet does, ROUNDS times. A small message completes at once, so the wait
   frees the request, and the library can give the freed handle to the other thread's next
   Isend before the first thread's wait has returned. The tracer, which knew the request only
   by its handle and the variable it was posted in, then credited one thread's completion with
   the other's request and degraded the trace (threads-handoff). Rank 1 receives each thread's
   messages on its own tag. Expected: no finding, every request completed by its own thread. */
#include "../common.h"
#include <pthread.h>
#define ROUNDS 100
static void *sender(void *arg) {
    int tag = *(int *)arg;
    for (int i = 0; i < ROUNDS; i++) {
        int v = tag * ROUNDS + i;
        MPI_Request r, reqs[1];
        MPI_Isend(&v, 1, MPI_INT, 1, tag, MPI_COMM_WORLD, &r);
        reqs[0] = r;
        MPI_Waitall(1, reqs, MPI_STATUSES_IGNORE);
    }
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        int tags[2] = {1, 2};
        pthread_t a, b;
        pthread_create(&a, NULL, sender, &tags[0]);
        pthread_create(&b, NULL, sender, &tags[1]);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    } else if (rank == 1) {
        int bad = 0;
        for (int tag = 1; tag <= 2; tag++)
            for (int i = 0; i < ROUNDS; i++) {
                int v;
                MPI_Recv(&v, 1, MPI_INT, 0, tag, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
                bad += v != tag * ROUNDS + i;
            }
        if (bad) printf("rank 1: %d messages out of order\n", bad);
    }
    MPI_Finalize();
    return 0;
}
