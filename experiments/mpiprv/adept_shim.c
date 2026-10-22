/* LD_PRELOAD helper for the Adept Kernel check (experiments/mpiprv/adept.sh); the kernel's
 * source is not changed.
 *
 * Always: free() of a block of at least 1 MiB prints "[adept] rank R free N fnv H" on stderr,
 * an FNV-1a hash of the block's bytes. The 27-point stencil frees its two work arrays a0 and
 * a1 at the end, so the hashes are the result of the run, bit for bit.
 *
 * Built with -DORDER, it also wraps MPI_Isend, MPI_Irecv, MPI_Wait and MPI_Barrier: each prints
 * the rank, the call, its peer (MPI_Isend, MPI_Irecv) and CLOCK_REALTIME in nanoseconds, so that
 * the order of calls on different ranks can be read off (the kernel calls its marker right after
 * its last MPI_Irecv); and ADEPT_DELAY="rank:ms" makes that rank
 * sleep before each of its MPI_Isend calls, which posts its halo sends only after its
 * neighbours have run their interior loops: the reordering MPI-PRV reports. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <malloc.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#ifdef ORDER
#include <mpi.h>
#endif

static int my_rank(void) {
    const char *r = getenv("OMPI_COMM_WORLD_RANK");
    if (!r) r = getenv("PMI_RANK");
    return r ? atoi(r) : -1;
}

void free(void *p) {
    static void (*real_free)(void *);
    if (!real_free) real_free = (void (*)(void *))dlsym(RTLD_NEXT, "free");
    if (p) {
        size_t n = malloc_usable_size(p);
        if (n >= (1u << 20)) {
            uint64_t h = 1469598103934665603ULL;
            const unsigned char *b = p;
            for (size_t i = 0; i < n; i++) { h ^= b[i]; h *= 1099511628211ULL; }
            fprintf(stderr, "[adept] rank %d free %zu fnv %016llx\n", my_rank(), n, (unsigned long long)h);
        }
    }
    real_free(p);
}

#ifdef ORDER
static long long now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static int delay_ms(int rank) {
    const char *d = getenv("ADEPT_DELAY");
    int r, ms;
    if (d && sscanf(d, "%d:%d", &r, &ms) == 2 && r == rank) return ms;
    return 0;
}

int MPI_Isend(const void *buf, int count, MPI_Datatype type, int dest, int tag, MPI_Comm comm, MPI_Request *req) {
    int rank = my_rank(), ms = delay_ms(rank);
    if (ms) usleep(1000 * ms);
    fprintf(stderr, "[adept] rank %d isend %d t %lld\n", rank, dest, now_ns());
    return PMPI_Isend(buf, count, type, dest, tag, comm, req);
}

int MPI_Irecv(void *buf, int count, MPI_Datatype type, int src, int tag, MPI_Comm comm, MPI_Request *req) {
    int rc = PMPI_Irecv(buf, count, type, src, tag, comm, req);
    fprintf(stderr, "[adept] rank %d irecv %d t %lld\n", my_rank(), src, now_ns());
    return rc;
}

int MPI_Wait(MPI_Request *req, MPI_Status *st) {
    int rank = my_rank();
    fprintf(stderr, "[adept] rank %d wait-enter t %lld\n", rank, now_ns());
    int rc = PMPI_Wait(req, st);
    fprintf(stderr, "[adept] rank %d wait-exit t %lld\n", rank, now_ns());
    return rc;
}

int MPI_Barrier(MPI_Comm comm) {
    fprintf(stderr, "[adept] rank %d barrier t %lld\n", my_rank(), now_ns());
    return PMPI_Barrier(comm);
}
#endif
