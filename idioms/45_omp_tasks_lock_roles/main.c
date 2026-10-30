/* 45: OpenMP tasks that take their roles in the order they take a lock, the shape of SLATE's
   tasks and its tile locks (experiments/slate). Rank 0's master thread of a team of 2 creates
   two tasks that no dependence orders; each takes an OpenMP nest lock, draws the next slot and
   receives from rank 1 on tag 5 into the slot's buffer: slot 0 8 bytes at once, slot 1 400
   bytes after 100 ms. Rank 1 sends 8 bytes, then 400. Observed: each slot's receive takes its
   own size. Expected: an L1 concurrent swap of the two receives with a 1a truncation (the 400
   bytes taken by the 8-byte receive). Which task draws which slot varies from run to run, so a
   replay names the right receive only with the trace's lock order (docs/threads.md, section
   13). */
#include "../common.h"
#include <omp.h>
#define TAG 5
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        static char small[8], big[400];
        char *buf[2] = {small, big};
        int size[2] = {8, 400}, next = 0;
        omp_nest_lock_t lock;
        omp_init_nest_lock(&lock);
#pragma omp parallel num_threads(2)
#pragma omp master
        for (int k = 0; k < 2; k++) {
#pragma omp task shared(lock, next, buf, size) firstprivate(k)
            {
                unsigned seed = (unsigned)getpid() * 31u + (unsigned)k;
                idiom_delay_ms(rand_r(&seed) % 20);  /* the tasks take the lock in another order in each run */
                omp_set_nest_lock(&lock);
                int s = next++;
                omp_unset_nest_lock(&lock);
                if (s == 1) idiom_delay_ms(100);
                MPI_Recv(buf[s], size[s], MPI_CHAR, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
            }
        }
        omp_destroy_nest_lock(&lock);
    } else if (rank == 1) {
        static char buf[400];
        MPI_Send(buf, 8, MPI_CHAR, 0, TAG, MPI_COMM_WORLD);
        MPI_Send(buf, 400, MPI_CHAR, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
