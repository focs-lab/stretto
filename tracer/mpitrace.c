/* Stretto PMPI tracer. One JSON record per MPI event, per rank, in
   $STRETTO_TRACE_DIR/rank-<r>.jsonl (default ./traces). Builds with the system MPI.

   Compile with -DSTRETTO_PIN_ONLY to get the replay library: same interposition, same
   wildcard counting and pinning, no file output.

   Record kinds (all carry "r" and "seq"):
     init      np, thread (provided level). Under MPI_THREAD_MULTIPLE every record also
               carries tid (the calling thread), since per-rank order is then not total.
     begin     call=<recv|wait|probe|coll|send> + the call's arguments; emitted BEFORE a
               potentially blocking call. The loader drops a begin followed by its
               record; an unpaired begin means the rank was killed inside that call.
     send      mode(std|ssend|bsend|rsend|isend|issend|ibsend|irsend), dst, tag, comm,
               count, tsize, tsig (a named type's name, or a derived type flattened into
               "MPI_INT*4,MPI_DOUBLE*2"), [req], [procnull], [persistent], [data]
     recv      src, tag, comm, count, tsize, tsig, [anysrc], [anytag], [procnull], [widx],
               msrc, mtag, mcount (null when unknown), [cancelled], [pinned]
     irecv     same arguments plus req ([mrecv] on both: Mrecv/Imrecv of a probed message;
               [persistent] for a Start, whose [pinned] receive ran as a shadow request)
     wait      kind(wait|waitall|waitany|waitsome|test|testall|testany|testsome), [idx],
               reqs:[ids], done:[{req, [msrc, mtag, mcount, cancelled]}]; a send request that
               was cancelled carries cancelled:0|1 (did the cancel succeed)
     pollrepeat n: the previous record (a failed Test* or Iprobe/Improbe) repeated n more
               times; busy-polling loops are summarised instead of written per iteration
     cancel    req
     reqfree   req (MPI_Request_free: the operation completes unobserved)
     probe     kind(probe|iprobe|mprobe|improbe), src, tag, comm, [anysrc], [anytag], flag,
               [msrc, mtag, mbytes], [pidx] (the probe's ordinal among the rank's wildcard
               probes, what STRETTO_PIN_PROBE counts)
     coll      name, comm, root(-1 if none), count, tsize, [req], [din], [dout]
     comm      name, parent(local id), id(local id), ranks:[world ranks in comm-rank order],
               gord (creation ordinal of that group); an intercommunicator carries
               inter:1, ranks = the remote group (what src/dst mean there), local = the
               local group, members = both sorted (its identity across ranks)
     bufattach size | bufdetach
     overflow  what: a fixed table filled up and later records may be incomplete
     unmodelled call: a call whose communication the analysis does not model (one-sided
               windows, neighbourhood collectives, dynamic processes, generalised requests,
               MPI-4 Isendrecv, partitioned and large-count calls, the Start of a persistent
               collective); the analysis then reports observed findings only. The Start of
               a partitioned request the guard follows carries its req.
     site      id, obj, addr, base: a call site met for the first time, written just before
               the first record from it; records from user code carry site:id. obj is the
               object holding the return address of the MPI call (the executable or a library,
               such as a language binding), addr that address and base the object's load
               address, so addr - base is the same in every process
     abort     code
     finalize  [truncated]
     guard     what, req: with STRETTO_GUARD=1 (see "buffer guard" below), a buffer that the
               pending operation with request id req owns was misused. what=access: the
               application touched it before the completion, kind read|write, off its byte
               offset from the buffer argument, site the instruction that faulted (not a return
               address); what=modified: a send buffer's checksum at the completion differs from
               the one at the call; what=overlap: the buffer of call (an MPI function name, with
               its request id if it has one) overlaps the operation's, and one of the two
               writes to it. The init record carries guard:1 when the guard is on, guard:0 when
               it was asked for and is off (MPI_THREAD_MULTIPLE).
   A record of kind wait with kind getstatus (MPI_Request_get_status) completes nothing.
   Ranks in send/recv records are comm-local; the loader translates them.

   Payloads (STRETTO_PAYLOAD=<bytes>, off when unset or 0): a send carries data, the message
   packed with MPI_Pack in hexadecimal, and a blocking collective of a fixed-size shape
   (bcast, reduce, allreduce, scan, exscan, gather, scatter, allgather, alltoall) carries din,
   what this rank contributes, and dout, what it receives, wherever the shape gives it one; a
   buffer whose packed size exceeds the limit is written as <field>big:<size> instead, and an
   MPI_IN_PLACE one not at all. They let a rank be rerun alone on the recorded messages, which
   is how level L2 learns what it does after a result the trace did not record. */
#define _GNU_SOURCE
#include <mpi.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <link.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>
#include "../replay/pin.h"

/* ------------------------------------------------------------------ state */
static FILE *g_out = NULL;
static int g_rank = -1;
static long g_seq __attribute__((unused)) = 0;
static int g_truncated = 0;
static int g_multi = 0;           /* MPI_THREAD_MULTIPLE was provided: records carry tid */
static long g_payload = 0;        /* STRETTO_PAYLOAD: record message contents up to this many bytes */
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;

static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n);
    if (!q) { fprintf(stderr, "stretto tracer: out of memory\n"); abort(); }
    return q;
}

typedef struct { MPI_Request req; int id; int kind; MPI_Datatype dt; int persistent; int active; int cancel_req; int pin1; MPI_Request *slot; } reqent; /* kind: 0 send, 1 recv, 2 coll, 3 idup, 4 partitioned (guard only); pin1: 1 + the widx of a pinned receive, 0 if none; slot: where the program received the handle */
static reqent *g_reqs = NULL;
static int g_nreqs = 0, g_capreqs = 0, g_next_req = 0;

typedef struct { MPI_Comm c; int id; } comment;
static comment *g_comms = NULL;
static int g_ncomms = 0, g_capcomms = 0, g_next_comm = 0;

typedef struct { char *key; int count; } groupent;
static groupent *g_groups = NULL;
static int g_ngroups = 0, g_capgroups = 0;

/* --------------------------------------------------------------- records */
typedef struct { char *p; size_t len, cap; void *site; } rec_t;

static void rb_init(rec_t *b) { b->cap = 256; b->len = 0; b->p = malloc(b->cap); b->p[0] = 0; b->site = NULL; }
static void rb_printf(rec_t *b, const char *fmt, ...) {
    va_list ap;
    for (;;) {
        va_start(ap, fmt);
        int n = vsnprintf(b->p + b->len, b->cap - b->len, fmt, ap);
        va_end(ap);
        if (n < 0) return;
        if ((size_t)n < b->cap - b->len) { b->len += n; return; }
        b->cap = (b->cap + n + 1) * 2;
        b->p = xrealloc(b->p, b->cap);
    }
}
/* Busy-polling loops (Iprobe/Improbe/Test* returning flag 0) would write one record per
   iteration, gigabytes for a loop that spins until a timeout. A failed poll identical to
   the previous record is not written; the run of repeats is summarised by one
   {"op":"pollrepeat","n":k} record before the next different record. */
#ifndef STRETTO_PIN_ONLY
static char *g_poll_body = NULL;
static long g_poll_rep = 0;

static void flush_poll_locked(void) {
    if (g_poll_rep > 0 && g_out)
        fprintf(g_out, "{\"r\":%d,\"seq\":%ld,\"op\":\"pollrepeat\",\"n\":%ld}\n", g_rank, g_seq++, g_poll_rep);
    g_poll_rep = 0;
}
#endif

#ifndef STRETTO_PIN_ONLY
/* Call sites: the return address of the MPI call, taken in the wrapper's own frame by REC.
   A site is resolved once (dladdr) and written as a site record; later records name its id.
   The helpers that write a call's record are always inlined into its wrapper, so that the
   address is the caller's; records written from other helpers carry no site. */
#define SITE_SLOTS 8192
static struct { void *ra; int id; } g_sitetab[SITE_SLOTS];
static int g_nsites = 0, g_sites_full = 0;
static void *g_self_base = NULL;

static void json_str(FILE *f, const char *s) {
    fputc('"', f);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') fputc('\\', f);
        if ((unsigned char)*s >= 0x20) fputc(*s, f);
    }
    fputc('"', f);
}

static int site_id_locked(void *ra) {
    if (!ra || g_sites_full) return -1;
    size_t h = ((uintptr_t)ra >> 2) % SITE_SLOTS;
    for (size_t k = 0; k < SITE_SLOTS; k++) {
        size_t i = (h + k) % SITE_SLOTS;
        if (g_sitetab[i].ra == ra) return g_sitetab[i].id;
        if (g_sitetab[i].ra != NULL) continue;
        if (g_nsites + 1 >= SITE_SLOTS / 2) {          /* keep probing short: stop naming new sites */
            g_sites_full = 1;
            fprintf(g_out, "{\"r\":%d,\"seq\":%ld,\"op\":\"overflow\",\"what\":\"site table\"}\n", g_rank, g_seq++);
            return -1;
        }
        Dl_info di;
        int id = -1;
        if (dladdr(ra, &di) && di.dli_fbase != g_self_base) {
            char path[PATH_MAX];
            const char *obj = di.dli_fname ? di.dli_fname : "";
            if (obj[0] != '/') {                            /* the executable, named as it was started */
                if (!(obj[0] && realpath(obj, path))) {
                    ssize_t n = readlink("/proc/self/exe", path, sizeof path - 1);
                    path[n > 0 ? n : 0] = 0;
                }
                obj = path;
            }
            id = g_nsites++;
            fprintf(g_out, "{\"r\":%d,\"seq\":%ld,\"op\":\"site\",\"id\":%d,\"obj\":", g_rank, g_seq++, id);
            json_str(g_out, obj);
            fprintf(g_out, ",\"addr\":%lu,\"base\":%lu}\n", (unsigned long)(uintptr_t)ra, (unsigned long)(uintptr_t)di.dli_fbase);
        }
        g_sitetab[i].ra = ra;
        g_sitetab[i].id = id;
        return id;
    }
    return -1;
}
#endif

static void rb_emit_kind(rec_t *b, int failed_poll) {
#ifndef STRETTO_PIN_ONLY
    pthread_mutex_lock(&g_mu);
    if (g_out) {
        if (failed_poll && g_poll_body && strcmp(g_poll_body, b->p) == 0) {
            g_poll_rep++;
            pthread_mutex_unlock(&g_mu);
            free(b->p);
            return;
        }
        flush_poll_locked();
        free(g_poll_body);
        g_poll_body = failed_poll ? strdup(b->p) : NULL;
        int site = site_id_locked(b->site);
        char head[96];
        if (g_multi) snprintf(head, sizeof head, "{\"r\":%d,\"seq\":%ld,\"tid\":%ld,", g_rank, g_seq++, (long)gettid());
        else snprintf(head, sizeof head, "{\"r\":%d,\"seq\":%ld,", g_rank, g_seq++);
        fwrite(head, 1, strlen(head), g_out);
        fwrite(b->p, 1, b->len, g_out);
        if (site >= 0) fprintf(g_out, ",\"site\":%d", site);
        fwrite("}\n", 1, 2, g_out);
        fflush(g_out);
    }
    pthread_mutex_unlock(&g_mu);
#else
    (void)failed_poll;
#endif
    free(b->p);
}
static void rb_emit(rec_t *b) { rb_emit_kind(b, 0); }
#define REC(b, op) rec_t b; rb_init(&b); b.site = __builtin_return_address(0); rb_printf(&b, "\"op\":\"%s\"", op)

/* ----------------------------------------------------------- buffer guard
   STRETTO_GUARD=1 records local concurrency on buffers (family 3): the application touching
   a buffer that a pending nonblocking operation owns, from the call to its completion (from
   the Start of a persistent request). The bytes each pending operation owns are kept as
   spans. Outside MPI calls their pages are write-protected, and pages that lie wholly inside
   a point-to-point receive buffer are read-protected too; every wrapper lifts the protection
   on entry and restores it on exit, so the library's own accesses run unprotected. The
   SIGSEGV handler records a fault that a thread calling MPI takes inside a pending span from
   its own code, then lets the access proceed by unprotecting the page until the next MPI
   call; a fault elsewhere on the page (other data shares it), in another thread or from
   inside the MPI library unprotects the page unrecorded. The record is written at the next
   MPI call, since the handler can only note it. Two checks need no protection: a send
   buffer's checksum at the completion against the one at the call (catching what a page
   opened by another access let through), and every call's buffers against the pending spans
   (two operations that own overlapping bytes, one of them writing).

   Only bytes known exactly are tracked: a datatype is flattened into at most GUARD_SPANS
   contiguous pieces, MPI_IN_PLACE and arguments not significant at the rank are skipped,
   and so are intercommunicator collectives. Pages are protected only in private writable
   mappings off the calling thread's stack: a protected page also makes a system call that
   writes to it fail with EFAULT, and a stack page holds the frames of every call. Remote
   reads by single-copy transports (CMA) still succeed on read-only pages, which is why a
   page holding anything but a point-to-point receive buffer is never made inaccessible.
   The guard is off under MPI_THREAD_MULTIPLE, where another thread's MPI call would lift
   the protection under the access. */
#define GUARD_SPANS 64            /* contiguous pieces per buffer; a datatype with more is not tracked */
#define GUARD_BUDGET 4096         /* type-map elements visited per buffer */
#define GUARD_MAX 65536           /* spans tracked at once */
#define GUARD_NOTES 1024          /* records waiting for the next MPI call */
#define GUARD_LIBS 256            /* executable segments of the MPI library and its components */
#define GTLS(type, name) static __thread type name __attribute__((tls_model("initial-exec")))

typedef struct { const void *buf; long count; MPI_Datatype dt; int write, p2p; } gbuf;   /* write: MPI writes it; p2p: a point-to-point receive */
typedef struct { uintptr_t lo, hi, base; uint64_t sum; int req; unsigned char write, p2p, prot, flags; } gspan;   /* flags: 1 access recorded, 2 overlap */
typedef struct { uintptr_t lo, hi; int prot; } grun;
typedef struct { int what, req, kind, with; long off; uintptr_t pc; const char *call; int ready; } gnote;
typedef struct { void *ra; } gctx;
enum { G_ACCESS, G_MODIFIED, G_OVERLAP };

static int g_guard = 0, g_guard_asked = 0;
static long g_pagesize = 4096;
static gspan *g_spans; static int g_nspans;     /* sorted by lo */
static grun *g_runs; static int g_nruns;        /* the page runs protected outside MPI calls, sorted */
static int g_applied, g_gdirty, g_gfull;        /* runs in force; spans changed since the runs; table full */
static uintptr_t g_gmaxlen;                     /* the longest span tracked so far: bounds the overlap search */
static unsigned g_gseq;                         /* seqlock over spans and runs, for the handler */
static gnote *g_notes; static int g_nnotes;
GTLS(int, t_gdepth);                            /* MPI calls of this thread in progress */
GTLS(int, t_gcaller);                           /* this thread calls MPI: its faults are recorded */
GTLS(int, t_gwriting);                          /* this thread is updating the tables */
GTLS(uintptr_t, t_stack_lo); GTLS(uintptr_t, t_stack_hi);
#ifndef STRETTO_PIN_ONLY
static struct { uintptr_t lo, hi; } g_libtext[GUARD_LIBS]; static int g_nlibtext;
static struct sigaction g_prev_segv;            /* the handler faults that are not the guard's go to */
static int (*real_sigaction)(int, const struct sigaction *, struct sigaction *);
GTLS(uintptr_t, t_lastfault);
#endif

static void gw_begin(void) { t_gwriting = 1; __atomic_add_fetch(&g_gseq, 1, __ATOMIC_SEQ_CST); }
static void gw_end(void) { __atomic_add_fetch(&g_gseq, 1, __ATOMIC_SEQ_CST); t_gwriting = 0; }

static void guard_note(int what, int req, int kind, long off, uintptr_t pc, int with, const char *call) {
    int i = __atomic_fetch_add(&g_nnotes, 1, __ATOMIC_ACQ_REL);
    if (i >= GUARD_NOTES) return;
    gnote *x = &g_notes[i];
    x->what = what; x->req = req; x->kind = kind; x->with = with; x->off = off; x->pc = pc; x->call = call;
    __atomic_store_n(&x->ready, 1, __ATOMIC_RELEASE);
}
static void guard_flush(void) {
    static const char *what[] = {"access", "modified", "overlap"};
    int n = __atomic_load_n(&g_nnotes, __ATOMIC_ACQUIRE);
    if (n > GUARD_NOTES) n = GUARD_NOTES;
    for (int i = 0; i < n; i++) {
        gnote *x = &g_notes[i];
        if (!__atomic_load_n(&x->ready, __ATOMIC_ACQUIRE)) continue;
        rec_t b; rb_init(&b); b.site = (void *)x->pc;
        rb_printf(&b, "\"op\":\"guard\",\"what\":\"%s\",\"req\":%d", what[x->what], x->req);
        if (x->what == G_ACCESS) rb_printf(&b, ",\"kind\":\"%s\",\"off\":%ld", x->kind ? "write" : "read", x->off);
        if (x->what == G_OVERLAP) { rb_printf(&b, ",\"call\":\"%s\"", x->call); if (x->with >= 0) rb_printf(&b, ",\"with\":%d", x->with); }
        rb_emit(&b);
        x->ready = 0;
    }
    if (n) __atomic_store_n(&g_nnotes, 0, __ATOMIC_RELEASE);
}

