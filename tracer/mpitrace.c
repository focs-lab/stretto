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
               "MPI_INT*4,MPI_DOUBLE*2"), [req], [procnull], [persistent]
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
     coll      name, comm, root(-1 if none), count, tsize, [req]
     comm      name, parent(local id), id(local id), ranks:[world ranks in comm-rank order],
               gord (creation ordinal of that group); an intercommunicator carries
               inter:1, ranks = the remote group (what src/dst mean there), local = the
               local group, members = both sorted (its identity across ranks)
     bufattach size | bufdetach
     overflow  what: a fixed table filled up and later records may be incomplete
     unmodelled call: a call whose communication the analysis does not model (one-sided
               windows, neighbourhood collectives, dynamic processes, generalised requests,
               MPI-4 Isendrecv, partitioned and large-count calls, the Start of a persistent
               collective); the analysis then reports observed findings only
     abort     code
     finalize  [truncated]
   A record of kind wait with kind getstatus (MPI_Request_get_status) completes nothing.
   Ranks in send/recv records are comm-local; the loader translates them. */
#define _GNU_SOURCE
#include <mpi.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "../replay/pin.h"

/* ------------------------------------------------------------------ state */
static FILE *g_out = NULL;
static int g_rank = -1;
static long g_seq __attribute__((unused)) = 0;
static int g_truncated = 0;
static int g_multi = 0;           /* MPI_THREAD_MULTIPLE was provided: records carry tid */
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;

static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n);
    if (!q) { fprintf(stderr, "stretto tracer: out of memory\n"); abort(); }
    return q;
}

typedef struct { MPI_Request req; int id; int kind; MPI_Datatype dt; int persistent; int active; int cancel_req; int pin1; } reqent; /* kind: 0 send, 1 recv, 2 coll, 3 idup; pin1: 1 + the widx of a pinned receive, 0 if none */
static reqent *g_reqs = NULL;
static int g_nreqs = 0, g_capreqs = 0, g_next_req = 0;

typedef struct { MPI_Comm c; int id; } comment;
static comment *g_comms = NULL;
static int g_ncomms = 0, g_capcomms = 0, g_next_comm = 0;

typedef struct { char *key; int count; } groupent;
static groupent *g_groups = NULL;
static int g_ngroups = 0, g_capgroups = 0;

/* --------------------------------------------------------------- records */
typedef struct { char *p; size_t len, cap; } rec_t;

static void rb_init(rec_t *b) { b->cap = 256; b->len = 0; b->p = malloc(b->cap); b->p[0] = 0; }
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
        char head[96];
        if (g_multi) snprintf(head, sizeof head, "{\"r\":%d,\"seq\":%ld,\"tid\":%ld,", g_rank, g_seq++, (long)gettid());
        else snprintf(head, sizeof head, "{\"r\":%d,\"seq\":%ld,", g_rank, g_seq++);
        fwrite(head, 1, strlen(head), g_out);
        fwrite(b->p, 1, b->len, g_out);
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
#define REC(b, op) rec_t b; rb_init(&b); rb_printf(&b, "\"op\":\"%s\"", op)

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

