// Driver and oracle for dlaf::matrix::copy(src, dst, grid), the copy between two distributions
// of one matrix on a 2D process grid (include/dlaf/matrix/copy.h).
//
//   copy_check M N MB_SRC NB_SRC MB_DST NB_DST P Q SRC_R SRC_C DST_R DST_C REPS JITTER_US
//
// The matrix is M x N; the source is distributed in MB_SRC x NB_SRC tiles with source rank
// (SRC_R, SRC_C), the destination in MB_DST x NB_DST tiles with source rank (DST_R, DST_C), on a
// P x Q grid (column-major ordering, as the miniapps). Each repetition sets the source with
// dlaf::matrix::util::set (one task per tile), optionally keeps each source tile busy for a random
// 0..JITTER_US microseconds (standing for the computation that produced it), copies, and compares
// every element of the destination with the value it must have, row * 100000 + col.
//
// Rank 0 first prints how many pairs of tiles copy.h sends between the same two ranks with the
// same tag ("colliding pairs"), computed here from the two distributions and copy.h's tag formula.
// Per repetition it prints the wrong elements and tiles over all ranks, and for the first wrong
// element the global index whose value it holds.
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <random>
#include <thread>
#include <tuple>

#include <mpi.h>

#include <pika/execution.hpp>
#include <pika/init.hpp>
#include <pika/runtime.hpp>

#include <dlaf/common/index2d.h>
#include <dlaf/common/range2d.h>
#include <dlaf/communication/communicator_grid.h>
#include <dlaf/communication/init.h>
#include <dlaf/init.h>
#include <dlaf/matrix/copy.h>
#include <dlaf/matrix/distribution.h>
#include <dlaf/matrix/matrix.h>
#include <dlaf/sender/transform.h>
#include <dlaf/util_matrix.h>

using namespace dlaf;
namespace ex = pika::execution::experimental;
using T = double;

static long a[14];

static T value(const GlobalElementIndex& ij) {
  return T(ij.row() * 100000 + ij.col());
}

// Pairs of (retiled) tiles that copy.h exchanges between the same two ranks with the same tag.
static long colliding_pairs(const matrix::Distribution& ds, const matrix::Distribution& dd, int P) {
  const SizeType mb = std::min(ds.tile_size().rows(), dd.tile_size().rows());
  const SizeType nb = std::min(ds.tile_size().cols(), dd.tile_size().cols());
  const SizeType mt = util::ceilDiv(ds.size().rows(), mb), nt = util::ceilDiv(ds.size().cols(), nb);
  const SizeType ld = util::ceilDiv(mt, SizeType(P));  // copy.h: ceilDiv(nrTiles().rows(), rows)
  std::map<std::tuple<long, long, long, long, long>, long> n;
  long pairs = 0;
  for (SizeType c = 0; c < nt; ++c)
    for (SizeType r = 0; r < mt; ++r) {
      const GlobalElementIndex el{r * mb, c * nb};
      const auto s = ds.rankGlobalTile(ds.globalTileIndex(el));
      const auto d = dd.rankGlobalTile(dd.globalTileIndex(el));
      if (s == d)
        continue;
      const long tag = r + c * ld;  // copy.h: ij.row() + ij.col() * ld
      pairs += n[{s.row(), s.col(), d.row(), d.col(), tag}]++;
    }
  return pairs;
}

