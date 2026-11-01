/* Emulate several nodes on one machine for the program's own code (LD_PRELOAD).
 *
 * ComEx serves requests between nodes through MPI and requests within a node through shared
 * memory, so on one machine its progress engine hardly sends a message. libcomex, linked
 * statically into the GA test programs, imports gethostname and gethostid (nm -u); this
 * library answers both
 * with a node of its own for every FAKEHOST_PPN consecutive ranks (rank from the launcher's
 * environment: OMPI_COMM_WORLD_RANK, PMI_RANK or PMIX_RANK), but only to callers in the main
 * executable: the MPI library and the launcher see the real host, so that they start and
 * connect the ranks as usual. FAKEHOST_PPN unset or 0: the real answers everywhere.
 *   gcc -O2 -shared -fPIC -o libfakehost.so fakehost.c -ldl
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <link.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Does addr lie in a loaded segment of the main executable (the first object listed)? */
struct probe { const void *addr; int first, inside; };

static int in_main(struct dl_phdr_info *info, size_t size, void *data)
{
    struct probe *p = data;
    (void)size;
    if (!p->first)
        return 1;
    p->first = 0;
    for (int i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr) *ph = &info->dlpi_phdr[i];
        uintptr_t lo = info->dlpi_addr + ph->p_vaddr, a = (uintptr_t)p->addr;
        if (ph->p_type == PT_LOAD && a >= lo && a < lo + ph->p_memsz)
            p->inside = 1;
    }
    return 1;
}

static int node_of_caller(const void *ra)
{
    const char *ppn = getenv("FAKEHOST_PPN");
    if (!ppn || atoi(ppn) <= 0)
        return -1;
    struct probe p = {ra, 1, 0};
    dl_iterate_phdr(in_main, &p);
    if (!p.inside)
        return -1;
    const char *r = getenv("OMPI_COMM_WORLD_RANK");
    if (!r) r = getenv("PMI_RANK");
    if (!r) r = getenv("PMIX_RANK");
    if (!r)
        return -1;
    return atoi(r) / atoi(ppn);
}

int gethostname(char *name, size_t len)
{
    int node = node_of_caller(__builtin_return_address(0));
    if (node < 0) {
        int (*real)(char *, size_t) = (int (*)(char *, size_t))dlsym(RTLD_NEXT, "gethostname");
        return real(name, len);
    }
    if (snprintf(name, len, "fakenode%d", node) >= (int)len)
        return -1;
    return 0;
}

long gethostid(void)
{
    int node = node_of_caller(__builtin_return_address(0));
    if (node < 0) {
        long (*real)(void) = (long (*)(void))dlsym(RTLD_NEXT, "gethostid");
        return real();
    }
    return 0x10000 + node;
}
