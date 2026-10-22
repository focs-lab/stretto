/* Probe for MPI-PRV: the blocking form of wild_irecv.c. Rank 1 sends with MPI_Send; rank 0
 * receives with MPI_Recv from SRC and then calls the marker usebuf2() ("Use"). The pattern
 * Recv Use Send asks for an execution in which rank 1's send starts after rank 0 has used
 * the message, which none has. Built with SRC = 1 and SRC = MPI_ANY_SOURCE. */
#include <mpi.h>
#include <stdio.h>
#include <unistd.h>

#ifndef SRC
#define SRC MPI_ANY_SOURCE
#endif

void usebuf2(void) {}

int main(int argc, char **argv) {
  int rank, buf = -1, val = 42;
  MPI_Init(&argc, &argv);
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  if (rank == 0) {
    MPI_Recv(&buf, 1, MPI_INT, SRC, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    sleep(1);
    usebuf2();
    printf("rank 0 received %d\n", buf);
  } else if (rank == 1) {
    MPI_Send(&val, 1, MPI_INT, 0, 0, MPI_COMM_WORLD);
  }
  MPI_Finalize();
  return 0;
}
