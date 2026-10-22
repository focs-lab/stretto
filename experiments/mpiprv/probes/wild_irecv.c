/* Probe for MPI-PRV: a correct program whose receive takes its source from SRC.
 * Rank 1 sends one integer with MPI_Isend; rank 0 receives it with MPI_Irecv, waits for it,
 * and only then calls the marker usebuf2(), the "Use" event of the shipped receive-buffer
 * property (pattern Irecv Use Isend). No execution can place rank 1's MPI_Isend after the
 * marker: the wait cannot return before the message is sent. A report of the pattern on
 * this program is therefore a reordering that no execution has.
 * Built twice by experiments/mpiprv/probes.sh: SRC = 1 (the sender) and SRC = MPI_ANY_SOURCE.
 * The pause before the marker makes sure that rank 1's record of its MPI_Isend, which the
 * instrumentation writes after the call returns, precedes the marker in the shared trace. */
#include <mpi.h>
#include <stdio.h>
#include <unistd.h>

#ifndef SRC
#define SRC MPI_ANY_SOURCE
#endif

void usebuf2(void) {}

int main(int argc, char **argv) {
  int rank, buf = -1, val = 42;
  MPI_Request req = MPI_REQUEST_NULL;
  MPI_Init(&argc, &argv);
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  if (rank == 0) {
    MPI_Irecv(&buf, 1, MPI_INT, SRC, 0, MPI_COMM_WORLD, &req);
    MPI_Wait(&req, MPI_STATUS_IGNORE);
    sleep(1);
    usebuf2();
    printf("rank 0 received %d\n", buf);
  } else if (rank == 1) {
    MPI_Isend(&val, 1, MPI_INT, 0, 0, MPI_COMM_WORLD, &req);
    MPI_Wait(&req, MPI_STATUS_IGNORE);
  }
  MPI_Finalize();
  return 0;
}