/* ---------------------------------------------------------- request table */
static int req_add(MPI_Request r, int kind, MPI_Datatype dt, int persistent) {
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nreqs; i++) if (g_reqs[i].req == r) {   /* recycled handle */
        g_reqs[i].id = g_next_req++; g_reqs[i].kind = kind; g_reqs[i].dt = dt; g_reqs[i].persistent = persistent; g_reqs[i].active = !persistent; g_reqs[i].cancel_req = 0; g_reqs[i].pin1 = 0;
        int id = g_reqs[i].id; pthread_mutex_unlock(&g_mu); return id;
    }
    if (g_nreqs == g_capreqs) { g_capreqs = g_capreqs ? g_capreqs * 2 : 64; g_reqs = xrealloc(g_reqs, sizeof *g_reqs * g_capreqs); }
    int id = g_next_req++;
    g_reqs[g_nreqs].req = r; g_reqs[g_nreqs].id = id; g_reqs[g_nreqs].kind = kind; g_reqs[g_nreqs].dt = dt;
    g_reqs[g_nreqs].persistent = persistent; g_reqs[g_nreqs].active = !persistent; g_reqs[g_nreqs].cancel_req = 0; g_reqs[g_nreqs].pin1 = 0; g_nreqs++;
    pthread_mutex_unlock(&g_mu);
    return id;
}
static reqent *req_find_locked(MPI_Request r) {
    if (r == MPI_REQUEST_NULL) return NULL;
    for (int i = 0; i < g_nreqs; i++) if (g_reqs[i].req == r) return &g_reqs[i];
    return NULL;
}
/* A pinned nonblocking receive reports its match when the completion call returns it. */
static void req_set_pin(MPI_Request r, int widx) {
    pthread_mutex_lock(&g_mu); reqent *e = req_find_locked(r); if (e) e->pin1 = widx + 1; pthread_mutex_unlock(&g_mu);
}
static int req_id(MPI_Request r) {
    pthread_mutex_lock(&g_mu); reqent *e = req_find_locked(r); int id = e ? e->id : -1; pthread_mutex_unlock(&g_mu); return id;
}
static void req_remove(MPI_Request r) {
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_nreqs; i++) if (g_reqs[i].req == r) { g_reqs[i] = g_reqs[--g_nreqs]; break; }
    pthread_mutex_unlock(&g_mu);
}
/* A persistent request gets a fresh id at every Start (each start is a new operation). */
static int req_start(MPI_Request r, int *kind, MPI_Datatype *dt) {
    pthread_mutex_lock(&g_mu);
    reqent *e = req_find_locked(r);
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
    const char *dir = getenv("STRETTO_TRACE_DIR");
    if (!dir) dir = "traces";
    mkdir(dir, 0777);
    char path[1024];
    snprintf(path, sizeof path, "%s/rank-%d.jsonl", dir, g_rank);
    g_out = fopen(path, "w");
    if (!g_out) { perror("stretto tracer: fopen"); g_out = stderr; }
#endif
    stretto_pin_init(g_rank);
    g_multi = provided == MPI_THREAD_MULTIPLE;
    int np; PMPI_Comm_size(MPI_COMM_WORLD, &np);
    REC(b, "init"); rb_printf(&b, ",\"np\":%d,\"thread\":%d", np, provided); rb_emit(&b);
    comm_register("world", MPI_COMM_WORLD, MPI_COMM_WORLD);   /* local id 0 */
    comm_register("self", MPI_COMM_WORLD, MPI_COMM_SELF);     /* local id 1 */
}
int MPI_Init(int *argc, char ***argv) { int rc = PMPI_Init(argc, argv); open_trace(MPI_THREAD_SINGLE); return rc; }
int MPI_Init_thread(int *argc, char ***argv, int req, int *prov) { int rc = PMPI_Init_thread(argc, argv, req, prov); open_trace(*prov); return rc; }
int MPI_Finalize(void) {
    stretto_pin_finalize();
    REC(b, "finalize"); if (g_truncated) rb_printf(&b, ",\"truncated\":1"); rb_emit(&b);
    pthread_mutex_lock(&g_mu);
    if (g_out && g_out != stderr) fclose(g_out);
    g_out = NULL;
    pthread_mutex_unlock(&g_mu);
    return PMPI_Finalize();
}
int MPI_Abort(MPI_Comm comm, int code) {
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
    stretto_pin_before_send(); \
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"send\""); send_fields(&b, MODE, count, dt, dst, tag, comm); rb_emit(&b); } \
    int rc = P##NAME(buf, count, dt, dst, tag, comm); \
    { REC(b, "send"); send_fields(&b, MODE, count, dt, dst, tag, comm); rb_emit(&b); } \
    return rc; }
