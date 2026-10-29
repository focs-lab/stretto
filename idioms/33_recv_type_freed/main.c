/* 33: receives whose datatype is freed while they are pending (negative), the shape of SLATE's
   Tile::irecv: MPI_Type_vector and MPI_Type_commit for a strided tile, MPI_Irecv with it,
   MPI_Type_free at once, the completion later. MPI allows it ("Any communication that is
   currently using this datatype will complete normally", MPI 4.1, MPI_TYPE_FREE). Rank 0 does it
   twice, the first receive completed by MPI_Wait, the second by MPI_Waitall; rank 1 sends 16
   doubles each time; further ranks only finalize. Expected: no finding, and both completions
   record mcount 1.
   The library releases the type when the request completes, before the tracer writes the
   completion, so a tracer that asks MPI_Get_count about the handle reads freed memory. In a
   single-threaded run that memory is usually intact; the program therefore runs itself again
   with glibc's thread cache off and freed memory overwritten, which makes such a read record
   an unknown count or fail with MPI_ERR_TYPE (the tracer before 30 Oct 2026). */
#include "../common.h"
#define TAG 3
int main(int argc, char **argv) {
    if (!getenv("STRETTO_IDIOM33")) {
        setenv("STRETTO_IDIOM33", "1", 1);
        setenv("GLIBC_TUNABLES", "glibc.malloc.tcache_count=0", 1);
        setenv("MALLOC_PERTURB_", "1", 1);
        execv("/proc/self/exe", argv);
        perror("execv");
        return 1;
    }
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    double buf[64] = {0};
    if (rank == 0) {
        MPI_Request r[2];
        for (int i = 0; i < 2; i++) {
            MPI_Datatype t;
            MPI_Type_vector(4, 4, 8, MPI_DOUBLE, &t);
            MPI_Type_commit(&t);
            MPI_Irecv(buf + 32 * i, 1, t, 1, TAG, MPI_COMM_WORLD, &r[i]);
            MPI_Type_free(&t);
        }
        MPI_Barrier(MPI_COMM_WORLD);
        MPI_Wait(&r[0], MPI_STATUS_IGNORE);
        MPI_Waitall(1, &r[1], MPI_STATUSES_IGNORE);
        printf("%s\n", buf[9] == 5 && buf[32 + 26] == 14 ? "ok" : "wrong data");
    } else {
        MPI_Barrier(MPI_COMM_WORLD);
        if (rank == 1) {
            for (int i = 0; i < 16; i++) buf[i] = i;
            for (int i = 0; i < 2; i++) MPI_Send(buf, 16, MPI_DOUBLE, 0, TAG, MPI_COMM_WORLD);
        }
    }
    MPI_Finalize();
    return 0;
}
