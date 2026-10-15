/* Rematch PMPI tracer. One JSON record per MPI event, per rank, in
   $REMATCH_TRACE_DIR/rank-<r>.jsonl (default ./traces). Builds with the system MPI.

   Compile with -DREMATCH_PIN_ONLY to get the replay library: same interposition, same
   wildcard counting and pinning, no file output.

   Record kinds (all carry "r" and "seq"):
     init      np, thread (provided level)
     begin     call=<recv|wait|probe|coll|send> + the call's arguments; emitted BEFORE a
               potentially blocking call. The loader drops a begin followed by its
               record; an unpaired begin means the rank was killed inside that call.
     send      mode(std|ssend|bsend|rsend|isend|issend|ibsend|irsend), dst, tag, comm,
               count, tsize, tsig, [req], [procnull], [persistent]
     recv      src, tag, comm, count, tsize, tsig, [anysrc], [anytag], [procnull], [widx],
               msrc, mtag, mcount (null when unknown), [cancelled], [pinned]
     irecv     same arguments plus req ([mrecv] on both: Mrecv/Imrecv of a probed message)
     wait      kind(wait|waitall|waitany|waitsome|test|testall|testany|testsome), [idx],
               reqs:[ids], done:[{req, [msrc, mtag, mcount, cancelled]}]
     pollrepeat n: the previous record (a failed Test* or Iprobe/Improbe) repeated n more
               times; busy-polling loops are summarised instead of written per iteration
     cancel    req
     probe     kind(probe|iprobe|mprobe|improbe), src, tag, comm, [anysrc], [anytag], flag,
               [msrc, mtag, mbytes]
     coll      name, comm, root(-1 if none), count, tsize, [req]
     comm      name, parent(local id), id(local id), ranks:[world ranks in comm-rank order],
               gord (creation ordinal of that group)
     bufattach size | bufdetach
     abort     code
     finalize  [truncated]
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
#include "../replay/pin.h"

/* ------------------------------------------------------------------ state */
static FILE *g_out = NULL;
static int g_rank = -1;
static long g_seq __attribute__((unused)) = 0;
static int g_truncated = 0;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;

typedef struct { MPI_Request req; int id; int kind; MPI_Datatype dt; int persistent; int active; } reqent; /* kind: 0 send, 1 recv, 2 coll */
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
        b->p = realloc(b->p, b->cap);
    }
}
/* Busy-polling loops (Iprobe/Improbe/Test* returning flag 0) would write one record per
   iteration, gigabytes for a loop that spins until a timeout. A failed poll identical to
   the previous record is not written; the run of repeats is summarised by one
   {"op":"pollrepeat","n":k} record before the next different record. */
#ifndef REMATCH_PIN_ONLY
static char *g_poll_body = NULL;
static long g_poll_rep = 0;

static void flush_poll_locked(void) {
    if (g_poll_rep > 0 && g_out)
        fprintf(g_out, "{\"r\":%d,\"seq\":%ld,\"op\":\"pollrepeat\",\"n\":%ld}\n", g_rank, g_seq++, g_poll_rep);
    g_poll_rep = 0;
}
#endif

