/* The entry points of ThreadSanitizer's compiler instrumentation, as a minimal runtime that
   performs every atomic operation and records the synchronisation it carries
   (docs/threads.md, section 17).

   A program, or the runtime library that synchronises its threads (pika, HDF5), is compiled
   with the system compiler's ThreadSanitizer instrumentation and without ThreadSanitizer's own
   runtime. clang (atomic operations only; memory accesses and function entry and exit are
   left alone, and volatile accesses become calls only when asked for):

     clang -fsanitize=thread -fno-sanitize-link-runtime \
           -mllvm -tsan-instrument-memory-accesses=0 -mllvm -tsan-instrument-func-entry-exit=0 \
           -mllvm -tsan-instrument-memintrinsics=0 ... -L<tracer/bin> -lstretto-tsan

   (with volatile flags: -mllvm -tsan-distinguish-volatile=1 and memory accesses left on, whose
   calls do nothing here). gcc instruments every memory access, so it costs a call per access:

     gcc -fsanitize=thread --param=tsan-instrument-func-entry-exit=0 ... (compile)
     gcc ... -L<tracer/bin> -lstretto-tsan (link without -fsanitize=thread)

   This file is compiled twice. Alone (-DSTRETTO_TSAN_STUB) it is libstretto-tsan.so, which the
   instrumented program links: every operation is performed and nothing is recorded, so the
   program runs untraced and under the replay library. Included by mpitrace.c it is the
   tracer's: the tracer, preloaded, interposes these definitions, and the hooks at_* below
   (defined in mpitrace.c, section "atomics") turn an operation into clock updates.

   Memory orders are TSan's (and the compilers' __ATOMIC_* values): 0 relaxed, 1 consume, 2
   acquire, 3 release, 4 acq_rel, 5 seq_cst. What is recorded:
   - a load, or the read of a read-modify-write or of a failed compare-exchange, with
     consume, acquire, acq_rel or seq_cst order acquires the location's clock;
   - a store with release or seq_cst order sets the location's clock to the thread's;
   - a read-modify-write (or successful compare-exchange) with release, acq_rel or seq_cst
     order joins the thread's clock into the location's, so that the location keeps every
     releasing RMW's clock (a release sequence); a relaxed one leaves the clock as it is;
   - a release fence makes every later relaxed store and RMW of the thread a release; an
     acquire fence acquires the locations of the thread's relaxed loads since its previous
     acquire fence (the last AT_RING of them);
   - a volatile write is a release store, a volatile read an acquire whose clock is taken at
     the thread's next recorded operation or MPI record (the call comes before the read, and
     the clock must be at least the one of the write the read sees);
   - __tsan_acquire/__tsan_release, AnnotateHappensAfter/AnnotateHappensBefore and Stretto's
     stretto_happens_after/stretto_happens_before (stretto_sync.h) acquire and release (join)
     at their address.
   The operation and the clock update of an atomic operation are done under one lock of the
   location's bucket, so an acquire takes the clock of exactly the release whose value it read
   (or of a later one in the location's order). A clock taken later than the operation it
   belongs to (volatile reads, the release of a fence) only adds edges from releases that came
   before it in real time, which never contradicts the run. */
#ifdef STRETTO_TSAN_STUB
#define _GNU_SOURCE
#include <stdint.h>
#include <string.h>
#define AT_EXPORT __attribute__((visibility("default")))
static inline int at_on(void) { return 0; }
static inline unsigned at_lock(const volatile void *a) { (void)a; return 0; }
static inline void at_unlock(unsigned h) { (void)h; }
static inline void at_acquire_locked(unsigned h, const volatile void *a) { (void)h; (void)a; }
static inline void at_release_locked(unsigned h, const volatile void *a, int join) { (void)h; (void)a; (void)join; }
static inline int at_fence_released(void) { return 0; }
static inline void at_note_relaxed(const volatile void *a) { (void)a; }
static inline void at_fence(int acq, int rel) { (void)acq; (void)rel; }
static inline void at_volatile_read(const volatile void *a) { (void)a; }
static inline void at_volatile_write(const volatile void *a) { (void)a; }
static inline void at_annotate(const volatile void *a, int release) { (void)a; (void)release; }
static inline void at_seen(void) {}
#else
#define AT_EXPORT __attribute__((visibility("default")))
#endif