/* ---- spans of a buffer: count elements of a datatype, flattened into contiguous pieces */
typedef struct { MPI_Aint off, len; } grel;
typedef struct { grel s[GUARD_SPANS]; int n, budget; } grels;
static int grel_add(grels *o, MPI_Aint off, MPI_Aint len) {
    if (len <= 0) return 0;
    if (--o->budget < 0) return -1;
    if (o->n && o->s[o->n - 1].off + o->s[o->n - 1].len == off) { o->s[o->n - 1].len += len; return 0; }
    if (o->n == GUARD_SPANS) return -1;
    o->s[o->n].off = off; o->s[o->n].len = len; o->n++;
    return 0;
}
/* 1 if one element of dt has no holes (its bytes are [true lb, true lb + size)), 0 if it has, -1 on error */
static int type_dense(MPI_Datatype dt, MPI_Aint *tlb, MPI_Aint *ext, MPI_Aint *size) {
    MPI_Aint lb, tex; int sz;
    if (PMPI_Type_get_extent(dt, &lb, ext) != MPI_SUCCESS || PMPI_Type_get_true_extent(dt, tlb, &tex) != MPI_SUCCESS
        || PMPI_Type_size(dt, &sz) != MPI_SUCCESS) return -1;
    *size = sz;
    return sz == tex;
}
static int type_grels(MPI_Datatype dt, MPI_Aint base, grels *o, int depth) {
    MPI_Aint tlb, ext, size;
    int d = type_dense(dt, &tlb, &ext, &size), ni, na, nd, comb;
    if (d) return d < 0 ? -1 : grel_add(o, base + tlb, size);
    if (depth > 16 || PMPI_Type_get_envelope(dt, &ni, &na, &nd, &comb) != MPI_SUCCESS || comb == MPI_COMBINER_NAMED) return -1;
    int *ints = malloc(sizeof(int) * (ni > 0 ? ni : 1)); MPI_Aint *addrs = malloc(sizeof(MPI_Aint) * (na > 0 ? na : 1));
    MPI_Datatype *types = malloc(sizeof(MPI_Datatype) * (nd > 0 ? nd : 1));
    int r = -1;
    if (PMPI_Type_get_contents(dt, ni, na, nd, ints, addrs, types) == MPI_SUCCESS) {
        MPI_Aint e = 0, l;
        if (nd > 0) PMPI_Type_get_extent(types[0], &l, &e);
        r = 0;
        switch (comb) {
        case MPI_COMBINER_DUP: case MPI_COMBINER_RESIZED:
            r = type_grels(types[0], base, o, depth + 1); break;
        case MPI_COMBINER_CONTIGUOUS:
            for (int i = 0; i < ints[0] && r >= 0; i++) r = type_grels(types[0], base + i * e, o, depth + 1);
            break;
        case MPI_COMBINER_VECTOR: case MPI_COMBINER_HVECTOR:
            for (int i = 0; i < ints[0] && r >= 0; i++)
                for (int j = 0; j < ints[1] && r >= 0; j++)
                    r = type_grels(types[0], base + (comb == MPI_COMBINER_VECTOR ? (MPI_Aint)i * ints[2] * e : i * addrs[0]) + j * e, o, depth + 1);
            break;
        case MPI_COMBINER_INDEXED: case MPI_COMBINER_HINDEXED:
            for (int k = 0; k < ints[0] && r >= 0; k++)
                for (int j = 0; j < ints[1 + k] && r >= 0; j++)
                    r = type_grels(types[0], base + (comb == MPI_COMBINER_INDEXED ? (MPI_Aint)ints[1 + ints[0] + k] * e : addrs[k]) + j * e, o, depth + 1);
            break;
        case MPI_COMBINER_INDEXED_BLOCK: case MPI_COMBINER_HINDEXED_BLOCK:
            for (int k = 0; k < ints[0] && r >= 0; k++)
                for (int j = 0; j < ints[1] && r >= 0; j++)
                    r = type_grels(types[0], base + (comb == MPI_COMBINER_INDEXED_BLOCK ? (MPI_Aint)ints[2 + k] * e : addrs[k]) + j * e, o, depth + 1);
            break;
        case MPI_COMBINER_STRUCT:
            for (int k = 0; k < ints[0] && r >= 0; k++) {
                MPI_Aint ek = 0; PMPI_Type_get_extent(types[k], &l, &ek);
                for (int j = 0; j < ints[1 + k] && r >= 0; j++) r = type_grels(types[k], base + addrs[k] + j * ek, o, depth + 1);
            }
            break;
        default: r = -1;   /* subarray, darray, f90 types: not followed */
        }
        for (int k = 0; k < nd; k++) {   /* get_contents hands out references to the inner types */
            int ni2, na2, nd2, comb2;
            if (PMPI_Type_get_envelope(types[k], &ni2, &na2, &nd2, &comb2) == MPI_SUCCESS && comb2 != MPI_COMBINER_NAMED) PMPI_Type_free(&types[k]);
        }
    }
    free(ints); free(addrs); free(types);
    return r;
}
static int buf_grels(const gbuf *g, grels *o) {
    o->n = 0; o->budget = GUARD_BUDGET;
    if (g->buf == MPI_IN_PLACE || g->count <= 0 || g->dt == MPI_DATATYPE_NULL) return 0;
    MPI_Aint tlb, ext, size;
    int d = type_dense(g->dt, &tlb, &ext, &size);
    if (d < 0) return -1;
    if (d && ext == size) return grel_add(o, tlb, size * g->count);    /* contiguous elements */
    for (long i = 0; i < g->count; i++) if (type_grels(g->dt, i * ext, o, 0) < 0) return -1;
    return 0;
}

/* ---- where pages may be protected: private writable mappings, off the thread's stack */
typedef struct { uintptr_t lo, hi; int ok; } gmap;
static gmap *g_maps; static int g_nmaps, g_capmaps;
static void maps_read(void) {
    g_nmaps = 0;
    int fd = open("/proc/self/maps", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    size_t cap = 1 << 16, len = 0; char *buf = malloc(cap);
    for (;;) {
        if (len + 4096 > cap) buf = xrealloc(buf, cap *= 2);
        ssize_t r = read(fd, buf + len, cap - len - 1);
        if (r <= 0) break;
        len += r;
    }
    close(fd); buf[len] = 0;
    for (char *line = buf; *line; ) {
        char *end = strchr(line, '\n'), perms[8];
        unsigned long lo, hi;
        if (end) *end = 0;
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perms) == 3) {
            if (g_nmaps == g_capmaps) { g_capmaps = g_capmaps ? 2 * g_capmaps : 256; g_maps = xrealloc(g_maps, sizeof *g_maps * g_capmaps); }
            g_maps[g_nmaps++] = (gmap){lo, hi, perms[0] == 'r' && perms[1] == 'w' && perms[3] == 'p' && !strstr(line, "[stack")};
        }
        if (!end) break;
        line = end + 1;
    }
    free(buf);
}
/* 1: [lo, hi) lies in private writable mappings, 0: it does not, -1: not in the table */
static int maps_ok(uintptr_t lo, uintptr_t hi) {
    int a = 0, b = g_nmaps - 1, k = -1;
    while (a <= b) { int m = (a + b) / 2; if (g_maps[m].lo <= lo) { k = m; a = m + 1; } else b = m - 1; }
    if (k < 0 || lo >= g_maps[k].hi) return -1;
    for (uintptr_t at = lo; ; k++) {
        if (k >= g_nmaps || g_maps[k].lo > at) return -1;
        if (!g_maps[k].ok) return 0;
        if (hi <= g_maps[k].hi) return 1;
        at = g_maps[k].hi;
    }
}
static int guard_protectable(uintptr_t lo, uintptr_t hi) {
    if (lo < t_stack_hi && hi > t_stack_lo) return 0;
    int r = maps_ok(lo, hi);
    if (r < 0) { maps_read(); r = maps_ok(lo, hi); }
    return r > 0;
}
static void guard_thread(void) {
    pthread_attr_t a; void *addr; size_t size;
    t_gcaller = 1;
    if (pthread_getattr_np(pthread_self(), &a) == 0) {
        if (pthread_attr_getstack(&a, &addr, &size) == 0) { t_stack_lo = (uintptr_t)addr; t_stack_hi = t_stack_lo + size; }
        pthread_attr_destroy(&a);
    }
}

/* ---- the span table and the protected runs */
static uint64_t guard_sum(uintptr_t lo, uintptr_t hi) {
    const unsigned char *p = (const unsigned char *)lo;
    size_t n = hi - lo;
    uint64_t h = 0x9e3779b97f4a7c15ULL;
    for (; n >= 8; p += 8, n -= 8) { uint64_t w; memcpy(&w, p, 8); h = (h ^ w) * 0x100000001b3ULL; h ^= h >> 29; }
    for (; n; p++, n--) h = (h ^ *p) * 0x100000001b3ULL;
    return h;
}
static int guard_mapped(uintptr_t a) {     /* a freed buffer may have been unmapped since */
    unsigned char v;
    return mincore((void *)(a & ~(uintptr_t)(g_pagesize - 1)), 1, &v) == 0;
}
static int span_pos(uintptr_t lo) {        /* the first span starting after lo */
    int a = 0, b = g_nspans;
    while (a < b) { int m = (a + b) / 2; if (g_spans[m].lo <= lo) a = m + 1; else b = m; }
    return a;
}
static void span_insert(const gspan *s) {
    if (g_nspans >= GUARD_MAX) {
        if (!g_gfull) { g_gfull = 1; REC(b, "overflow"); rb_printf(&b, ",\"what\":\"guard table\""); rb_emit(&b); }
        return;
    }
    int i = span_pos(s->lo);
    gw_begin();
    memmove(&g_spans[i + 1], &g_spans[i], sizeof *g_spans * (g_nspans - i));
    g_spans[i] = *s; g_nspans++;
    gw_end();
    if (s->hi - s->lo > g_gmaxlen) g_gmaxlen = s->hi - s->lo;
    if (s->prot) g_gdirty = 1;
}
/* Pending spans that share bytes with [lo, hi) where one of the two is written, each pending
   request reported once per call (seen holds the ones reported, *nseen of them). */
static int guard_overlaps(uintptr_t lo, uintptr_t hi, int write, int req, const char *call, void *ra, int *seen, int *nseen) {
    int hit = 0;
    for (int k = span_pos(hi - 1) - 1; k >= 0 && g_spans[k].lo + g_gmaxlen > lo; k--) {
        gspan *s = &g_spans[k];
        if (s->hi <= lo || s->req == req || !(write || s->write)) continue;
        hit = 1; s->flags |= 2;
        int dup = 0;
        for (int j = 0; j < *nseen; j++) dup |= seen[j] == s->req;
        if (dup || *nseen >= 16) continue;
        seen[(*nseen)++] = s->req;
        guard_note(G_OVERLAP, s->req, 0, 0, (uintptr_t)ra, req, call);
    }
    return hit;
}
/* A nonblocking call (req >= 0) owns its buffers until its completion; a blocking one
   (req = -1) is only checked against the pending ones. */
static void guard_buffers(int req, const char *call, const gbuf *gb, int n, void *ra) {
    int seen[16], nseen = 0;
    if (!g_guard || t_gdepth != 1) return;      /* a nested call is the library's own */
    for (int b = 0; b < n; b++) {
        grels o;
        if (buf_grels(&gb[b], &o) < 0) continue;
        for (int j = 0; j < o.n; j++) {
            gspan s = {0};
            s.lo = (uintptr_t)gb[b].buf + o.s[j].off; s.hi = s.lo + o.s[j].len; s.base = (uintptr_t)gb[b].buf;
            s.req = req; s.write = gb[b].write; s.p2p = gb[b].p2p;
            s.flags = guard_overlaps(s.lo, s.hi, s.write, req, call, ra, seen, &nseen) ? 2 : 0;
            if (req < 0) continue;
            s.prot = guard_protectable(s.lo, s.hi);
            if (!s.write) s.sum = guard_sum(s.lo, s.hi);
            span_insert(&s);
        }
    }
    if (req < 0) guard_flush();                 /* before the call's own records */
}
/* The completion of req (check = 1) or its MPI_Request_free (check = 0): its spans go. */
static void guard_done(int req, int check) {
    if (!g_guard || req < 0) return;
    int any = 0, flags = 0, modified = 0;
    for (int i = 0; i < g_nspans; i++) {
        gspan *s = &g_spans[i];
        if (s->req != req) continue;
        any = 1; flags |= s->flags;
        if (check && !s->write && !modified && guard_mapped(s->lo) && guard_mapped(s->hi - 1) && guard_sum(s->lo, s->hi) != s->sum) modified = 1;
    }
    if (!any) return;
    gw_begin();
    int j = 0;
    for (int i = 0; i < g_nspans; i++) {
        if (g_spans[i].req != req) g_spans[j++] = g_spans[i];
        else if (g_spans[i].prot) g_gdirty = 1;
    }
    g_nspans = j;
    gw_end();
    if (modified && !flags) guard_note(G_MODIFIED, req, 0, 0, 0, -1, NULL);
}
/* The spans of req's buffer argument base go before the completion (a received partition). */
static void __attribute__((unused)) guard_release(int req, uintptr_t base) {
    if (!g_guard || req < 0) return;
    gw_begin();
    int j = 0;
    for (int i = 0; i < g_nspans; i++) {
        if (g_spans[i].req != req || g_spans[i].base != base) g_spans[j++] = g_spans[i];
        else if (g_spans[i].prot) g_gdirty = 1;
    }
    g_nspans = j;
    gw_end();
}
/* Page runs from the spans: read-only, and inaccessible where a page lies wholly inside
   point-to-point receive buffers and holds no other tracked buffer. */
typedef struct { uintptr_t at; int any, none, other; } gev;
static int gev_cmp(const void *a, const void *b) { uintptr_t x = ((const gev *)a)->at, y = ((const gev *)b)->at; return x < y ? -1 : x > y; }
static void guard_runs(void) {
    uintptr_t pm = ~(uintptr_t)(g_pagesize - 1);
    gev *ev = malloc(sizeof *ev * (4 * (size_t)g_nspans + 1));
    int ne = 0, nr = 0, any = 0, none = 0, other = 0;
    for (int i = 0; i < g_nspans; i++) {
        gspan *s = &g_spans[i];
        if (!s->prot) continue;
        uintptr_t plo = s->lo & pm, phi = (s->hi + g_pagesize - 1) & pm, ilo = (s->lo + g_pagesize - 1) & pm, ihi = s->hi & pm;
        ev[ne++] = (gev){plo, 1, 0, !s->p2p}; ev[ne++] = (gev){phi, -1, 0, -!s->p2p};
        if (s->p2p && ilo < ihi) { ev[ne++] = (gev){ilo, 0, 1, 0}; ev[ne++] = (gev){ihi, 0, -1, 0}; }
    }
    qsort(ev, ne, sizeof *ev, gev_cmp);
    gw_begin();
    for (int i = 0; i < ne; ) {
        uintptr_t at = ev[i].at;
        for (; i < ne && ev[i].at == at; i++) { any += ev[i].any; none += ev[i].none; other += ev[i].other; }
        if (i == ne || !any) continue;
        int prot = none > 0 && other == 0 ? PROT_NONE : PROT_READ;
        if (nr && g_runs[nr - 1].hi == at && g_runs[nr - 1].prot == prot) g_runs[nr - 1].hi = ev[i].at;
        else g_runs[nr++] = (grun){at, ev[i].at, prot};
    }
    g_nruns = nr;
    gw_end();
    free(ev);
    g_gdirty = 0;
}
static void guard_open(void) {
    if (!g_applied) return;
    for (int i = 0; i < g_nruns; i++) mprotect((void *)g_runs[i].lo, g_runs[i].hi - g_runs[i].lo, PROT_READ | PROT_WRITE);
    g_applied = 0;
}
static void guard_close(void) {
    if (g_gdirty) guard_runs();
    for (int i = 0; i < g_nruns; i++) mprotect((void *)g_runs[i].lo, g_runs[i].hi - g_runs[i].lo, g_runs[i].prot);
    g_applied = g_nruns > 0;
}

/* ---- the wrappers' bracket: GUARD is the first statement of every wrapper */
static inline gctx guard_enter(void *ra) {
    gctx g = {ra};
    if (g_guard && t_gdepth++ == 0) {
        if (!t_gcaller) guard_thread();
        guard_open();
        guard_flush();
    }
    return g;
}
static inline void guard_leave(gctx *g) {
    (void)g;
    if (g_guard && t_gdepth > 0 && --t_gdepth == 0) { guard_flush(); guard_close(); }
}
#define GUARD __attribute__((cleanup(guard_leave))) gctx _g = guard_enter(__builtin_return_address(0)); (void)_g
#define GUARD_BUF(req, call, buf, count, dt, write, p2p) \
    do { if (g_guard) { gbuf gb_ = {(buf), (count), (dt), (write), (p2p)}; guard_buffers((req), (call), &gb_, 1, _g.ra); } } while (0)
/* gc: an expression that fills gbuf *gb_ (malloc'ed) with a collective's buffers and gives their number */
#define GUARD_COLL(req, call, gc) \
    do { if (g_guard) { gbuf *gb_ = NULL; int k_ = (gc); guard_buffers((req), (call), gb_, k_, _g.ra); free(gb_); } } while (0)
/* MPI_Finalize, MPI_Abort: nothing is protected any more */
static void guard_finish(void) {
    if (!g_guard) return;
    guard_flush();
    gw_begin(); g_nspans = 0; g_nruns = 0; gw_end();
    g_applied = 0; g_guard = 0;
}

