/* 52: two collectives of one communicator on two threads of every rank, ordered by a volatile
   state flag, the shape of Uintah's thread_fence (UnifiedScheduler.cc): the main thread sets
   the worker's state to active, the worker calls MPI_Allreduce and sets its state back to
   inactive, and the main thread spins until it is inactive, makes a fence and calls its own
   MPI_Allreduce. The program is correct. The idiom is compiled with ThreadSanitizer's
   instrumentation with volatile accesses distinguished (file "tsan"), so the tracer records a
   volatile write as a release and a volatile read as an acquire (docs/threads.md, section 17).
   Expected: nothing. Traced with STRETTO_ATOMICS=0: an observed 2-thread-collective. */
#include "../common.h"
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
enum { INACTIVE, ACTIVE };
static volatile int state = INACTIVE;
static void *worker(void *arg) {
    (void)arg;
    int x = 1, y;
    while (state == INACTIVE) sched_yield();
    MPI_Allreduce(&x, &y, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD);
    state = INACTIVE;
    return NULL;
}
int main(int argc, char **argv) {
    int rank, np, x = 2, y;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    pthread_t w;
    pthread_create(&w, NULL, worker, NULL);
    idiom_delay_ms(20);
    state = ACTIVE;
    while (state == ACTIVE) sched_yield();
    atomic_thread_fence(memory_order_seq_cst);
    MPI_Allreduce(&x, &y, 1, MPI_INT, MPI_MAX, MPI_COMM_WORLD);
    pthread_join(w, NULL);
    MPI_Finalize();
    return 0;
}