static void rb_emit_kind(rec_t *b, int failed_poll) {
#ifndef REMATCH_PIN_ONLY
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
        char head[64];
        snprintf(head, sizeof head, "{\"r\":%d,\"seq\":%ld,", g_rank, g_seq++);
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
static void tsig(MPI_Datatype dt, char *out, size_t n) {
    if (dt == MPI_DATATYPE_NULL) { snprintf(out, n, "null"); return; }
    int ni, na, nd, comb;
    if (PMPI_Type_get_envelope(dt, &ni, &na, &nd, &comb) != MPI_SUCCESS) { snprintf(out, n, "?"); return; }
    if (comb == MPI_COMBINER_NAMED) {
        char name[MPI_MAX_OBJECT_NAME]; int len = 0;
        PMPI_Type_get_name(dt, name, &len);
        snprintf(out, n, "%s", len ? name : "named");
    } else {
        MPI_Aint lb = 0, ext = 0; int sz = 0;
        PMPI_Type_get_extent(dt, &lb, &ext); PMPI_Type_size(dt, &sz);
        snprintf(out, n, "derived:c%d/s%d/e%ld/lb%ld", comb, sz, (long)ext, (long)lb);
    }
}
static void rb_type(rec_t *b, int count, MPI_Datatype dt) {
    char sig[128]; tsig(dt, sig, sizeof sig);
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
    if (g_ncomms == g_capcomms) { g_capcomms = g_capcomms ? g_capcomms * 2 : 16; g_comms = realloc(g_comms, sizeof *g_comms * g_capcomms); }
    g_comms[g_ncomms].c = c; g_comms[g_ncomms].id = g_next_comm++; g_ncomms++;
    return g_comms[g_ncomms - 1].id;
}
static void comm_forget(MPI_Comm c) {
    for (int i = 0; i < g_ncomms; i++) if (g_comms[i].c == c) { g_comms[i] = g_comms[--g_ncomms]; return; }
}
static int world_ranks(MPI_Comm c, int **out) {
    MPI_Group g, w; int n;
    PMPI_Comm_group(c, &g); PMPI_Comm_group(MPI_COMM_WORLD, &w); PMPI_Group_size(g, &n);
    int *loc = malloc(sizeof(int) * (n > 0 ? n : 1)), *wr = malloc(sizeof(int) * (n > 0 ? n : 1));
    for (int i = 0; i < n; i++) loc[i] = i;
    PMPI_Group_translate_ranks(g, n, loc, w, wr);
    PMPI_Group_free(&g); PMPI_Group_free(&w);
    free(loc); *out = wr; return n;
}
static int group_ordinal(const char *key) {
    for (int i = 0; i < g_ngroups; i++) if (strcmp(g_groups[i].key, key) == 0) return g_groups[i].count++;
    if (g_ngroups == g_capgroups) { g_capgroups = g_capgroups ? g_capgroups * 2 : 16; g_groups = realloc(g_groups, sizeof *g_groups * g_capgroups); }
    g_groups[g_ngroups].key = strdup(key); g_groups[g_ngroups].count = 1; g_ngroups++;
    return 0;
}
/* Register a communicator and emit its comm record (ranks + group ordinal). */
static int comm_register(const char *how, MPI_Comm parent, MPI_Comm c) {
    if (c == MPI_COMM_NULL) return -1;
    pthread_mutex_lock(&g_mu);
    int id = comm_lookup(c);
    if (id < 0) id = comm_add(c);
    int pid = comm_lookup(parent);
    pthread_mutex_unlock(&g_mu);
    int *wr; int n = world_ranks(c, &wr);
    rec_t kb; rb_init(&kb);
    for (int i = 0; i < n; i++) rb_printf(&kb, "%s%d", i ? "," : "", wr[i]);
    pthread_mutex_lock(&g_mu);
    int gord = group_ordinal(kb.p);
    pthread_mutex_unlock(&g_mu);
    REC(b, "comm");
    rb_printf(&b, ",\"name\":\"%s\",\"parent\":%d,\"id\":%d,\"ranks\":[%s],\"gord\":%d", how, pid, id, kb.p, gord);
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
        g_reqs[i].id = g_next_req++; g_reqs[i].kind = kind; g_reqs[i].dt = dt; g_reqs[i].persistent = persistent; g_reqs[i].active = !persistent;
        int id = g_reqs[i].id; pthread_mutex_unlock(&g_mu); return id;
    }
    if (g_nreqs == g_capreqs) { g_capreqs = g_capreqs ? g_capreqs * 2 : 64; g_reqs = realloc(g_reqs, sizeof *g_reqs * g_capreqs); }
    int id = g_next_req++;
    g_reqs[g_nreqs].req = r; g_reqs[g_nreqs].id = id; g_reqs[g_nreqs].kind = kind; g_reqs[g_nreqs].dt = dt;
    g_reqs[g_nreqs].persistent = persistent; g_reqs[g_nreqs].active = !persistent; g_nreqs++;
    pthread_mutex_unlock(&g_mu);
    return id;
}
static reqent *req_find_locked(MPI_Request r) {
    if (r == MPI_REQUEST_NULL) return NULL;
    for (int i = 0; i < g_nreqs; i++) if (g_reqs[i].req == r) return &g_reqs[i];
    return NULL;
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
#ifndef REMATCH_PIN_ONLY
    const char *dir = getenv("REMATCH_TRACE_DIR");
    if (!dir) dir = "traces";
    mkdir(dir, 0777);
    char path[1024];
    snprintf(path, sizeof path, "%s/rank-%d.jsonl", dir, g_rank);
    g_out = fopen(path, "w");
    if (!g_out) { perror("rematch tracer: fopen"); g_out = stderr; }
#endif
    rematch_pin_init(g_rank);
    int np; PMPI_Comm_size(MPI_COMM_WORLD, &np);
    REC(b, "init"); rb_printf(&b, ",\"np\":%d,\"thread\":%d", np, provided); rb_emit(&b);
    comm_register("world", MPI_COMM_WORLD, MPI_COMM_WORLD);   /* local id 0 */
    comm_register("self", MPI_COMM_WORLD, MPI_COMM_SELF);     /* local id 1 */
}
int MPI_Init(int *argc, char ***argv) { int rc = PMPI_Init(argc, argv); open_trace(MPI_THREAD_SINGLE); return rc; }
int MPI_Init_thread(int *argc, char ***argv, int req, int *prov) { int rc = PMPI_Init_thread(argc, argv, req, prov); open_trace(*prov); return rc; }
int MPI_Finalize(void) {
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
    rematch_pin_before_send(); \
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
    rematch_pin_before_send(); int rc = P##NAME(buf, count, dt, dst, tag, comm, req); \
    int id = req_add(*req, 0, dt, 0); \
    REC(b, "send"); send_fields(&b, MODE, count, dt, dst, tag, comm); rb_printf(&b, ",\"req\":%d", id); rb_emit(&b); return rc; }
