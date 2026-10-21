/* Probe for MPI-PRV: does a barrier, or a blocking message, order events of two ranks?
 * Rank 0 calls the marker markA() ("A") and then synchronises with rank 1; rank 1 calls
 * markB() ("B") after the synchronisation. In every execution A precedes B, so the pattern
 * B A has no execution. SYNC selects the synchronisation:
 *   SYNC = 0  MPI_Barrier on both ranks
 *   SYNC = 1  rank 0 MPI_Send, rank 1 MPI_Recv from rank 0 (the control: a modelled match) */
#include <mpi.h>
#include <stdio.h>
#include <unistd.h>

#ifndef SYNC
#define SYNC 0
#endif

void markA(void) {}
void markB(void) {}

int main(int argc, char **argv) {
  int rank, v = 7;
  MPI_Init(&argc, &argv);
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  if (rank == 0) {
    markA();
    if (SYNC == 0)
      MPI_Barrier(MPI_COMM_WORLD);
    else
      MPI_Send(&v, 1, MPI_INT, 1, 0, MPI_COMM_WORLD);
  } else if (rank == 1) {
    if (SYNC == 0)
      MPI_Barrier(MPI_COMM_WORLD);
    else
      MPI_Recv(&v, 1, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    sleep(1);
    markB();
  }
  MPI_Finalize();
  return 0;
}