BLOCKING_SEND(MPI_Send,  "std")
BLOCKING_SEND(MPI_Ssend, "ssend")
BLOCKING_SEND(MPI_Bsend, "bsend")
BLOCKING_SEND(MPI_Rsend, "rsend")
#define NONBLOCKING_SEND(NAME, MODE) \
int NAME(const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { \
    stretto_pin_before_send(); int rc = P##NAME(buf, count, dt, dst, tag, comm, req); \
    int id = rc == MPI_SUCCESS ? req_add(*req, 0, dt, 0) : -1; \
    REC(b, "send"); send_fields(&b, MODE, count, dt, dst, tag, comm); if (id >= 0) rb_printf(&b, ",\"req\":%d", id); rb_emit(&b); return rc; }
NONBLOCKING_SEND(MPI_Isend,  "isend")
NONBLOCKING_SEND(MPI_Issend, "issend")
NONBLOCKING_SEND(MPI_Ibsend, "ibsend")
NONBLOCKING_SEND(MPI_Irsend, "irsend")

static void unmodelled(const char *call);
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
#define PERSISTENT_SEND(NAME, MODE) \
int NAME(const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { \
    int rc = P##NAME(buf, count, dt, dst, tag, comm, req); if (rc != MPI_SUCCESS) return rc; \
    req_add(*req, 0, dt, 1); pers_add(*req, count, dt, dst, tag, comm, MODE, NULL); return rc; }
PERSISTENT_SEND(MPI_Send_init,  "isend")
PERSISTENT_SEND(MPI_Ssend_init, "issend")
PERSISTENT_SEND(MPI_Bsend_init, "ibsend")
PERSISTENT_SEND(MPI_Rsend_init, "irsend")
int MPI_Recv_init(void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Request *req) {
    int rc = PMPI_Recv_init(buf, count, dt, src, tag, comm, req); if (rc != MPI_SUCCESS) return rc;
    req_add(*req, 1, dt, 1); pers_add(*req, count, dt, src, tag, comm, "irecv", buf); return rc;
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
    req_remove(before);
    if (*slot == before) PMPI_Request_free(slot);
    *slot = original;
}
static void start_one(MPI_Request *rp) {
    MPI_Request r = *rp;
    int kind; MPI_Datatype dt;
    int id = req_start(r, &kind, &dt);
    pthread_mutex_lock(&g_mu); pinfo *pi = pers_find(r); pinfo copy; if (pi) copy = *pi; pthread_mutex_unlock(&g_mu);
    if (!pi) { unmodelled("Start of an untracked persistent request"); return; }   /* persistent collective, partitioned */
    if (kind == 0) {
        stretto_pin_before_send();
        REC(b, "send"); send_fields(&b, copy.mode, copy.count, copy.dt, copy.peer, copy.tag, copy.comm);
        rb_printf(&b, ",\"req\":%d,\"persistent\":1", id); rb_emit(&b);
    } else {
        int src = copy.peer, tag = copy.tag, widx = -1;
        stretto_pin_before_recv();
        int pinned = stretto_pin_recv(&src, &tag, copy.comm, &widx);
        if (pinned) {
            MPI_Request shadow;
            if (PMPI_Recv_init(copy.buf, copy.count, copy.dt, src, tag, copy.comm, &shadow) == MPI_SUCCESS) {
                pthread_mutex_lock(&g_mu);
                if (g_nreqs == g_capreqs) { g_capreqs = g_capreqs ? g_capreqs * 2 : 64; g_reqs = xrealloc(g_reqs, sizeof *g_reqs * g_capreqs); }
                g_reqs[g_nreqs++] = (reqent){shadow, id, 1, copy.dt, 1, 1, 0, widx + 1};
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
    }
}
int MPI_Start(MPI_Request *req) { start_one(req); return PMPI_Start(req); }
int MPI_Startall(int n, MPI_Request reqs[]) { for (int i = 0; i < n; i++) start_one(&reqs[i]); return PMPI_Startall(n, reqs); }

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
    stretto_pin_before_recv();
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
    stretto_pin_before_recv();
    int osrc = src, otag = tag, widx = -1;
    int pinned = stretto_pin_recv(&src, &tag, comm, &widx);
    int rc = PMPI_Irecv(buf, count, dt, src, tag, comm, req);
    int id = rc == MPI_SUCCESS ? req_add(*req, 1, dt, 0) : -1;
    if (pinned && id >= 0) req_set_pin(*req, widx);
    REC(b, "irecv"); recv_args(&b, osrc, otag, comm, count, dt, widx); if (id >= 0) rb_printf(&b, ",\"req\":%d", id);
    if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d,\"tag\":%d}", src, tag);
    rb_emit(&b);
    return rc;
}
static int sendrecv_common(int scount, MPI_Datatype sdt, int dst, int stag, int rcount, MPI_Datatype rdt, int *src, int *rtag, MPI_Comm comm, int *widx, int *reqid) {
    /* Modelled as Isend + Recv + Wait. */
    stretto_pin_before_send();
    pthread_mutex_lock(&g_mu); *reqid = g_next_req++; pthread_mutex_unlock(&g_mu);
    { REC(b, "send"); send_fields(&b, "isend", scount, sdt, dst, stag, comm); rb_printf(&b, ",\"req\":%d", *reqid); rb_emit(&b); }
    int osrc = *src, otag = *rtag;
    int pinned = stretto_pin_recv(src, rtag, comm, widx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"recv\""); recv_args(&b, osrc, otag, comm, rcount, rdt, *widx); rb_emit(&b); }
    return pinned;
}
static void sendrecv_finish(int rcount, MPI_Datatype rdt, int osrc, int otag, int src, int rtag, MPI_Comm comm, MPI_Status *st, int rc, int widx, int pinned, int reqid) {
    if (pinned && rc == MPI_SUCCESS) stretto_pin_matched(widx, st->MPI_SOURCE);
    { REC(b, "recv"); recv_args(&b, osrc, otag, comm, rcount, rdt, widx); status_fields(&b, st, rdt, rc == MPI_SUCCESS);
      if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d,\"tag\":%d}", src, rtag);
      rb_emit(&b); }
    { REC(b, "wait"); rb_printf(&b, ",\"kind\":\"wait\",\"reqs\":[%d],\"done\":[{\"req\":%d}]", reqid, reqid); rb_emit(&b); }
}
int MPI_Sendrecv(const void *sbuf, int scount, MPI_Datatype sdt, int dst, int stag,
                 void *rbuf, int rcount, MPI_Datatype rdt, int src, int rtag, MPI_Comm comm, MPI_Status *st) {
    int osrc = src, otag = rtag, widx = -1, reqid;
    int pinned = sendrecv_common(scount, sdt, dst, stag, rcount, rdt, &src, &rtag, comm, &widx, &reqid);
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Sendrecv(sbuf, scount, sdt, dst, stag, rbuf, rcount, rdt, src, rtag, comm, st);
    sendrecv_finish(rcount, rdt, osrc, otag, src, rtag, comm, st, rc, widx, pinned, reqid);
    return rc;
}
int MPI_Sendrecv_replace(void *buf, int count, MPI_Datatype dt, int dst, int stag, int src, int rtag, MPI_Comm comm, MPI_Status *st) {
    int osrc = src, otag = rtag, widx = -1, reqid;
    int pinned = sendrecv_common(count, dt, dst, stag, count, dt, &src, &rtag, comm, &widx, &reqid);
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Sendrecv_replace(buf, count, dt, dst, stag, src, rtag, comm, st);
    sendrecv_finish(count, dt, osrc, otag, src, rtag, comm, st, rc, widx, pinned, reqid);
    return rc;
}