/* ---- the SIGSEGV handler */
#ifndef STRETTO_PIN_ONLY
static int guard_in_mpi(uintptr_t a) {
    for (int i = 0; i < g_nlibtext; i++) if (a >= g_libtext[i].lo && a < g_libtext[i].hi) return 1;
    return 0;
}
/* The access comes from the MPI library: its own code, or a leaf such as memcpy that it
   called (whose return address is still on the top of the stack). */
static int guard_by_mpi(const ucontext_t *uc) {
    uintptr_t pc = uc->uc_mcontext.gregs[REG_RIP], sp = uc->uc_mcontext.gregs[REG_RSP];
    return guard_in_mpi(pc) || guard_in_mpi(*(const uintptr_t *)sp);
}
/* The protected run holding a's page and the pending span holding a (*k = -1 if none),
   read consistently against another thread's update. 0 if the page is not protected. */
static int guard_lookup(uintptr_t a, grun *run, gspan *span, int *k) {
    for (;;) {
        unsigned s = __atomic_load_n(&g_gseq, __ATOMIC_ACQUIRE);
        if (s & 1) continue;
        int found = 0, n = g_nruns, lo = 0, hi = n - 1, at = -1;
        *k = -1;
        while (lo <= hi) { int m = (lo + hi) / 2; if (g_runs[m].lo <= a) { at = m; lo = m + 1; } else hi = m - 1; }
        if (at >= 0 && a < g_runs[at].hi) {
            found = 1; *run = g_runs[at];
            int ns = g_nspans, b = 0, e = ns;
            while (b < e) { int m = (b + e) / 2; if (g_spans[m].lo <= a) b = m + 1; else e = m; }
            for (int j = b - 1, steps = 0; j >= 0 && steps < 256 && g_spans[j].lo + g_gmaxlen > a; j--, steps++)
                if (a < g_spans[j].hi) { *span = g_spans[j]; *k = j; break; }
        }
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        if (__atomic_load_n(&g_gseq, __ATOMIC_RELAXED) == s) return found;
    }
}
static void guard_chain(int sig, siginfo_t *si, void *ctx) {
    struct sigaction *p = &g_prev_segv;
    if ((p->sa_flags & SA_SIGINFO) && p->sa_sigaction) { p->sa_sigaction(sig, si, ctx); return; }
    if (!(p->sa_flags & SA_SIGINFO) && p->sa_handler != SIG_DFL && p->sa_handler != SIG_IGN) { p->sa_handler(sig); return; }
    struct sigaction d;                         /* the default action: the access faults again and kills */
    memset(&d, 0, sizeof d); d.sa_handler = SIG_DFL; sigemptyset(&d.sa_mask);
    real_sigaction(sig, &d, NULL);
    if (si->si_code <= 0) raise(sig);           /* sent, not a fault */
}
static void guard_segv(int sig, siginfo_t *si, void *ctx) {
    int saved = errno;
    uintptr_t a = (uintptr_t)si->si_addr;
    ucontext_t *uc = ctx;
    grun run; gspan span; int k;
    if (si->si_code != SEGV_ACCERR || t_gwriting || !guard_lookup(a, &run, &span, &k)) {
        /* not a page the guard protects, unless the guard lifted it between the fault and
           the lookup: retry the access once before handing the fault on */
        if (si->si_code == SEGV_ACCERR && !t_gwriting && a != t_lastfault) t_lastfault = a;
        else { t_lastfault = 0; guard_chain(sig, si, ctx); }
        errno = saved;
        return;
    }
    t_lastfault = 0;
    if (k >= 0 && t_gcaller && t_gdepth == 0 && !(span.flags & 1) && !guard_by_mpi(uc)) {
        __atomic_fetch_or(&g_spans[k].flags, 1, __ATOMIC_RELAXED);
        guard_note(G_ACCESS, span.req, (uc->uc_mcontext.gregs[REG_ERR] & 2) != 0, (long)(a - span.base),
                   uc->uc_mcontext.gregs[REG_RIP], -1, NULL);
    }
    uintptr_t page = a & ~(uintptr_t)(g_pagesize - 1);
    if (mprotect((void *)page, g_pagesize, PROT_READ | PROT_WRITE) != 0
        && mprotect((void *)run.lo, run.hi - run.lo, PROT_READ | PROT_WRITE) != 0) {
        static const char msg[] = "stretto tracer: the buffer guard cannot unprotect a page\n";
        if (write(2, msg, sizeof msg - 1) < 0) { /* nothing more to do */ }
        guard_chain(sig, si, ctx);
    }
    errno = saved;
}
/* The guard's handler stays installed: a program's own SIGSEGV handler becomes the one that
   faults outside the guard's pages go to. */
int sigaction(int sig, const struct sigaction *act, struct sigaction *old) {
    if (!real_sigaction) real_sigaction = (int (*)(int, const struct sigaction *, struct sigaction *))dlsym(RTLD_NEXT, "sigaction");
    if (sig != SIGSEGV || !g_guard) return real_sigaction(sig, act, old);
    struct sigaction prev = g_prev_segv;
    if (act && !((act->sa_flags & SA_SIGINFO) && act->sa_sigaction == guard_segv)) g_prev_segv = *act;
    if (old) *old = prev;
    return 0;
}
sighandler_t signal(int sig, sighandler_t h) {
    static sighandler_t (*real)(int, sighandler_t);
    if (sig != SIGSEGV || !g_guard) {
        if (!real) real = (sighandler_t (*)(int, sighandler_t))dlsym(RTLD_NEXT, "signal");
        return real(sig, h);
    }
    sighandler_t prev = (g_prev_segv.sa_flags & SA_SIGINFO) ? SIG_DFL : g_prev_segv.sa_handler;
    memset(&g_prev_segv, 0, sizeof g_prev_segv); g_prev_segv.sa_handler = h; sigemptyset(&g_prev_segv.sa_mask);
    return prev;
}
static int guard_libtext(struct dl_phdr_info *info, size_t size, void *data) {
    static const char *mpi[] = {"libmpi", "libopen-", "mca_", "libmca", "libucp", "libucs", "libuct", "libucm", "libfabric",
                                "libpmix", "libpmi", "libprrte", "libhwloc", "libevent", "libpsm", "libibverbs", "librdmacm",
                                "libxpmem", "libknem", "libnl-"};
    (void)size; (void)data;
    const char *name = info->dlpi_name ? info->dlpi_name : "", *base = strrchr(name, '/');
    base = base ? base + 1 : name;
    int ours = 0, lib = 0;
    for (size_t i = 0; i < sizeof mpi / sizeof *mpi; i++) lib |= strncmp(base, mpi[i], strlen(mpi[i])) == 0;
    for (int i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr) *p = &info->dlpi_phdr[i];
        uintptr_t lo = info->dlpi_addr + p->p_vaddr;
        if (p->p_type == PT_LOAD && (uintptr_t)guard_libtext >= lo && (uintptr_t)guard_libtext < lo + p->p_memsz) ours = 1;
    }
    if (!lib && !ours) return 0;
    for (int i = 0; i < info->dlpi_phnum && g_nlibtext < GUARD_LIBS; i++) {
        const ElfW(Phdr) *p = &info->dlpi_phdr[i];
        if (p->p_type != PT_LOAD || !(p->p_flags & PF_X)) continue;
        g_libtext[g_nlibtext].lo = info->dlpi_addr + p->p_vaddr;
        g_libtext[g_nlibtext].hi = g_libtext[g_nlibtext].lo + p->p_memsz;
        g_nlibtext++;
    }
    return 0;
}
/* After MPI_Init: the library and its components are loaded, its own handlers installed. */
static void guard_init(int provided) {
    const char *e = getenv("STRETTO_GUARD");
    g_guard_asked = e && strcmp(e, "1") == 0;
    if (!g_guard_asked || provided == MPI_THREAD_MULTIPLE) return;
    g_pagesize = sysconf(_SC_PAGESIZE);
    int fl = MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, rw = PROT_READ | PROT_WRITE;
    void *s = mmap(NULL, sizeof(gspan) * GUARD_MAX, rw, fl, -1, 0), *r = mmap(NULL, sizeof(grun) * (4 * GUARD_MAX + 1), rw, fl, -1, 0);
    void *n = mmap(NULL, sizeof(gnote) * GUARD_NOTES, rw, fl, -1, 0);
    if (s == MAP_FAILED || r == MAP_FAILED || n == MAP_FAILED) return;
    g_spans = s; g_runs = r; g_notes = n;    /* own mappings: never a program's buffer, never protected */
    dl_iterate_phdr(guard_libtext, NULL);
    if (!real_sigaction) real_sigaction = (int (*)(int, const struct sigaction *, struct sigaction *))dlsym(RTLD_NEXT, "sigaction");
    struct sigaction sa;
    memset(&sa, 0, sizeof sa); sa.sa_sigaction = guard_segv; sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_RESTART; sigemptyset(&sa.sa_mask);
    if (real_sigaction(SIGSEGV, &sa, &g_prev_segv) == 0) g_guard = 1;
}
#else
static void guard_init(int provided) { (void)provided; }   /* the replay library has no guard */
#endif

/* ---- the buffers of a collective at this rank (into *out, malloc'ed); none on an
   intercommunicator. root < 0: the operation has no root (all*, scan, exscan). */
static int gc_ctx(MPI_Comm comm, int *np, int *me, gbuf **out) {
    int inter = 0;
    if (PMPI_Comm_test_inter(comm, &inter) != MPI_SUCCESS || inter) return -1;
    PMPI_Comm_size(comm, np); PMPI_Comm_rank(comm, me);
    *out = malloc(sizeof(gbuf) * (2 * (size_t)*np + 2));
    return 0;
}
static MPI_Aint gc_ext(MPI_Datatype dt) { MPI_Aint lb = 0, ext = 0; if (dt != MPI_DATATYPE_NULL) PMPI_Type_get_extent(dt, &lb, &ext); return ext; }
#define GB(p, n, t, w) ((*out)[k++] = (gbuf){(p), (n), (t), (w), 0})
#define GC_BEGIN int np, me, k = 0; if (gc_ctx(comm, &np, &me, out) < 0) return 0
static int gc_bcast(const void *b, int n, MPI_Datatype dt, int root, MPI_Comm comm, gbuf **out) {
    GC_BEGIN; GB(b, n, dt, me != root); return k;
}
/* reduce (root >= 0), allreduce and scan (root < 0); exscan's result is undefined at rank 0 */
static int gc_reduce(const void *s, const void *r, int n, MPI_Datatype dt, int root, int exscan, MPI_Comm comm, gbuf **out) {
    GC_BEGIN;
    if (s != MPI_IN_PLACE) GB(s, n, dt, 0);
    if ((root < 0 || me == root) && !(exscan && me == 0)) GB(r, n, dt, 1);
    return k;
}
/* gather (root >= 0) and allgather; displs: the v variants */
static int gc_gather(const void *s, int sn, MPI_Datatype sdt, const void *r, int rn, const int *rc, const int *d, MPI_Datatype rdt, int root, MPI_Comm comm, gbuf **out) {
    GC_BEGIN;
    if (s != MPI_IN_PLACE) GB(s, sn, sdt, 0);
    if (root < 0 || me == root) {
        if (!d) GB(r, (long)rn * np, rdt, 1);
        else for (int i = 0; i < np; i++) GB((const char *)r + d[i] * gc_ext(rdt), rc[i], rdt, 1);
    }
    return k;
}
static int gc_scatter(const void *s, int sn, const int *sc, const int *d, MPI_Datatype sdt, const void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, gbuf **out) {
    GC_BEGIN;
    if (me == root) {
        if (!d) GB(s, (long)sn * np, sdt, 0);
        else for (int i = 0; i < np; i++) GB((const char *)s + d[i] * gc_ext(sdt), sc[i], sdt, 0);
    }
    if (r != MPI_IN_PLACE) GB(r, rn, rdt, 1);
    return k;
}
/* alltoall (counts NULL), alltoallv (displacements in extents), alltoallw (types per rank, bytes) */
static int gc_alltoall(const void *s, int sn, const int *sc, const int *sd, const MPI_Datatype *sdt, const void *r, int rn,
                       const int *rc, const int *rd, const MPI_Datatype *rdt, int w, MPI_Comm comm, gbuf **out) {
    GC_BEGIN;
    for (int i = 0; i < np && s != MPI_IN_PLACE; i++) {
        if (!sc) { GB(s, (long)sn * np, sdt[0], 0); break; }
        GB((const char *)s + sd[i] * (w ? 1 : gc_ext(sdt[0])), sc[i], sdt[w ? i : 0], 0);
    }
    for (int i = 0; i < np; i++) {
        if (!rc) { GB(r, (long)rn * np, rdt[0], 1); break; }
        GB((const char *)r + rd[i] * (w ? 1 : gc_ext(rdt[0])), rc[i], rdt[w ? i : 0], 1);
    }
    return k;
}
/* reduce_scatter (rc) and reduce_scatter_block (n per rank) */
static int gc_redscat(const void *s, const void *r, const int *rc, int n, MPI_Datatype dt, MPI_Comm comm, gbuf **out) {
    GC_BEGIN;
    long total = 0;
    for (int i = 0; i < np; i++) total += rc ? rc[i] : n;
    if (s != MPI_IN_PLACE) { GB(s, total, dt, 0); GB(r, rc ? rc[me] : n, dt, 1); }
    else GB(r, total, dt, 1);
    return k;
}

/* ------------------------------------------------------------ small utils */
static int tsize(MPI_Datatype dt) { int s = 0; if (dt != MPI_DATATYPE_NULL) PMPI_Type_size(dt, &s); return s; }
/* Type signatures (analysis/stretto/types.py reads them): a named type by its name, a
   derived type flattened into runs of named types, "MPI_INT*4,MPI_DOUBLE*2", in type-map
   order. Combiners the flattening does not follow (darray, f90) and signatures longer than
   the record field give the opaque "derived:..." form, which the analysis treats as unknown.
   Flattened strings are cached per handle; MPI_Type_free evicts, since handles are reused. */
