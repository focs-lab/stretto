/* 48: 20 with the order fixed by a spin flag (negative): thread A sends, then sets an atomic
   flag with a release store; thread B spins on the flag with acquire loads and sends once it
   is set. The idiom is compiled with ThreadSanitizer's instrumentation and linked with
   libstretto-tsan (file "tsan"), so the tracer records the flag's release and acquire
   (docs/threads.md, section 17) and the sends are ordered. Expected: nothing at any level.
   Traced with STRETTO_ATOMICS=0 (the flag not recorded) it is idiom 20: the sender-side
   alternative, L1 1f. */
#include "../common.h"
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#define TAG 0
static atomic_int a_done;
static void *first(void *arg) {
    (void)arg;
    int v = 1;
    idiom_delay_ms(50);
    MPI_Send(&v, 1, MPI_INT, 1, TAG, MPI_COMM_WORLD);
    atomic_store_explicit(&a_done, 1, memory_order_release);
    return NULL;
}
static void *second(void *arg) {
    (void)arg;
    int v = 2;
    while (!atomic_load_explicit(&a_done, memory_order_acquire)) sched_yield();
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