#define MO_ACQ(mo) ((mo) == 1 || (mo) == 2 || (mo) >= 4)
#define MO_REL(mo) ((mo) >= 3)
/* the memory order as a constant for the builtins (a variable one is taken as seq_cst) */
#define AT_LOAD(a, mo) ((mo) == 0 ? __atomic_load_n(a, __ATOMIC_RELAXED) : (mo) == 5 ? __atomic_load_n(a, __ATOMIC_SEQ_CST) \
                        : __atomic_load_n(a, __ATOMIC_ACQUIRE))
#define AT_STORE(a, v, mo) do { if ((mo) == 0) __atomic_store_n(a, v, __ATOMIC_RELAXED); \
                               else if ((mo) == 3) __atomic_store_n(a, v, __ATOMIC_RELEASE); \
                               else __atomic_store_n(a, v, __ATOMIC_SEQ_CST); } while (0)

/* one read-modify-write: OP(a, v) performs it and gives the old value */
#define AT_RMW(T, a, mo, OP) do { \
    int acq_ = MO_ACQ(mo), rel_ = MO_REL(mo); \
    if (at_on()) { \
        if (!acq_) at_note_relaxed(a); \
        if (!rel_ && at_fence_released()) rel_ = 1; \
        if (acq_ || rel_) { \
            unsigned h_ = at_lock(a); T old_ = OP; \
            if (acq_) at_acquire_locked(h_, a); \
            if (rel_) at_release_locked(h_, a, 1); \
            at_unlock(h_); return old_; \
        } \
    } \
    return OP; } while (0)

#define AT_SIZED(N, T) \
AT_EXPORT T __tsan_atomic##N##_load(const volatile T *a, int mo) { \
    if (MO_ACQ(mo) && at_on()) { unsigned h = at_lock(a); T v = AT_LOAD(a, mo); at_acquire_locked(h, a); at_unlock(h); return v; } \
    if (mo == 0 && at_on()) at_note_relaxed(a); \
    return AT_LOAD(a, mo); } \
AT_EXPORT void __tsan_atomic##N##_store(volatile T *a, T v, int mo) { \
    if ((MO_REL(mo) || at_fence_released()) && at_on()) { unsigned h = at_lock(a); at_release_locked(h, a, 0); AT_STORE(a, v, mo); at_unlock(h); return; } \
    AT_STORE(a, v, mo); } \
AT_EXPORT T __tsan_atomic##N##_exchange(volatile T *a, T v, int mo) { AT_RMW(T, a, mo, __atomic_exchange_n(a, v, __ATOMIC_SEQ_CST)); } \
AT_EXPORT T __tsan_atomic##N##_fetch_add(volatile T *a, T v, int mo) { AT_RMW(T, a, mo, __atomic_fetch_add(a, v, __ATOMIC_SEQ_CST)); } \
AT_EXPORT T __tsan_atomic##N##_fetch_sub(volatile T *a, T v, int mo) { AT_RMW(T, a, mo, __atomic_fetch_sub(a, v, __ATOMIC_SEQ_CST)); } \
AT_EXPORT T __tsan_atomic##N##_fetch_and(volatile T *a, T v, int mo) { AT_RMW(T, a, mo, __atomic_fetch_and(a, v, __ATOMIC_SEQ_CST)); } \
AT_EXPORT T __tsan_atomic##N##_fetch_or(volatile T *a, T v, int mo) { AT_RMW(T, a, mo, __atomic_fetch_or(a, v, __ATOMIC_SEQ_CST)); } \
AT_EXPORT T __tsan_atomic##N##_fetch_xor(volatile T *a, T v, int mo) { AT_RMW(T, a, mo, __atomic_fetch_xor(a, v, __ATOMIC_SEQ_CST)); } \
AT_EXPORT T __tsan_atomic##N##_fetch_nand(volatile T *a, T v, int mo) { AT_RMW(T, a, mo, __atomic_fetch_nand(a, v, __ATOMIC_SEQ_CST)); } \
AT_EXPORT int __tsan_atomic##N##_compare_exchange_strong(volatile T *a, T *c, T v, int mo, int fmo) { \
    int acq = MO_ACQ(mo), rel = MO_REL(mo), facq = MO_ACQ(fmo); \
    if (at_on()) { \
        if (!acq || !facq) at_note_relaxed(a); \
        if (!rel && at_fence_released()) rel = 1; \
        if (acq || rel || facq) { \
            unsigned h = at_lock(a); \
            int ok = __atomic_compare_exchange_n(a, c, v, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); \
            if (ok ? acq : facq) at_acquire_locked(h, a); \
            if (ok && rel) at_release_locked(h, a, 1); \
            at_unlock(h); return ok; \
        } \
    } \
    return __atomic_compare_exchange_n(a, c, v, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); } \
