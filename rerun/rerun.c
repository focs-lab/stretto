/* Stretto re-execution shim. One rank of a traced program runs alone, without mpirun; this
   library, preloaded, answers its MPI calls from a script and writes down every call it makes.
   It is how level L2 learns what a rank does after results the trace did not record: the
   analysis writes the script from a trace made with STRETTO_PAYLOAD and reads the log. Builds
   with the system MPI; MPI itself runs as a singleton and serves only local calls (datatypes,
   packing, statuses, time).

   Environment (all set by the analysis):
     STRETTO_RERUN_RANK, STRETTO_RERUN_NP   the rank this process plays and the job size
     STRETTO_RERUN_SCRIPT                   the results to serve, one per line:
         R <src> <tag> <nbytes> <hex|->     the next receive completes with this message
         I <index>                          the next MPI_Waitany returns this index
         C <name> <root> <din|-> <dout|->   the rank's k-th collective on MPI_COMM_WORLD as
                                            recorded: the k-th call gets dout if it is the same
                                            collective with the same contribution din
     STRETTO_RERUN_LOG                      where the calls go, one JSON record per line

   Records carry the tracer's fields (tracer/mpitrace.c; ranks of MPI_COMM_WORLD, comm 0,
   request ids numbered as the tracer numbers them, the packed payload of every send as data)
   and h, the number of result-bearing calls (a receive, a wait that completes a receive, a
   Waitany) completed before the call:
     send, recv, irecv, wait(kind wait|waitall|waitany, reqs), coll
   and the last record says how the run ended:
     stop      the previous call needs a result the script does not have
     unknown   call, why: a call this library does not serve, or a collective whose result
               is not the recorded one
     abort     why: MPI_Abort
     finalize
   A run that ends without one of them crashed, called abort() or exited without
   MPI_Finalize; the analysis reads the exit status.

   Only MPI_COMM_WORLD is served. A receive that a message does not fit gets the part that
   fits and returns MPI_ERR_TRUNCATE, as under MPI_ERRORS_RETURN, which is how the explorer
   treats a count overflow (docs/model.md 4b). */
#define _GNU_SOURCE
#include <mpi.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#pragma GCC diagnostic ignored "-Wunused-parameter"

static int g_rank = 0, g_np = 1, g_h = 0, g_next_req = 0;
static FILE *g_log = NULL;

typedef struct { char kind; int src, tag, nbytes, idx; char *hex; } result_t;
static result_t *g_res = NULL;
static int g_nres = 0, g_ires = 0;
typedef struct { char name[64]; int root; char *din, *dout; } collrec_t;
static collrec_t *g_coll = NULL;
static int g_ncoll = 0, g_icoll = 0;

/* requests: the program holds fake handles that index this table */
typedef struct { int id, kind; void *buf; int count; MPI_Datatype dt; } slot_t;   /* kind: 0 send, 1 receive, 2 receive from MPI_PROC_NULL, -1 free */
static slot_t *g_slots = NULL;
static int g_nslots = 0;

static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n ? n : 1);
    if (!q) { fprintf(stderr, "stretto rerun: out of memory\n"); _exit(3); }
    return q;
}

/* ------------------------------------------------------------------ log */
static void out(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt); vfprintf(g_log, fmt, ap); va_end(ap);
}
static void rec_begin(const char *op) { fprintf(g_log, "{\"h\":%d,\"op\":\"%s\"", g_h, op); }
static void rec_end(void) { fputs("}\n", g_log); }
static void leave(void) { fflush(g_log); _exit(0); }
static void stop(void) { rec_begin("stop"); rec_end(); leave(); }
static void unknown(const char *call, const char *why) {
    rec_begin("unknown"); out(",\"call\":\"%s\",\"why\":\"%s\"", call, why); rec_end(); leave();
}