NONBLOCKING_SEND(MPI_Isend,  "isend")
NONBLOCKING_SEND(MPI_Issend, "issend")
NONBLOCKING_SEND(MPI_Ibsend, "ibsend")
NONBLOCKING_SEND(MPI_Irsend, "irsend")

/* persistent: *_init registers; Start emits the operation */
typedef struct { int count; MPI_Datatype dt; int peer, tag; MPI_Comm comm; const char *mode; } pinfo;
static pinfo *g_pinfo = NULL; static MPI_Request *g_preq = NULL; static int g_npers = 0, g_cappers = 0;
static void pers_add(MPI_Request r, int count, MPI_Datatype dt, int peer, int tag, MPI_Comm comm, const char *mode) {
    pthread_mutex_lock(&g_mu);
    if (g_npers == g_cappers) { g_cappers = g_cappers ? g_cappers * 2 : 16; g_pinfo = realloc(g_pinfo, sizeof *g_pinfo * g_cappers); g_preq = realloc(g_preq, sizeof *g_preq * g_cappers); }
    g_preq[g_npers] = r; g_pinfo[g_npers] = (pinfo){count, dt, peer, tag, comm, mode}; g_npers++;
    pthread_mutex_unlock(&g_mu);
}
static pinfo *pers_find(MPI_Request r) {
    for (int i = 0; i < g_npers; i++) if (g_preq[i] == r) return &g_pinfo[i];
    return NULL;
}
#define PERSISTENT_SEND(NAME, MODE) \
int NAME(const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { \
    int rc = P##NAME(buf, count, dt, dst, tag, comm, req); req_add(*req, 0, dt, 1); pers_add(*req, count, dt, dst, tag, comm, MODE); return rc; }
PERSISTENT_SEND(MPI_Send_init,  "isend")
PERSISTENT_SEND(MPI_Ssend_init, "issend")
PERSISTENT_SEND(MPI_Bsend_init, "ibsend")
PERSISTENT_SEND(MPI_Rsend_init, "irsend")
int MPI_Recv_init(void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Request *req) {
    int rc = PMPI_Recv_init(buf, count, dt, src, tag, comm, req); req_add(*req, 1, dt, 1); pers_add(*req, count, dt, src, tag, comm, "irecv"); return rc;
}
static void start_one(MPI_Request r) {
    int kind; MPI_Datatype dt;
    int id = req_start(r, &kind, &dt);
    pthread_mutex_lock(&g_mu); pinfo *pi = pers_find(r); pinfo copy; if (pi) copy = *pi; pthread_mutex_unlock(&g_mu);
    if (!pi) return;
    if (kind == 0) {
        rematch_pin_before_send();
        REC(b, "send"); send_fields(&b, copy.mode, copy.count, copy.dt, copy.peer, copy.tag, copy.comm);
        rb_printf(&b, ",\"req\":%d,\"persistent\":1", id); rb_emit(&b);
    } else {
        int src = copy.peer, tag = copy.tag, widx = -1;
        rematch_pin_before_recv();
        int pinned = rematch_pin_recv(&src, &tag, &widx);   /* NOTE: cannot change the request's source */
        REC(b, "irecv"); rb_src(&b, copy.peer); rb_tag(&b, copy.tag); rb_printf(&b, ",\"comm\":%d", comm_id(copy.comm));
        rb_type(&b, copy.count, copy.dt); rb_printf(&b, ",\"req\":%d,\"persistent\":1", id);
        if (widx >= 0) rb_printf(&b, ",\"widx\":%d", widx);
        if (pinned) rb_printf(&b, ",\"pin_ignored\":1");
        rb_emit(&b);
    }
}
int MPI_Start(MPI_Request *req) { start_one(*req); return PMPI_Start(req); }
int MPI_Startall(int n, MPI_Request reqs[]) { for (int i = 0; i < n; i++) start_one(reqs[i]); return PMPI_Startall(n, reqs); }

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
    rematch_pin_before_recv();
    int osrc = src, otag = tag, widx = -1;
    int pinned = rematch_pin_recv(&src, &tag, &widx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"recv\""); recv_args(&b, osrc, otag, comm, count, dt, widx); rb_emit(&b); }
    MPI_Status mine;
    if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Recv(buf, count, dt, src, tag, comm, st);
    REC(b, "recv"); recv_args(&b, osrc, otag, comm, count, dt, widx);
    status_fields(&b, st, dt, rc == MPI_SUCCESS);
    if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d,\"tag\":%d}", src, tag);
    rb_emit(&b);
    return rc;
}
int MPI_Irecv(void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Request *req) {
    rematch_pin_before_recv();
    int osrc = src, otag = tag, widx = -1;
    int pinned = rematch_pin_recv(&src, &tag, &widx);
    int rc = PMPI_Irecv(buf, count, dt, src, tag, comm, req);
    int id = req_add(*req, 1, dt, 0);
    REC(b, "irecv"); recv_args(&b, osrc, otag, comm, count, dt, widx); rb_printf(&b, ",\"req\":%d", id);
    if (pinned) rb_printf(&b, ",\"pinned\":{\"src\":%d,\"tag\":%d}", src, tag);
    rb_emit(&b);
    return rc;
}
static int sendrecv_common(int scount, MPI_Datatype sdt, int dst, int stag, int rcount, MPI_Datatype rdt, int *src, int *rtag, MPI_Comm comm, int *widx, int *reqid) {
    /* Modelled as Isend + Recv + Wait. */
    rematch_pin_before_send();
    pthread_mutex_lock(&g_mu); *reqid = g_next_req++; pthread_mutex_unlock(&g_mu);
    { REC(b, "send"); send_fields(&b, "isend", scount, sdt, dst, stag, comm); rb_printf(&b, ",\"req\":%d", *reqid); rb_emit(&b); }
    int osrc = *src, otag = *rtag;
    int pinned = rematch_pin_recv(src, rtag, widx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"recv\""); recv_args(&b, osrc, otag, comm, rcount, rdt, *widx); rb_emit(&b); }
    return pinned;
}
static void sendrecv_finish(int rcount, MPI_Datatype rdt, int osrc, int otag, int src, int rtag, MPI_Comm comm, MPI_Status *st, int rc, int widx, int pinned, int reqid) {
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
static void reqs_list(rec_t *b, int n, MPI_Request before[]) {
    rb_printf(b, ",\"reqs\":[");
    for (int i = 0; i < n; i++) { int id = req_id(before[i]); if (id >= 0) rb_printf(b, "%s%d", i ? "," : "", id); else rb_printf(b, "%snull", i ? "," : ""); }
    rb_printf(b, "]");
}
static void done_entry(rec_t *b, int first, MPI_Request before, MPI_Status *st, int ok) {
    if (before == MPI_REQUEST_NULL) return;
    pthread_mutex_lock(&g_mu);
    reqent *e = req_find_locked(before);
    int id = e ? e->id : -1, kind = e ? e->kind : -1, persistent = e ? e->persistent : 0; MPI_Datatype dt = e ? e->dt : MPI_DATATYPE_NULL;
    if (e && !persistent) { *e = g_reqs[--g_nreqs]; }
    else if (e) e->active = 0;
    pthread_mutex_unlock(&g_mu);
    if (id < 0) { rb_printf(b, "%s{\"req\":null}", first ? "" : ","); return; }
    rb_printf(b, "%s{\"req\":%d", first ? "" : ",", id);
    if (kind == 1) status_fields(b, st, dt, ok);
    rb_printf(b, "}");
}
static void begin_wait(const char *kind, int n, MPI_Request reqs[]) {
    REC(b, "begin"); rb_printf(&b, ",\"call\":\"wait\",\"kind\":\"%s\"", kind); reqs_list(&b, n, reqs); rb_emit(&b);
}
int MPI_Wait(MPI_Request *req, MPI_Status *st) {
    MPI_Request before = *req; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    begin_wait("wait", 1, &before);
    int rc = PMPI_Wait(req, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"wait\""); reqs_list(&b, 1, &before); rb_printf(&b, ",\"done\":["); done_entry(&b, 1, before, st, rc == MPI_SUCCESS); rb_printf(&b, "]"); rb_emit(&b);
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
    free(before); free(mine);
    return rc;
}
int MPI_Test(MPI_Request *req, int *flag, MPI_Status *st) {
    MPI_Request before = *req; MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Test(req, flag, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"test\",\"flag\":%d", *flag); reqs_list(&b, 1, &before); rb_printf(&b, ",\"done\":[");
    if (*flag) done_entry(&b, 1, before, st, rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    return rc;
}
int MPI_Testall(int n, MPI_Request reqs[], int *flag, MPI_Status sts[]) {
    COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    int rc = PMPI_Testall(n, reqs, flag, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testall\",\"flag\":%d", *flag); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    if (*flag) { int first = 1; for (int i = 0; i < n; i++) if (before[i] != MPI_REQUEST_NULL) { done_entry(&b, first, before[i], &sts[i], rc == MPI_SUCCESS); first = 0; } }
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    free(before); free(mine);
    return rc;
}
int MPI_Testany(int n, MPI_Request reqs[], int *idx, int *flag, MPI_Status *st) {
    COPY_REQS(n, reqs); MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int rc = PMPI_Testany(n, reqs, idx, flag, st);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testany\",\"flag\":%d,\"idx\":%d", *flag, *idx); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    if (*flag && *idx != MPI_UNDEFINED) done_entry(&b, 1, before[*idx], st, rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, !*flag);
    free(before);
    return rc;
}
int MPI_Testsome(int n, MPI_Request reqs[], int *outcount, int idx[], MPI_Status sts[]) {
    COPY_REQS(n, reqs); OWN_STATUSES(n, sts);
    int rc = PMPI_Testsome(n, reqs, outcount, idx, sts);
    REC(b, "wait"); rb_printf(&b, ",\"kind\":\"testsome\",\"flag\":%d", *outcount > 0); reqs_list(&b, n, before); rb_printf(&b, ",\"done\":[");
    if (*outcount != MPI_UNDEFINED) for (int i = 0; i < *outcount; i++) done_entry(&b, i == 0, before[idx[i]], &sts[i], rc == MPI_SUCCESS);
    rb_printf(&b, "]"); rb_emit_kind(&b, *outcount == 0);
    free(before); free(mine);
    return rc;
}
int MPI_Request_free(MPI_Request *req) {
    int id = req_id(*req);
    REC(b, "reqfree"); rb_printf(&b, ",\"req\":%d", id); rb_emit(&b);
    req_remove(*req);
    pthread_mutex_lock(&g_mu); for (int i = 0; i < g_npers; i++) if (g_preq[i] == *req) { g_preq[i] = g_preq[--g_npers]; g_pinfo[i] = g_pinfo[g_npers]; break; } pthread_mutex_unlock(&g_mu);
    return PMPI_Request_free(req);
}
int MPI_Cancel(MPI_Request *req) {
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
    if (g_nmsg < MAXMSG) { g_msg[g_nmsg].m = m; g_msg[g_nmsg].comm = cid; g_msg[g_nmsg].src = st->MPI_SOURCE; g_msg[g_nmsg].tag = st->MPI_TAG; g_nmsg++; }
    pthread_mutex_unlock(&g_mu);
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
    if (flag) rematch_pin_probe_commit(pidx);
    rb_emit_kind(&b, !flag);
}
int MPI_Probe(int src, int tag, MPI_Comm comm, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = rematch_pin_probe(&src, &tag, &pidx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"probe\",\"kind\":\"probe\""); rb_src(&b, osrc); rb_tag(&b, otag); rb_printf(&b, ",\"comm\":%d", comm_id(comm)); rb_emit(&b); }
    int rc = PMPI_Probe(src, tag, comm, st); probe_rec("probe", osrc, otag, comm, 1, st, rc, pidx, pinned, src); return rc;
}
int MPI_Iprobe(int src, int tag, MPI_Comm comm, int *flag, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = rematch_pin_probe(&src, &tag, &pidx);
    int rc = PMPI_Iprobe(src, tag, comm, flag, st); probe_rec("iprobe", osrc, otag, comm, *flag, st, rc, pidx, pinned, src); return rc;
}
int MPI_Mprobe(int src, int tag, MPI_Comm comm, MPI_Message *msg, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = rematch_pin_probe(&src, &tag, &pidx);
    { REC(b, "begin"); rb_printf(&b, ",\"call\":\"probe\",\"kind\":\"mprobe\""); rb_src(&b, osrc); rb_tag(&b, otag); rb_printf(&b, ",\"comm\":%d", comm_id(comm)); rb_emit(&b); }
    int rc = PMPI_Mprobe(src, tag, comm, msg, st); if (rc == MPI_SUCCESS) msg_add(*msg, comm, st);
    probe_rec("mprobe", osrc, otag, comm, 1, st, rc, pidx, pinned, src); return rc;
}
int MPI_Improbe(int src, int tag, MPI_Comm comm, int *flag, MPI_Message *msg, MPI_Status *st) {
    MPI_Status mine; if (st == MPI_STATUS_IGNORE) st = &mine;
    int osrc = src, otag = tag, pidx = -1; int pinned = rematch_pin_probe(&src, &tag, &pidx);
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
    int id = req_add(*req, 1, dt, 0);
    REC(b, "irecv"); rb_printf(&b, ",\"src\":%d,\"tag\":%d,\"comm\":%d,\"mrecv\":1", src, tag, comm); rb_type(&b, count, dt);
    rb_printf(&b, ",\"req\":%d", id); rb_emit(&b);
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
    int rc = call; coll_rec(name, comm, root, count, dt, req_add(*req, 2, dt, 0), 0); return rc;

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
int MPI_Ireduce_scatter(const void *s, void *r, const int *rc_, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ireduce_scatter, "ireduce_scatter", -1, 0, dt, PMPI_Ireduce_scatter(s, r, rc_, dt, op, comm, req)) }
int MPI_Ireduce_scatter_block(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Ireduce_scatter_block, "ireduce_scatter_block", -1, n, dt, PMPI_Ireduce_scatter_block(s, r, n, dt, op, comm, req)) }
int MPI_Iscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iscan, "iscan", -1, n, dt, PMPI_Iscan(s, r, n, dt, op, comm, req)) }
int MPI_Iexscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req) { NBL(MPI_Iexscan, "iexscan", -1, n, dt, PMPI_Iexscan(s, r, n, dt, op, comm, req)) }

