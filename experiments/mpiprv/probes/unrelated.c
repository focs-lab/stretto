/* Probe for MPI-PRV: the shipped receive-buffer property (pattern Irecv Use Isend) does not
 * relate the three events: any rank's MPI_Irecv, any rank's marker, any rank's MPI_Isend.
 * A correct program at four ranks: rank 1 sends to rank 0, which receives with MPI_Irecv
 * from rank 1, waits, and then calls the marker usebuf2(); later, rank 2 sends to rank 3 with
 * MPI_Isend. Rank 0's receive buffer is never used before its receive completes, yet the run
 * contains Irecv (rank 0), Use (rank 0), Isend (rank 2) in this order. */
#include <mpi.h>
#include <stdio.h>
#include <unistd.h>

void usebuf2(void) {}

int main(int argc, char **argv) {
  int rank, buf = -1, val = 42;
  MPI_Request req = MPI_REQUEST_NULL;
  MPI_Init(&argc, &argv);
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  if (rank == 0) {
    MPI_Irecv(&buf, 1, MPI_INT, 1, 0, MPI_COMM_WORLD, &req);
    MPI_Wait(&req, MPI_STATUS_IGNORE);
    sleep(1);
    usebuf2();
  } else if (rank == 1) {
    MPI_Send(&val, 1, MPI_INT, 0, 0, MPI_COMM_WORLD);
  } else if (rank == 2) {
    sleep(3);
    MPI_Isend(&val, 1, MPI_INT, 3, 0, MPI_COMM_WORLD, &req);
    MPI_Wait(&req, MPI_STATUS_IGNORE);
  } else if (rank == 3) {
    MPI_Recv(&buf, 1, MPI_INT, 2, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  }
  MPI_Finalize();
  return 0;
}