/* ------------------------------------------------------------------ data */
static int hexval(int c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1; }
static unsigned char *unhex(const char *hex, int *n) {
    size_t len = hex ? strlen(hex) : 0;
    unsigned char *b = xrealloc(NULL, len / 2);
    *n = (int)(len / 2);
    for (size_t i = 0; i < len / 2; i++) b[i] = (unsigned char)(hexval(hex[2 * i]) * 16 + hexval(hex[2 * i + 1]));
    return b;
}
/* (buf, count, dt) packed with MPI_Pack, as the tracer packs it, in hexadecimal (malloc'd) */
static char *packed_hex(const void *buf, int count, MPI_Datatype dt) {
    int size = 0, pos = 0;
    PMPI_Pack_size(count, dt, MPI_COMM_WORLD, &size);
    unsigned char *p = xrealloc(NULL, size);
    if (size > 0) PMPI_Pack(buf, count, dt, p, size, &pos, MPI_COMM_WORLD);
    static const char digits[] = "0123456789abcdef";
    char *hex = xrealloc(NULL, 2 * (size_t)pos + 1);
    for (int i = 0; i < pos; i++) { hex[2 * i] = digits[p[i] >> 4]; hex[2 * i + 1] = digits[p[i] & 15]; }
    hex[2 * pos] = 0;
    free(p);
    return hex;
}
static int tsize(MPI_Datatype dt) { int s = 0; if (dt != MPI_DATATYPE_NULL) PMPI_Type_size(dt, &s); return s; }
/* Unpack hex into (buf, count, dt): as many whole elements as the message has, at most count;
   *got: the bytes received. MPI_ERR_TRUNCATE if the message is longer. */
static int unpack_into(const char *hex, int nbytes, void *buf, int count, MPI_Datatype dt, int *got) {
    int n = 0, pos = 0, ts = tsize(dt), rc = MPI_SUCCESS;
    unsigned char *b = unhex(hex, &n);
    if (n > nbytes) n = nbytes;
    int k = ts > 0 ? n / ts : 0;
    if (k > count) { k = count; rc = MPI_ERR_TRUNCATE; }
    if (k > 0) PMPI_Unpack(b, n, &pos, buf, k, dt, MPI_COMM_WORLD);
    *got = k * ts;
    free(b);
    return rc;
}

/* ---------------------------------------------------------------- script */
static void read_script(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "stretto rerun: cannot read %s\n", path); _exit(3); }
    char *line = NULL; size_t cap = 0;
    while (getline(&line, &cap, f) > 0) {
        char *save = NULL, *kind = strtok_r(line, " \n", &save);
        if (!kind) continue;
        if (kind[0] == 'C') {
            g_coll = xrealloc(g_coll, sizeof *g_coll * (g_ncoll + 1));
            collrec_t *c = &g_coll[g_ncoll++];
            char *name = strtok_r(NULL, " \n", &save), *root = strtok_r(NULL, " \n", &save);
            char *din = strtok_r(NULL, " \n", &save), *dout = strtok_r(NULL, " \n", &save);
            snprintf(c->name, sizeof c->name, "%s", name ? name : "");
            c->root = root ? atoi(root) : -1;
            c->din = din && strcmp(din, "-") ? strdup(din) : NULL;
            c->dout = dout && strcmp(dout, "-") ? strdup(dout) : NULL;
            continue;
        }
        g_res = xrealloc(g_res, sizeof *g_res * (g_nres + 1));
        result_t *r = &g_res[g_nres++];
        memset(r, 0, sizeof *r);
        r->kind = kind[0];
        if (r->kind == 'I') {
            char *idx = strtok_r(NULL, " \n", &save);
            r->idx = idx ? atoi(idx) : -1;
        } else {
            char *src = strtok_r(NULL, " \n", &save), *tag = strtok_r(NULL, " \n", &save);
            char *nb = strtok_r(NULL, " \n", &save), *hex = strtok_r(NULL, " \n", &save);
            r->src = src ? atoi(src) : -1; r->tag = tag ? atoi(tag) : -1; r->nbytes = nb ? atoi(nb) : 0;
            r->hex = hex && strcmp(hex, "-") ? strdup(hex) : NULL;
        }
    }
    free(line);
    fclose(f);
}
static void setup(void) {
    const char *rank = getenv("STRETTO_RERUN_RANK"), *np = getenv("STRETTO_RERUN_NP");
    const char *script = getenv("STRETTO_RERUN_SCRIPT"), *log = getenv("STRETTO_RERUN_LOG");
    if (!rank || !np || !script || !log) {
        fprintf(stderr, "stretto rerun: STRETTO_RERUN_RANK, _NP, _SCRIPT and _LOG must be set\n");
        _exit(3);
    }
    g_rank = atoi(rank); g_np = atoi(np);
    g_log = fopen(log, "w");
    if (!g_log) { fprintf(stderr, "stretto rerun: cannot write %s\n", log); _exit(3); }
    setvbuf(g_log, NULL, _IOLBF, 0);   /* every record reaches the file before an abort() or a crash */
    read_script(script);
}
/* the next scripted result, which must be of this kind; stop() if the script has no more */
static result_t *next_result(char kind, const char *call) {
    if (g_ires >= g_nres) stop();
    result_t *r = &g_res[g_ires++];
    if (r->kind != kind) unknown(call, "the script is out of step with the calls");
    return r;
}

