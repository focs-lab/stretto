/* 47: two threads of rank 0 each copy a header to themselves with MPI_Sendrecv on one duplicate
   of MPI_COMM_SELF with one tag (the shape of PaRSEC's local reshapes, experiments/parsec). A
   Sendrecv's send and receive are concurrent with the other thread's, so either receive can take
   either send (MPI 4.1 sections 3.10 and 4.5, Order). Observed: thread B starts late, and the
   tracer posts each Sendrecv's two operations under one hold of its issue lock, so each thread
   takes its own header. Expected: L1 alternative, a concurrent swap of the two receives;
   consequence a wrong result ("wrong header"), stated by a replay only, which must post B's send
   before A's and hold B's receive back until A's is posted. */
#include "../common.h"
#include <pthread.h>
#define TAG 0
static MPI_Comm self;
static void *copy_one(void *arg) {
    int want = (int)(long)arg, out[2] = {want, 10 * want}, in[2] = {0, 0};
    if (want == 2) idiom_delay_ms(100);
    MPI_Sendrecv(out, 2, MPI_INT, 0, TAG, in, 2, MPI_INT, 0, TAG, self, MPI_STATUS_IGNORE);
    if (in[0] != want) printf("thread %d: wrong header %d\n", want, in[0]);
    return NULL;
}
int main(int argc, char **argv) {
    int rank;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_dup(MPI_COMM_SELF, &self);
    if (rank == 0) {
        pthread_t a, b;
        pthread_create(&a, NULL, copy_one, (void *)1L);
        pthread_create(&b, NULL, copy_one, (void *)2L);
        pthread_join(a, NULL);
        pthread_join(b, NULL);
    }
    MPI_Comm_free(&self);
    MPI_Finalize();
    return 0;
}