#define SIG_RUNS 32
#define SIG_DEPTH 16
typedef struct { char name[MPI_MAX_OBJECT_NAME + 1]; long count; } sigrun;
static int sig_push(sigrun *runs, int n, const char *name, long count) {
    if (n > 0 && strcmp(runs[n - 1].name, name) == 0) { runs[n - 1].count += count; return n; }
    if (n >= SIG_RUNS) return -1;
    snprintf(runs[n].name, sizeof runs[n].name, "%s", name); runs[n].count = count;
    return n + 1;
}
static int sig_flatten(MPI_Datatype dt, long mult, sigrun *runs, int n, int depth) {
    int ni, na, nd, comb;
    if (n < 0 || depth > SIG_DEPTH || mult <= 0) return -1;
    if (PMPI_Type_get_envelope(dt, &ni, &na, &nd, &comb) != MPI_SUCCESS) return -1;
    if (comb == MPI_COMBINER_NAMED) {
        char name[MPI_MAX_OBJECT_NAME]; int len = 0;
        PMPI_Type_get_name(dt, name, &len);
        return len ? sig_push(runs, n, name, mult) : -1;
    }
    int *ints = malloc(sizeof(int) * (ni > 0 ? ni : 1)); MPI_Aint *addrs = malloc(sizeof(MPI_Aint) * (na > 0 ? na : 1));
    MPI_Datatype *types = malloc(sizeof(MPI_Datatype) * (nd > 0 ? nd : 1));
    if (PMPI_Type_get_contents(dt, ni, na, nd, ints, addrs, types) != MPI_SUCCESS) { free(ints); free(addrs); free(types); return -1; }
    int r = -1;
    switch (comb) {
    case MPI_COMBINER_DUP: case MPI_COMBINER_RESIZED:
        r = sig_flatten(types[0], mult, runs, n, depth + 1); break;
    case MPI_COMBINER_CONTIGUOUS:
        r = sig_flatten(types[0], mult * ints[0], runs, n, depth + 1); break;
    case MPI_COMBINER_VECTOR: case MPI_COMBINER_HVECTOR:
        r = sig_flatten(types[0], mult * ints[0] * ints[1], runs, n, depth + 1); break;
    case MPI_COMBINER_INDEXED_BLOCK: case MPI_COMBINER_HINDEXED_BLOCK:
        r = sig_flatten(types[0], mult * ints[0] * ints[1], runs, n, depth + 1); break;
    case MPI_COMBINER_INDEXED: case MPI_COMBINER_HINDEXED:
        r = n;
        for (int k = 1; k <= ints[0] && r >= 0; k++) if (ints[k] > 0) r = sig_flatten(types[0], mult * ints[k], runs, r, depth + 1);
        break;
    case MPI_COMBINER_STRUCT:
        r = n;
        for (int k = 0; k < ints[0] && r >= 0; k++) if (ints[1 + k] > 0) r = sig_flatten(types[k], mult * ints[1 + k], runs, r, depth + 1);
        break;
    case MPI_COMBINER_SUBARRAY: {
        long total = 1; int ndims = ints[0];
        for (int k = 0; k < ndims; k++) total *= ints[1 + ndims + k];   /* subsizes */
        r = sig_flatten(types[0], mult * total, runs, n, depth + 1); break; }
    default: r = -1;   /* darray, f90 types: not followed */
    }
    for (int k = 0; k < nd; k++) {   /* get_contents hands out references to the inner types */
        int ni2, na2, nd2, comb2;
        if (PMPI_Type_get_envelope(types[k], &ni2, &na2, &nd2, &comb2) == MPI_SUCCESS && comb2 != MPI_COMBINER_NAMED) PMPI_Type_free(&types[k]);
    }
    free(ints); free(addrs); free(types);
    return r;
}
static void tsig_compute(MPI_Datatype dt, char *out, size_t n) {
    sigrun runs[SIG_RUNS];
    int k = sig_flatten(dt, 1, runs, 0, 0);
    if (k > 0) {
        size_t len = 0; out[0] = 0;
        for (int i = 0; i < k; i++) {
            int w = runs[i].count == 1 ? snprintf(out + len, n - len, "%s%s", i ? "," : "", runs[i].name)
                                      : snprintf(out + len, n - len, "%s%s*%ld", i ? "," : "", runs[i].name, runs[i].count);
            if (w < 0 || (size_t)w >= n - len) { k = -1; break; }
            len += w;
        }
        if (k > 0) return;
    }
    int ni, na, nd, comb; MPI_Aint lb = 0, ext = 0; int sz = 0;
    PMPI_Type_get_envelope(dt, &ni, &na, &nd, &comb);
    PMPI_Type_get_extent(dt, &lb, &ext); PMPI_Type_size(dt, &sz);
    snprintf(out, n, "derived:c%d/s%d/e%ld/lb%ld", comb, sz, (long)ext, (long)lb);
}
#define SIGCACHE 64
static struct { MPI_Datatype dt; char sig[256]; } g_sigs[SIGCACHE];
static int g_nsigs = 0;
static void tsig(MPI_Datatype dt, char *out, size_t n) {
    if (dt == MPI_DATATYPE_NULL) { snprintf(out, n, "null"); return; }
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nsigs; i++) if (g_sigs[i].dt == dt) { snprintf(out, n, "%s", g_sigs[i].sig); pthread_mutex_unlock(&g_mu); return; }
    pthread_mutex_unlock(&g_mu);
    tsig_compute(dt, out, n);
    pthread_mutex_lock(&g_mu);
    if (g_nsigs < SIGCACHE) { g_sigs[g_nsigs].dt = dt; snprintf(g_sigs[g_nsigs].sig, sizeof g_sigs[g_nsigs].sig, "%s", out); g_nsigs++; }
    pthread_mutex_unlock(&g_mu);
}
int MPI_Type_free(MPI_Datatype *dt) {
    GUARD;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nsigs; i++) if (g_sigs[i].dt == *dt) { g_sigs[i] = g_sigs[--g_nsigs]; break; }
    pthread_mutex_unlock(&g_mu);
    return PMPI_Type_free(dt);
}
static void rb_type(rec_t *b, int count, MPI_Datatype dt) {
    char sig[256]; tsig(dt, sig, sizeof sig);
    rb_printf(b, ",\"count\":%d,\"tsize\":%d,\"tsig\":\"%s\"", count, tsize(dt), sig);
}
static void rb_src(rec_t *b, int src) {
    if (src == MPI_ANY_SOURCE) rb_printf(b, ",\"src\":-1,\"anysrc\":1");
    else if (src == MPI_PROC_NULL) rb_printf(b, ",\"src\":-1,\"procnull\":1");
    else rb_printf(b, ",\"src\":%d", src);
}
static void rb_tag(rec_t *b, int tag) {
    if (tag == MPI_ANY_TAG) rb_printf(b, ",\"tag\":-1,\"anytag\":1");
    else rb_printf(b, ",\"tag\":%d", tag);
}
/* The contents of (buf, count, dt) packed with MPI_Pack, in hexadecimal, as field key; only
   with STRETTO_PAYLOAD set, and only up to that many packed bytes (keybig:<size> above). */
static void rb_payload(rec_t *b, const char *key, const void *buf, int count, MPI_Datatype dt) {
    if (g_payload <= 0 || buf == MPI_IN_PLACE || count < 0 || dt == MPI_DATATYPE_NULL) return;
    int size = 0, pos = 0;
    if (PMPI_Pack_size(count, dt, MPI_COMM_WORLD, &size) != MPI_SUCCESS) return;
    if (size > g_payload) { rb_printf(b, ",\"%sbig\":%d", key, size); return; }
    unsigned char *packed = xrealloc(NULL, size > 0 ? size : 1);
    if (PMPI_Pack(buf, count, dt, packed, size, &pos, MPI_COMM_WORLD) == MPI_SUCCESS) {
        static const char digits[] = "0123456789abcdef";
        char *hex = xrealloc(NULL, 2 * (size_t)pos + 1);
        for (int i = 0; i < pos; i++) { hex[2 * i] = digits[packed[i] >> 4]; hex[2 * i + 1] = digits[packed[i] & 15]; }
        hex[2 * pos] = 0;
        rb_printf(b, ",\"%s\":\"%s\"", key, hex);
        free(hex);
    }
    free(packed);
}
static void rb_send_data(rec_t *b, int dst, const void *buf, int count, MPI_Datatype dt) {
    if (dst != MPI_PROC_NULL) rb_payload(b, "data", buf, count, dt);
}

/* ------------------------------------------------------------- comm table */
static int comm_lookup(MPI_Comm c) {
    for (int i = 0; i < g_ncomms; i++) if (g_comms[i].c == c) return g_comms[i].id;
    return -1;
}
static int comm_add(MPI_Comm c) {
    if (g_ncomms == g_capcomms) { g_capcomms = g_capcomms ? g_capcomms * 2 : 16; g_comms = xrealloc(g_comms, sizeof *g_comms * g_capcomms); }
    g_comms[g_ncomms].c = c; g_comms[g_ncomms].id = g_next_comm++; g_ncomms++;
    return g_comms[g_ncomms - 1].id;
}
static void comm_forget(MPI_Comm c) {
    for (int i = 0; i < g_ncomms; i++) if (g_comms[i].c == c) { g_comms[i] = g_comms[--g_ncomms]; return; }
}
static int group_world_ranks(MPI_Group g, int **out) {
    MPI_Group w; int n;
    PMPI_Comm_group(MPI_COMM_WORLD, &w); PMPI_Group_size(g, &n);
    int *loc = malloc(sizeof(int) * (n > 0 ? n : 1)), *wr = malloc(sizeof(int) * (n > 0 ? n : 1));
    for (int i = 0; i < n; i++) loc[i] = i;
    PMPI_Group_translate_ranks(g, n, loc, w, wr);
    PMPI_Group_free(&w);
    free(loc); *out = wr; return n;
}
static int world_ranks(MPI_Comm c, int **out) {
    MPI_Group g; PMPI_Comm_group(c, &g); int n = group_world_ranks(g, out); PMPI_Group_free(&g); return n;
}
static int remote_world_ranks(MPI_Comm c, int **out) {
    MPI_Group g; PMPI_Comm_remote_group(c, &g); int n = group_world_ranks(g, out); PMPI_Group_free(&g); return n;
}
static int cmp_int(const void *a, const void *b) { return *(const int *)a - *(const int *)b; }
static int group_ordinal(const char *key) {
    for (int i = 0; i < g_ngroups; i++) if (strcmp(g_groups[i].key, key) == 0) return g_groups[i].count++;
    if (g_ngroups == g_capgroups) { g_capgroups = g_capgroups ? g_capgroups * 2 : 16; g_groups = xrealloc(g_groups, sizeof *g_groups * g_capgroups); }
    g_groups[g_ngroups].key = strdup(key); g_groups[g_ngroups].count = 1; g_ngroups++;
    return 0;
}
/* Register a communicator and emit its comm record (ranks + group ordinal). */
static int comm_register(const char *how, MPI_Comm parent, MPI_Comm c) {
    if (c == MPI_COMM_NULL) return -1;   /* also the result of a failed or non-member creation */
    pthread_mutex_lock(&g_mu);
    int id = comm_lookup(c);
    if (id < 0) id = comm_add(c);
    int pid = comm_lookup(parent);
    pthread_mutex_unlock(&g_mu);
    int inter = 0; PMPI_Comm_test_inter(c, &inter);
    int *wr; int n = world_ranks(c, &wr);
    rec_t kb; rb_init(&kb);
    if (!inter) {
        for (int i = 0; i < n; i++) rb_printf(&kb, "%s%d", i ? "," : "", wr[i]);
    } else {
        /* identity across both sides: every member, sorted */
        int *rr; int m = remote_world_ranks(c, &rr);
        int *all = malloc(sizeof(int) * (n + m > 0 ? n + m : 1));
        for (int i = 0; i < n; i++) all[i] = wr[i];
        for (int i = 0; i < m; i++) all[n + i] = rr[i];
        qsort(all, n + m, sizeof(int), cmp_int);
        for (int i = 0; i < n + m; i++) rb_printf(&kb, "%s%d", i ? "," : "", all[i]);
        free(all); free(rr);
    }
    pthread_mutex_lock(&g_mu);
    int gord = group_ordinal(kb.p);
    pthread_mutex_unlock(&g_mu);
    REC(b, "comm");
    rb_printf(&b, ",\"name\":\"%s\",\"parent\":%d,\"id\":%d", how, pid, id);
    if (!inter) rb_printf(&b, ",\"ranks\":[%s]", kb.p);
    else {
        int *rr; int m = remote_world_ranks(c, &rr);
        rb_printf(&b, ",\"inter\":1,\"ranks\":[");
        for (int i = 0; i < m; i++) rb_printf(&b, "%s%d", i ? "," : "", rr[i]);
        rb_printf(&b, "],\"local\":[");
        for (int i = 0; i < n; i++) rb_printf(&b, "%s%d", i ? "," : "", wr[i]);
        rb_printf(&b, "],\"members\":[%s]", kb.p);
        free(rr);
    }
    rb_printf(&b, ",\"gord\":%d", gord);
    free(kb.p); free(wr);
    rb_emit(&b);
    return id;
}
static int comm_id(MPI_Comm c) {
    pthread_mutex_lock(&g_mu);
    int id = comm_lookup(c);
    pthread_mutex_unlock(&g_mu);
    if (id >= 0) return id;
    return comm_register("unknown", MPI_COMM_WORLD, c);   /* created through an unwrapped call */
}

/* ---------------------------------------------------------- request table
   Pending requests can share a handle: for a send that completed at once the library may
   return one shared request (Open MPI's empty request for an inline send, MPICH's
   lightweight requests). Entries are therefore told apart by the variable the program
   received the handle in (slot), which it hands back at the completion, and else by age:
   the oldest entry of the handle goes first. A new request in the slot and with the handle
   of a pending one replaces it, since the program can no longer name the old one. */
static int req_add(MPI_Request *slot, int kind, MPI_Datatype dt, int persistent) {
    MPI_Request r = *slot;
    int dropped = -1;
    reqent *e = NULL;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nreqs && !e; i++)
        if (g_reqs[i].req == r && (g_reqs[i].persistent || g_reqs[i].slot == slot)) e = &g_reqs[i];   /* recycled handle */
    if (e && !e->persistent) dropped = e->id;
    if (!e) {
        if (g_nreqs == g_capreqs) { g_capreqs = g_capreqs ? g_capreqs * 2 : 64; g_reqs = xrealloc(g_reqs, sizeof *g_reqs * g_capreqs); }
        e = &g_reqs[g_nreqs++];
    }
    int id = g_next_req++;
    *e = (reqent){r, id, kind, dt, persistent, !persistent, 0, 0, slot};
    pthread_mutex_unlock(&g_mu);
    guard_done(dropped, 0);
    return id;
}
static reqent *req_find_locked(MPI_Request r, MPI_Request *slot) {
    if (r == MPI_REQUEST_NULL) return NULL;
    reqent *oldest = NULL;
    for (int i = 0; i < g_nreqs; i++) {
        if (g_reqs[i].req != r) continue;
        if (slot && g_reqs[i].slot == slot) return &g_reqs[i];
        if (!oldest || g_reqs[i].id < oldest->id) oldest = &g_reqs[i];
    }
    return oldest;
}
/* A pinned nonblocking receive reports its match when the completion call returns it. */
static void req_set_pin(MPI_Request *slot, int widx) {
    pthread_mutex_lock(&g_mu); reqent *e = req_find_locked(*slot, slot); if (e) e->pin1 = widx + 1; pthread_mutex_unlock(&g_mu);
}
static int req_id(MPI_Request r, MPI_Request *slot) {
    pthread_mutex_lock(&g_mu); reqent *e = req_find_locked(r, slot); int id = e ? e->id : -1; pthread_mutex_unlock(&g_mu); return id;
}
static void req_remove(MPI_Request r, MPI_Request *slot) {
    pthread_mutex_lock(&g_mu);
    reqent *e = req_find_locked(r, slot);
    if (e) *e = g_reqs[--g_nreqs];
    pthread_mutex_unlock(&g_mu);
}
/* A persistent request gets a fresh id at every Start (each start is a new operation). */
static int req_start(MPI_Request r, int *kind, MPI_Datatype *dt) {
    pthread_mutex_lock(&g_mu);
    reqent *e = req_find_locked(r, NULL);
    int id = -1;
    if (e) { e->id = g_next_req++; e->active = 1; id = e->id; *kind = e->kind; *dt = e->dt; }
    else *kind = -1;
    pthread_mutex_unlock(&g_mu);
    return id;
}

/* ------------------------------------------------------------- init/fini */
static void open_trace(int provided) {
    PMPI_Comm_rank(MPI_COMM_WORLD, &g_rank);
#ifndef STRETTO_PIN_ONLY
    Dl_info self;
    if (dladdr((void *)open_trace, &self)) g_self_base = self.dli_fbase;
    const char *payload = getenv("STRETTO_PAYLOAD");
    if (payload) g_payload = atol(payload);
    const char *dir = getenv("STRETTO_TRACE_DIR");
    if (!dir) dir = "traces";
    mkdir(dir, 0777);
    char path[1024];
    snprintf(path, sizeof path, "%s/rank-%d.jsonl", dir, g_rank);
    g_out = fopen(path, "w");
    if (!g_out) {
        /* no trace for this rank: the loader reports the rank as missing, and nothing is
           written into the program's own output */
        fprintf(stderr, "stretto tracer: rank %d: cannot write %s: ", g_rank, path);
        perror(NULL);
    }
#endif
    stretto_pin_init(g_rank);
    g_multi = provided == MPI_THREAD_MULTIPLE;
    guard_init(provided);
    int np; PMPI_Comm_size(MPI_COMM_WORLD, &np);
    REC(b, "init"); rb_printf(&b, ",\"np\":%d,\"thread\":%d", np, provided);
    if (g_guard_asked) rb_printf(&b, ",\"guard\":%d", g_guard);
    rb_emit(&b);
    comm_register("world", MPI_COMM_WORLD, MPI_COMM_WORLD);   /* local id 0 */
    comm_register("self", MPI_COMM_WORLD, MPI_COMM_SELF);     /* local id 1 */
}
int MPI_Init(int *argc, char ***argv) {
    int rc = PMPI_Init(argc, argv), provided = MPI_THREAD_SINGLE;
    PMPI_Query_thread(&provided);      /* an implementation may provide more than MPI_Init asks */
    open_trace(provided);
    return rc;
}
int MPI_Init_thread(int *argc, char ***argv, int req, int *prov) { int rc = PMPI_Init_thread(argc, argv, req, prov); open_trace(*prov); return rc; }
int MPI_Finalize(void) {
    GUARD;
    guard_finish();
    stretto_pin_finalize();
    REC(b, "finalize"); if (g_truncated) rb_printf(&b, ",\"truncated\":1"); rb_emit(&b);
    pthread_mutex_lock(&g_mu);
    if (g_out && (ferror(g_out) | fclose(g_out)))
        fprintf(stderr, "stretto tracer: rank %d: writing the trace failed; it is incomplete\n", g_rank);
    g_out = NULL;
    pthread_mutex_unlock(&g_mu);
    return PMPI_Finalize();
}
int MPI_Abort(MPI_Comm comm, int code) {
    GUARD;
    guard_finish();
    REC(b, "abort"); rb_printf(&b, ",\"code\":%d", code); rb_emit(&b);
    pthread_mutex_lock(&g_mu); if (g_out) fflush(g_out); pthread_mutex_unlock(&g_mu);
    return PMPI_Abort(comm, code);
}

/* ------------------------------------------------------------------ sends */
static void send_fields(rec_t *b, const char *mode, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm) {
    rb_printf(b, ",\"mode\":\"%s\"", mode);
    if (dst == MPI_PROC_NULL) rb_printf(b, ",\"dst\":-1,\"procnull\":1"); else rb_printf(b, ",\"dst\":%d", dst);
    rb_printf(b, ",\"tag\":%d,\"comm\":%d", tag, comm_id(comm));
    rb_type(b, count, dt);
}
#define BLOCKING_SEND(NAME, MODE) \
int NAME(const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm) { \
    GUARD; stretto_pin_before_send(); \
    if (dst != MPI_PROC_NULL) GUARD_BUF(-1, #NAME, buf, count, dt, 0, 0); \
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"send\""); send_fields(&b, MODE, count, dt, dst, tag, comm); rb_emit(&b); } \
    int rc = P##NAME(buf, count, dt, dst, tag, comm); \
    { REC(b, "send"); send_fields(&b, MODE, count, dt, dst, tag, comm); rb_send_data(&b, dst, buf, count, dt); rb_emit(&b); } \
    return rc; }
