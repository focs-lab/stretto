/* 53: 20 with the order fixed by a pipe, which no wrapper sees, and annotated: thread A
   sends, then writes a byte into a pipe; thread B reads the byte and sends. The program marks
   the hand-off with Stretto's annotations (tracer/stretto_sync.h: STRETTO_HAPPENS_BEFORE
   before the write, STRETTO_HAPPENS_AFTER after the read, on one address), which need neither
   the instrumentation nor a library (docs/threads.md, section 17). Expected: nothing at any
   level. Without the annotations (or traced with STRETTO_ATOMICS=0) it is idiom 20: L1 1f. */
#include "../common.h"
#include "../../tracer/stretto_sync.h"
#include <pthread.h>
#define TAG 0
static int fds[2];
static void *first(void *arg) {
    (void)arg;
    int v = 1;
    char c = 1;
    idiom_delay_ms(50);
    MPI_Send(&v, 1, MPI_INT, 1, TAG, MPI_COMM_WORLD);
    STRETTO_HAPPENS_BEFORE(fds);
    if (write(fds[1], &c, 1) != 1) perror("write");
    return NULL;
}
static void *second(void *arg) {
    (void)arg;
    int v = 2;
    char c;
    if (read(fds[0], &c, 1) != 1) perror("read");
    STRETTO_HAPPENS_AFTER(fds);
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
        if (pipe(fds) != 0) { perror("pipe"); MPI_Abort(MPI_COMM_WORLD, 1); }
        pthread_create(&b, NULL, second, NULL);
        pthread_create(&a, NULL, first, NULL);
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
