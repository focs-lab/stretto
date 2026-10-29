// LU factorisation of an m-by-n matrix (wide, n > m, for the finding) with SLATE, checked by
// its residual.
//
// SLATE's tester checks getrf only through getrs, which needs a square matrix, so a wide
// factorisation has no oracle there. This driver fills A with values that depend only on the
// global indices, factors it with slate::getrf (MethodLU::CALU, tournament pivoting, or
// PartialPiv), gathers the factors to rank 0 and prints
//     || P A - L U ||_max / (|| A ||_max * m)
// together with a checksum of the factors. A correct factorisation gives a residual of the order
// of the machine epsilon (1e-16 .. 1e-18); a wrong one O(1e-3) or more.
//
// usage: wide_lu m n nb p q method(C|P) lookahead [repeat]
#include <slate/slate.hh>
#include <blas.hh>
#include <mpi.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>

static double value(int64_t i, int64_t j)
{
    uint64_t x = uint64_t(i) * 0x9E3779B97F4A7C15ull ^ uint64_t(j) * 0xC2B2AE3D27D4EB4Full;
    x ^= x >> 31; x *= 0xBF58476D1CE4E5B9ull; x ^= x >> 29;
    return double(x >> 11) / double(1ull << 53) - 0.5;
}

int main(int argc, char** argv)
{
    int provided;
    MPI_Init_thread(&argc, &argv, MPI_THREAD_MULTIPLE, &provided);
    int rank;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    if (argc < 8) {
        if (rank == 0)
            std::fprintf(stderr, "usage: wide_lu m n nb p q method(C|P) lookahead [repeat]\n");
        MPI_Abort(MPI_COMM_WORLD, 2);
    }
    int64_t m = std::atol(argv[1]), n = std::atol(argv[2]), nb = std::atol(argv[3]);
    int p = std::atoi(argv[4]), q = std::atoi(argv[5]);
    slate::MethodLU method = argv[6][0] == 'C' ? slate::MethodLU::CALU : slate::MethodLU::PartialPiv;
    int64_t lookahead = std::atol(argv[7]);
    int repeat = argc > 8 ? std::atoi(argv[8]) : 1;
    if (rank == 0)
        std::printf("provided %d, m %ld n %ld nb %ld grid %dx%d method %c lookahead %ld\n",
                    provided, long(m), long(n), long(nb), p, q, argv[6][0], long(lookahead));

    int failed = 0;
    for (int rep = 0; rep < repeat; ++rep) {
        slate::Matrix<double> A(m, n, nb, p, q, MPI_COMM_WORLD);
        A.insertLocalTiles();
        for (int64_t j = 0; j < A.nt(); ++j)
            for (int64_t i = 0; i < A.mt(); ++i)
                if (A.tileIsLocal(i, j)) {
                    auto T = A(i, j);
                    for (int64_t jj = 0; jj < T.nb(); ++jj)
                        for (int64_t ii = 0; ii < T.mb(); ++ii)
                            T.at(ii, jj) = value(i*nb + ii, j*nb + jj);
                }
        slate::Pivots pivots;
        slate::getrf(A, pivots, {{slate::Option::MethodLU, method},
                                 {slate::Option::Lookahead, lookahead}});
        std::vector<double> F;
        if (rank == 0)
            F.resize(m*n);
        A.gather(F.data(), m);
        if (rank == 0) {
            // P A: apply each panel's pivots forward, as permuteRows does
            std::vector<double> PA(m*n);
            for (int64_t j = 0; j < n; ++j)
                for (int64_t i = 0; i < m; ++i)
                    PA[i + j*m] = value(i, j);
            for (size_t k = 0; k < pivots.size(); ++k)
                for (size_t i = 0; i < pivots[k].size(); ++i) {
                    int64_t r1 = int64_t(k)*nb + int64_t(i);
                    int64_t r2 = (int64_t(k) + pivots[k][i].tileIndex())*nb + pivots[k][i].elementOffset();
                    if (r1 != r2)
                        for (int64_t j = 0; j < n; ++j)
                            std::swap(PA[r1 + j*m], PA[r2 + j*m]);
                }
            // L (m-by-r, unit lower trapezoidal) and U (r-by-n, upper trapezoidal), r = min(m, n)
            int64_t r = std::min(m, n);
            std::vector<double> L(m*r, 0.0), U(r*n, 0.0);
            for (int64_t j = 0; j < r; ++j) {
                L[j + j*m] = 1.0;
                for (int64_t i = j+1; i < m; ++i)
                    L[i + j*m] = F[i + j*m];
            }
            for (int64_t j = 0; j < n; ++j)
                for (int64_t i = 0; i <= std::min(j, r-1); ++i)
                    U[i + j*r] = F[i + j*m];
            blas::gemm(blas::Layout::ColMajor, blas::Op::NoTrans, blas::Op::NoTrans, m, n, r,
                       1.0, L.data(), m, U.data(), r, -1.0, PA.data(), m);
            double rmax = 0, amax = 0, sum = 0;
            for (int64_t j = 0; j < n; ++j)
                for (int64_t i = 0; i < m; ++i) {
                    rmax = std::max(rmax, std::fabs(PA[i + j*m]));
                    amax = std::max(amax, std::fabs(value(i, j)));
                    sum += F[i + j*m] * double(1 + (i*7 + j*13) % 97);
                }
            double resid = rmax / (amax * double(m));
            bool ok = resid < 1e-12;
            failed += !ok;
            std::printf("rep %d residual %.3e checksum %.17g %s\n", rep, resid, sum, ok ? "ok" : "WRONG");
        }
    }
    MPI_Bcast(&failed, 1, MPI_INT, 0, MPI_COMM_WORLD);
    MPI_Finalize();
    return failed ? 1 : 0;
}
