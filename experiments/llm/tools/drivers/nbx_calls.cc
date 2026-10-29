// Calls of deal.II's ConsensusAlgorithm_NBX on one communicator, each checked.
//
// Every call is one ConsensusAlgorithm_NBX<std::pair<types::global_dof_index,
// types::global_dof_index>, unsigned int> on MPI_COMM_WORLD, the instantiation that
// compute_index_owner() uses. The algorithm is called directly. In call c every rank sends one
// request to every other rank, the pair (c, its rank); a rank answers a request with the call it
// is in itself, the call and the rank the request names, and the rank it took the request from.
// A rank checks every answer it unpacks: it must come from the rank it asked, in the same call,
// and name its own request. Each wrong answer is printed; at the end rank 0 prints how many there
// were in all, and "ok" when there were none.
//
// usage: nbx_calls [calls (default 3)]
#include <deal.II/base/mpi.h>

#include <cstdlib>
#include <iostream>
#include <sstream>
#include <utility>
#include <vector>

using namespace dealii;

using Request = std::pair<types::global_dof_index, types::global_dof_index>;
using Answer  = unsigned int;

// source/base/mpi.cc instantiates ConsensusAlgorithm_NBX only implicitly, through the
// selector's constructor, which inlines NBX's: the library exports every member of the class
// but the two constructors. These are their definitions, copied from source/base/mpi.cc, the
// same as the library's; the algorithm itself is the library's.
namespace dealii
{
  namespace Utilities
  {
    namespace MPI
    {
      template <typename T1, typename T2>
      ConsensusAlgorithm<T1, T2>::ConsensusAlgorithm(
        ConsensusAlgorithmProcess<T1, T2> &process,
        const MPI_Comm &                   comm)
        : process(process)
        , comm(comm)
        , my_rank(this_mpi_process(comm))
        , n_procs(n_mpi_processes(comm))
      {}

      template <typename T1, typename T2>
      ConsensusAlgorithm_NBX<T1, T2>::ConsensusAlgorithm_NBX(
        ConsensusAlgorithmProcess<T1, T2> &process,
        const MPI_Comm &                   comm)
        : ConsensusAlgorithm<T1, T2>(process, comm)
      {}
    } // namespace MPI
  }   // namespace Utilities
} // namespace dealii

class Calls : public Utilities::MPI::ConsensusAlgorithmProcess<Request, Answer>
{
public:
  Calls(const unsigned int call, const MPI_Comm &comm)
    : call(call)
    , my_rank(Utilities::MPI::this_mpi_process(comm))
    , n_procs(Utilities::MPI::n_mpi_processes(comm))
  {}

  std::vector<unsigned int>
  compute_targets() override
  {
    std::vector<unsigned int> targets;
    for (unsigned int r = 0; r < n_procs; ++r)
      if (r != my_rank)
        targets.push_back(r);
    return targets;
  }

  void
  pack_recv_buffer(const int, std::vector<Request> &send_buffer) override
  {
    send_buffer = {Request(call, my_rank)};
  }

  void
  process_request(const unsigned int          other_rank,
                  const std::vector<Request> &buffer_recv,
                  std::vector<Answer> &       request_buffer) override
  {
    const Answer none = numbers::invalid_unsigned_int;
    request_buffer    = {call,
                      buffer_recv.empty() ? none : Answer(buffer_recv[0].first),
                      buffer_recv.empty() ? none : Answer(buffer_recv[0].second),
                      other_rank};
  }

  void
  prepare_recv_buffer(const int, std::vector<Answer> &recv_buffer) override
  {
    recv_buffer.resize(4);
  }

  void
  unpack_recv_buffer(const int                  other_rank,
                     const std::vector<Answer> &recv_buffer) override
  {
    ++answers;
    if (recv_buffer.size() == 4 && recv_buffer[0] == call &&
        recv_buffer[1] == call && recv_buffer[2] == my_rank &&
        recv_buffer[3] == my_rank)
      return;
    ++wrong;
    std::ostringstream s;
    s << "call " << call << " rank " << my_rank << ": the answer of rank "
      << other_rank << " is";
    for (const auto v : recv_buffer)
      s << ' ' << static_cast<int>(v);
    s << " (expected " << call << ' ' << call << ' ' << my_rank << ' '
      << my_rank << ": its call, the call and rank of the request, the rank "
      << "it took the request from)\n";
    std::cout << s.str() << std::flush;
  }

  const unsigned int call;
  const unsigned int my_rank;
  const unsigned int n_procs;
  unsigned int       answers = 0;
  unsigned int       wrong   = 0;
};

int
main(int argc, char **argv)
{
  Utilities::MPI::MPI_InitFinalize mpi(argc, argv, 1);
  const MPI_Comm     comm  = MPI_COMM_WORLD;
  const unsigned int calls = argc > 1 ? std::atoi(argv[1]) : 3;
  const unsigned int n     = Utilities::MPI::n_mpi_processes(comm);

  unsigned int answers = 0, wrong = 0;
  for (unsigned int c = 0; c < calls; ++c)
    {
      Calls process(c, comm);
      Utilities::MPI::ConsensusAlgorithm_NBX<Request, Answer> nbx(process,
                                                                  comm);
      nbx.run();
      answers += process.answers;
      wrong += process.wrong;
    }

  // after the last call: every rank expects n - 1 answers per call
  if (answers != calls * (n - 1))
    {
      std::cout << "rank " << Utilities::MPI::this_mpi_process(comm)
                << ": " << answers << " answers, expected " << calls * (n - 1)
                << '\n'
                << std::flush;
      ++wrong;
    }
  const unsigned int total = Utilities::MPI::sum(wrong, comm);
  if (Utilities::MPI::this_mpi_process(comm) == 0)
    std::cout << "nbx_calls: " << calls << " calls on " << n << " ranks, "
              << total << " wrong answers" << (total == 0 ? ": ok" : "")
              << '\n'
              << std::flush;
  return 0;
}