/* -------------------------------------------------------------- completion */
static void idup_complete(MPI_Request before);
static void reqs_list(rec_t *b, int n, MPI_Request before[]) {
    rb_printf(b, ",\"reqs\":[");
    for (int i = 0; i < n; i++) { int id = req_id(before[i]); if (id >= 0) rb_printf(b, "%s%d", i ? "," : "", id); else rb_printf(b, "%snull", i ? "," : ""); }
    rb_printf(b, "]");
}
static void done_entry(rec_t *b, int first, MPI_Request before, MPI_Status *st, int ok) {
    if (before == MPI_REQUEST_NULL) return;
    pthread_mutex_lock(&g_mu);
    reqent *e = req_find_locked(before);
    int id = e ? e->id : -1, kind = e ? e->kind : -1, persistent = e ? e->persistent : 0, cancel_req = e ? e->cancel_req : 0;
    int pin1 = e ? e->pin1 : 0;
    MPI_Datatype dt = e ? e->dt : MPI_DATATYPE_NULL;
    if (e && !persistent) { *e = g_reqs[--g_nreqs]; }
    else if (e) { e->active = 0; e->cancel_req = 0; }
    pthread_mutex_unlock(&g_mu);
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
    int rc = PMPI_Request_get_status(req, flag, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"getstatus\",\"flag\":%d", *flag); reqs_list(&b, 1, &req); rb_printf(&b, ",\"done\":[]"); rb_emit_kind(&b, !*flag);
    return rc;
}
static void begin_wait(const char *kind, int n, MPI_Request reqs[]) {
    REC(b, "begin"); rb_printf(&b, ",\"call\":\"wait\",\"kind\":\"%s\"", kind); reqs_list(&b, n, reqs); rb_emit(&b);
}
int MPI_Wait(MPI_Request *req, MPI_Status *st) {
    MPI_Request before = *req; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    begin_wait("wait", 1, &before);
    int rc = PMPI_Wait(req, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"wait\""); reqs_list(&b, 1, &before); rb_printf(&b, ",\"done\":["); done_entry(&b, 1, before, st, rc == MPI_SUCCESS); rb_printf(&b, "]"); rb_emit(&b);
    restore_shadow(req, before);
    return rc;
}
#define COPY_REQS(n, reqs) MPI_Request *before = malloc(sizeof(MPI_Request) * ((n) > 0 ? (n) : 1)); memcpy(before, reqs, sizeof(MPI_Request) * (n))
#define OWN_STATUSES(n, sts) MPI_Status *mine = (sts == MPI_STATUSES_IGNORE) ? malloc(sizeof(MPI_Status) * ((n) > 0 ? (n) : 1)) : NULL; if (mine) sts = mine
int MPI_Waitall(int n, MPI_Request reqs[], MPI_Status sts[]) {
    COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    begin_wait("waitall", n, before);
    int rc = PMPI_Waitall(n, reqs, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"waitall\""); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    int first = 1; for (int i = 0; i < n; i++) if (before[i] != MPI_REQUEST_NULL) { done_entry(&b, first, before[i], &sts[i], rc == MPI_SUCCESS); first = 0; }
    rb_printf(&b, "]"); rb_emit(&b);
    for (int i = 0; i < n; i++) restore_shadow(&reqs[i], before[i]);
    free(before); free(mine);
    return rc;
}
int MPI_Waitany(int n, MPI_Request reqs[], int *idx, MPI_Status *st) {
    COPY_REQS(n, reqs); MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    begin_wait("waitany", n, before);
    int rc = PMPI_Waitany(n, reqs, idx, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"waitany\",\"idx\":%d", *idx); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    if (*idx != MPI_UNDEFINED) done_entry(&b, 1, before[*idx], st, rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit(&b);
    if (*idx != MPI_UNDEFINED) restore_shadow(&reqs[*idx], before[*idx]);
    free(before);
    return rc;
}
int MPI_Waitsome(int n, MPI_Request reqs[], int *outcount, int idx[], MPI_Status sts[]) {
    COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    begin_wait("waitsome", n, before);
    int rc = PMPI_Waitsome(n, reqs, outcount, idx, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"waitsome\""); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) done_entry(&b, i == 0, before[idx[i]], &sts[i], rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit(&b);
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) restore_shadow(&reqs[idx[i]], before[idx[i]]);
    free(before); free(mine);
    return rc;
}
int MPI_Test(MPI_Request *req, int *flag, MPI_Status *st) {
    MPI_Request before = *req; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Test(req, flag, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"test\",\"flag\":%d", *flag); reqs_list(&b, 1, &before); rb_printf(&b, ",\"done\":[");
    if (*flag) done_entry(&b, 1, before, st, rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    if (*flag) restore_shadow(req, before);
    return rc;
}
int MPI_Testall(int n, MPI_Request reqs[], int *flag, MPI_Status sts[]) {
    COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    int rc = PMPI_Testall(n, reqs, flag, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testall\",\"flag\":%d", *flag); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    if (*flag) { int first = 1; for (int i = 0; i < n; i++) if (before[i] != MPI_REQUEST_NULL) { done_entry(&b, first, before[i], &sts[i], rc == MPI_SUCCESS); first = 0; } }
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    if (*flag) for (int i = 0; i < n; i++) restore_shadow(&reqs[i], before[i]);
    free(before); free(mine);
    return rc;
}
int MPI_Testany(int n, MPI_Request reqs[], int *idx, int *flag, MPI_Status *st) {
    COPY_REQS(n, reqs); MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Testany(n, reqs, idx, flag, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testany\",\"flag\":%d,\"idx\":%d", *flag, *idx); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    if (*flag && *idx != MPI_UNDEFINED) done_entry(&b, 1, before[*idx], st, rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    if (*flag && *idx != MPI_UNDEFINED) restore_shadow(&reqs[*idx], before[*idx]);
    free(before);
    return rc;
}
int MPI_Testsome(int n, MPI_Request reqs[], int *outcount, int idx[], MPI_Status sts[]) {
    COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    int rc = PMPI_Testsome(n, reqs, outcount, idx, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testsome\",\"flag\":%d", *outcount > 0); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) done_entry(&b, i == 0, before[idx[i]], &sts[i], rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, *outcount == 0);
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) restore_shadow(&reqs[idx[i]], before[idx[i]]);
    free(before); free(mine);
    return rc;
}
int MPI_Request_free(MPI_Request *req) {
    int id = req_id(*req);
    REC(b, "reqfree"); rb_printf(&b, ",\"req\":%d", id); rb_emit(&b);
    MPI_Request original;
    if (shadow_pop(*req, &original)) {     /* freed while a pinned start was in flight: free both */
        req_remove(*req); PMPI_Request_free(req); *req = original;
    }
    req_remove(*req);
    pthread_mutex_lock(&g_mu); pers_remove(*req); pthread_mutex_unlock(&g_mu);
    return PMPI_Request_free(req);
}
int MPI_Cancel(MPI_Request *req) {
    pthread_mutex_lock(&g_mu); reqent *e = req_find_locked(*req); if (e) e->cancel_req = 1; pthread_mutex_unlock(&g_mu);
    REC(b, "cancel"); rb_printf(&b, ",\"req\":%d", req_id(*req)); rb_emit(&b);
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
static void probe_rec(const char *kind, int src, int tag, MPI_Comm comm, int flag, MPI_Status *st, int rc, int pidx, int pinned, int psrc) {
    REC(b, "probe");
    rb_printf(&b, ",\"kind\":\"%s\"", kind); rb_src(&b, src); rb_tag(&b, tag); rb_printf(&b, ",\"comm\":%d,\"flag\":%d", comm_id(comm), flag);
    if (flag && pidx >= 0) rb_printf(&b, ",\"pidx\":%d", pidx);
    if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d}", psrc);
    if (flag && rc == MPI_SUCCESS && st) { int cnt = -1; PMPI_Get_count(st, MPI_BYTE, &cnt); rb_printf(&b, ",\"msrc\":%d,\"mtag\":%d,\"mbytes\":%d", st->MPI_SOURCE, st->MPI_TAG, cnt); }
    if (flag) stretto_pin_probe_commit(pidx, rc == MPI_SUCCESS && st ? st->MPI_SOURCE : -1, rc == MPI_SUCCESS && st ? st->MPI_TAG : -1);
    rb_emit_kind(&b, !flag);
}
int MPI_Probe(int src, int tag, MPI_Comm comm, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = stretto_pin_probe(&src, &tag, comm, &pidx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"probe\",\"kind\":\"probe\""); rb_src(&b, osrc); rb_tag(&b, otag); rb_printf(&b, ",\"comm\":%d", comm_id(comm)); rb_emit(&b); }
    int rc = PMPI_Probe(src, tag, comm, st); probe_rec("probe", osrc, otag, comm, 1, st, rc, pidx, pinned, src); return rc;
}
int MPI_Iprobe(int src, int tag, MPI_Comm comm, int *flag, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = stretto_pin_probe(&src, &tag, comm, &pidx);
    int rc = PMPI_Iprobe(src, tag, comm, flag, st); probe_rec("iprobe", osrc, otag, comm, *flag, st, rc, pidx, pinned, src); return rc;
}
int MPI_Mprobe(int src, int tag, MPI_Comm comm, MPI_Message *msg, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = stretto_pin_probe(&src, &tag, comm, &pidx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"probe\",\"kind\":\"mprobe\""); rb_src(&b, osrc); rb_tag(&b, otag); rb_printf(&b, ",\"comm\":%d", comm_id(comm)); rb_emit(&b); }
    int rc = PMPI_Mprobe(src, tag, comm, msg, st); if (rc == MPI_SUCCESS) msg_add(*msg, comm, st);
    probe_rec("mprobe", osrc, otag, comm, 1, st, rc, pidx, pinned, src); return rc;
}
int MPI_Improbe(int src, int tag, MPI_Comm comm, int *flag, MPI_Message *msg, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = stretto_pin_probe(&src, &tag, comm, &pidx);
    int rc = PMPI_Improbe(src, tag, comm, flag, msg, st); if (rc == MPI_SUCCESS && *flag) msg_add(*msg, comm, st);
    probe_rec("improbe", osrc, otag, comm, *flag, st, rc, pidx, pinned, src); return rc;
}
/* Mrecv/Imrecv consume the probed message: recorded as a receive whose source/tag are the
   message's (known from the probe's status), so the loader matches it by FIFO. */