BLOCKING_SEND(MPI_Send,  "std")
BLOCKING_SEND(MPI_Ssend, "ssend")
BLOCKING_SEND(MPI_Bsend, "bsend")
BLOCKING_SEND(MPI_Rsend, "rsend")
#define NONBLOCKING_SEND(NAME, MODE) \
int NAME(const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { \
    GUARD; stretto_pin_before_send(); int rc = P##NAME(buf, count, dt, dst, tag, comm, req); \
    int id = rc == MPI_SUCCESS ? req_add(req, 0, dt, 0) : -1; \
    REC(b, "send"); send_fields(&b, MODE, count, dt, dst, tag, comm); if (id >= 0) rb_printf(&b, ",\"req\":%d", id); \
    rb_send_data(&b, dst, buf, count, dt); rb_emit(&b); \
    if (id >= 0 && dst != MPI_PROC_NULL) GUARD_BUF(id, #NAME, buf, count, dt, 0, 0); \
    return rc; }
NONBLOCKING_SEND(MPI_Isend,  "isend")
NONBLOCKING_SEND(MPI_Issend, "issend")
NONBLOCKING_SEND(MPI_Ibsend, "ibsend")
NONBLOCKING_SEND(MPI_Irsend, "irsend")

static inline __attribute__((always_inline)) void unmodelled(const char *call);
/* persistent: *_init registers; Start emits the operation */
typedef struct { int count; MPI_Datatype dt; int peer, tag; MPI_Comm comm; const char *mode; void *buf; } pinfo;
static pinfo *g_pinfo = NULL; static MPI_Request *g_preq = NULL; static int g_npers = 0, g_cappers = 0;
static void pers_add(MPI_Request r, int count, MPI_Datatype dt, int peer, int tag, MPI_Comm comm, const char *mode, void *buf) {
    pthread_mutex_lock(&g_mu);
    if (g_npers == g_cappers) { g_cappers = g_cappers ? g_cappers * 2 : 16; g_pinfo = xrealloc(g_pinfo, sizeof *g_pinfo * g_cappers); g_preq = xrealloc(g_preq, sizeof *g_preq * g_cappers); }
    g_preq[g_npers] = r; g_pinfo[g_npers] = (pinfo){count, dt, peer, tag, comm, mode, buf}; g_npers++;
    pthread_mutex_unlock(&g_mu);
}
static pinfo *pers_find(MPI_Request r) {
    for (int i = 0; i < g_npers; i++) if (g_preq[i] == r) return &g_pinfo[i];
    return NULL;
}
static void pers_remove(MPI_Request r) {
    for (int i = 0; i < g_npers; i++) if (g_preq[i] == r) { g_preq[i] = g_preq[--g_npers]; g_pinfo[i] = g_pinfo[g_npers]; break; }
}
#if MPI_VERSION >= 4
/* Partitioned requests (MPI-4) are not modelled, but with the guard on their buffers are
   followed (request kind 4): a receive buffer is MPI's from the Start, a received partition
   the program's again once MPI_Parrived reports it; a send partition is MPI's from its
   MPI_Pready. Every partition is one buffer of count elements. */
typedef struct { MPI_Request r; const void *buf; int parts; MPI_Count count; MPI_Datatype dt; int recv; } partent;
static partent *g_parts = NULL; static int g_nparts = 0, g_capparts = 0;
static void part_add(MPI_Request r, const void *buf, int parts, MPI_Count count, MPI_Datatype dt, int recv) {
    pthread_mutex_lock(&g_mu);
    if (g_nparts == g_capparts) { g_capparts = g_capparts ? g_capparts * 2 : 8; g_parts = xrealloc(g_parts, sizeof *g_parts * g_capparts); }
    g_parts[g_nparts++] = (partent){r, buf, parts, count, dt, recv};
    pthread_mutex_unlock(&g_mu);
}
static int part_find(MPI_Request r, partent *out) {
    int found = 0;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nparts; i++) if (g_parts[i].r == r) { *out = g_parts[i]; found = 1; break; }
    pthread_mutex_unlock(&g_mu);
    return found;
}
static void part_remove(MPI_Request r) {
    for (int i = 0; i < g_nparts; i++) if (g_parts[i].r == r) { g_parts[i] = g_parts[--g_nparts]; break; }
}
static gbuf part_buf(const partent *p, int i) {
    return (gbuf){(const char *)p->buf + (MPI_Aint)i * p->count * gc_ext(p->dt), (long)p->count, p->dt, p->recv, p->recv};
}
#endif
#define PERSISTENT_SEND(NAME, MODE) \
int NAME(const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { \
    GUARD; int rc = P##NAME(buf, count, dt, dst, tag, comm, req); if (rc != MPI_SUCCESS) return rc; \
    req_add(req, 0, dt, 1); pers_add(*req, count, dt, dst, tag, comm, MODE, (void *)buf); return rc; }
PERSISTENT_SEND(MPI_Send_init,  "isend")
PERSISTENT_SEND(MPI_Ssend_init, "issend")
PERSISTENT_SEND(MPI_Bsend_init, "ibsend")
PERSISTENT_SEND(MPI_Rsend_init, "irsend")
int MPI_Recv_init(void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Request *req) {
    GUARD;
    int rc = PMPI_Recv_init(buf, count, dt, src, tag, comm, req); if (rc != MPI_SUCCESS) return rc;
    req_add(req, 1, dt, 1); pers_add(*req, count, dt, src, tag, comm, "irecv", buf); return rc;
}
/* A pinned Start of a persistent wildcard receive cannot change the request's source, so
   it starts a shadow request created with the pinned source instead: the caller's handle
   is replaced by the shadow until the completion that returns it to them (see
   restore_shadow), and a later Start of the same handle is a fresh, unpinned operation. */
typedef struct { MPI_Request shadow, original; } shadowent;
static shadowent *g_shadows = NULL; static int g_nshadows = 0, g_capshadows = 0;
static void shadow_add(MPI_Request shadow, MPI_Request original) {
    pthread_mutex_lock(&g_mu);
    if (g_nshadows == g_capshadows) { g_capshadows = g_capshadows ? g_capshadows * 2 : 8; g_shadows = xrealloc(g_shadows, sizeof *g_shadows * g_capshadows); }
    g_shadows[g_nshadows++] = (shadowent){shadow, original};
    pthread_mutex_unlock(&g_mu);
}
static int shadow_pop(MPI_Request shadow, MPI_Request *original) {
    int found = 0;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nshadows; i++) if (g_shadows[i].shadow == shadow) { *original = g_shadows[i].original; g_shadows[i] = g_shadows[--g_nshadows]; found = 1; break; }
    pthread_mutex_unlock(&g_mu);
    return found;
}
/* After a completion: if the slot holds a shadow, free it and give the caller their request back. */
static void restore_shadow(MPI_Request *slot, MPI_Request before) {
    MPI_Request original;
    if (before == MPI_REQUEST_NULL || !shadow_pop(before, &original)) return;
    req_remove(before, slot);
    if (*slot == before) PMPI_Request_free(slot);
    *slot = original;
}
static inline __attribute__((always_inline)) void start_one(MPI_Request *rp, void *ra) {
    MPI_Request r = *rp;
    int kind; MPI_Datatype dt;
    int id = req_start(r, &kind, &dt);
    pthread_mutex_lock(&g_mu); pinfo *pi = pers_find(r); pinfo copy; if (pi) copy = *pi; pthread_mutex_unlock(&g_mu);
#if MPI_VERSION >= 4
    partent part;
    if (!pi && kind == 4 && part_find(r, &part)) {
        REC(b, "unmodelled"); rb_printf(&b, ",\"call\":\"Start of a partitioned %s\",\"req\":%d", part.recv ? "receive" : "send", id); rb_emit(&b);
        if (!part.recv || part.parts <= 0) return;
        gbuf *gb = malloc(sizeof(gbuf) * part.parts);
        for (int i = 0; i < part.parts; i++) gb[i] = part_buf(&part, i);
        guard_buffers(id, "MPI_Start", gb, part.parts, ra);
        free(gb);
        return;
    }
#endif
    if (!pi) { unmodelled("Start of an untracked persistent request"); return; }   /* persistent collective, partitioned */
    gbuf gb = {copy.buf, copy.count, copy.dt, kind != 0, kind != 0};
    if (kind == 0) {
        stretto_pin_before_send();
        REC(b, "send"); send_fields(&b, copy.mode, copy.count, copy.dt, copy.peer, copy.tag, copy.comm);
        rb_printf(&b, ",\"req\":%d,\"persistent\":1", id); rb_emit(&b);
        if (id >= 0 && copy.peer != MPI_PROC_NULL) guard_buffers(id, "MPI_Start", &gb, 1, ra);
    } else {
        int src = copy.peer, tag = copy.tag, widx = -1;
        stretto_pin_before_recv();
        int pinned = stretto_pin_recv(&src, &tag, copy.comm, &widx);
        if (pinned) {
            MPI_Request shadow;
            if (PMPI_Recv_init(copy.buf, copy.count, copy.dt, src, tag, copy.comm, &shadow) == MPI_SUCCESS) {
                pthread_mutex_lock(&g_mu);
                if (g_nreqs == g_capreqs) { g_capreqs = g_capreqs ? g_capreqs * 2 : 64; g_reqs = xrealloc(g_reqs, sizeof *g_reqs * g_capreqs); }
                g_reqs[g_nreqs++] = (reqent){shadow, id, 1, copy.dt, 1, 1, 0, widx + 1, rp};
                pthread_mutex_unlock(&g_mu);
                shadow_add(shadow, r);
                *rp = shadow;
            } else pinned = 0;
        }
        REC(b, "irecv"); rb_src(&b, copy.peer); rb_tag(&b, copy.tag); rb_printf(&b, ",\"comm\":%d", comm_id(copy.comm));
        rb_type(&b, copy.count, copy.dt); rb_printf(&b, ",\"req\":%d,\"persistent\":1", id);
        if (widx >= 0) rb_printf(&b, ",\"widx\":%d", widx);
        if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d,\"tag\":%d}", src, tag);
        rb_emit(&b);
        if (id >= 0 && copy.peer != MPI_PROC_NULL) guard_buffers(id, "MPI_Start", &gb, 1, ra);
    }
}
int MPI_Start(MPI_Request *req) { GUARD; start_one(req, _g.ra); return PMPI_Start(req); }
int MPI_Startall(int n, MPI_Request reqs[]) { GUARD; for (int i = 0; i < n; i++) start_one(&reqs[i], _g.ra); return PMPI_Startall(n, reqs); }

/* -------------------------------------------------------------- receives */
static void status_fields(rec_t *b, MPI_Status *st, MPI_Datatype dt, int ok) {
    int cancelled = 0;
    if (!ok || !st) { rb_printf(b, ",\"msrc\":null,\"mtag\":null,\"mcount\":null"); return; }
    PMPI_Test_cancelled(st, &cancelled);
    if (cancelled) { rb_printf(b, ",\"msrc\":null,\"mtag\":null,\"mcount\":null,\"cancelled\":1"); return; }
    int cnt = -1;
    if (dt != MPI_DATATYPE_NULL) PMPI_Get_count(st, dt, &cnt);
    if (st->MPI_SOURCE == MPI_PROC_NULL) rb_printf(b, ",\"msrc\":null,\"mtag\":null,\"mcount\":0,\"procnull\":1");
    else if (cnt == MPI_UNDEFINED) rb_printf(b, ",\"msrc\":%d,\"mtag\":%d,\"mcount\":null", st->MPI_SOURCE, st->MPI_TAG);
    else rb_printf(b, ",\"msrc\":%d,\"mtag\":%d,\"mcount\":%d", st->MPI_SOURCE, st->MPI_TAG, cnt);
}
static void recv_args(rec_t *b, int src, int tag, MPI_Comm comm, int count, MPI_Datatype dt, int widx) {
    rb_src(b, src); rb_tag(b, tag); rb_printf(b, ",\"comm\":%d", comm_id(comm)); rb_type(b, count, dt);
    if (widx >= 0) rb_printf(b, ",\"widx\":%d", widx);
}
int MPI_Recv(void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Status *st) {
    GUARD; stretto_pin_before_recv();
    if (src != MPI_PROC_NULL) GUARD_BUF(-1, "MPI_Recv", buf, count, dt, 1, 0);
    int osrc = src, otag = tag, widx = -1;
    int pinned = stretto_pin_recv(&src, &tag, comm, &widx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"recv\""); recv_args(&b, osrc, otag, comm, count, dt, widx); rb_emit(&b); }
    MPI_Status mine;
    if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Recv(buf, count, dt, src, tag, comm, st);
    if (pinned && rc == MPI_SUCCESS) stretto_pin_matched(widx, st->MPI_SOURCE);
    REC(b, "recv"); recv_args(&b, osrc, otag, comm, count, dt, widx);
    status_fields(&b, st, dt, rc == MPI_SUCCESS);
    if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d,\"tag\":%d}", src, tag);
    rb_emit(&b);
    return rc;
}
int MPI_Irecv(void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Request *req) {
    GUARD; stretto_pin_before_recv();
    int osrc = src, otag = tag, widx = -1;
    int pinned = stretto_pin_recv(&src, &tag, comm, &widx);
    int rc = PMPI_Irecv(buf, count, dt, src, tag, comm, req);
    int id = rc == MPI_SUCCESS ? req_add(req, 1, dt, 0) : -1;
    if (pinned && id >= 0) req_set_pin(req, widx);
    REC(b, "irecv"); recv_args(&b, osrc, otag, comm, count, dt, widx); if (id >= 0) rb_printf(&b, ",\"req\":%d", id);
    if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d,\"tag\":%d}", src, tag);
    rb_emit(&b);
    if (id >= 0 && osrc != MPI_PROC_NULL) GUARD_BUF(id, "MPI_Irecv", buf, count, dt, 1, 1);
    return rc;
}
static inline __attribute__((always_inline)) int sendrecv_common(const void *sbuf, int scount, MPI_Datatype sdt, int dst, int stag, int rcount, MPI_Datatype rdt, int *src, int *rtag, MPI_Comm comm, int *widx, int *reqid) {
    /* Modelled as Isend + Recv + Wait. */
    stretto_pin_before_send();
    pthread_mutex_lock(&g_mu); *reqid = g_next_req++; pthread_mutex_unlock(&g_mu);
    { REC(b, "send"); send_fields(&b, "isend", scount, sdt, dst, stag, comm); rb_printf(&b, ",\"req\":%d", *reqid);
      rb_send_data(&b, dst, sbuf, scount, sdt); rb_emit(&b); }
    int osrc = *src, otag = *rtag;
    int pinned = stretto_pin_recv(src, rtag, comm, widx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"recv\""); recv_args(&b, osrc, otag, comm, rcount, rdt, *widx); rb_emit(&b); }
    return pinned;
}
static inline __attribute__((always_inline)) void sendrecv_finish(int rcount, MPI_Datatype rdt, int osrc, int otag, int src, int rtag, MPI_Comm comm, MPI_Status *st, int rc, int widx, int pinned, int reqid) {
    if (pinned && rc == MPI_SUCCESS) stretto_pin_matched(widx, st->MPI_SOURCE);
    { REC(b, "recv"); recv_args(&b, osrc, otag, comm, rcount, rdt, widx); status_fields(&b, st, rdt, rc == MPI_SUCCESS);
      if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d,\"tag\":%d}", src, rtag);
      rb_emit(&b); }
    { REC(b, "wait"); rb_printf(&b, ",\"kind\":\"wait\",\"reqs\":[%d],\"done\":[{\"req\":%d}]", reqid, reqid); rb_emit(&b); }
}
int MPI_Sendrecv(const void *sbuf, int scount, MPI_Datatype sdt, int dst, int stag,
                 void *rbuf, int rcount, MPI_Datatype rdt, int src, int rtag, MPI_Comm comm, MPI_Status *st) {
    GUARD;
    if (g_guard) {
        gbuf gb[2]; int k = 0;
        if (dst != MPI_PROC_NULL) gb[k++] = (gbuf){sbuf, scount, sdt, 0, 0};
        if (src != MPI_PROC_NULL) gb[k++] = (gbuf){rbuf, rcount, rdt, 1, 0};
        guard_buffers(-1, "MPI_Sendrecv", gb, k, _g.ra);
    }
    int osrc = src, otag = rtag, widx = -1, reqid;
    int pinned = sendrecv_common(sbuf, scount, sdt, dst, stag, rcount, rdt, &src, &rtag, comm, &widx, &reqid);
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Sendrecv(sbuf, scount, sdt, dst, stag, rbuf, rcount, rdt, src, rtag, comm, st);
    sendrecv_finish(rcount, rdt, osrc, otag, src, rtag, comm, st, rc, widx, pinned, reqid);
    return rc;
}
int MPI_Sendrecv_replace(void *buf, int count, MPI_Datatype dt, int dst, int stag, int src, int rtag, MPI_Comm comm, MPI_Status *st) {
    GUARD;
    if (dst != MPI_PROC_NULL || src != MPI_PROC_NULL) GUARD_BUF(-1, "MPI_Sendrecv_replace", buf, count, dt, src != MPI_PROC_NULL, 0);
    int osrc = src, otag = rtag, widx = -1, reqid;
    int pinned = sendrecv_common(buf, count, dt, dst, stag, count, dt, &src, &rtag, comm, &widx, &reqid);
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Sendrecv_replace(buf, count, dt, dst, stag, src, rtag, comm, st);
    sendrecv_finish(count, dt, osrc, otag, src, rtag, comm, st, rc, widx, pinned, reqid);
    return rc;
}