static int run(pika::program_options::variables_map& vm) {
  pika::scoped_finalize finalizer;
  dlaf::ScopedInitializer init(vm);
  const GlobalElementSize size{a[0], a[1]};
  const TileElementSize ts_src{a[2], a[3]}, ts_dst{a[4], a[5]};
  const int P = int(a[6]), Q = int(a[7]);
  const comm::Index2D o_src{int(a[8]), int(a[9])}, o_dst{int(a[10]), int(a[11])};
  const long reps = a[12], jitter = a[13];

  comm::Communicator world(MPI_COMM_WORLD);
  comm::CommunicatorGrid grid(world, P, Q, common::Ordering::ColumnMajor);
  matrix::Distribution ds(size, ts_src, grid.size(), grid.rank(), o_src);
  matrix::Distribution dd(size, ts_dst, grid.size(), grid.rank(), o_dst);
  int rank;
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  int provided;
  MPI_Query_thread(&provided);
  if (rank == 0)
    std::printf("provided %d threads %zu colliding pairs %ld\n", provided,
                pika::get_os_thread_count(), colliding_pairs(ds, dd, P));

  std::mt19937 rng(std::random_device{}() + unsigned(rank));
  long bad_reps = 0;
  for (long rep = 0; rep < reps; ++rep) {
    Matrix<T, Device::CPU> src(ds);
    Matrix<T, Device::CPU> dst(dd);
    matrix::util::set(src, value);
    if (jitter > 0)
      for (const auto ij_lc : common::iterate_range2d(ds.localNrTiles())) {
        const long us = std::uniform_int_distribution<long>(0, jitter)(rng);
        dlaf::internal::transformDetach(
            dlaf::internal::Policy<Backend::MC>(), [us](const matrix::Tile<T, Device::CPU>&) {
              std::this_thread::sleep_for(std::chrono::microseconds(us));
            },
            src.readwrite(ij_lc));
      }
    {
      Matrix<const T, Device::CPU>& src_const = src;
      matrix::copy(src_const, dst, grid);
    }
    long wrong[2] = {0, 0};  // elements, tiles
    long first[4] = {-1, -1, -1, -1};
    for (const auto ij_lc : common::iterate_range2d(dd.localNrTiles())) {
      auto w = pika::this_thread::experimental::sync_wait(dst.read(ij_lc));
      const auto& tile = w.get();
      const auto ij = dd.globalTileIndex(ij_lc);
      long bad = 0;
      for (const auto e : common::iterate_range2d(tile.size())) {
        const auto g = dd.global_element_index(ij, e);
        const T got = tile(e);
        if (got != value(g)) {
          if (bad++ == 0 && first[0] < 0) {
            first[0] = g.row(), first[1] = g.col();
            first[2] = long(got) / 100000, first[3] = long(got) % 100000;
          }
        }
      }
      wrong[0] += bad;
      wrong[1] += bad > 0;
    }
    grid.wait_all_communicators();
    long sum[2];
    MPI_Allreduce(wrong, sum, 2, MPI_LONG, MPI_SUM, MPI_COMM_WORLD);
    if (sum[0] > 0 && rank != 0 && first[0] >= 0)
      std::printf("rank %d rep %ld first wrong (%ld,%ld) holds (%ld,%ld)\n", rank, rep, first[0],
                  first[1], first[2], first[3]);
    if (rank == 0) {
      if (first[0] >= 0)
        std::printf("rank 0 rep %ld first wrong (%ld,%ld) holds (%ld,%ld)\n", rep, first[0],
                    first[1], first[2], first[3]);
      std::printf("rep %ld wrong elements %ld tiles %ld %s\n", rep, sum[0], sum[1],
                  sum[0] ? "WRONG" : "ok");
    }
    bad_reps += sum[0] > 0;
  }
  if (rank == 0)
    std::printf("summary reps %ld wrong %ld\n", reps, bad_reps);
  std::fflush(stdout);
  return EXIT_SUCCESS;
}

int main(int argc, char** argv) {
  dlaf::comm::mpi_init mpi_initter(argc, argv);
  if (argc < 15) {
    std::fprintf(stderr, "usage: copy_check M N MB_SRC NB_SRC MB_DST NB_DST P Q SRC_R SRC_C DST_R "
                         "DST_C REPS JITTER_US [pika options]\n");
    return 2;
  }
  for (int i = 0; i < 14; ++i)
    a[i] = std::atol(argv[i + 1]);
  pika::program_options::options_description desc("copy_check");
  desc.add(dlaf::getOptionsDescription());
  pika::init_params p;
  p.desc_cmdline = desc;
  // pika sees the program name and the options after the 14 numbers.
  argv[14] = argv[0];
  return pika::init(run, argc - 14, argv + 14, p);
}