int MPI_Mrecv(void *buf, int count, MPI_Datatype dt, MPI_Message *msg, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int comm = 0, src = -1, tag = -1; msg_take(*msg, &comm, &src, &tag);
    int rc = PMPI_Mrecv(buf, count, dt, msg, st);
    if (rc == MPI_SUCCESS) { src = st->MPI_SOURCE; tag = st->MPI_TAG; }
    REC(b, "recv"); rb_printf(&b, ",\"src\":%d,\"tag\":%d,\"comm\":%d,\"mrecv\":1", src, tag, comm); rb_type(&b, count, dt);
    status_fields(&b, st, dt, rc == MPI_SUCCESS); rb_emit(&b);
    return rc;
}
int MPI_Imrecv(void *buf, int count, MPI_Datatype dt, MPI_Message *msg, MPI_Request *req) {
    int comm = 0, src = -1, tag = -1; msg_take(*msg, &comm, &src, &tag);
    int rc = PMPI_Imrecv(buf, count, dt, msg, req);
    int id = rc == MPI_SUCCESS ? req_add(*req, 1, dt, 0) : -1;
    REC(b, "irecv"); rb_printf(&b, ",\"src\":%d,\"tag\":%d,\"comm\":%d,\"mrecv\":1", src, tag, comm); rb_type(&b, count, dt);
    if (id >= 0) rb_printf(&b, ",\"req\":%d", id);
    rb_emit(&b);
    return rc;
}

