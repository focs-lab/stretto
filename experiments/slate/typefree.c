/* A receive whose datatype is freed while the receive is pending, as SLATE's Tile::irecv does for
 * a strided tile (MPI_Type_vector, MPI_Type_commit, MPI_Irecv, MPI_Type_free, later MPI_Wait).
 * MPI allows it: "Any communication that is currently using this datatype will complete
 * normally" (MPI 4.1, MPI_TYPE_FREE). Untraced the program prints "ok"; under the tracer before
 * 30 Oct 2026 the completion's MPI_Get_count used the freed datatype (idioms/33_recv_type_freed).
 *   mpicc typefree.c -o typefree && mpirun -np 2 ./typefree */
#include <mpi.h>
#include <stdio.h>

int main(int argc, char **argv) {
    MPI_Init(&argc, &argv);
    int rank;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    double buf[64] = {0};
    if (rank == 0) {
        MPI_Datatype t;
        MPI_Request r;
        MPI_Type_vector(4, 4, 8, MPI_DOUBLE, &t);
        MPI_Type_commit(&t);
        MPI_Irecv(buf, 1, t, 1, 0, MPI_COMM_WORLD, &r);
        MPI_Type_free(&t);
        MPI_Barrier(MPI_COMM_WORLD);
        MPI_Wait(&r, MPI_STATUS_IGNORE);
        printf("ok %g\n", buf[9]);
    } else {
        for (int i = 0; i < 16; i++) buf[i] = i;
        MPI_Barrier(MPI_COMM_WORLD);
        MPI_Send(buf, 16, MPI_DOUBLE, 0, 0, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
