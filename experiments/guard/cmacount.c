/* Preloaded after the tracer by experiments/guard/run.sh: counts the single-copy calls of the
   MPI library (process_vm_readv/writev, Open MPI's CMA and UCX's cma transport) and their
   failures, and prints them at exit. EPERM: the container refuses the system calls (no
   CAP_SYS_PTRACE), so the library copies instead. EFAULT: a remote page was not accessible,
   which a protected page of the guard would cause on a remote write. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <sys/uio.h>
#include <unistd.h>

typedef ssize_t (*vmfn)(pid_t, const struct iovec *, unsigned long, const struct iovec *, unsigned long, unsigned long);
static long calls[2], perm[2], fault[2];

static ssize_t counted(int w, const char *name, pid_t p, const struct iovec *l, unsigned long nl, const struct iovec *r, unsigned long nr, unsigned long f) {
    static vmfn real[2];
    if (!real[w]) real[w] = (vmfn)dlsym(RTLD_NEXT, name);
    ssize_t n = real[w](p, l, nl, r, nr, f);
    int e = errno;
    __atomic_add_fetch(&calls[w], 1, __ATOMIC_RELAXED);
    if (n < 0 && e == EPERM) __atomic_add_fetch(&perm[w], 1, __ATOMIC_RELAXED);
    if (n < 0 && e == EFAULT) __atomic_add_fetch(&fault[w], 1, __ATOMIC_RELAXED);
    errno = e;
    return n;
}
ssize_t process_vm_readv(pid_t p, const struct iovec *l, unsigned long nl, const struct iovec *r, unsigned long nr, unsigned long f) {
    return counted(0, "process_vm_readv", p, l, nl, r, nr, f);
}
ssize_t process_vm_writev(pid_t p, const struct iovec *l, unsigned long nl, const struct iovec *r, unsigned long nr, unsigned long f) {
    return counted(1, "process_vm_writev", p, l, nl, r, nr, f);
}
__attribute__((destructor)) static void report(void) {
    if (calls[0] || calls[1])
        fprintf(stderr, "[single-copy] readv %ld (EPERM %ld, EFAULT %ld) writev %ld (EPERM %ld, EFAULT %ld)\n",
                calls[0], perm[0], fault[0], calls[1], perm[1], fault[1]);
}