/* ------------------------------------------------------------ collectives */
static void coll_rec(const char *name, MPI_Comm comm, int root, int count, MPI_Datatype dt, int reqid, int begin) {
    REC(b, begin ? "begin" : "coll");
    if (begin) rb_printf(&b, ",\"call\":\"coll\"");
    rb_printf(&b, ",\"name\":\"%s\",\"comm\":%d,\"root\":%d", name, comm_id(comm), root); rb_type(&b, count, dt);
    if (reqid >= 0) rb_printf(&b, ",\"req\":%d", reqid);
    rb_emit(&b);
}
#define BLK(NAME, name, root, count, dt, call) \
    coll_rec(name, comm, root, count, dt, -1, 1); int rc = call; coll_rec(name, comm, root, count, dt, -1, 0); return rc;
#define NBL(NAME, name, root, count, dt, call) \
    int rc = call; coll_rec(name, comm, root, count, dt, rc == MPI_SUCCESS ? req_add(*req, 2, dt, 0) : -1, 0); return rc;

int MPI_Barrier(MPI_Comm comm) { BLK(MPI_Barrier, "barrier", -1, 0, MPI_DATATYPE_NULL, PMPI_Barrier(comm)) }
int MPI_Bcast(void *b, int n, MPI_Datatype dt, int root, MPI_Comm comm) { BLK(MPI_Bcast, "bcast", root, n, dt, PMPI_Bcast(b, n, dt, root, comm)) }
int MPI_Reduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, int root, MPI_Comm comm) { BLK(MPI_Reduce, "reduce", root, n, dt, PMPI_Reduce(s, r, n, dt, op, root, comm)) }
int MPI_Allreduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) { BLK(MPI_Allreduce, "allreduce", -1, n, dt, PMPI_Allreduce(s, r, n, dt, op, comm)) }
int MPI_Gather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm) { BLK(MPI_Gather, "gather", root, sn, sdt, PMPI_Gather(s, sn, sdt, r, rn, rdt, root, comm)) }
int MPI_Gatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, int root, MPI_Comm comm) { BLK(MPI_Gatherv, "gatherv", root, sn, sdt, PMPI_Gatherv(s, sn, sdt, r, rc_, d, rdt, root, comm)) }
int MPI_Scatter(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm) { BLK(MPI_Scatter, "scatter", root, rn, rdt, PMPI_Scatter(s, sn, sdt, r, rn, rdt, root, comm)) }
int MPI_Scatterv(const void *s, const int *sc, const int *d, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm) { BLK(MPI_Scatterv, "scatterv", root, rn, rdt, PMPI_Scatterv(s, sc, d, sdt, r, rn, rdt, root, comm)) }
int MPI_Allgather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm) { BLK(MPI_Allgather, "allgather", -1, sn, sdt, PMPI_Allgather(s, sn, sdt, r, rn, rdt, comm)) }
int MPI_Allgatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, MPI_Comm comm) { BLK(MPI_Allgatherv, "allgatherv", -1, sn, sdt, PMPI_Allgatherv(s, sn, sdt, r, rc_, d, rdt, comm)) }
int MPI_Alltoall(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm) { BLK(MPI_Alltoall, "alltoall", -1, sn, sdt, PMPI_Alltoall(s, sn, sdt, r, rn, rdt, comm)) }
int MPI_Alltoallv(const void *s, const int *sc, const int *sd, MPI_Datatype sdt, void *r, const int *rc_, const int *rd, MPI_Datatype rdt, MPI_Comm comm) { BLK(MPI_Alltoallv, "alltoallv", -1, 0, sdt, PMPI_Alltoallv(s, sc, sd, sdt, r, rc_, rd, rdt, comm)) }
int MPI_Alltoallw(const void *s, const int *sc, const int *sd, const MPI_Datatype *sdt, void *r, const int *rc_, const int *rd, const MPI_Datatype *rdt, MPI_Comm comm) { BLK(MPI_Alltoallw, "alltoallw", -1, 0, MPI_DATATYPE_NULL, PMPI_Alltoallw(s, sc, sd, sdt, r, rc_, rd, rdt, comm)) }
int MPI_Reduce_scatter(const void *s, void *r, const int *rc_, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) { BLK(MPI_Reduce_scatter, "reduce_scatter", -1, 0, dt, PMPI_Reduce_scatter(s, r, rc_, dt, op, comm)) }
int MPI_Reduce_scatter_block(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) { BLK(MPI_Reduce_scatter_block, "reduce_scatter_block", -1, n, dt, PMPI_Reduce_scatter_block(s, r, n, dt, op, comm)) }
int MPI_Scan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) { BLK(MPI_Scan, "scan", -1, n, dt, PMPI_Scan(s, r, n, dt, op, comm)) }
int MPI_Exscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) { BLK(MPI_Exscan, "exscan", -1, n, dt, PMPI_Exscan(s, r, n, dt, op, comm)) }

