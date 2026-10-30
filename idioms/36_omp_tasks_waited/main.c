/* 36: pairs of receives on one tag, each pair ordered otherwise than by a dependence
   (negative): by a taskgroup (A1 inside it, B1 after its end; a taskwait then separates the
   pairs), by a taskwait (A2, taskwait, B2), by a team barrier (A3 created by one thread before
   an explicit barrier, B3 by another thread after it), by a nested task (A4 creates A4',
   which receives; A4 waits for it with a taskwait, and B4 depends on A4), and by an undeferred
   task (A5 with if(0); its creator receives B5 itself after it). Rank 1 sends 8 bytes, then
   400 bytes, five times. The receives of each pair would race as in idiom 34 if nothing
   ordered them. Expected: no finding. */
#include "../common.h"
#include <omp.h>
#define TAG 5
static void small_recv(void) { char b[8]; MPI_Recv(b, 8, MPI_CHAR, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE); }
static void big_recv(void) { char b[400]; MPI_Recv(b, 400, MPI_CHAR, 1, TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE); }
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        int x = 0;
#pragma omp parallel num_threads(2)
        {
#pragma omp single
            {
#pragma omp taskgroup
                {
#pragma omp task
                    small_recv();
                }
#pragma omp task
                big_recv();
#pragma omp taskwait
#pragma omp task
                small_recv();
#pragma omp taskwait
#pragma omp task
                big_recv();
            }
            if (omp_get_thread_num() == 0) {
#pragma omp task
                small_recv();
            }
#pragma omp barrier
            if (omp_get_thread_num() == 1) {
#pragma omp task
                big_recv();
            }
#pragma omp barrier
#pragma omp single
            {
#pragma omp task depend(out: x)
                {
#pragma omp task
                    small_recv();
#pragma omp taskwait
                }
#pragma omp task depend(in: x)
                big_recv();
#pragma omp taskwait
#pragma omp task if(0)
                small_recv();
                big_recv();
            }
        }
        (void)x;
    } else if (rank == 1) {
        char buf[400] = {0};
        for (int i = 0; i < 5; i++) {
            MPI_Send(buf, 8, MPI_CHAR, 0, TAG, MPI_COMM_WORLD);
            MPI_Send(buf, 400, MPI_CHAR, 0, TAG, MPI_COMM_WORLD);
        }
    }
    MPI_Finalize();
    return 0;
}
