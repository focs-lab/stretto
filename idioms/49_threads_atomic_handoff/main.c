/* 49: 15 with the two receives handed off through atomics (negative): thread A of rank 0
   receives, then publishes a ticket with an acq_rel fetch-add; thread B takes the ticket with
   an acquire compare-exchange loop and receives only then. The idiom is compiled with
   ThreadSanitizer's instrumentation (file "tsan"), so the tracer records the read-modify-write
   and the compare-exchange (docs/threads.md, section 17) and the receives are ordered.
   Expected: nothing at any level. Traced with STRETTO_ATOMICS=0 it is idiom 15: an L1
   concurrent swap of the two receives. */
#include "../common.h"
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#define TAG 0
static atomic_int ticket;
static void *recv_first(void *arg) {
    (void)arg;
    int msg[2];
    MPI_Recv(msg, 2, MPI_INT, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    if (msg[0] != 1) printf("thread 1: wrong header %d\n", msg[0]);
    atomic_fetch_add_explicit(&ticket, 1, memory_order_acq_rel);
    return NULL;
}
static void *recv_second(void *arg) {
    (void)arg;
    int msg[2], one = 1;
    while (!atomic_compare_exchange_weak_explicit(&ticket, &one, 2, memory_order_acquire, memory_order_relaxed)) {
        one = 1;
        sched_yield();
    }
    MPI_Recv(msg, 2, MPI_INT, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    if (msg[0] != 2) printf("thread 2: wrong header %d\n", msg[0]);
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
        pthread_create(&b, NULL, recv_second, NULL);
        pthread_create(&a, NULL, recv_first, NULL);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    } else if (rank == 1) {
        int m1[2] = {1, 10}, m2[2] = {2, 20};
        MPI_Send(m1, 2, MPI_INT, 0, TAG, MPI_COMM_WORLD);
        MPI_Send(m2, 2, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