int MPI_Ibarrier(MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ibarrier, "ibarrier", -1, 0, MPI_DATATYPE_NULL, PMPI_Ibarrier(comm, req)) }
int MPI_Ibcast(void *b, int n, MPI_Datatype dt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ibcast, "ibcast", root, n, dt, PMPI_Ibcast(b, n, dt, root, comm, req)) }
int MPI_Ireduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ireduce, "ireduce", root, n, dt, PMPI_Ireduce(s, r, n, dt, op, root, comm, req)) }
int MPI_Iallreduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iallreduce, "iallreduce", -1, n, dt, PMPI_Iallreduce(s, r, n, dt, op, comm, req)) }
int MPI_Igather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Igather, "igather", root, sn, sdt, PMPI_Igather(s, sn, sdt, r, rn, rdt, root, comm, req)) }
int MPI_Igatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Igatherv, "igatherv", root, sn, sdt, PMPI_Igatherv(s, sn, sdt, r, rc_, d, rdt, root, comm, req)) }
int MPI_Iscatter(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iscatter, "iscatter", root, rn, rdt, PMPI_Iscatter(s, sn, sdt, r, rn, rdt, root, comm, req)) }
int MPI_Iscatterv(const void *s, const int *sc, const int *d, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iscatterv, "iscatterv", root, rn, rdt, PMPI_Iscatterv(s, sc, d, sdt, r, rn, rdt, root, comm, req)) }
int MPI_Iallgather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iallgather, "iallgather", -1, sn, sdt, PMPI_Iallgather(s, sn, sdt, r, rn, rdt, comm, req)) }
int MPI_Iallgatherv(const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iallgatherv, "iallgatherv", -1, sn, sdt, PMPI_Iallgatherv(s, sn, sdt, r, rc_, d, rdt, comm, req)) }
int MPI_Ialltoall(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ialltoall, "ialltoall", -1, sn, sdt, PMPI_Ialltoall(s, sn, sdt, r, rn, rdt, comm, req)) }
int MPI_Ialltoallv(const void *s, const int *sc, const int *sd, MPI_Datatype sdt, void *r, const int *rc_, const int *rd, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ialltoallv, "ialltoallv", -1, 0, sdt, PMPI_Ialltoallv(s, sc, sd, sdt, r, rc_, rd, rdt, comm, req)) }
int MPI_Ialltoallw(const void *s, const int *sc, const int *sd, const MPI_Datatype *sdt, void *r, const int *rc_, const int *rd, const MPI_Datatype *rdt, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ialltoallw, "ialltoallw", -1, 0, MPI_DATATYPE_NULL, PMPI_Ialltoallw(s, sc, sd, sdt, r, rc_, rd, rdt, comm, req)) }
int MPI_Ireduce_scatter(const void *s, void *r, const int *rc_, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ireduce_scatter, "ireduce_scatter", -1, 0, dt, PMPI_Ireduce_scatter(s, r, rc_, dt, op, comm, req)) }
int MPI_Ireduce_scatter_block(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ireduce_scatter_block, "ireduce_scatter_block", -1, n, dt, PMPI_Ireduce_scatter_block(s, r, n, dt, op, comm, req)) }
int MPI_Iscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iscan, "iscan", -1, n, dt, PMPI_Iscan(s, r, n, dt, op, comm, req)) }
int MPI_Iexscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iexscan, "iexscan", -1, n, dt, PMPI_Iexscan(s, r, n, dt, op, comm, req)) }