AT_EXPORT int __tsan_atomic##N##_compare_exchange_weak(volatile T *a, T *c, T v, int mo, int fmo) { \
    return __tsan_atomic##N##_compare_exchange_strong(a, c, v, mo, fmo); } \
AT_EXPORT T __tsan_atomic##N##_compare_exchange_val(volatile T *a, T c, T v, int mo, int fmo) { \
    __tsan_atomic##N##_compare_exchange_strong(a, &c, v, mo, fmo); return c; }

AT_SIZED(8, char)
AT_SIZED(16, short)
AT_SIZED(32, int)
AT_SIZED(64, long)

#ifdef __SIZEOF_INT128__
/* 16 bytes, by cmpxchg16b (-mcx16): gcc sends the __atomic forms of this size to libatomic */
typedef __int128 a128;
static inline a128 cas128(volatile a128 *a, a128 c, a128 v) { return __sync_val_compare_and_swap(a, c, v); }
static inline a128 load128(volatile a128 *a) { return cas128(a, 0, 0); }
#define RMW128(EXPR) ({ a128 o_ = load128(a), n_; for (;;) { n_ = (EXPR); a128 s_ = cas128(a, o_, n_); if (s_ == o_) break; o_ = s_; } o_; })
#define OLD o_
AT_EXPORT a128 __tsan_atomic128_load(const volatile a128 *a, int mo) {
    volatile a128 *p = (volatile a128 *)a;
    if (MO_ACQ(mo) && at_on()) { unsigned h = at_lock(a); a128 v = load128(p); at_acquire_locked(h, a); at_unlock(h); return v; }
    if (mo == 0 && at_on()) at_note_relaxed(a);
    return load128(p);
}
AT_EXPORT void __tsan_atomic128_store(volatile a128 *a, a128 v, int mo) {
    if ((MO_REL(mo) || at_fence_released()) && at_on()) { unsigned h = at_lock(a); at_release_locked(h, a, 0); RMW128(v); at_unlock(h); return; }
    RMW128(v);
}
AT_EXPORT a128 __tsan_atomic128_exchange(volatile a128 *a, a128 v, int mo) { AT_RMW(a128, a, mo, RMW128(v)); }
AT_EXPORT a128 __tsan_atomic128_fetch_add(volatile a128 *a, a128 v, int mo) { AT_RMW(a128, a, mo, RMW128(OLD + v)); }
AT_EXPORT a128 __tsan_atomic128_fetch_sub(volatile a128 *a, a128 v, int mo) { AT_RMW(a128, a, mo, RMW128(OLD - v)); }
AT_EXPORT a128 __tsan_atomic128_fetch_and(volatile a128 *a, a128 v, int mo) { AT_RMW(a128, a, mo, RMW128(OLD & v)); }
AT_EXPORT a128 __tsan_atomic128_fetch_or(volatile a128 *a, a128 v, int mo) { AT_RMW(a128, a, mo, RMW128(OLD | v)); }
AT_EXPORT a128 __tsan_atomic128_fetch_xor(volatile a128 *a, a128 v, int mo) { AT_RMW(a128, a, mo, RMW128(OLD ^ v)); }
AT_EXPORT a128 __tsan_atomic128_fetch_nand(volatile a128 *a, a128 v, int mo) { AT_RMW(a128, a, mo, RMW128(~(OLD & v))); }
AT_EXPORT int __tsan_atomic128_compare_exchange_strong(volatile a128 *a, a128 *c, a128 v, int mo, int fmo) {
    int acq = MO_ACQ(mo), rel = MO_REL(mo), facq = MO_ACQ(fmo), rec = 0;
    unsigned h = 0;
    if (at_on()) {
        if (!rel && at_fence_released()) rel = 1;
        if ((rec = acq || rel || facq)) h = at_lock(a);
    }
    a128 old = cas128(a, *c, v);
    int ok = old == *c;
    if (!ok) *c = old;
    if (rec) {
        if (ok ? acq : facq) at_acquire_locked(h, a);
        if (ok && rel) at_release_locked(h, a, 1);
        at_unlock(h);
    }
    return ok;
}
AT_EXPORT int __tsan_atomic128_compare_exchange_weak(volatile a128 *a, a128 *c, a128 v, int mo, int fmo) {
    return __tsan_atomic128_compare_exchange_strong(a, c, v, mo, fmo);
}
AT_EXPORT a128 __tsan_atomic128_compare_exchange_val(volatile a128 *a, a128 c, a128 v, int mo, int fmo) {
    __tsan_atomic128_compare_exchange_strong(a, &c, v, mo, fmo);
    return c;
}
#undef OLD
#endif