/* ---------------------------------------------------------- communicators */
int MPI_Comm_dup(MPI_Comm c, MPI_Comm *n) { int rc = PMPI_Comm_dup(c, n); comm_register("dup", c, *n); return rc; }
int MPI_Comm_split(MPI_Comm c, int color, int key, MPI_Comm *n) { int rc = PMPI_Comm_split(c, color, key, n); comm_register("split", c, *n); return rc; }
int MPI_Comm_split_type(MPI_Comm c, int type, int key, MPI_Info info, MPI_Comm *n) { int rc = PMPI_Comm_split_type(c, type, key, info, n); comm_register("split_type", c, *n); return rc; }
int MPI_Comm_create(MPI_Comm c, MPI_Group g, MPI_Comm *n) { int rc = PMPI_Comm_create(c, g, n); comm_register("create", c, *n); return rc; }
int MPI_Comm_create_group(MPI_Comm c, MPI_Group g, int tag, MPI_Comm *n) { int rc = PMPI_Comm_create_group(c, g, tag, n); comm_register("create_group", c, *n); return rc; }
int MPI_Cart_create(MPI_Comm c, int nd, const int *dims, const int *periods, int reorder, MPI_Comm *n) { int rc = PMPI_Cart_create(c, nd, dims, periods, reorder, n); comm_register("cart", c, *n); return rc; }
int MPI_Graph_create(MPI_Comm c, int nn, const int *idx, const int *edges, int reorder, MPI_Comm *n) { int rc = PMPI_Graph_create(c, nn, idx, edges, reorder, n); comm_register("graph", c, *n); return rc; }
int MPI_Comm_free(MPI_Comm *c) {
    int id = comm_id(*c);
    REC(b, "comm"); rb_printf(&b, ",\"name\":\"free\",\"parent\":-1,\"id\":%d", id); rb_emit(&b);
    pthread_mutex_lock(&g_mu); comm_forget(*c); pthread_mutex_unlock(&g_mu);
    return PMPI_Comm_free(c);
}

/* ------------------------------------------------------------- buffering */
int MPI_Buffer_attach(void *buf, int size) { REC(b, "bufattach"); rb_printf(&b, ",\"size\":%d", size); rb_emit(&b); return PMPI_Buffer_attach(buf, size); }
int MPI_Buffer_detach(void *buf, int *size) { int rc = PMPI_Buffer_detach(buf, size); REC(b, "bufdetach"); rb_emit(&b); return rc; }