/* -------------------------------------------------------------- statuses */
static void set_status(MPI_Status *st, int src, int tag, int nbytes, int err) {
    if (st == MPI_STATUS_IGNORE) return;
    st->MPI_SOURCE = src; st->MPI_TAG = tag; st->MPI_ERROR = err;
    PMPI_Status_set_elements(st, MPI_BYTE, nbytes);
    PMPI_Status_set_cancelled(st, 0);
}
static void empty_status(MPI_Status *st) { set_status(st, MPI_ANY_SOURCE, MPI_ANY_TAG, 0, MPI_SUCCESS); }

/* -------------------------------------------------------------- requests */
#define REQ_BASE 0x5e000000u
static MPI_Request fake_req(int slot) {
    MPI_Request r; uint32_t v = REQ_BASE + 8u * (uint32_t)slot;
    memset(&r, 0, sizeof r);
    memcpy(&r, &v, sizeof v < sizeof r ? sizeof v : sizeof r);
    return r;
}
/* the slot of a handle; -1 for MPI_REQUEST_NULL (the program holds no other handles) */
static int slot_of(MPI_Request r) {
    if (r == MPI_REQUEST_NULL) return -1;
    uint32_t v = 0;
    memcpy(&v, &r, sizeof v < sizeof r ? sizeof v : sizeof r);
    int s = (int)((v - REQ_BASE) / 8u);
    if (v < REQ_BASE || s >= g_nslots || g_slots[s].kind < 0) unknown("a completion call", "a request this library did not create");
    return s;
}
static MPI_Request new_req(int kind, void *buf, int count, MPI_Datatype dt) {
    int s = 0;
    while (s < g_nslots && g_slots[s].kind >= 0) s++;
    if (s == g_nslots) { g_slots = xrealloc(g_slots, sizeof *g_slots * (g_nslots + 1)); g_nslots++; }
    g_slots[s] = (slot_t){g_next_req++, kind, buf, count, dt};
    return fake_req(s);
}
/* Complete request slot s into st; a receive takes the next scripted message. */
static int complete(int s, MPI_Status *st, const char *call) {
    slot_t *e = &g_slots[s];
    int rc = MPI_SUCCESS;
    if (e->kind == 1) {
        result_t *r = next_result('R', call);
        int got = 0;
        rc = unpack_into(r->hex, r->nbytes, e->buf, e->count, e->dt, &got);
        set_status(st, r->src, r->tag, got, rc);
    } else if (e->kind == 2) {
        set_status(st, MPI_PROC_NULL, MPI_ANY_TAG, 0, MPI_SUCCESS);
    } else {
        empty_status(st);
    }
    e->kind = -1;
    return rc;
}
static void log_reqs(int n, const MPI_Request reqs[]) {
    out(",\"reqs\":[");
    for (int i = 0; i < n; i++) { int s = slot_of(reqs[i]); out("%s%d", i ? "," : "", s >= 0 ? g_slots[s].id : -1); }
    out("]");
}

