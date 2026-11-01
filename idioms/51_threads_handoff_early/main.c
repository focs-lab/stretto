/* 51: 49 with the ticket published too early (the positive of 49): thread A publishes the
   ticket before it receives, so B's receive waits for the ticket but not for A's receive. The
   atomics are recorded (file "tsan", docs/threads.md section 17) and order only what precedes
   the fetch-add. Observed: B receives late, A takes the first message. Expected: the L1
   concurrent swap of idiom 15. */
#include "../common.h"
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#define TAG 0
static atomic_int ticket;
static void *recv_first(void *arg) {
    (void)arg;
    int msg[2];
    atomic_fetch_add_explicit(&ticket, 1, memory_order_acq_rel);
    MPI_Recv(msg, 2, MPI_INT, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    if (msg[0] != 1) printf("thread 1: wrong header %d\n", msg[0]);
    return NULL;
}
static void *recv_second(void *arg) {
    (void)arg;
    int msg[2], one = 1;
    while (!atomic_compare_exchange_weak_explicit(&ticket, &one, 2, memory_order_acquire, memory_order_relaxed)) {
        one = 1;
        sched_yield();
    }
    idiom_delay_ms(100);
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
