// Harness around stempy's own gatherEvents() (stempy/electron_mpi.cpp; the functions from
// serializeEvents to gatherBlocks are included verbatim as gather.inc): every worker rank holds
// the electron events of a part of the scan positions, as electronCount() leaves them, and
// rank 0 gathers them. The workers' maps differ in size. Rank 0 then checks the number of events
// per scan position against what every worker contributed and prints "gather ok" or what is
// wrong. STAGGER (ms, third argument, default 0) lets worker r start the gather r*STAGGER ms
// after the first.
// usage: harness [positions=256] [max events per position=12] [stagger ms=0]
#include <bigmpi.h>
#include <cereal/archives/binary.hpp>
#include <cereal/cereal.hpp>
#include <cereal/types/array.hpp>
#include <cereal/types/map.hpp>
#include <cereal/types/vector.hpp>
#include <emp/io/ContiguousStream.hpp>
#include <mpi.h>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <map>
#include <vector>
#include <unistd.h>
#include "streamview.h"

namespace stempy {
// as in stempy/electron.h and electron_mpi.cpp at the tag
using Events = std::vector<std::vector<std::vector<uint32_t>>>;
using SparseEventMap = std::map<uint32_t, std::vector<std::vector<uint32_t>>>;
// serializeBlocks(), which lies between the extracted functions, needs a Block; unused here
struct Block { template <class A> void serialize(A&) {} };
#include "gather.inc"
}

// the events worker r reports at position p: a deterministic function of (r, p)
static size_t nEvents(int r, int p, int maxEv) { return (p % (r + 1) == 0) ? (size_t)((r * 7 + p * 3) % maxEv) : 0; }

int main(int argc, char** argv)
{
  MPI_Init(&argc, &argv);
  int rank, size;
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  MPI_Comm_size(MPI_COMM_WORLD, &size);
  int positions = argc > 1 ? atoi(argv[1]) : 256, maxEv = argc > 2 ? atoi(argv[2]) : 12;
  stempy::Events events(positions);
  if (rank > 0)
    for (int p = 0; p < positions; p++)
      for (size_t k = 0; k < nEvents(rank, p, maxEv); k++)
        events[p].push_back(std::vector<uint32_t>(1 + (k % 3), (uint32_t)(rank * 100000 + p)));
  int stagger = argc > 3 ? atoi(argv[3]) : 0;
  if (rank > 0 && stagger > 0) usleep((useconds_t)rank * stagger * 1000);
  try {
    stempy::gatherEvents(size, rank, events);
  } catch (std::exception& e) {
    fprintf(stderr, "rank %d: exception: %s\n", rank, e.what());
    MPI_Abort(MPI_COMM_WORLD, 3);
  }
  if (rank == 0) {
    long wrong = 0;
    for (int p = 0; p < positions; p++) {
      size_t want = 0;
      for (int r = 1; r < size; r++) want += nEvents(r, p, maxEv);
      if (events[p].size() != want) wrong++;
    }
    if (wrong) printf("gather WRONG: %ld of %d positions\n", wrong, positions);
    else printf("gather ok\n");
  }
  MPI_Finalize();
  return 0;
}
