/* Happens-before annotations for synchronisation the tracer cannot see (docs/threads.md,
   section 17): a pipe, a file, a home-made queue written with plain memory accesses, a raw
   futex. STRETTO_HAPPENS_BEFORE(addr) on the side that publishes, STRETTO_HAPPENS_AFTER(addr)
   on the side that observes it, with the same address: the tracer then orders everything the
   first thread did before the annotation before everything the second does after it, as for a
   release and an acquire of an atomic at addr. The functions are weak, so a program that uses
   the macros needs no library: traced, the tracer (preloaded) defines them; otherwise, or when
   the program links libstretto-tsan.so, they do nothing. */
#ifndef STRETTO_SYNC_H
#define STRETTO_SYNC_H
#ifdef __cplusplus
extern "C" {
#endif
void stretto_happens_before(const volatile void *addr) __attribute__((weak));
void stretto_happens_after(const volatile void *addr) __attribute__((weak));
#ifdef __cplusplus
}
#endif
#define STRETTO_HAPPENS_BEFORE(addr) do { if (stretto_happens_before) stretto_happens_before(addr); } while (0)
#define STRETTO_HAPPENS_AFTER(addr) do { if (stretto_happens_after) stretto_happens_after(addr); } while (0)
#endif