/* ---------------------------------------------------------- communicators */
#define REG(how, parent, n) do { if (rc == MPI_SUCCESS) comm_register(how, parent, *(n)); } while (0)
int MPI_Comm_dup(MPI_Comm c, MPI_Comm *n) { int rc = PMPI_Comm_dup(c, n); REG("dup", c, n); return rc; }
int MPI_Comm_split(MPI_Comm c, int color, int key, MPI_Comm *n) { int rc = PMPI_Comm_split(c, color, key, n); REG("split", c, n); return rc; }
int MPI_Comm_split_type(MPI_Comm c, int type, int key, MPI_Info info, MPI_Comm *n) { int rc = PMPI_Comm_split_type(c, type, key, info, n); REG("split_type", c, n); return rc; }
int MPI_Comm_create(MPI_Comm c, MPI_Group g, MPI_Comm *n) { int rc = PMPI_Comm_create(c, g, n); REG("create", c, n); return rc; }
int MPI_Comm_create_group(MPI_Comm c, MPI_Group g, int tag, MPI_Comm *n) { int rc = PMPI_Comm_create_group(c, g, tag, n); REG("create_group", c, n); return rc; }
int MPI_Cart_create(MPI_Comm c, int nd, const int *dims, const int *periods, int reorder, MPI_Comm *n) { int rc = PMPI_Cart_create(c, nd, dims, periods, reorder, n); REG("cart", c, n); return rc; }
int MPI_Cart_sub(MPI_Comm c, const int *remain, MPI_Comm *n) { int rc = PMPI_Cart_sub(c, remain, n); REG("cart_sub", c, n); return rc; }
int MPI_Graph_create(MPI_Comm c, int nn, const int *idx, const int *edges, int reorder, MPI_Comm *n) { int rc = PMPI_Graph_create(c, nn, idx, edges, reorder, n); REG("graph", c, n); return rc; }
int MPI_Dist_graph_create(MPI_Comm c, int n_, const int *sources, const int *degrees, const int *dests, const int *weights, MPI_Info info, int reorder, MPI_Comm *n) {
    int rc = PMPI_Dist_graph_create(c, n_, sources, degrees, dests, weights, info, reorder, n); REG("dist_graph", c, n); return rc; }
int MPI_Dist_graph_create_adjacent(MPI_Comm c, int indeg, const int *sources, const int *sw, int outdeg, const int *dests, const int *dw, MPI_Info info, int reorder, MPI_Comm *n) {
    int rc = PMPI_Dist_graph_create_adjacent(c, indeg, sources, sw, outdeg, dests, dw, info, reorder, n); REG("dist_graph_adjacent", c, n); return rc; }
int MPI_Intercomm_create(MPI_Comm local, int lleader, MPI_Comm peer, int rleader, int tag, MPI_Comm *n) {
    int rc = PMPI_Intercomm_create(local, lleader, peer, rleader, tag, n); REG("intercomm", local, n); return rc; }
int MPI_Intercomm_merge(MPI_Comm inter, int high, MPI_Comm *n) { int rc = PMPI_Intercomm_merge(inter, high, n); REG("merge", inter, n); return rc; }
int MPI_Comm_dup_with_info(MPI_Comm c, MPI_Info info, MPI_Comm *n) { int rc = PMPI_Comm_dup_with_info(c, info, n); REG("dup", c, n); return rc; }

/* ------------------------------------------------------- outside the model
   Communication the analysis does not model: one-sided windows, neighbourhood collectives,
   dynamic processes, generalised requests and the MPI-4 calls below. Each call writes an
   "unmodelled" record, so that the loader knows the trace is not a complete record of the
   run's communication and the analysis reports observed findings only (the premise of
   theorem T1 in docs/model.md). */
static void unmodelled(const char *call) { REC(b, "unmodelled"); rb_printf(&b, ",\"call\":\"%s\"", call); rb_emit(&b); }
#define UNMODELLED(name, call) do { unmodelled(name); return call; } while (0)
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
#if MPI_VERSION >= 4
int MPI_Isendrecv(const void *sb, int sn, MPI_Datatype sdt, int dst, int stag, void *rb, int rn, MPI_Datatype rdt, int src, int rtag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Isendrecv", PMPI_Isendrecv(sb, sn, sdt, dst, stag, rb, rn, rdt, src, rtag, comm, req)); }
int MPI_Isendrecv_replace(void *buf, int n, MPI_Datatype dt, int dst, int stag, int src, int rtag, MPI_Comm comm, MPI_Request *req) { UNMODELLED("Isendrecv_replace", PMPI_Isendrecv_replace(buf, n, dt, dst, stag, src, rtag, comm, req)); }
int MPI_Psend_init(const void *buf, int parts, MPI_Count n, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Info info, MPI_Request *req) { UNMODELLED("Psend_init", PMPI_Psend_init(buf, parts, n, dt, dst, tag, comm, info, req)); }
int MPI_Precv_init(void *buf, int parts, MPI_Count n, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Info info, MPI_Request *req) { UNMODELLED("Precv_init", PMPI_Precv_init(buf, parts, n, dt, src, tag, comm, info, req)); }
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
    int rc = PMPI_Comm_idup(c, n, req);
    if (rc != MPI_SUCCESS) return rc;
    req_add(*req, 3, MPI_DATATYPE_NULL, 0);
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
int MPI_Comm_free(MPI_Comm *c) { comm_release("free", c); return PMPI_Comm_free(c); }
int MPI_Comm_disconnect(MPI_Comm *c) { comm_release("free", c); return PMPI_Comm_disconnect(c); }

/* ------------------------------------------------------------- buffering */
int MPI_Buffer_attach(void *buf, int size) { REC(b, "bufattach"); rb_printf(&b, ",\"size\":%d", size); rb_emit(&b); return PMPI_Buffer_attach(buf, size); }
int MPI_Buffer_detach(void *buf, int *size) { int rc = PMPI_Buffer_detach(buf, size); REC(b, "bufdetach"); rb_emit(&b); return rc; }