/* ------------------------------------------------------------ init, fini */
int MPI_Init(int *argc, char ***argv) { int rc = PMPI_Init(argc, argv); setup(); return rc; }
int MPI_Init_thread(int *argc, char ***argv, int req, int *prov) { int rc = PMPI_Init_thread(argc, argv, req, prov); setup(); return rc; }
int MPI_Finalize(void) { rec_begin("finalize"); rec_end(); fflush(g_log); return PMPI_Finalize(); }
int MPI_Abort(MPI_Comm comm, int code) {
    rec_begin("abort"); out(",\"why\":\"MPI_Abort(%d)\"", code); rec_end();
    fflush(g_log); _exit(code ? code : 1);
}
static int world(MPI_Comm c, const char *call) {
    if (c != MPI_COMM_WORLD) unknown(call, "a communicator other than MPI_COMM_WORLD");
    return 1;
}
int MPI_Comm_size(MPI_Comm c, int *n) {
    if (c == MPI_COMM_SELF) { *n = 1; return MPI_SUCCESS; }
    world(c, "MPI_Comm_size"); *n = g_np; return MPI_SUCCESS;
}
int MPI_Comm_rank(MPI_Comm c, int *r) {
    if (c == MPI_COMM_SELF) { *r = 0; return MPI_SUCCESS; }
    world(c, "MPI_Comm_rank"); *r = g_rank; return MPI_SUCCESS;
}