/* -------------------------------------------------------------- completion */
static void idup_complete(MPI_Request before);
static void reqs_list(rec_t *b, int n, MPI_Request before[], MPI_Request *slots) {   /* slots: the program's requests, NULL if not passed */
    rb_printf(b, ",\"reqs\":[");
    for (int i = 0; i < n; i++) { int id = req_id(before[i], slots ? &slots[i] : NULL); if (id >= 0) rb_printf(b, "%s%d", i ? "," : "", id); else rb_printf(b, "%snull", i ? "," : ""); }
    rb_printf(b, "]");
}
static void done_entry(rec_t *b, int first, MPI_Request before, MPI_Request *slot, MPI_Status *st, int ok) {
    if (before == MPI_REQUEST_NULL) return;
    pthread_mutex_lock(&g_mu);
    reqent *e = req_find_locked(before, slot);
    int id = e ? e->id : -1, kind = e ? e->kind : -1, persistent = e ? e->persistent : 0, cancel_req = e ? e->cancel_req : 0;
    int pin1 = e ? e->pin1 : 0;
    MPI_Datatype dt = e ? e->dt : MPI_DATATYPE_NULL;
    if (e && !persistent) { *e = g_reqs[--g_nreqs]; }
    else if (e) { e->active = 0; e->cancel_req = 0; }
    pthread_mutex_unlock(&g_mu);
    guard_done(id, 1);
    if (kind == 3) idup_complete(before);
    if (id < 0) { rb_printf(b, "%s{\"req\":null}", first ? "" : ","); return; }
    rb_printf(b, "%s{\"req\":%d", first ? "" : ",", id);
    if (kind == 1) status_fields(b, st, dt, ok);
    if (kind == 1 && pin1 && ok && st) {
        int cancelled = 0; PMPI_Test_cancelled(st, &cancelled);
        if (!cancelled) stretto_pin_matched(pin1 - 1, st->MPI_SOURCE);
    }
    else if (kind == 0 && cancel_req) {          /* a send that was cancelled: did the cancel succeed? */
        int cancelled = 0; if (ok && st) PMPI_Test_cancelled(st, &cancelled);
        rb_printf(b, ",\"cancelled\":%d", cancelled);
    }
    rb_printf(b, "}");
}
/* MPI_Request_get_status completes nothing: recorded as a poll (a loop over it is one). */
int MPI_Request_get_status(MPI_Request req, int *flag, MPI_Status *st) {
    GUARD;
    int rc = PMPI_Request_get_status(req, flag, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"getstatus\",\"flag\":%d", *flag); reqs_list(&b, 1, &req, NULL); rb_printf(&b, ",\"done\":[]"); rb_emit_kind(&b, !*flag);
    return rc;
}
static inline __attribute__((always_inline)) void begin_wait(const char *kind, int n, MPI_Request reqs[], MPI_Request *slots) {
    REC(b, "begin"); rb_printf(&b, ",\"call\":\"wait\",\"kind\":\"%s\"", kind); reqs_list(&b, n, reqs, slots); rb_emit(&b);
}
int MPI_Wait(MPI_Request *req, MPI_Status *st) {
    GUARD;
    MPI_Request before = *req; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    begin_wait("wait", 1, &before, req);
    int rc = PMPI_Wait(req, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"wait\""); reqs_list(&b, 1, &before, req); rb_printf(&b, ",\"done\":["); done_entry(&b, 1, before, req, st, rc == MPI_SUCCESS); rb_printf(&b, "]"); rb_emit(&b);
    restore_shadow(req, before);
    return rc;
}
#define COPY_REQS(n, reqs) MPI_Request *before = malloc(sizeof(MPI_Request) * ((n) > 0 ? (n) : 1)); memcpy(before, reqs, sizeof(MPI_Request) * (n))
#define OWN_STATUSES(n, sts) MPI_Status *mine = (sts == MPI_STATUSES_IGNORE) ? malloc(sizeof(MPI_Status) * ((n) > 0 ? (n) : 1)) : NULL; if (mine) sts = mine
int MPI_Waitall(int n, MPI_Request reqs[], MPI_Status sts[]) {
    GUARD; COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    begin_wait("waitall", n, before, reqs);
    int rc = PMPI_Waitall(n, reqs, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"waitall\""); reqs_list(&b, n, before, reqs); rb_printf(&b, ",\"done\":[");
    int first = 1; for (int i = 0; i < n; i++) if (before[i] != MPI_REQUEST_NULL) { done_entry(&b, first, before[i], &reqs[i], &sts[i], rc == MPI_SUCCESS); first = 0; }
    rb_printf(&b, "]"); rb_emit(&b);
    for (int i = 0; i < n; i++) restore_shadow(&reqs[i], before[i]);
    free(before); free(mine);
    return rc;
}
int MPI_Waitany(int n, MPI_Request reqs[], int *idx, MPI_Status *st) {
    GUARD; COPY_REQS(n, reqs); MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    begin_wait("waitany", n, before, reqs);
    int rc = PMPI_Waitany(n, reqs, idx, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"waitany\",\"idx\":%d", *idx); reqs_list(&b, n, before, reqs); rb_printf(&b, ",\"done\":[");
    if (*idx != MPI_UNDEFINED) done_entry(&b, 1, before[*idx], &reqs[*idx], st, rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit(&b);
    if (*idx != MPI_UNDEFINED) restore_shadow(&reqs[*idx], before[*idx]);
    free(before);
    return rc;
}
int MPI_Waitsome(int n, MPI_Request reqs[], int *outcount, int idx[], MPI_Status sts[]) {
    GUARD; COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    begin_wait("waitsome", n, before, reqs);
    int rc = PMPI_Waitsome(n, reqs, outcount, idx, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"waitsome\""); reqs_list(&b, n, before, reqs); rb_printf(&b, ",\"done\":[");
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) done_entry(&b, i == 0, before[idx[i]], &reqs[idx[i]], &sts[i], rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit(&b);
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) restore_shadow(&reqs[idx[i]], before[idx[i]]);
    free(before); free(mine);
    return rc;
}
int MPI_Test(MPI_Request *req, int *flag, MPI_Status *st) {
    GUARD;
    MPI_Request before = *req; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Test(req, flag, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"test\",\"flag\":%d", *flag); reqs_list(&b, 1, &before, req); rb_printf(&b, ",\"done\":[");
    if (*flag) done_entry(&b, 1, before, req, st, rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    if (*flag) restore_shadow(req, before);
    return rc;
}
int MPI_Testall(int n, MPI_Request reqs[], int *flag, MPI_Status sts[]) {
    GUARD; COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    int rc = PMPI_Testall(n, reqs, flag, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testall\",\"flag\":%d", *flag); reqs_list(&b, n, before, reqs); rb_printf(&b, ",\"done\":[");
    if (*flag) { int first = 1; for (int i = 0; i < n; i++) if (before[i] != MPI_REQUEST_NULL) { done_entry(&b, first, before[i], &reqs[i], &sts[i], rc == MPI_SUCCESS); first = 0; } }
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    if (*flag) for (int i = 0; i < n; i++) restore_shadow(&reqs[i], before[i]);
    free(before); free(mine);
    return rc;
}
int MPI_Testany(int n, MPI_Request reqs[], int *idx, int *flag, MPI_Status *st) {
    GUARD; COPY_REQS(n, reqs); MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Testany(n, reqs, idx, flag, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testany\",\"flag\":%d,\"idx\":%d", *flag, *idx); reqs_list(&b, n, before, reqs); rb_printf(&b, ",\"done\":[");
    if (*flag && *idx != MPI_UNDEFINED) done_entry(&b, 1, before[*idx], &reqs[*idx], st, rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    if (*flag && *idx != MPI_UNDEFINED) restore_shadow(&reqs[*idx], before[*idx]);
    free(before);
    return rc;
}
int MPI_Testsome(int n, MPI_Request reqs[], int *outcount, int idx[], MPI_Status sts[]) {
    GUARD; COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    int rc = PMPI_Testsome(n, reqs, outcount, idx, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testsome\",\"flag\":%d", *outcount > 0); reqs_list(&b, n, before, reqs); rb_printf(&b, ",\"done\":[");
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) done_entry(&b, i == 0, before[idx[i]], &reqs[idx[i]], &sts[i], rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, *outcount == 0);
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) restore_shadow(&reqs[idx[i]], before[idx[i]]);
    free(before); free(mine);
    return rc;
}
int MPI_Request_free(MPI_Request *req) {
    GUARD;
    int id = req_id(*req, req);
    REC(b, "reqfree"); rb_printf(&b, ",\"req\":%d", id); rb_emit(&b);
    guard_done(id, 0);      /* the operation completes unobserved: its buffers are no longer followed */
    MPI_Request original;
    if (shadow_pop(*req, &original)) {     /* freed while a pinned start was in flight: free both */
        req_remove(*req, req); PMPI_Request_free(req); *req = original;
    }
    req_remove(*req, req);
    pthread_mutex_lock(&g_mu); pers_remove(*req);
#if MPI_VERSION >= 4
    part_remove(*req);
#endif
    pthread_mutex_unlock(&g_mu);
    return PMPI_Request_free(req);
}
int MPI_Cancel(MPI_Request *req) {
    GUARD;
    pthread_mutex_lock(&g_mu); reqent *e = req_find_locked(*req, req); if (e) e->cancel_req = 1; pthread_mutex_unlock(&g_mu);
    REC(b, "cancel"); rb_printf(&b, ",\"req\":%d", req_id(*req, req)); rb_emit(&b);
    return PMPI_Cancel(req);
}

/* ------------------------------------------------------------------ probe */
/* A matched Mprobe/Improbe hands out a message handle that Mrecv/Imrecv later consume.
   The handle exposes neither the communicator nor the envelope, so they are kept here. */
#define MAXMSG 256
static struct { MPI_Message m; int comm, src, tag; } g_msg[MAXMSG];
static int g_nmsg = 0;
static void msg_add(MPI_Message m, MPI_Comm comm, MPI_Status *st) {
    if (m == MPI_MESSAGE_NULL || m == MPI_MESSAGE_NO_PROC) return;
    int cid = comm_id(comm);           /* takes g_mu itself */
    pthread_mutex_lock(&g_mu);
    int full = g_nmsg >= MAXMSG;
    if (!full) { g_msg[g_nmsg].m = m; g_msg[g_nmsg].comm = cid; g_msg[g_nmsg].src = st->MPI_SOURCE; g_msg[g_nmsg].tag = st->MPI_TAG; g_nmsg++; }
    int report = full && !g_truncated;
    if (full) g_truncated = 1;
    pthread_mutex_unlock(&g_mu);
    if (report) { REC(b, "overflow"); rb_printf(&b, ",\"what\":\"message table\""); rb_emit(&b); }
}
static int msg_take(MPI_Message m, int *comm, int *src, int *tag) {
    int found = 0;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nmsg; i++) if (g_msg[i].m == m) { *comm = g_msg[i].comm; *src = g_msg[i].src; *tag = g_msg[i].tag; g_msg[i] = g_msg[--g_nmsg]; found = 1; break; }
    pthread_mutex_unlock(&g_mu);
    return found;
}
static inline __attribute__((always_inline)) void probe_rec(const char *kind, int src, int tag, MPI_Comm comm, int flag, MPI_Status *st, int rc, int pidx, int pinned, int psrc) {
    REC(b, "probe");
    rb_printf(&b, ",\"kind\":\"%s\"", kind); rb_src(&b, src); rb_tag(&b, tag); rb_printf(&b, ",\"comm\":%d,\"flag\":%d", comm_id(comm), flag);
    if (flag && pidx >= 0) rb_printf(&b, ",\"pidx\":%d", pidx);
    if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d}", psrc);
    if (flag && rc == MPI_SUCCESS && st) { int cnt = -1; PMPI_Get_count(st, MPI_BYTE, &cnt); rb_printf(&b, ",\"msrc\":%d,\"mtag\":%d,\"mbytes\":%d", st->MPI_SOURCE, st->MPI_TAG, cnt); }
    if (flag) stretto_pin_probe_commit(pidx, rc == MPI_SUCCESS && st ? st->MPI_SOURCE : -1, rc == MPI_SUCCESS && st ? st->MPI_TAG : -1);
    rb_emit_kind(&b, !flag);
}
int MPI_Probe(int src, int tag, MPI_Comm comm, MPI_Status *st) {
    GUARD; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = stretto_pin_probe(&src, &tag, comm, &pidx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"probe\",\"kind\":\"probe\""); rb_src(&b, osrc); rb_tag(&b, otag); rb_printf(&b, ",\"comm\":%d", comm_id(comm)); rb_emit(&b); }
    int rc = PMPI_Probe(src, tag, comm, st); probe_rec("probe", osrc, otag, comm, 1, st, rc, pidx, pinned, src); return rc;
}
int MPI_Iprobe(int src, int tag, MPI_Comm comm, int *flag, MPI_Status *st) {
    GUARD; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = stretto_pin_probe(&src, &tag, comm, &pidx);
    int rc = PMPI_Iprobe(src, tag, comm, flag, st); probe_rec("iprobe", osrc, otag, comm, *flag, st, rc, pidx, pinned, src); return rc;
}
int MPI_Mprobe(int src, int tag, MPI_Comm comm, MPI_Message *msg, MPI_Status *st) {
    GUARD; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = stretto_pin_probe(&src, &tag, comm, &pidx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"probe\",\"kind\":\"mprobe\""); rb_src(&b, osrc); rb_tag(&b, otag); rb_printf(&b, ",\"comm\":%d", comm_id(comm)); rb_emit(&b); }
    int rc = PMPI_Mprobe(src, tag, comm, msg, st); if (rc == MPI_SUCCESS) msg_add(*msg, comm, st);
    probe_rec("mprobe", osrc, otag, comm, 1, st, rc, pidx, pinned, src); return rc;
}
int MPI_Improbe(int src, int tag, MPI_Comm comm, int *flag, MPI_Message *msg, MPI_Status *st) {
    GUARD; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = stretto_pin_probe(&src, &tag, comm, &pidx);
    int rc = PMPI_Improbe(src, tag, comm, flag, msg, st); if (rc == MPI_SUCCESS && *flag) msg_add(*msg, comm, st);
    probe_rec("improbe", osrc, otag, comm, *flag, st, rc, pidx, pinned, src); return rc;
}
/* Mrecv/Imrecv consume the probed message: recorded as a receive whose source/tag are the
   message's (known from the probe's status), so the loader matches it by FIFO. */
int MPI_Mrecv(void *buf, int count, MPI_Datatype dt, MPI_Message *msg, MPI_Status *st) {
    GUARD; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    if (*msg != MPI_MESSAGE_NO_PROC) GUARD_BUF(-1, "MPI_Mrecv", buf, count, dt, 1, 0);
    int comm = 0, src = -1, tag = -1; msg_take(*msg, &comm, &src, &tag);
    int rc = PMPI_Mrecv(buf, count, dt, msg, st);
    if (rc == MPI_SUCCESS) { src = st->MPI_SOURCE; tag = st->MPI_TAG; }
    REC(b, "recv"); rb_printf(&b, ",\"src\":%d,\"tag\":%d,\"comm\":%d,\"mrecv\":1", src, tag, comm); rb_type(&b, count, dt);
    status_fields(&b, st, dt, rc == MPI_SUCCESS); rb_emit(&b);
    return rc;
}
int MPI_Imrecv(void *buf, int count, MPI_Datatype dt, MPI_Message *msg, MPI_Request *req) {
    GUARD;
    int comm = 0, src = -1, tag = -1, noproc = *msg == MPI_MESSAGE_NO_PROC; msg_take(*msg, &comm, &src, &tag);
    int rc = PMPI_Imrecv(buf, count, dt, msg, req);
    int id = rc == MPI_SUCCESS ? req_add(req, 1, dt, 0) : -1;
    REC(b, "irecv"); rb_printf(&b, ",\"src\":%d,\"tag\":%d,\"comm\":%d,\"mrecv\":1", src, tag, comm); rb_type(&b, count, dt);
    if (id >= 0) rb_printf(&b, ",\"req\":%d", id);
    rb_emit(&b);
    if (id >= 0 && !noproc) GUARD_BUF(id, "MPI_Imrecv", buf, count, dt, 1, 1);
    return rc;
}

/* ------------------------------------------------------------ collectives */
/* What this rank contributes to a collective and what it receives, written as din and dout
   when payloads are recorded; a NULL buffer is left out. */
typedef struct { const void *in; int nin; MPI_Datatype tin; const void *out; int nout; MPI_Datatype tout; } cdata;
static inline __attribute__((always_inline)) void coll_rec(const char *name, MPI_Comm comm, int root, int count, MPI_Datatype dt, int reqid, int begin, const cdata *d) {
    REC(b, begin ? "begin" : "coll");
    if (begin) rb_printf(&b, ",\"call\":\"coll\"");
    rb_printf(&b, ",\"name\":\"%s\",\"comm\":%d,\"root\":%d", name, comm_id(comm), root); rb_type(&b, count, dt);
    if (reqid >= 0) rb_printf(&b, ",\"req\":%d", reqid);
    if (d && d->in) rb_payload(&b, "din", d->in, d->nin, d->tin);
    if (d && d->out) rb_payload(&b, "dout", d->out, d->nout, d->tout);
    rb_emit(&b);
}
/* This rank's rank in comm and the size of comm, when payloads are recorded; -1 otherwise and
   on an intercommunicator, whose collectives are recorded without payloads. */
