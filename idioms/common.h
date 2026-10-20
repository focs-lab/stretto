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
#endif