/* ------------------------------------------------------------------ sends */
static void log_send(const char *mode, const void *buf, int count, MPI_Datatype dt, int dst, int tag, int id) {
    rec_begin("send"); out(",\"mode\":\"%s\"", mode);
    if (dst == MPI_PROC_NULL) out(",\"dst\":-1,\"procnull\":1"); else out(",\"dst\":%d", dst);
    out(",\"tag\":%d,\"comm\":0,\"count\":%d,\"tsize\":%d", tag, count, tsize(dt));
    if (id >= 0) out(",\"req\":%d", id);
    if (dst != MPI_PROC_NULL) { char *hex = packed_hex(buf, count, dt); out(",\"data\":\"%s\"", hex); free(hex); }
    rec_end();
}
#define BLOCKING_SEND(NAME, MODE) \
int NAME(const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm) { \
    world(comm, #NAME); log_send(MODE, buf, count, dt, dst, tag, -1); return MPI_SUCCESS; }
BLOCKING_SEND(MPI_Send,  "std")
BLOCKING_SEND(MPI_Ssend, "ssend")
BLOCKING_SEND(MPI_Bsend, "bsend")
BLOCKING_SEND(MPI_Rsend, "rsend")
#define NONBLOCKING_SEND(NAME, MODE) \
int NAME(const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req) { \
    world(comm, #NAME); *req = new_req(0, NULL, 0, dt); \
    log_send(MODE, buf, count, dt, dst, tag, g_slots[slot_of(*req)].id); return MPI_SUCCESS; }
NONBLOCKING_SEND(MPI_Isend,  "isend")
NONBLOCKING_SEND(MPI_Issend, "issend")
NONBLOCKING_SEND(MPI_Ibsend, "ibsend")
NONBLOCKING_SEND(MPI_Irsend, "irsend")

/* -------------------------------------------------------------- receives */
static void log_recv(const char *op, int src, int tag, int count, MPI_Datatype dt, int id) {
    rec_begin(op);
    if (src == MPI_ANY_SOURCE) out(",\"src\":-1,\"anysrc\":1");
    else if (src == MPI_PROC_NULL) out(",\"src\":-1,\"procnull\":1");
    else out(",\"src\":%d", src);
    if (tag == MPI_ANY_TAG) out(",\"tag\":-1,\"anytag\":1"); else out(",\"tag\":%d", tag);
    out(",\"comm\":0,\"count\":%d,\"tsize\":%d", count, tsize(dt));
    if (id >= 0) out(",\"req\":%d", id);
    rec_end();
}
static int recv_now(void *buf, int count, MPI_Datatype dt, int src, MPI_Status *st, const char *call) {
    if (src == MPI_PROC_NULL) { set_status(st, MPI_PROC_NULL, MPI_ANY_TAG, 0, MPI_SUCCESS); return MPI_SUCCESS; }
    result_t *r = next_result('R', call);
    int got = 0, rc = unpack_into(r->hex, r->nbytes, buf, count, dt, &got);
    set_status(st, r->src, r->tag, got, rc);
    g_h++;
    return rc;
}
int MPI_Recv(void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Status *st) {
    world(comm, "MPI_Recv");
    log_recv("recv", src, tag, count, dt, -1);
    return recv_now(buf, count, dt, src, st, "MPI_Recv");
}
int MPI_Irecv(void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Request *req) {
    world(comm, "MPI_Irecv");
    *req = new_req(src == MPI_PROC_NULL ? 2 : 1, buf, count, dt);
    log_recv("irecv", src, tag, count, dt, g_slots[slot_of(*req)].id);
    return MPI_SUCCESS;
}
int MPI_Sendrecv(const void *sbuf, int scount, MPI_Datatype sdt, int dst, int stag,
                 void *rbuf, int rcount, MPI_Datatype rdt, int src, int rtag, MPI_Comm comm, MPI_Status *st) {
    world(comm, "MPI_Sendrecv");
    int id = g_next_req++;                      /* the tracer models it as Isend, Recv, Wait */
    log_send("isend", sbuf, scount, sdt, dst, stag, id);
    log_recv("recv", src, rtag, rcount, rdt, -1);
    int rc = recv_now(rbuf, rcount, rdt, src, st, "MPI_Sendrecv");
    rec_begin("wait"); out(",\"kind\":\"wait\",\"reqs\":[%d]", id); rec_end();
    return rc;
}
int MPI_Sendrecv_replace(void *buf, int count, MPI_Datatype dt, int dst, int stag, int src, int rtag, MPI_Comm comm, MPI_Status *st) {
    world(comm, "MPI_Sendrecv_replace");
    int id = g_next_req++;
    log_send("isend", buf, count, dt, dst, stag, id);
    log_recv("recv", src, rtag, count, dt, -1);
    int rc = recv_now(buf, count, dt, src, st, "MPI_Sendrecv_replace");
    rec_begin("wait"); out(",\"kind\":\"wait\",\"reqs\":[%d]", id); rec_end();
    return rc;
}

/* ------------------------------------------------------------- completion */
int MPI_Wait(MPI_Request *req, MPI_Status *st) {
    int s = slot_of(*req);
    rec_begin("wait"); out(",\"kind\":\"wait\""); log_reqs(1, req); rec_end();
    if (s < 0) { empty_status(st); return MPI_SUCCESS; }
    int recv = g_slots[s].kind == 1;
    if (recv && g_ires >= g_nres) stop();
    int rc = complete(s, st, "MPI_Wait");
    *req = MPI_REQUEST_NULL;
    if (recv) g_h++;
    return rc;
}
int MPI_Waitall(int n, MPI_Request reqs[], MPI_Status sts[]) {
    rec_begin("wait"); out(",\"kind\":\"waitall\""); log_reqs(n, reqs); rec_end();
    int recvs = 0, rc = MPI_SUCCESS;
    for (int i = 0; i < n; i++) { int s = slot_of(reqs[i]); if (s >= 0 && g_slots[s].kind == 1) recvs++; }
    if (g_ires + recvs > g_nres) stop();
    for (int i = 0; i < n; i++) {
        MPI_Status *st = sts == MPI_STATUSES_IGNORE ? MPI_STATUS_IGNORE : &sts[i];
        int s = slot_of(reqs[i]);
        if (s < 0) { empty_status(st); continue; }
        if (complete(s, st, "MPI_Waitall") != MPI_SUCCESS) rc = MPI_ERR_IN_STATUS;
        reqs[i] = MPI_REQUEST_NULL;
    }
    if (recvs) g_h++;
    return rc;
}
int MPI_Waitany(int n, MPI_Request reqs[], int *idx, MPI_Status *st) {
    int live = 0;
    for (int i = 0; i < n; i++) if (slot_of(reqs[i]) >= 0) live++;
    rec_begin("wait"); out(",\"kind\":\"waitany\""); log_reqs(n, reqs);
    if (!live) out(",\"undefined\":1");
    rec_end();
    if (!live) { *idx = MPI_UNDEFINED; empty_status(st); return MPI_SUCCESS; }
    result_t *r = next_result('I', "MPI_Waitany");
    int i = r->idx;
    if (i < 0 || i >= n || slot_of(reqs[i]) < 0) unknown("MPI_Waitany", "the scripted index names no active request");
    int s = slot_of(reqs[i]);
    if (g_slots[s].kind == 1 && g_ires >= g_nres) unknown("MPI_Waitany", "the script is out of step with the calls");
    int rc = complete(s, st, "MPI_Waitany");
    reqs[i] = MPI_REQUEST_NULL;
    *idx = i;
    g_h++;
    return rc;
}

/* ------------------------------------------------------------ collectives */
/* The k-th collective of the rank gets the recorded result if it is the recorded k-th one with
   the recorded contribution: a collective's result depends only on the contributions of the
   members its shape requires (premise P1), and every other member contributes as recorded or
   stops before the collective in its own run. in, out: what this rank gives and gets, NULL
   where its shape gives it none. */
static int coll(const char *call, const char *name, MPI_Comm comm, int root, int count, MPI_Datatype dt,
                const void *in, int nin, MPI_Datatype tin, void *outb, int nout, MPI_Datatype tout) {
    world(comm, call);
    rec_begin("coll"); out(",\"name\":\"%s\",\"comm\":0,\"root\":%d,\"count\":%d,\"tsize\":%d", name, root, count, tsize(dt)); rec_end();
    if (g_icoll >= g_ncoll) unknown(call, "more collectives than the recorded run has");
    collrec_t *c = &g_coll[g_icoll++];
    if (strcmp(c->name, name) || c->root != root) unknown(call, "another collective than the recorded one at this position");
    if (in) {
        if (in == MPI_IN_PLACE) unknown(call, "an MPI_IN_PLACE contribution is not compared");
        if (!c->din) unknown(call, "the recorded run has no payload for its contribution");
        char *hex = packed_hex(in, nin, tin);
        int same = strcmp(hex, c->din) == 0;
        free(hex);
        if (!same) unknown(call, "the contribution differs from the recorded one, so the result may too");
    }
    if (outb) {
        if (outb == MPI_IN_PLACE) unknown(call, "an MPI_IN_PLACE result is not served");
        if (!c->dout) unknown(call, "the recorded run has no payload for its result");
        int got = 0, n = (int)(strlen(c->dout) / 2);
        unpack_into(c->dout, n, outb, nout, tout, &got);
    }
    return MPI_SUCCESS;
}
int MPI_Barrier(MPI_Comm comm) { return coll("MPI_Barrier", "barrier", comm, -1, 0, MPI_DATATYPE_NULL, NULL, 0, MPI_DATATYPE_NULL, NULL, 0, MPI_DATATYPE_NULL); }
int MPI_Bcast(void *b, int n, MPI_Datatype dt, int root, MPI_Comm comm) {
    return coll("MPI_Bcast", "bcast", comm, root, n, dt, g_rank == root ? b : NULL, n, dt, g_rank != root ? b : NULL, n, dt); }
int MPI_Reduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, int root, MPI_Comm comm) {
    return coll("MPI_Reduce", "reduce", comm, root, n, dt, s, n, dt, g_rank == root ? r : NULL, n, dt); }
int MPI_Allreduce(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) {
    return coll("MPI_Allreduce", "allreduce", comm, -1, n, dt, s, n, dt, r, n, dt); }
int MPI_Scan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) {
    return coll("MPI_Scan", "scan", comm, -1, n, dt, s, n, dt, r, n, dt); }
int MPI_Exscan(const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm) {
    return coll("MPI_Exscan", "exscan", comm, -1, n, dt, s, n, dt, r, n, dt); }
int MPI_Gather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm) {
    return coll("MPI_Gather", "gather", comm, root, sn, sdt, s, sn, sdt, g_rank == root ? r : NULL, rn * g_np, rdt); }
