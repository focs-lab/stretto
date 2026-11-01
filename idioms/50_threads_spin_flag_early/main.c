/* 50: 48 with the flag set too early (the positive of 48): thread A sets the atomic flag
   before it sends, so B's send waits for the flag but not for A's send. The flag's edge is
   recorded (file "tsan", docs/threads.md section 17) and orders only what precedes the
   store. Expected: L1 1f, the sender-side alternative of idiom 20 (B's message first). */
#include "../common.h"
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#define TAG 0
static atomic_int a_started;
static void *first(void *arg) {
    (void)arg;
    int v = 1;
    atomic_store_explicit(&a_started, 1, memory_order_release);
    idiom_delay_ms(50);
    MPI_Send(&v, 1, MPI_INT, 1, TAG, MPI_COMM_WORLD);
    return NULL;
}
static void *second(void *arg) {
    (void)arg;
    int v = 2;
    while (!atomic_load_explicit(&a_started, memory_order_acquire)) sched_yield();
    idiom_delay_ms(200);
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