static int coll_me(MPI_Comm comm, int *np) {
    int me = -1, inter = 0;
    *np = 0;
    if (g_payload <= 0) return -1;
    PMPI_Comm_test_inter(comm, &inter);
    if (inter) return -1;
    PMPI_Comm_rank(comm, &me); PMPI_Comm_size(comm, np);
    return me;
}
/* gc: the call's buffers for the guard (an expression filling gb_, see GUARD_COLL); D: the
   payload (WITH_DATA). A wrapper with a payload starts with GUARD itself. */
#define BLK(NAME, name, root, count, dt, call, gc) GUARD; BLKD(NAME, name, root, count, dt, call, NULL, gc)
#define BLKD(NAME, name, root, count, dt, call, D, gc) \
    GUARD_COLL(-1, #NAME, gc); \
    coll_rec(name, comm, root, count, dt, -1, 1, NULL); int rc = call; coll_rec(name, comm, root, count, dt, -1, 0, D); return rc;
#define NBL(NAME, name, root, count, dt, call, gc) \
    GUARD; int rc = call; int id_ = rc == MPI_SUCCESS ? req_add(req, 2, dt, 0) : -1; coll_rec(name, comm, root, count, dt, id_, 0, NULL); \
    if (id_ >= 0) GUARD_COLL(id_, #NAME, gc); \
    return rc;
/* the payload of a fixed-size collective: D is the cdata, built from me and np */
#define WITH_DATA(...) int np_ = 0, me = coll_me(comm, &np_); cdata d_ = __VA_ARGS__; const cdata *D = me >= 0 ? &d_ : NULL

int MPI_Barrier(MPI_Comm comm) { BLK(MPI_Barrier, "barrier", -1, 0, MPI_DATATYPE_NULL, PMPI_Barrier(comm), 0) }
int MPI_Bcast(void *b, int n, MPI_Datatype dt, int root, MPI_Comm comm) {
    GUARD; WITH_DATA({me == root ? b : NULL, n, dt, me != root ? b : NULL, n, dt});
    BLKD(MPI_Bcast, "bcast", root, n, dt, PMPI_Bcast(b, n, dt, root, comm), D, gc_bcast(b, n, dt, root, comm, &gb_)) }
int MPI_Reduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, int root, MPI_Comm comm) {
    GUARD; WITH_DATA({s, n, dt, me == root ? r : NULL, n, dt});
    BLKD(MPI_Reduce, "reduce", root, n, dt, PMPI_Reduce(s, r, n, dt, op, root, comm), D, gc_reduce(s, r, n, dt, root, 0, comm, &gb_)) }
int MPI_Allreduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) {
    GUARD; WITH_DATA({s, n, dt, r, n, dt});
    BLKD(MPI_Allreduce, "allreduce", -1, n, dt, PMPI_Allreduce(s, r, n, dt, op, comm), D, gc_reduce(s, r, n, dt, -1, 0, comm, &gb_)) }
int MPI_Gather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm) {
    GUARD; WITH_DATA({s, sn, sdt, me == root ? r : NULL, rn * np_, rdt});
    BLKD(MPI_Gather, "gather", root, sn, sdt, PMPI_Gather(s, sn, sdt, r, rn, rdt, root, comm), D, gc_gather(s, sn, sdt, r, rn, NULL, NULL, rdt, root, comm, &gb_)) }
int MPI_Gatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, int root, MPI_Comm comm) { BLK(MPI_Gatherv, "gatherv", root, sn, sdt, PMPI_Gatherv(s, sn, sdt, r, rc_, d, rdt, root, comm), gc_gather(s, sn, sdt, r, 0, rc_, d, rdt, root, comm, &gb_)) }
int MPI_Scatter(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm) {
    GUARD; WITH_DATA({me == root ? s : NULL, sn * np_, sdt, r, rn, rdt});
    BLKD(MPI_Scatter, "scatter", root, rn, rdt, PMPI_Scatter(s, sn, sdt, r, rn, rdt, root, comm), D, gc_scatter(s, sn, NULL, NULL, sdt, r, rn, rdt, root, comm, &gb_)) }
int MPI_Scatterv(const void *s, const int *sc, const int *d, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm) { BLK(MPI_Scatterv, "scatterv", root, rn, rdt, PMPI_Scatterv(s, sc, d, sdt, r, rn, rdt, root, comm), gc_scatter(s, 0, sc, d, sdt, r, rn, rdt, root, comm, &gb_)) }
int MPI_Allgather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm) {
    GUARD; WITH_DATA({s, sn, sdt, r, rn * np_, rdt});
    BLKD(MPI_Allgather, "allgather", -1, sn, sdt, PMPI_Allgather(s, sn, sdt, r, rn, rdt, comm), D, gc_gather(s, sn, sdt, r, rn, NULL, NULL, rdt, -1, comm, &gb_)) }
int MPI_Allgatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, MPI_Comm comm) { BLK(MPI_Allgatherv, "allgatherv", -1, sn, sdt, PMPI_Allgatherv(s, sn, sdt, r, rc_, d, rdt, comm), gc_gather(s, sn, sdt, r, 0, rc_, d, rdt, -1, comm, &gb_)) }
int MPI_Alltoall(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm) {
    GUARD; WITH_DATA({s, sn * np_, sdt, r, rn * np_, rdt});
    BLKD(MPI_Alltoall, "alltoall", -1, sn, sdt, PMPI_Alltoall(s, sn, sdt, r, rn, rdt, comm), D, gc_alltoall(s, sn, NULL, NULL, &sdt, r, rn, NULL, NULL, &rdt, 0, comm, &gb_)) }
int MPI_Alltoallv(const void *s, const int *sc, const int *sd, MPI_Datatype sdt, void *r, const int *rc_, const int *rd, MPI_Datatype rdt, MPI_Comm comm) { BLK(MPI_Alltoallv, "alltoallv", -1, 0, sdt, PMPI_Alltoallv(s, sc, sd, sdt, r, rc_, rd, rdt, comm), gc_alltoall(s, 0, sc, sd, &sdt, r, 0, rc_, rd, &rdt, 0, comm, &gb_)) }
int MPI_Alltoallw(const void *s, const int *sc, const int *sd, const MPI_Datatype *sdt, void *r, const int *rc_, const int *rd, const MPI_Datatype *rdt, MPI_Comm comm) { BLK(MPI_Alltoallw, "alltoallw", -1, 0, MPI_DATATYPE_NULL, PMPI_Alltoallw(s, sc, sd, sdt, r, rc_, rd, rdt, comm), gc_alltoall(s, 0, sc, sd, sdt, r, 0, rc_, rd, rdt, 1, comm, &gb_)) }
int MPI_Reduce_scatter(const void *s, void *r, const int *rc_, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) { BLK(MPI_Reduce_scatter, "reduce_scatter", -1, 0, dt, PMPI_Reduce_scatter(s, r, rc_, dt, op, comm), gc_redscat(s, r, rc_, 0, dt, comm, &gb_)) }
int MPI_Reduce_scatter_block(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) { BLK(MPI_Reduce_scatter_block, "reduce_scatter_block", -1, n, dt, PMPI_Reduce_scatter_block(s, r, n, dt, op, comm), gc_redscat(s, r, NULL, n, dt, comm, &gb_)) }
int MPI_Scan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) {
    GUARD; WITH_DATA({s, n, dt, r, n, dt});
    BLKD(MPI_Scan, "scan", -1, n, dt, PMPI_Scan(s, r, n, dt, op, comm), D, gc_reduce(s, r, n, dt, -1, 0, comm, &gb_)) }
int MPI_Exscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) {
    GUARD; WITH_DATA({s, n, dt, r, n, dt});
    BLKD(MPI_Exscan, "exscan", -1, n, dt, PMPI_Exscan(s, r, n, dt, op, comm), D, gc_reduce(s, r, n, dt, -1, 1, comm, &gb_)) }

int MPI_Ibarrier(MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ibarrier, "ibarrier", -1, 0, MPI_DATATYPE_NULL, PMPI_Ibarrier(comm, req), 0) }
int MPI_Ibcast(void *b, int n, MPI_Datatype dt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ibcast, "ibcast", root, n, dt, PMPI_Ibcast(b, n, dt, root, comm, req), gc_bcast(b, n, dt, root, comm, &gb_)) }
int MPI_Ireduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ireduce, "ireduce", root, n, dt, PMPI_Ireduce(s, r, n, dt, op, root, comm, req), gc_reduce(s, r, n, dt, root, 0, comm, &gb_)) }
int MPI_Iallreduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iallreduce, "iallreduce", -1, n, dt, PMPI_Iallreduce(s, r, n, dt, op, comm, req), gc_reduce(s, r, n, dt, -1, 0, comm, &gb_)) }
int MPI_Igather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Igather, "igather", root, sn, sdt, PMPI_Igather(s, sn, sdt, r, rn, rdt, root, comm, req), gc_gather(s, sn, sdt, r, rn, NULL, NULL, rdt, root, comm, &gb_)) }
int MPI_Igatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Igatherv, "igatherv", root, sn, sdt, PMPI_Igatherv(s, sn, sdt, r, rc_, d, rdt, root, comm, req), gc_gather(s, sn, sdt, r, 0, rc_, d, rdt, root, comm, &gb_)) }
int MPI_Iscatter(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iscatter, "iscatter", root, rn, rdt, PMPI_Iscatter(s, sn, sdt, r, rn, rdt, root, comm, req), gc_scatter(s, sn, NULL, NULL, sdt, r, rn, rdt, root, comm, &gb_)) }
int MPI_Iscatterv(const void *s, const int *sc, const int *d, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iscatterv, "iscatterv", root, rn, rdt, PMPI_Iscatterv(s, sc, d, sdt, r, rn, rdt, root, comm, req), gc_scatter(s, 0, sc, d, sdt, r, rn, rdt, root, comm, &gb_)) }
int MPI_Iallgather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iallgather, "iallgather", -1, sn, sdt, PMPI_Iallgather(s, sn, sdt, r, rn, rdt, comm, req), gc_gather(s, sn, sdt, r, rn, NULL, NULL, rdt, -1, comm, &gb_)) }
int MPI_Iallgatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iallgatherv, "iallgatherv", -1, sn, sdt, PMPI_Iallgatherv(s, sn, sdt, r, rc_, d, rdt, comm, req), gc_gather(s, sn, sdt, r, 0, rc_, d, rdt, -1, comm, &gb_)) }
int MPI_Ialltoall(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ialltoall, "ialltoall", -1, sn, sdt, PMPI_Ialltoall(s, sn, sdt, r, rn, rdt, comm, req), gc_alltoall(s, sn, NULL, NULL, &sdt, r, rn, NULL, NULL, &rdt, 0, comm, &gb_)) }
int MPI_Ialltoallv(const void *s, const int *sc, const int *sd, MPI_Datatype sdt, void *r, const int *rc_, const int *rd, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ialltoallv, "ialltoallv", -1, 0, sdt, PMPI_Ialltoallv(s, sc, sd, sdt, r, rc_, rd, rdt, comm, req), gc_alltoall(s, 0, sc, sd, &sdt, r, 0, rc_, rd, &rdt, 0, comm, &gb_)) }
int MPI_Ialltoallw(const void *s, const int *sc, const int *sd, const MPI_Datatype *sdt, void *r, const int *rc_, const int *rd, const MPI_Datatype *rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ialltoallw, "ialltoallw", -1, 0, MPI_DATATYPE_NULL, PMPI_Ialltoallw(s, sc, sd, sdt, r, rc_, rd, rdt, comm, req), gc_alltoall(s, 0, sc, sd, sdt, r, 0, rc_, rd, rdt, 1, comm, &gb_)) }
int MPI_Ireduce_scatter(const void *s, void *r, const int *rc_, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ireduce_scatter, "ireduce_scatter", -1, 0, dt, PMPI_Ireduce_scatter(s, r, rc_, dt, op, comm, req), gc_redscat(s, r, rc_, 0, dt, comm, &gb_)) }
int MPI_Ireduce_scatter_block(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ireduce_scatter_block, "ireduce_scatter_block", -1, n, dt, PMPI_Ireduce_scatter_block(s, r, n, dt, op, comm, req), gc_redscat(s, r, NULL, n, dt, comm, &gb_)) }
int MPI_Iscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iscan, "iscan", -1, n, dt, PMPI_Iscan(s, r, n, dt, op, comm, req), gc_reduce(s, r, n, dt, -1, 0, comm, &gb_)) }
int MPI_Iexscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iexscan, "iexscan", -1, n, dt, PMPI_Iexscan(s, r, n, dt, op, comm, req), gc_reduce(s, r, n, dt, -1, 1, comm, &gb_)) }

/* ---------------------------------------------------------- communicators */
#define REG(how, parent, n) do { if (rc == MPI_SUCCESS) comm_register(how, parent, *(n)); } while (0)
int MPI_Comm_dup(MPI_Comm c, MPI_Comm *n) { GUARD; int rc = PMPI_Comm_dup(c, n); REG("dup", c, n); return rc; }
int MPI_Comm_split(MPI_Comm c, int color, int key, MPI_Comm *n) { GUARD; int rc = PMPI_Comm_split(c, color, key, n); REG("split", c, n); return rc; }
int MPI_Comm_split_type(MPI_Comm c, int type, int key, MPI_Info info, MPI_Comm *n) { GUARD; int rc = PMPI_Comm_split_type(c, type, key, info, n); REG("split_type", c, n); return rc; }
int MPI_Comm_create(MPI_Comm c, MPI_Group g, MPI_Comm *n) { GUARD; int rc = PMPI_Comm_create(c, g, n); REG("create", c, n); return rc; }
int MPI_Comm_create_group(MPI_Comm c, MPI_Group g, int tag, MPI_Comm *n) { GUARD; int rc = PMPI_Comm_create_group(c, g, tag, n); REG("create_group", c, n); return rc; }
int MPI_Cart_create(MPI_Comm c, int nd, const int *dims, const int *periods, int reorder, MPI_Comm *n) { GUARD; int rc = PMPI_Cart_create(c, nd, dims, periods, reorder, n); REG("cart", c, n); return rc; }
int MPI_Cart_sub(MPI_Comm c, const int *remain, MPI_Comm *n) { GUARD; int rc = PMPI_Cart_sub(c, remain, n); REG("cart_sub", c, n); return rc; }
int MPI_Graph_create(MPI_Comm c, int nn, const int *idx, const int *edges, int reorder, MPI_Comm *n) { GUARD; int rc = PMPI_Graph_create(c, nn, idx, edges, reorder, n); REG("graph", c, n); return rc; }
int MPI_Dist_graph_create(MPI_Comm c, int n_, const int *sources, const int *degrees, const int *dests, const int *weights, MPI_Info info, int reorder, MPI_Comm *n) {
    GUARD; int rc = PMPI_Dist_graph_create(c, n_, sources, degrees, dests, weights, info, reorder, n); REG("dist_graph", c, n); return rc; }
int MPI_Dist_graph_create_adjacent(MPI_Comm c, int indeg, const int *sources, const int *sw, int outdeg, const int *dests, const int *dw, MPI_Info info, int reorder, MPI_Comm *n) {
    GUARD; int rc = PMPI_Dist_graph_create_adjacent(c, indeg, sources, sw, outdeg, dests, dw, info, reorder, n); REG("dist_graph_adjacent", c, n); return rc; }
int MPI_Intercomm_create(MPI_Comm local, int lleader, MPI_Comm peer, int rleader, int tag, MPI_Comm *n) {
    GUARD; int rc = PMPI_Intercomm_create(local, lleader, peer, rleader, tag, n); REG("intercomm", local, n); return rc; }
int MPI_Intercomm_merge(MPI_Comm inter, int high, MPI_Comm *n) { GUARD; int rc = PMPI_Intercomm_merge(inter, high, n); REG("merge", inter, n); return rc; }
int MPI_Comm_dup_with_info(MPI_Comm c, MPI_Info info, MPI_Comm *n) { GUARD; int rc = PMPI_Comm_dup_with_info(c, info, n); REG("dup", c, n); return rc; }

/* ------------------------------------------------------- outside the model
   Communication the analysis does not model: one-sided windows, neighbourhood collectives,
   dynamic processes, generalised requests and the MPI-4 calls below. Each call writes an
   "unmodelled" record, so that the loader knows the trace is not a complete record of the
   run's communication and the analysis reports observed findings only (the premise of
   theorem T1 in docs/model.md). */
