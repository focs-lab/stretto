/* 18: MPI-CorrBench's deadlock_probe_2 shape, OpenMP. Two threads of rank 1 each probe rank 0
   with any tag and receive with the probed tag; rank 0 sends tag 0, then tag 1. If both probes
   report the tag-0 message, one thread's receive waits forever. Observed: thread 1 starts late
   and probes the tag-1 message. Expected: L1 probe alternative across chains (thread 1's probe
   reports the tag-0 message), a warning; the hang is a consequence beyond the probe (replay). */
#include "../common.h"
#include <omp.h>
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        int a[2] = {1, 1}, b = 2;
        MPI_Send(a, 2, MPI_INT, 1, 0, MPI_COMM_WORLD);
        MPI_Send(&b, 1, MPI_INT, 1, 1, MPI_COMM_WORLD);
    } else if (rank == 1) {
#pragma omp parallel num_threads(2)
        {
            MPI_Status st; int count;
            if (omp_get_thread_num() == 1) idiom_delay_ms(100);
            MPI_Probe(0, MPI_ANY_TAG, MPI_COMM_WORLD, &st);
            MPI_Get_count(&st, MPI_INT, &count);
            int *buf = malloc(sizeof(int) * count);
            MPI_Recv(buf, count, MPI_INT, 0, st.MPI_TAG, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
            free(buf);
        }
    }
    MPI_Finalize();
    return 0;
}
