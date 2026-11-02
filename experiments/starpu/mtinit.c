/* LD_PRELOAD shim for StarPU's shipped MPI tests, which initialise MPI with
 * MPI_THREAD_SERIALIZED (mpi/tests/helper.h): it asks for MPI_THREAD_MULTIPLE instead, so that
 * StarPU-MPI may let its workers post their own sends (STARPU_MPI_THREAD_MULTIPLE_SEND, which
 * needs that level). It forwards to the next definition (the tracer's, then the library's), so
 * the tracer records the call with the level it got. Built per MPI by scan.sh:
 *   mpicc -O2 -Wall -shared -fPIC -o libmtinit.so mtinit.c -ldl */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <mpi.h>

int MPI_Init_thread(int *argc, char ***argv, int required, int *provided) {
    static int (*next)(int *, char ***, int, int *);
    (void)required;
    if (!next) next = (int (*)(int *, char ***, int, int *))dlsym(RTLD_NEXT, "MPI_Init_thread");
    return next(argc, argv, MPI_THREAD_MULTIPLE, provided);
}

int MPI_Init(int *argc, char ***argv) {
    int provided;
    return MPI_Init_thread(argc, argv, MPI_THREAD_MULTIPLE, &provided);
}