static inline __attribute__((always_inline)) void unmodelled(const char *call) { REC(b, "unmodelled"); rb_printf(&b, ",\"call\":\"%s\"", call); rb_emit(&b); }
#define UNMODELLED(name, call) do { GUARD; unmodelled(name); return call; } while (0)
int MPI_Win_create(void *base, MPI_Aint size, int du, MPI_Info info, MPI_Comm comm, MPI_Win *win) { UNMODELLED("Win_create", PMPI_Win_create(base, size, du, info, comm, win)); }
int MPI_Win_allocate(MPI_Aint size, int du, MPI_Info info, MPI_Comm comm, void *base, MPI_Win *win) { UNMODELLED("Win_allocate", PMPI_Win_allocate(size, du, info, comm, base, win)); }
int MPI_Win_allocate_shared(MPI_Aint size, int du, MPI_Info info, MPI_Comm comm, void *base, MPI_Win *win) { UNMODELLED("Win_allocate_shared", PMPI_Win_allocate_shared(size, du, info, comm, base, win)); }
int MPI_Win_create_dynamic(MPI_Info info, MPI_Comm comm, MPI_Win *win) { UNMODELLED("Win_create_dynamic", PMPI_Win_create_dynamic(info, comm, win)); }
int MPI_Neighbor_allgather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm) { UNMODELLED("Neighbor_allgather", PMPI_Neighbor_allgather(s, sn, sdt, r, rn, rdt, comm)); }
int MPI_Neighbor_allgatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int rc_[], const int d[], MPI_Datatype rdt, MPI_Comm comm) { UNMODELLED("Neighbor_allgatherv", PMPI_Neighbor_allgatherv(s, sn, sdt, r, rc_, d, rdt, comm)); }
int MPI_Neighbor_alltoall(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm) { UNMODELLED("Neighbor_alltoall", PMPI_Neighbor_alltoall(s, sn, sdt, r, rn, rdt, comm)); }
int MPI_Neighbor_alltoallv(const void *s, const int sc[], const int sd[], MPI_Datatype sdt, void *r, const int rc_[], const int rd[], MPI_Datatype rdt, MPI_Comm comm) { UNMODELLED("Neighbor_alltoallv", PMPI_Neighbor_alltoallv(s, sc, sd, sdt, r, rc_, rd, rdt, comm)); }
int MPI_Neighbor_alltoallw(const void *s, const int sc[], const MPI_Aint sd[], const MPI_Datatype sdt[], void *r, const int rc_[], const MPI_Aint rd[], const MPI_Datatype rdt[], MPI_Comm comm) { UNMODELLED("Neighbor_alltoallw", PMPI_Neighbor_alltoallw(s, sc, sd, sdt, r, rc_, rd, rdt, comm)); }
int MPI_Ineighbor_allgather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Ineighbor_allgather", PMPI_Ineighbor_allgather(s, sn, sdt, r, rn, rdt, comm, req)); }
int MPI_Ineighbor_allgatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int rc_[], const int d[], MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Ineighbor_allgatherv", PMPI_Ineighbor_allgatherv(s, sn, sdt, r, rc_, d, rdt, comm, req)); }
int MPI_Ineighbor_alltoall(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Ineighbor_alltoall", PMPI_Ineighbor_alltoall(s, sn, sdt, r, rn, rdt, comm, req)); }
int MPI_Ineighbor_alltoallv(const void *s, const int sc[], const int sd[], MPI_Datatype sdt, void *r, const int rc_[], const int rd[], MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Ineighbor_alltoallv", PMPI_Ineighbor_alltoallv(s, sc, sd, sdt, r, rc_, rd, rdt, comm, req)); }
int MPI_Ineighbor_alltoallw(const void *s, const int sc[], const MPI_Aint sd[], const MPI_Datatype sdt[], void *r, const int rc_[], const MPI_Aint rd[], const MPI_Datatype rdt[], MPI_Comm comm, MPI_Request *req) { UNMODELLED("Ineighbor_alltoallw", PMPI_Ineighbor_alltoallw(s, sc, sd, sdt, r, rc_, rd, rdt, comm, req)); }
int MPI_Comm_spawn(const char *cmd, char *argv[], int maxprocs, MPI_Info info, int root, MPI_Comm comm, MPI_Comm *inter, int errs[]) { UNMODELLED("Comm_spawn", PMPI_Comm_spawn(cmd, argv, maxprocs, info, root, comm, inter, errs)); }
int MPI_Comm_spawn_multiple(int count, char *cmds[], char **argvs[], const int maxprocs[], const MPI_Info infos[], int root, MPI_Comm comm, MPI_Comm *inter, int errs[]) { UNMODELLED("Comm_spawn_multiple", PMPI_Comm_spawn_multiple(count, cmds, argvs, maxprocs, infos, root, comm, inter, errs)); }
int MPI_Comm_connect(const char *port, MPI_Info info, int root, MPI_Comm comm, MPI_Comm *n) { UNMODELLED("Comm_connect", PMPI_Comm_connect(port, info, root, comm, n)); }
int MPI_Comm_accept(const char *port, MPI_Info info, int root, MPI_Comm comm, MPI_Comm *n) { UNMODELLED("Comm_accept", PMPI_Comm_accept(port, info, root, comm, n)); }
int MPI_Comm_join(int fd, MPI_Comm *inter) { UNMODELLED("Comm_join", PMPI_Comm_join(fd, inter)); }
int MPI_Grequest_start(MPI_Grequest_query_function *q, MPI_Grequest_free_function *f, MPI_Grequest_cancel_function *c, void *extra, MPI_Request *req) { UNMODELLED("Grequest_start", PMPI_Grequest_start(q, f, c, extra, req)); }
/* One-sided communication and synchronisation drive the library's progress, which may
   complete a pending receive into a guarded buffer: no record, only the guard's bracket. */
#define BRACKET(call) do { GUARD; return call; } while (0)
int MPI_Put(const void *o, int on, MPI_Datatype odt, int t, MPI_Aint td, int tn, MPI_Datatype tdt, MPI_Win w) { BRACKET(PMPI_Put(o, on, odt, t, td, tn, tdt, w)); }
int MPI_Get(void *o, int on, MPI_Datatype odt, int t, MPI_Aint td, int tn, MPI_Datatype tdt, MPI_Win w) { BRACKET(PMPI_Get(o, on, odt, t, td, tn, tdt, w)); }
int MPI_Accumulate(const void *o, int on, MPI_Datatype odt, int t, MPI_Aint td, int tn, MPI_Datatype tdt, MPI_Op op, MPI_Win w) { BRACKET(PMPI_Accumulate(o, on, odt, t, td, tn, tdt, op, w)); }
int MPI_Get_accumulate(const void *o, int on, MPI_Datatype odt, void *r, int rn, MPI_Datatype rdt, int t, MPI_Aint td, int tn, MPI_Datatype tdt, MPI_Op op, MPI_Win w) { BRACKET(PMPI_Get_accumulate(o, on, odt, r, rn, rdt, t, td, tn, tdt, op, w)); }
int MPI_Fetch_and_op(const void *o, void *r, MPI_Datatype dt, int t, MPI_Aint td, MPI_Op op, MPI_Win w) { BRACKET(PMPI_Fetch_and_op(o, r, dt, t, td, op, w)); }
int MPI_Compare_and_swap(const void *o, const void *c, void *r, MPI_Datatype dt, int t, MPI_Aint td, MPI_Win w) { BRACKET(PMPI_Compare_and_swap(o, c, r, dt, t, td, w)); }
int MPI_Rput(const void *o, int on, MPI_Datatype odt, int t, MPI_Aint td, int tn, MPI_Datatype tdt, MPI_Win w, MPI_Request *q) { BRACKET(PMPI_Rput(o, on, odt, t, td, tn, tdt, w, q)); }
int MPI_Rget(void *o, int on, MPI_Datatype odt, int t, MPI_Aint td, int tn, MPI_Datatype tdt, MPI_Win w, MPI_Request *q) { BRACKET(PMPI_Rget(o, on, odt, t, td, tn, tdt, w, q)); }
int MPI_Raccumulate(const void *o, int on, MPI_Datatype odt, int t, MPI_Aint td, int tn, MPI_Datatype tdt, MPI_Op op, MPI_Win w, MPI_Request *q) { BRACKET(PMPI_Raccumulate(o, on, odt, t, td, tn, tdt, op, w, q)); }
int MPI_Rget_accumulate(const void *o, int on, MPI_Datatype odt, void *r, int rn, MPI_Datatype rdt, int t, MPI_Aint td, int tn, MPI_Datatype tdt, MPI_Op op, MPI_Win w, MPI_Request *q) { BRACKET(PMPI_Rget_accumulate(o, on, odt, r, rn, rdt, t, td, tn, tdt, op, w, q)); }
int MPI_Win_fence(int a, MPI_Win w) { BRACKET(PMPI_Win_fence(a, w)); }
int MPI_Win_lock(int lt, int r, int a, MPI_Win w) { BRACKET(PMPI_Win_lock(lt, r, a, w)); }
int MPI_Win_unlock(int r, MPI_Win w) { BRACKET(PMPI_Win_unlock(r, w)); }
int MPI_Win_lock_all(int a, MPI_Win w) { BRACKET(PMPI_Win_lock_all(a, w)); }
int MPI_Win_unlock_all(MPI_Win w) { BRACKET(PMPI_Win_unlock_all(w)); }
int MPI_Win_flush(int r, MPI_Win w) { BRACKET(PMPI_Win_flush(r, w)); }
int MPI_Win_flush_all(MPI_Win w) { BRACKET(PMPI_Win_flush_all(w)); }
int MPI_Win_flush_local(int r, MPI_Win w) { BRACKET(PMPI_Win_flush_local(r, w)); }
int MPI_Win_flush_local_all(MPI_Win w) { BRACKET(PMPI_Win_flush_local_all(w)); }
int MPI_Win_sync(MPI_Win w) { BRACKET(PMPI_Win_sync(w)); }
int MPI_Win_post(MPI_Group g, int a, MPI_Win w) { BRACKET(PMPI_Win_post(g, a, w)); }
int MPI_Win_start(MPI_Group g, int a, MPI_Win w) { BRACKET(PMPI_Win_start(g, a, w)); }
int MPI_Win_complete(MPI_Win w) { BRACKET(PMPI_Win_complete(w)); }
int MPI_Win_wait(MPI_Win w) { BRACKET(PMPI_Win_wait(w)); }
int MPI_Win_test(MPI_Win w, int *flag) { BRACKET(PMPI_Win_test(w, flag)); }
int MPI_Win_free(MPI_Win *w) { BRACKET(PMPI_Win_free(w)); }
#if MPI_VERSION >= 4
int MPI_Isendrecv(const void *sb, int sn, MPI_Datatype sdt, int dst, int stag, void *rb, int rn, MPI_Datatype rdt, int src, int rtag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Isendrecv", PMPI_Isendrecv(sb, sn, sdt, dst, stag, rb, rn, rdt, src, rtag, comm, req)); }
int MPI_Isendrecv_replace(void *buf, int n, MPI_Datatype dt, int dst, int stag, int src, int rtag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Isendrecv_replace", PMPI_Isendrecv_replace(buf, n, dt, dst, stag, src, rtag, comm, req)); }
int MPI_Psend_init(const void *buf, int parts, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Info info, MPI_Request *req) {
    GUARD; unmodelled("Psend_init");
    int rc = PMPI_Psend_init(buf, parts, n, dt, dst, tag, comm, info, req);
    if (rc == MPI_SUCCESS && g_guard && dst != MPI_PROC_NULL) { req_add(req, 4, dt, 1); part_add(*req, buf, parts, n, dt, 0); }
    return rc;
}
int MPI_Precv_init(void *buf, int parts, MPI_Count n, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Info info, MPI_Request *req) {
    GUARD; unmodelled("Precv_init");
    int rc = PMPI_Precv_init(buf, parts, n, dt, src, tag, comm, info, req);
    if (rc == MPI_SUCCESS && g_guard && src != MPI_PROC_NULL) { req_add(req, 4, dt, 1); part_add(*req, buf, parts, n, dt, 1); }
    return rc;
}
/* partitions [lo, hi) of a partitioned send (or those in list) become MPI's */
static void pready(int lo, int hi, const int *list, MPI_Request req, void *ra) {
    partent p; int id = req_id(req, NULL);
    if (!g_guard || id < 0 || !part_find(req, &p) || p.recv) return;
    for (int k = lo; k < hi; k++) {
        int i = list ? list[k] : k;
        if (i < 0 || i >= p.parts) continue;
        gbuf gb = part_buf(&p, i);
        guard_buffers(id, "MPI_Pready", &gb, 1, ra);
    }
}
int MPI_Pready(int i, MPI_Request req) { GUARD; int rc = PMPI_Pready(i, req); if (rc == MPI_SUCCESS) pready(i, i + 1, NULL, req, _g.ra); return rc; }
int MPI_Pready_range(int lo, int hi, MPI_Request req) { GUARD; int rc = PMPI_Pready_range(lo, hi, req); if (rc == MPI_SUCCESS) pready(lo, hi + 1, NULL, req, _g.ra); return rc; }
int MPI_Pready_list(int n, const int list[], MPI_Request req) { GUARD; int rc = PMPI_Pready_list(n, list, req); if (rc == MPI_SUCCESS) pready(0, n, list, req, _g.ra); return rc; }
int MPI_Parrived(MPI_Request req, int i, int *flag) {
    GUARD; partent p;
    int rc = PMPI_Parrived(req, i, flag);
    if (rc == MPI_SUCCESS && *flag && g_guard && part_find(req, &p) && p.recv && i >= 0 && i < p.parts)
        guard_release(req_id(req, NULL), (uintptr_t)part_buf(&p, i).buf);
    return rc;
}
int MPI_Comm_create_from_group(MPI_Group g, const char *stag, MPI_Info info, MPI_Errhandler eh, MPI_Comm *n) { UNMODELLED("Comm_create_from_group", PMPI_Comm_create_from_group(g, stag, info, eh, n)); }
int MPI_Send_c(const void *b, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm) { UNMODELLED("Send_c", PMPI_Send_c(b, n, dt, dst, tag, comm)); }
int MPI_Ssend_c(const void *b, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm) { UNMODELLED("Ssend_c", PMPI_Ssend_c(b, n, dt, dst, tag, comm)); }
int MPI_Bsend_c(const void *b, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm) { UNMODELLED("Bsend_c", PMPI_Bsend_c(b, n, dt, dst, tag, comm)); }
int MPI_Rsend_c(const void *b, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm) { UNMODELLED("Rsend_c", PMPI_Rsend_c(b, n, dt, dst, tag, comm)); }
int MPI_Isend_c(const void *b, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Isend_c", PMPI_Isend_c(b, n, dt, dst, tag, comm, req)); }
int MPI_Issend_c(const void *b, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Issend_c", PMPI_Issend_c(b, n, dt, dst, tag, comm, req)); }
int MPI_Ibsend_c(const void *b, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Ibsend_c", PMPI_Ibsend_c(b, n, dt, dst, tag, comm, req)); }
int MPI_Irsend_c(const void *b, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Irsend_c", PMPI_Irsend_c(b, n, dt, dst, tag, comm, req)); }
int MPI_Recv_c(void *b, MPI_Count n, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Status *st) { UNMODELLED("Recv_c", PMPI_Recv_c(b, n, dt, src, tag, comm, st)); }
int MPI_Irecv_c(void *b, MPI_Count n, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Irecv_c", PMPI_Irecv_c(b, n, dt, src, tag, comm, req)); }
int MPI_Sendrecv_c(const void *sb, MPI_Count sn, MPI_Datatype sdt, int dst, int stag, void *rb, MPI_Count rn, MPI_Datatype rdt, int src, int rtag, MPI_Comm comm, MPI_Status *st) { UNMODELLED("Sendrecv_c", PMPI_Sendrecv_c(sb, sn, sdt, dst, stag, rb, rn, rdt, src, rtag, comm, st)); }
#endif
/* MPI_Comm_idup: the new communicator is valid once the request completes; it is
   registered then (done_entry), under the name "idup". */
typedef struct { MPI_Request req; MPI_Comm parent; MPI_Comm *out; } idupent;
static idupent *g_idups = NULL; static int g_nidups = 0, g_capidups = 0;
int MPI_Comm_idup(MPI_Comm c, MPI_Comm *n, MPI_Request *req) {
    GUARD;
    int rc = PMPI_Comm_idup(c, n, req);
    if (rc != MPI_SUCCESS) return rc;
    req_add(req, 3, MPI_DATATYPE_NULL, 0);
    pthread_mutex_lock(&g_mu);
    if (g_nidups == g_capidups) { g_capidups = g_capidups ? g_capidups * 2 : 4; g_idups = xrealloc(g_idups, sizeof *g_idups * g_capidups); }
    g_idups[g_nidups++] = (idupent){*req, c, n};
    pthread_mutex_unlock(&g_mu);
    return rc;
}
static void idup_complete(MPI_Request before) {
    MPI_Comm parent, *out = NULL;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nidups; i++) if (g_idups[i].req == before) { parent = g_idups[i].parent; out = g_idups[i].out; g_idups[i] = g_idups[--g_nidups]; break; }
    pthread_mutex_unlock(&g_mu);
    if (out) comm_register("idup", parent, *out);
}
static int comm_release(const char *how, MPI_Comm *c) {
    int id = comm_id(*c);
    REC(b, "comm"); rb_printf(&b, ",\"name\":\"%s\",\"parent\":-1,\"id\":%d", how, id); rb_emit(&b);
    pthread_mutex_lock(&g_mu); comm_forget(*c); pthread_mutex_unlock(&g_mu);
    return id;
}
int MPI_Comm_free(MPI_Comm *c) { GUARD; comm_release("free", c); return PMPI_Comm_free(c); }
int MPI_Comm_disconnect(MPI_Comm *c) { GUARD; comm_release("free", c); return PMPI_Comm_disconnect(c); }

/* ------------------------------------------------------------- buffering */
int MPI_Buffer_attach(void *buf, int size) { GUARD; REC(b, "bufattach"); rb_printf(&b, ",\"size\":%d", size); rb_emit(&b); return PMPI_Buffer_attach(buf, size); }
int MPI_Buffer_detach(void *buf, int *size) { GUARD; int rc = PMPI_Buffer_detach(buf, size); REC(b, "bufdetach"); rb_emit(&b); return rc; }
