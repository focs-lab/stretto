/* 19: the matched-probe version of 16 and 18 (negative). Two threads of rank 0 each
   MPI_Mprobe any source on one tag and MPI_Mrecv the message they matched; ranks 1 and 2 send
   8 and 400 bytes. Expected: no truncation and no hang at any level; which thread matches
   which message is a race without consequence (at most a concurrent alternative of the Mprobes). */
#include "../common.h"
#include <omp.h>
#define TAG 0
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 3, rank);
    if (rank == 0) {
#pragma omp parallel num_threads(2)
        {
            MPI_Message m; MPI_Status st; int count;
            if (omp_get_thread_num() == 1) idiom_delay_ms(100);
            MPI_Mprobe(MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &m, &st);
            MPI_Get_count(&st, MPI_INT, &count);
            int *buf = malloc(sizeof(int) * count);
            MPI_Mrecv(buf, count, MPI_INT, &m, MPI_STATUS_IGNORE);
            free(buf);
        }
    } else if (rank == 1 || rank == 2) {
        int big[100] = {0};
        if (rank == 2) idiom_delay_ms(30);
        MPI_Send(big, rank == 1 ? 2 : 100, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
