/* Two different GA mutexes held at once by one process. pnga_lock (global/src/base.c) derives
 * the ComEx mutex from num_mutexes instead of its argument, so GA_Lock(1) after GA_Lock(0)
 * asks for the mutex the process already holds and, if that reading is right, never returns.
 * Rank 0 does it; the others wait in a barrier. Prints "nested ok" when both locks were taken.
 *   mpicc -I<build>/global/src -I<src>/global/src nested_lock.c <build>/.libs/libga.a \
 *         <build>/comex/.libs/libarmci.a <build>/comex/.libs/libcomex.a -lm */
#include <mpi.h>
#include <stdio.h>
#include "ga.h"
#include "macdecls.h"

int main(int argc, char **argv)
{
    int provided;
    MPI_Init_thread(&argc, &argv, MPI_THREAD_MULTIPLE, &provided);
    GA_Initialize();
    if (!GA_Create_mutexes(2))
        GA_Error("GA_Create_mutexes failed", 0);
    if (GA_Nodeid() == 0) {
        GA_Lock(0);
        printf("lock 0 taken\n");
        fflush(stdout);
        GA_Lock(1);
        printf("nested ok\n");
        fflush(stdout);
        GA_Unlock(1);
        GA_Unlock(0);
    }
    GA_Sync();
    GA_Destroy_mutexes();
    GA_Terminate();
    MPI_Finalize();
    return 0;
}