int MPI_Scatter(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm) {
    return coll("MPI_Scatter", "scatter", comm, root, rn, rdt, g_rank == root ? s : NULL, sn * g_np, sdt, r, rn, rdt); }
int MPI_Allgather(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm) {
    return coll("MPI_Allgather", "allgather", comm, -1, sn, sdt, s, sn, sdt, r, rn * g_np, rdt); }
int MPI_Alltoall(const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm) {
    return coll("MPI_Alltoall", "alltoall", comm, -1, sn, sdt, s, sn * g_np, sdt, r, rn * g_np, rdt); }

/* ------------------------------------------------------- not served here
   Each of these ends the run with an unknown record: the analysis then knows nothing about the
   rank from this call on. Probes and tests return flags and statuses the explorer does not
   model; the others create communicators, persistent or generalised requests, windows or
   files, or are collectives of a shape the trace records no payload for. */
#define NOT_SERVED(NAME, PARAMS) int NAME PARAMS { unknown(#NAME, "not served by the re-execution shim"); return MPI_ERR_OTHER; }
NOT_SERVED(MPI_Test, (MPI_Request *req, int *flag, MPI_Status *st))
NOT_SERVED(MPI_Testall, (int n, MPI_Request reqs[], int *flag, MPI_Status sts[]))
NOT_SERVED(MPI_Testany, (int n, MPI_Request reqs[], int *idx, int *flag, MPI_Status *st))
NOT_SERVED(MPI_Testsome, (int n, MPI_Request reqs[], int *outcount, int idx[], MPI_Status sts[]))
NOT_SERVED(MPI_Waitsome, (int n, MPI_Request reqs[], int *outcount, int idx[], MPI_Status sts[]))
NOT_SERVED(MPI_Request_get_status, (MPI_Request req, int *flag, MPI_Status *st))
NOT_SERVED(MPI_Request_free, (MPI_Request *req))
NOT_SERVED(MPI_Cancel, (MPI_Request *req))
NOT_SERVED(MPI_Probe, (int src, int tag, MPI_Comm comm, MPI_Status *st))
NOT_SERVED(MPI_Iprobe, (int src, int tag, MPI_Comm comm, int *flag, MPI_Status *st))
NOT_SERVED(MPI_Mprobe, (int src, int tag, MPI_Comm comm, MPI_Message *msg, MPI_Status *st))
NOT_SERVED(MPI_Improbe, (int src, int tag, MPI_Comm comm, int *flag, MPI_Message *msg, MPI_Status *st))
NOT_SERVED(MPI_Mrecv, (void *buf, int count, MPI_Datatype dt, MPI_Message *msg, MPI_Status *st))
NOT_SERVED(MPI_Imrecv, (void *buf, int count, MPI_Datatype dt, MPI_Message *msg, MPI_Request *req))
NOT_SERVED(MPI_Send_init, (const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Ssend_init, (const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Bsend_init, (const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Rsend_init, (const void *buf, int count, MPI_Datatype dt, int dst, int tag, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Recv_init, (void *buf, int count, MPI_Datatype dt, int src, int tag, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Start, (MPI_Request *req))
NOT_SERVED(MPI_Startall, (int n, MPI_Request reqs[]))
NOT_SERVED(MPI_Gatherv, (const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, int root, MPI_Comm comm))
NOT_SERVED(MPI_Scatterv, (const void *s, const int *sc, const int *d, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm))
NOT_SERVED(MPI_Allgatherv, (const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, MPI_Comm comm))
NOT_SERVED(MPI_Alltoallv, (const void *s, const int *sc, const int *sd, MPI_Datatype sdt, void *r, const int *rc_, const int *rd, MPI_Datatype rdt, MPI_Comm comm))
NOT_SERVED(MPI_Alltoallw, (const void *s, const int *sc, const int *sd, const MPI_Datatype *sdt, void *r, const int *rc_, const int *rd, const MPI_Datatype *rdt, MPI_Comm comm))
NOT_SERVED(MPI_Reduce_scatter, (const void *s, void *r, const int *rc_, MPI_Datatype dt, MPI_Op op, MPI_Comm comm))
NOT_SERVED(MPI_Reduce_scatter_block, (const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm))
NOT_SERVED(MPI_Ibarrier, (MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Ibcast, (void *b, int n, MPI_Datatype dt, int root, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Ireduce, (const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, int root, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Iallreduce, (const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Igather, (const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Igatherv, (const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Iscatter, (const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Iscatterv, (const void *s, const int *sc, const int *d, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, int root, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Iallgather, (const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Iallgatherv, (const void *s, int sn, MPI_Datatype sdt, void *r, const int *rc_, const int *d, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Ialltoall, (const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Ialltoallv, (const void *s, const int *sc, const int *sd, MPI_Datatype sdt, void *r, const int *rc_, const int *rd, MPI_Datatype rdt, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Ialltoallw, (const void *s, const int *sc, const int *sd, const MPI_Datatype *sdt, void *r, const int *rc_, const int *rd, const MPI_Datatype *rdt, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Ireduce_scatter, (const void *s, void *r, const int *rc_, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Ireduce_scatter_block, (const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Iscan, (const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Iexscan, (const void *s, void *r, int n, MPI_Datatype dt, MPI_Op op, MPI_Comm comm, MPI_Request *req))
NOT_SERVED(MPI_Comm_dup, (MPI_Comm c, MPI_Comm *n))
NOT_SERVED(MPI_Comm_dup_with_info, (MPI_Comm c, MPI_Info info, MPI_Comm *n))
NOT_SERVED(MPI_Comm_idup, (MPI_Comm c, MPI_Comm *n, MPI_Request *req))
NOT_SERVED(MPI_Comm_split, (MPI_Comm c, int color, int key, MPI_Comm *n))
NOT_SERVED(MPI_Comm_split_type, (MPI_Comm c, int type, int key, MPI_Info info, MPI_Comm *n))
NOT_SERVED(MPI_Comm_create, (MPI_Comm c, MPI_Group g, MPI_Comm *n))
NOT_SERVED(MPI_Comm_create_group, (MPI_Comm c, MPI_Group g, int tag, MPI_Comm *n))
NOT_SERVED(MPI_Comm_group, (MPI_Comm c, MPI_Group *g))
NOT_SERVED(MPI_Cart_create, (MPI_Comm c, int nd, const int *dims, const int *periods, int reorder, MPI_Comm *n))
NOT_SERVED(MPI_Cart_sub, (MPI_Comm c, const int *remain, MPI_Comm *n))
NOT_SERVED(MPI_Graph_create, (MPI_Comm c, int nn, const int *idx, const int *edges, int reorder, MPI_Comm *n))
NOT_SERVED(MPI_Dist_graph_create, (MPI_Comm c, int n_, const int *sources, const int *degrees, const int *dests, const int *weights, MPI_Info info, int reorder, MPI_Comm *n))
NOT_SERVED(MPI_Dist_graph_create_adjacent, (MPI_Comm c, int indeg, const int *sources, const int *sw, int outdeg, const int *dests, const int *dw, MPI_Info info, int reorder, MPI_Comm *n))
NOT_SERVED(MPI_Intercomm_create, (MPI_Comm local, int lleader, MPI_Comm peer, int rleader, int tag, MPI_Comm *n))
NOT_SERVED(MPI_Intercomm_merge, (MPI_Comm inter, int high, MPI_Comm *n))
NOT_SERVED(MPI_Win_create, (void *base, MPI_Aint size, int du, MPI_Info info, MPI_Comm comm, MPI_Win *win))
NOT_SERVED(MPI_Win_allocate, (MPI_Aint size, int du, MPI_Info info, MPI_Comm comm, void *base, MPI_Win *win))
NOT_SERVED(MPI_Win_allocate_shared, (MPI_Aint size, int du, MPI_Info info, MPI_Comm comm, void *base, MPI_Win *win))
NOT_SERVED(MPI_Win_create_dynamic, (MPI_Info info, MPI_Comm comm, MPI_Win *win))
NOT_SERVED(MPI_Neighbor_allgather, (const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm))
NOT_SERVED(MPI_Neighbor_alltoall, (const void *s, int sn, MPI_Datatype sdt, void *r, int rn, MPI_Datatype rdt, MPI_Comm comm))
NOT_SERVED(MPI_Comm_spawn, (const char *cmd, char *argv[], int maxprocs, MPI_Info info, int root, MPI_Comm comm, MPI_Comm *inter, int errs[]))
NOT_SERVED(MPI_Comm_connect, (const char *port, MPI_Info info, int root, MPI_Comm comm, MPI_Comm *n))
NOT_SERVED(MPI_Comm_accept, (const char *port, MPI_Info info, int root, MPI_Comm comm, MPI_Comm *n))
NOT_SERVED(MPI_Grequest_start, (MPI_Grequest_query_function *q, MPI_Grequest_free_function *f, MPI_Grequest_cancel_function *c, void *extra, MPI_Request *req))
NOT_SERVED(MPI_File_open, (MPI_Comm comm, const char *name, int amode, MPI_Info info, MPI_File *fh))