AT_EXPORT void __tsan_atomic_thread_fence(int mo) {
    if (at_on()) at_fence(MO_ACQ(mo), MO_REL(mo));
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}
AT_EXPORT void __tsan_atomic_signal_fence(int mo) { (void)mo; __atomic_signal_fence(__ATOMIC_SEQ_CST); }

/* annotations */
AT_EXPORT void __tsan_acquire(void *a) { if (at_on()) at_annotate(a, 0); }
AT_EXPORT void __tsan_release(void *a) { if (at_on()) at_annotate(a, 1); }
AT_EXPORT void AnnotateHappensAfter(const char *f, int l, const volatile void *a) { (void)f; (void)l; if (at_on()) at_annotate(a, 0); }
AT_EXPORT void AnnotateHappensBefore(const char *f, int l, const volatile void *a) { (void)f; (void)l; if (at_on()) at_annotate(a, 1); }
AT_EXPORT void stretto_happens_after(const volatile void *a) { if (at_on()) at_annotate(a, 0); }
AT_EXPORT void stretto_happens_before(const volatile void *a) { if (at_on()) at_annotate(a, 1); }

/* volatile accesses (clang -mllvm -tsan-distinguish-volatile=1, gcc --param=tsan-distinguish-volatile=1) */
#define AT_VOL(N) \
AT_EXPORT void __tsan_volatile_read##N(void *a) { if (at_on()) at_volatile_read(a); } \
AT_EXPORT void __tsan_volatile_write##N(void *a) { if (at_on()) at_volatile_write(a); } \
AT_EXPORT void __tsan_unaligned_volatile_read##N(void *a) { if (at_on()) at_volatile_read(a); } \
AT_EXPORT void __tsan_unaligned_volatile_write##N(void *a) { if (at_on()) at_volatile_write(a); }
AT_VOL(1) AT_VOL(2) AT_VOL(4) AT_VOL(8) AT_VOL(16)

/* the rest of the interface: plain accesses, function entry and exit, ignores, mutex and
   fiber annotations: nothing to record (a mutex annotated so is built of atomics, which are) */
AT_EXPORT void __tsan_init(void) { at_seen(); }
#define AT_NOP1(name) AT_EXPORT void name(void *a) { (void)a; }
#define AT_PLAIN(N) AT_NOP1(__tsan_read##N) AT_NOP1(__tsan_write##N) AT_NOP1(__tsan_unaligned_read##N) AT_NOP1(__tsan_unaligned_write##N) \
    AT_EXPORT void __tsan_read##N##_pc(void *a, void *pc) { (void)a; (void)pc; } \
    AT_EXPORT void __tsan_write##N##_pc(void *a, void *pc) { (void)a; (void)pc; }
