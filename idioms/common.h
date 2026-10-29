/* Shared helpers for the idioms. System MPI only. */
#ifndef STRETTO_IDIOM_COMMON_H
#define STRETTO_IDIOM_COMMON_H
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Sleep used to make the *observed* run deterministic. The analysis must
   predict the other outcome without ever seeing it. */
static inline void idiom_delay_ms(int ms) { usleep(ms * 1000); }

static inline void idiom_require_np(int np, int want, int rank) {
    if (np != want) {
        if (rank == 0) fprintf(stderr, "this idiom needs exactly %d ranks\n", want);
        MPI_Abort(MPI_COMM_WORLD, 1);
    }
}

/* The thread idioms (15 on): at least `want` ranks (the extra ones only finalize), and the
   thread level asked for, or the run is pointless. */
static inline void idiom_require_min_np(int np, int want, int rank) {
    if (np < want) {
        if (rank == 0) fprintf(stderr, "this idiom needs at least %d ranks\n", want);
        MPI_Abort(MPI_COMM_WORLD, 1);
    }
}
static inline void idiom_init_thread(int *argc, char ***argv, int level) {
    int provided;
    MPI_Init_thread(argc, argv, level, &provided);
    if (provided < level) {
        fprintf(stderr, "this idiom needs thread level %d, the library gives %d\n", level, provided);
        MPI_Abort(MPI_COMM_WORLD, 1);
    }
}
#endif