AT_PLAIN(1) AT_PLAIN(2) AT_PLAIN(4) AT_PLAIN(8) AT_PLAIN(16)
AT_EXPORT void __tsan_read_range(void *a, unsigned long n) { (void)a; (void)n; }
AT_EXPORT void __tsan_write_range(void *a, unsigned long n) { (void)a; (void)n; }
AT_EXPORT void __tsan_read_range_pc(void *a, unsigned long n, void *pc) { (void)a; (void)n; (void)pc; }
AT_EXPORT void __tsan_write_range_pc(void *a, unsigned long n, void *pc) { (void)a; (void)n; (void)pc; }
AT_EXPORT void __tsan_vptr_update(void **p, void *v) { (void)p; (void)v; }
AT_EXPORT void __tsan_vptr_read(void **p) { (void)p; }
AT_EXPORT void __tsan_func_entry(void *pc) { (void)pc; }
AT_EXPORT void __tsan_func_exit(void) {}
AT_EXPORT void __tsan_ignore_thread_begin(void) {}
AT_EXPORT void __tsan_ignore_thread_end(void) {}
AT_EXPORT void *__tsan_memcpy(void *d, const void *s, unsigned long n) { return memcpy(d, s, n); }
AT_EXPORT void *__tsan_memmove(void *d, const void *s, unsigned long n) { return memmove(d, s, n); }
AT_EXPORT void *__tsan_memset(void *d, int c, unsigned long n) { return memset(d, c, n); }
AT_EXPORT void __tsan_mutex_create(void *m, unsigned f) { (void)m; (void)f; }
AT_EXPORT void __tsan_mutex_destroy(void *m, unsigned f) { (void)m; (void)f; }
AT_EXPORT void __tsan_mutex_pre_lock(void *m, unsigned f) { (void)m; (void)f; }
AT_EXPORT void __tsan_mutex_post_lock(void *m, unsigned f, int r) { (void)m; (void)f; (void)r; }
AT_EXPORT int __tsan_mutex_pre_unlock(void *m, unsigned f) { (void)m; (void)f; return 0; }
AT_EXPORT void __tsan_mutex_post_unlock(void *m, unsigned f) { (void)m; (void)f; }
AT_EXPORT void __tsan_mutex_pre_signal(void *m, unsigned f) { (void)m; (void)f; }
AT_EXPORT void __tsan_mutex_post_signal(void *m, unsigned f) { (void)m; (void)f; }
AT_EXPORT void __tsan_mutex_pre_divert(void *m, unsigned f) { (void)m; (void)f; }
AT_EXPORT void __tsan_mutex_post_divert(void *m, unsigned f) { (void)m; (void)f; }
static char at_fiber_dummy;
AT_EXPORT void *__tsan_get_current_fiber(void) { return &at_fiber_dummy; }
AT_EXPORT void *__tsan_create_fiber(unsigned f) { (void)f; return &at_fiber_dummy; }
AT_EXPORT void __tsan_destroy_fiber(void *fb) { (void)fb; }
AT_EXPORT void __tsan_switch_to_fiber(void *fb, unsigned f) { (void)fb; (void)f; }
AT_EXPORT void __tsan_set_fiber_name(void *fb, const char *n) { (void)fb; (void)n; }
AT_EXPORT void AnnotateRWLockCreate(const char *f, int l, const volatile void *m) { (void)f; (void)l; (void)m; }
AT_EXPORT void AnnotateRWLockDestroy(const char *f, int l, const volatile void *m) { (void)f; (void)l; (void)m; }
AT_EXPORT void AnnotateRWLockAcquired(const char *f, int l, const volatile void *m, long w) { (void)f; (void)l; (void)m; (void)w; }
AT_EXPORT void AnnotateRWLockReleased(const char *f, int l, const volatile void *m, long w) { (void)f; (void)l; (void)m; (void)w; }
AT_EXPORT void AnnotateBenignRaceSized(const char *f, int l, const volatile void *m, long n, const char *d) { (void)f; (void)l; (void)m; (void)n; (void)d; }
AT_EXPORT void AnnotateIgnoreReadsBegin(const char *f, int l) { (void)f; (void)l; }
AT_EXPORT void AnnotateIgnoreReadsEnd(const char *f, int l) { (void)f; (void)l; }
AT_EXPORT void AnnotateIgnoreWritesBegin(const char *f, int l) { (void)f; (void)l; }
AT_EXPORT void AnnotateIgnoreWritesEnd(const char *f, int l) { (void)f; (void)l; }
AT_EXPORT void AnnotateNewMemory(const char *f, int l, const volatile void *m, long n) { (void)f; (void)l; (void)m; (void)n; }
AT_EXPORT void AnnotateThreadName(const char *f, int l, const char *n) { (void)f; (void)l; (void)n; }
