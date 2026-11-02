#define _GNU_SOURCE
#include "pin.h"
#include <dlfcn.h>
#include <mpi.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define HIDDEN __attribute__((visibility("hidden")))

/* tracer/mpitrace.c, linked into the same library */
int stretto_comm_ordinal(MPI_Comm c);
void *stretto_call_site(void);

static int g_rank = -1;
static int g_nwild = 0, g_nsend = 0, g_nrecv = 0, g_nprobe = 0;
/* A pin: the idx-th wildcard receive (or probe that found a message) of the rank takes src (and
   tag, for an ANY_TAG receive). Its identity, if the token gives one, is the traced operation's
   communicator (its creation ordinal on the rank), its tag argument (-1 for ANY_TAG) and its call
   site (offset in its object); each part is checked when given. seen_*: what the run's operation
   had, for the report lines. A flip pin (STRETTO_PIN_FLIP) has its poll's key as its idx; used:
   a pin that applies once (a flip, a probe pin to flag 0) has been applied. */
struct pin {
    int idx, src, tag;
    int ident, has_comm, has_otag, has_site;
    int comm, otag; long site;
    int seen_comm, seen_otag; long seen_site;
    /* the fourth field of the identity: how many wildcard probes of the rank's envelope (the
       communicator and tag argument of the identity) found a message before the call; for a
       probe pin its ordinal among them, which selects it, for a flip pin a check */
    int nprobe, has_nprobe;
    int used;
    char chain[160];   /* STRETTO_PIN_CHAIN: the chain whose idx-th receive the pin names */
};
static struct pin *g_pins = NULL, *g_ppins = NULL, *g_fpins = NULL;
static int g_npins = 0, g_nppins = 0, g_nfpins = 0;
/* probe pins that name their envelope's ordinal (STRETTO_PIN_PROBE with a fourth identity
   field), sorted by (comm, otag, nprobe); g_ppins keeps the others, by the rank's ordinal */
static struct pin *g_epins = NULL;
static int g_nepins = 0;
/* per envelope (communicator ordinal, tag argument, -1 for ANY_TAG): the wildcard probes of
   the rank that found a message */
static struct { int comm, otag, n; } *g_env = NULL;
static int g_nenv = 0;
/* the key of STRETTO_PIN_FLIP: how many of this rank's calls other than failed polls have
   written their record so far (stretto_pin_record) */
static int g_calls = 0;
static int g_send_delay_ms = 0, g_recv_delay_ms = 0;
static struct { int is_send, k, ms; } *g_at = NULL;
static int g_nat = 0;
static pthread_mutex_t g_pmu = PTHREAD_MUTEX_INITIALIZER;

static void msleep(int ms) {
    if (ms <= 0) return;
    struct timespec ts = { ms / 1000, (long)(ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
}

static int parse_rank_ms(const char *spec, int rank) {
    if (!spec) return 0;
    char *copy = strdup(spec), *save = NULL;
    int ms = 0;
    for (char *tok = strtok_r(copy, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
        int r, m;
        if (sscanf(tok, "%d:%d", &r, &m) == 2 && r == rank) ms = m;
    }
    free(copy);
    return ms;
}

/* The value of a pin variable: the variable itself, or, if it starts with '@', the file it
   names. A horizon of thousands of wildcard receives does not fit into one environment
   string (Linux limits each to 128 KiB), so long specs go to a file. A file that cannot be
   read ends the run: replaying with no pins would look like a replay of the alternative. */
static char *pin_spec(const char *name, int rank) {
    const char *v = getenv(name);
    if (!v) return NULL;
    if (v[0] != '@') return strdup(v);
    FILE *f = fopen(v + 1, "r");
    char *buf = NULL;
    long n = -1;
    if (f && fseek(f, 0, SEEK_END) == 0 && (n = ftell(f)) >= 0 && fseek(f, 0, SEEK_SET) == 0) {
        buf = malloc(n + 1);
        if (buf && fread(buf, 1, n, f) != (size_t)n) { free(buf); buf = NULL; }
    }
    if (f) fclose(f);
    if (!buf) {
        fprintf(stderr, "[stretto-pin] rank %d: cannot read %s from %s\n", rank, name, v + 1);
        PMPI_Abort(MPI_COMM_WORLD, 3);
    }
    buf[n] = '\0';
    return buf;
}

/* The identity after '@' in a token: "comm:tag[:site]", decimal, decimal and hexadecimal, each
   field 'x' when it is not to be checked. */
static void parse_ident(char *s, struct pin *p) {
    char *save = NULL;
    int k = 0;
    p->ident = 1;
    for (char *f = strtok_r(s, ":", &save); f; f = strtok_r(NULL, ":", &save), k++) {
        if (strcmp(f, "x") == 0) continue;
        if (k == 0) { p->comm = atoi(f); p->has_comm = 1; }
        else if (k == 1) { p->otag = atoi(f); p->has_otag = 1; }
        else if (k == 2) { p->site = strtol(f, NULL, 16); p->has_site = 1; }
        else if (k == 3) { p->nprobe = atoi(f); p->has_nprobe = 1; }   /* the envelope's wildcard probes found before */
    }
}

static int by_idx(const void *a, const void *b) {
    const struct pin *x = a, *y = b;
    return x->idx < y->idx ? -1 : x->idx > y->idx;
}

/* rank:idx:src[:tag][@comm:tag[:site]] tokens separated by commas (or whitespace, in a file);
   those of this rank are kept, sorted by idx (a token without '@' is the format before pins
   carried the operation's identity, and is applied without that check). A token that does not
   parse ends the run: a replay with a pin dropped would not be the alternative's. */
static int parse_pins(const char *name, int rank, struct pin **out) {
    char *copy = pin_spec(name, rank), *save = NULL;
    int n = 0, cap = 0;
    *out = NULL;
    if (!copy) return 0;
    for (char *tok = strtok_r(copy, ", \n", &save); tok; tok = strtok_r(NULL, ", \n", &save)) {
        struct pin p;
        memset(&p, 0, sizeof p);
        char *at = strchr(tok, '@');
        if (at) *at = '\0';
        int r, k = sscanf(tok, "%d:%d:%d:%d", &r, &p.idx, &p.src, &p.tag);
        if (k < 3) {
            fprintf(stderr, "[stretto-pin] rank %d: %s: token '%s' does not parse\n", rank, name, tok);
            PMPI_Abort(MPI_COMM_WORLD, 3);
        }
        if (r != rank) continue;
        if (k < 4) p.tag = MPI_ANY_TAG;
        if (at) parse_ident(at + 1, &p);
        if (n == cap) { cap = cap ? 2 * cap : 16; *out = realloc(*out, sizeof **out * cap); }
        (*out)[n++] = p;
    }
    free(copy);
    if (n > 1) qsort(*out, n, sizeof **out, by_idx);
    return n;
}

/* The first pin with this idx, by binary search (a horizon pins tens of thousands). */
static struct pin *find_pin(struct pin *pins, int n, int idx) {
    int lo = 0, hi = n;
    while (lo < hi) { int mid = (lo + hi) / 2; if (pins[mid].idx < idx) lo = mid + 1; else hi = mid; }
    return lo < n && pins[lo].idx == idx ? &pins[lo] : NULL;
}

static int env_cmp(int c1, int t1, int n1, int c2, int t2, int n2) {
    return c1 != c2 ? (c1 < c2 ? -1 : 1) : t1 != t2 ? (t1 < t2 ? -1 : 1) : n1 != n2 ? (n1 < n2 ? -1 : 1) : 0;
}

static int by_envelope(const void *a, const void *b) {
    const struct pin *x = a, *y = b;
    return env_cmp(x->comm, x->otag, x->nprobe, y->comm, y->otag, y->nprobe);
}

/* The probe pin of the envelope's ordinal n, by binary search. */
static struct pin *find_epin(int comm, int otag, int n) {
    int lo = 0, hi = g_nepins;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (env_cmp(g_epins[mid].comm, g_epins[mid].otag, g_epins[mid].nprobe, comm, otag, n) < 0) lo = mid + 1; else hi = mid;
    }
    return lo < g_nepins && env_cmp(g_epins[lo].comm, g_epins[lo].otag, g_epins[lo].nprobe, comm, otag, n) == 0 ? &g_epins[lo] : NULL;
}

/* The wildcard probes of the envelope that found a message so far (caller holds g_pmu). */
static int *env_slot(int comm, int otag) {
    for (int i = 0; i < g_nenv; i++)
        if (g_env[i].comm == comm && g_env[i].otag == otag) return &g_env[i].n;
    g_env = realloc(g_env, sizeof *g_env * (g_nenv + 1));
    g_env[g_nenv].comm = comm; g_env[g_nenv].otag = otag; g_env[g_nenv].n = 0;
    return &g_env[g_nenv++].n;
}

/* Probe pins with an envelope ordinal move from g_ppins to g_epins: a rank that runs two
   polling loops of different tags or communicators from one call site (VTK's particle
   tracker drains tags 622 and 624 through one Iprobe) finds their messages in another order in
   a replay than in the trace, and the rank's ordinal of the probe then names a probe of the
   other loop; the envelope's ordinal does not depend on that order. */
static void split_envelope_pins(void) {
    int k = 0;
    for (int i = 0; i < g_nppins; i++) {
        struct pin p = g_ppins[i];
        if (p.has_nprobe && p.has_comm && p.has_otag) {
            g_epins = realloc(g_epins, sizeof *g_epins * (g_nepins + 1));
            g_epins[g_nepins++] = p;
        } else
            g_ppins[k++] = p;
    }
    g_nppins = k;
    if (g_nepins > 1) qsort(g_epins, g_nepins, sizeof *g_epins, by_envelope);
}

static void chains_init(int rank);
HIDDEN void stretto_pin_init(int rank) {
    /* ld.so ignores a preloaded library it cannot open, and the run then goes on unpinned and
       undelayed, as a plain run. Rank 0 therefore announces the library, which the harness
       requires (stretto.confirm.run_pinned, experiments/harness.sh): always from the replay
       library, and from the tracer when it applies pins or delays. */
#ifdef STRETTO_PIN_ONLY
    int announce = 1;
    const char *what = "replay library";
#else
    int announce = getenv("STRETTO_PIN") || getenv("STRETTO_PIN_PROBE") || getenv("STRETTO_PIN_FLIP") || getenv("STRETTO_SEND_DELAY_MS")
                   || getenv("STRETTO_PIN_CHAIN") || getenv("STRETTO_PIN_ORDER") || getenv("STRETTO_PIN_LOCK")
                   || getenv("STRETTO_RECV_DELAY_MS") || getenv("STRETTO_DELAY_AT");
    const char *what = "tracer";
#endif
    if (announce && rank == 0) fprintf(stderr, "[stretto-pin] rank 0: loaded (%s)\n", what);
    g_rank = rank;
    g_nwild = g_nsend = g_nrecv = 0;
    g_npins = parse_pins("STRETTO_PIN", rank, &g_pins);
    if (g_npins)
        fprintf(stderr, "[stretto-pin] rank %d: %d pin(s) active\n", rank, g_npins);
    g_nprobe = 0;
    g_nenv = 0;
    g_nppins = parse_pins("STRETTO_PIN_PROBE", rank, &g_ppins);
    split_envelope_pins();
    g_calls = 0;
    g_nfpins = parse_pins("STRETTO_PIN_FLIP", rank, &g_fpins);
    chains_init(rank);
    g_send_delay_ms = parse_rank_ms(getenv("STRETTO_SEND_DELAY_MS"), rank);
    g_recv_delay_ms = parse_rank_ms(getenv("STRETTO_RECV_DELAY_MS"), rank);
    const char *at = getenv("STRETTO_DELAY_AT");
    if (at) {
        char *copy = strdup(at), *save = NULL;
        for (char *tok = strtok_r(copy, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
            int r, k, ms; char kind[8];
            if (sscanf(tok, "%d:%7[a-z]:%d:%d", &r, kind, &k, &ms) == 4 && r == rank) {
                g_at = realloc(g_at, sizeof *g_at * (g_nat + 1));
                g_at[g_nat].is_send = strcmp(kind, "send") == 0; g_at[g_nat].k = k; g_at[g_nat].ms = ms; g_nat++;
            }
        }
        free(copy);
    }
}

/* The sender's world rank as a rank of comm (the remote group of an intercommunicator);
   MPI_UNDEFINED if it is not a member. */
static int to_comm_rank(MPI_Comm comm, int world_rank) {
    int np = 0; PMPI_Comm_size(MPI_COMM_WORLD, &np);
    if (world_rank < 0 || world_rank >= np) return MPI_UNDEFINED;
    if (comm == MPI_COMM_WORLD) return world_rank;
    MPI_Group g, w; int inter = 0, r = MPI_UNDEFINED;
    PMPI_Comm_test_inter(comm, &inter);
    if (inter) PMPI_Comm_remote_group(comm, &g); else PMPI_Comm_group(comm, &g);
    PMPI_Comm_group(MPI_COMM_WORLD, &w);
    PMPI_Group_translate_ranks(w, 1, &world_rank, g, &r);
    PMPI_Group_free(&g); PMPI_Group_free(&w);
    return r;
}

/* The identity of the operation in progress, as the trace records it: the communicator's
   creation ordinal on this rank, the tag argument (-1 for ANY_TAG), and the return address of
   the program's MPI call as an offset in its object (-1 if unknown). */
static void current_ident(MPI_Comm comm, int otag, struct pin *p) {
    Dl_info di;
    void *ra = stretto_call_site();
    p->seen_comm = stretto_comm_ordinal(comm);
    p->seen_otag = otag == MPI_ANY_TAG ? -1 : otag;
    p->seen_site = ra && dladdr(ra, &di) ? (long)((uintptr_t)ra - (uintptr_t)di.dli_fbase) : -1;
}

static int ident_holds(const struct pin *p) {
    return !p->ident || ((!p->has_comm || p->comm == p->seen_comm) && (!p->has_otag || p->otag == p->seen_otag)
                         && (!p->has_site || p->site == p->seen_site));
}

/* " id=comm:tag:site" of the run's operation, for the report lines */
static const char *seen_ident(const struct pin *p, char *buf, size_t n) {
    snprintf(buf, n, " id=%d:%d:%lx", p->seen_comm, p->seen_otag, p->seen_site < 0 ? 0 : p->seen_site);
    if (p->seen_site < 0) { char *c = strrchr(buf, ':'); if (c) strcpy(c + 1, "x"); }
    return buf;
}

/* The operation at the pin's ordinal is not the one the trace recorded there: the replay has
   diverged before the pin, and pinning another operation would only look like the
   alternative. The run ends, with its own exit code. */
static void misapplied(const char *what, int idx, const struct pin *p) {
    char seen[96], want[96], c[16] = "x", t[16] = "x", s[24] = "x";
    if (p->has_comm) snprintf(c, sizeof c, "%d", p->comm);
    if (p->has_otag) snprintf(t, sizeof t, "%d", p->otag);
    if (p->has_site) snprintf(s, sizeof s, "%lx", p->site);
    snprintf(want, sizeof want, "%s:%s:%s", c, t, s);
    fprintf(stderr, "[stretto-pin] rank %d: %s=%d misapplied: the run's operation has%s, the trace's id=%s\n",
            g_rank, what, idx, seen_ident(p, seen, sizeof seen), want);
    fflush(stderr);
    PMPI_Abort(MPI_COMM_WORLD, STRETTO_PIN_MISAPPLIED);
}

static int chain_pin_recv(int *src, int *tag, MPI_Comm comm, int cord);
static __thread int t_cord = -1;      /* the chain ordinal of the thread's receive being posted */

static int wild_pin_recv(int *src, int *tag, MPI_Comm comm, int *widx);
HIDDEN int stretto_pin_recv(int *src, int *tag, MPI_Comm comm, int *widx) {
    *widx = -1;
    int cord = t_cord;
    t_cord = -1;
    int h = *src == MPI_ANY_SOURCE || *tag == MPI_ANY_TAG ? wild_pin_recv(src, tag, comm, widx) : 0;
    if (!h && cord >= 0) h = chain_pin_recv(src, tag, comm, cord);
    return h;
}

static int wild_pin_recv(int *src, int *tag, MPI_Comm comm, int *widx) {
    pthread_mutex_lock(&g_pmu);
    int idx = g_nwild++;
    pthread_mutex_unlock(&g_pmu);
    *widx = idx;
    struct pin *p = find_pin(g_pins, g_npins, idx);
    if (!p) return 0;
    char id[96];
    current_ident(comm, *tag, p);
    if (!ident_holds(p)) misapplied("pin widx", idx, p);
    if (*src == MPI_ANY_SOURCE) {
        int cr = to_comm_rank(comm, p->src);
        if (cr == MPI_UNDEFINED) {
            fprintf(stderr, "[stretto-pin] rank %d: pin widx=%d names world rank %d, not a member of the communicator: not applied\n", g_rank, idx, p->src);
            return 0;
        }
        *src = cr;
    }
    if (*tag == MPI_ANY_TAG && p->tag != MPI_ANY_TAG) *tag = p->tag;
    fprintf(stderr, "[stretto-pin] rank %d: applied widx=%d src=%d (world %d) tag=%d%s\n", g_rank, idx, *src, p->src, *tag,
            seen_ident(p, id, sizeof id));
    return idx + 1;
}

static void chain_pin_matched(int i, int comm_src);
/* A pinned receive completed: it can only have matched the pinned source, so this line is
   the evidence that the forced match happened ("applied" only says the argument was set). */
HIDDEN void stretto_pin_matched(int h, int comm_src) {
    if (h < 0) { chain_pin_matched(-h - 1, comm_src); return; }
    int widx = h - 1;
    char id[96] = "";
    struct pin *p = find_pin(g_pins, g_npins, widx);
    fprintf(stderr, "[stretto-pin] rank %d: matched widx=%d src=%d%s\n", g_rank, widx, comm_src,
            p ? seen_ident(p, id, sizeof id) : "");
}

/* The last peek (stretto_pin_probe): the pidx it gave, the pin it looked at (NULL if none), and
   whether that pin fell on an operation of another identity. Every peek of a wildcard probe sets
   all three, so that a commit never acts on an earlier probe's pin. */
static int g_peek_idx = -1;
static struct pin *g_peek_pin = NULL;
static int g_peek_bad = 0;

HIDDEN int stretto_pin_probe_ordinal(int src, int tag) {
    if (src != MPI_ANY_SOURCE && tag != MPI_ANY_TAG) return -1;
    pthread_mutex_lock(&g_pmu);
    int idx = g_nprobe;
    g_peek_idx = idx; g_peek_pin = NULL; g_peek_bad = 0;   /* a flip forces this call, no probe pin */
    pthread_mutex_unlock(&g_pmu);
    return idx;
}

HIDDEN int stretto_pin_probe(int *src, int *tag, MPI_Comm comm, int *pidx) {
    /* Peek only: the ordinals advance in stretto_pin_probe_commit(), called when the probe
       found a message. Failed polls (Iprobe/Improbe with flag 0) do not count, so the
       ordinals are the same in a traced run and in a pinned replay however long each busy
       loop spins. A pin with an envelope ordinal (the fourth field of its identity) is the
       one of this probe's communicator and tag at the number of its probes that found a
       message so far; one without is the one of the rank's ordinal. A probe whose identity
       differs from the pin's is not pinned; if it then finds a message, it takes the pinned
       ordinal, and stretto_pin_probe_commit ends the run. */
    *pidx = -1;
    if (*src != MPI_ANY_SOURCE && *tag != MPI_ANY_TAG) return 0;
    int ecomm = stretto_comm_ordinal(comm), eotag = *tag == MPI_ANY_TAG ? -1 : *tag;
    pthread_mutex_lock(&g_pmu);
    int idx = g_nprobe;
    struct pin *p = g_nepins ? find_epin(ecomm, eotag, *env_slot(ecomm, eotag)) : NULL;
    if (!p) p = find_pin(g_ppins, g_nppins, idx);
    g_peek_idx = idx; g_peek_pin = NULL; g_peek_bad = 0;
    pthread_mutex_unlock(&g_pmu);
    *pidx = idx;
    if (!p || p->used) return 0;
    current_ident(comm, *tag, p);
    if (!ident_holds(p)) {
        pthread_mutex_lock(&g_pmu); g_peek_pin = p; g_peek_bad = 1; pthread_mutex_unlock(&g_pmu);
        return 0;
    }
    if (p->src == -1) {
        /* a flag flip to false: this call returns flag 0 without polling, once; the ordinal does
           not advance (no message was found), and the loop's next call at it is not pinned */
        char id[96];
        p->used = 1;
        fprintf(stderr, "[stretto-pin] rank %d: applied unflip pidx=%d%s\n", g_rank, p->idx, seen_ident(p, id, sizeof id));
        return 2;
    }
    if (*src == MPI_ANY_SOURCE) {
        int cr = to_comm_rank(comm, p->src);
        if (cr == MPI_UNDEFINED) {
            fprintf(stderr, "[stretto-pin] rank %d: probe pin pidx=%d names world rank %d, not a member of the communicator: not applied\n", g_rank, p->idx, p->src);
            return 0;
        }
        *src = cr;
    }
    if (*tag == MPI_ANY_TAG && p->tag != MPI_ANY_TAG) *tag = p->tag;
    pthread_mutex_lock(&g_pmu); g_peek_pin = p; pthread_mutex_unlock(&g_pmu);
    return 1;
}

HIDDEN void stretto_pin_probe_commit(int pidx, MPI_Comm comm, int otag, int src, int tag) {
    if (pidx < 0) return;
    int ecomm = stretto_comm_ordinal(comm), eotag = otag == MPI_ANY_TAG ? -1 : otag;
    pthread_mutex_lock(&g_pmu);
    if (g_nprobe == pidx) {
        g_nprobe++;
        (*env_slot(ecomm, eotag))++;
        struct pin *p = g_peek_idx == pidx ? g_peek_pin : NULL;
        g_peek_idx = -1; g_peek_pin = NULL;
        if (p && g_peek_bad) {
            pthread_mutex_unlock(&g_pmu);
            misapplied("probe pin pidx", p->idx, p);
        }
        if (p) {
            char id[96];
            p->used = 1;
            fprintf(stderr, "[stretto-pin] rank %d: applied probe pidx=%d src=%d tag=%d%s\n", g_rank, p->idx, src, tag,
                    seen_ident(p, id, sizeof id));
        }
    }
    pthread_mutex_unlock(&g_pmu);
}

/* ---- flag flips (STRETTO_PIN_FLIP). A failed poll is named by its key: the number of the
   rank's calls before it that are not failed polls, as the tracer's records give them. Every
   record passes here; a failed poll (an Iprobe or Improbe with flag 0, a Test, Testall,
   Testany, Testsome or Request_get_status that found nothing) does not count, nor do the
   buffer guard's and overflow records, which are not calls, nor a blocking call's begin
   record, which the analysis drops with its completion (trace._fold_begins). The count is the
   analysis's (trace.poll_segments, Trace.poll_key). A program consistent with the trace issues
   the same calls between its failed polls, so the key does not depend on how often a polling
   loop spins, nor on whether it failed at all between two calls; the ordinal of the poll
   segment does, since a loop that finds its messages without failing in between has fewer. */
HIDDEN void stretto_pin_record(const char *body, int failed_poll) {
    if (!g_nfpins || failed_poll || strncmp(body, "\"op\":\"guard\"", 12) == 0
        || strncmp(body, "\"op\":\"overflow\"", 15) == 0 || strncmp(body, "\"op\":\"begin\"", 12) == 0)
        return;
    pthread_mutex_lock(&g_pmu);
    g_calls++;
    pthread_mutex_unlock(&g_pmu);
}

/* A flip pin that names the number of wildcard probes found before its call (the fourth field
   of its identity) and meets its call after another number: the replay diverged before the
   call, and forcing it could wait for a message that never comes. The run ends as for any
   misapplied pin. For a probe whose identity names its communicator and tag the number is that
   of its envelope's probes, which another polling loop of the rank (another tag, another
   communicator) does not change however its messages interleave; for a test, or a probe of an
   unchecked communicator, it is that of all the rank's wildcard probes. */
static void flip_count_holds(const struct pin *p, int key, int is_test) {
    if (!p->has_nprobe) return;
    pthread_mutex_lock(&g_pmu);
    int n = !is_test && p->has_comm && p->has_otag ? *env_slot(p->seen_comm, p->seen_otag) : g_nprobe;
    pthread_mutex_unlock(&g_pmu);
    if (n == p->nprobe) return;
    fprintf(stderr, "[stretto-pin] rank %d: flip pin key=%d misapplied: the run's call follows %d wildcard probe(s) that found a message, the trace's %d\n",
            g_rank, key, n, p->nprobe);
    fflush(stderr);
    PMPI_Abort(MPI_COMM_WORLD, STRETTO_PIN_MISAPPLIED);
}

static int g_flip_key = -1;          /* the key of the flip being forced (for the matched line) */
static struct pin *g_flip_pin = NULL;

HIDDEN int stretto_pin_flip(int is_test, int src, int tag, MPI_Comm comm, int *fsrc, int *ftag, int *key) {
    if (!g_nfpins) return 0;
    pthread_mutex_lock(&g_pmu);
    int k = g_calls;                          /* the calls before this one, its key if it fails */
    pthread_mutex_unlock(&g_pmu);
    *key = k;
    struct pin *p = find_pin(g_fpins, g_nfpins, k);
    for (; p && p < g_fpins + g_nfpins && p->idx == k; p++) {
        if (p->used || (is_test ? p->src >= 0 : p->src == -2)) continue;
        current_ident(comm, is_test ? MPI_ANY_TAG : tag, p);
        if (is_test) p->seen_comm = p->comm, p->seen_otag = p->otag;   /* a test has neither: its site names it */
        if (!ident_holds(p)) continue;
        char id[96];
        if (p->src == -1) {
            flip_count_holds(p, k, is_test);
            p->used = 1;
            fprintf(stderr, "[stretto-pin] rank %d: applied flip key=%d flag=0%s\n", g_rank, k, seen_ident(p, id, sizeof id));
            return -1;
        }
        if (is_test) {
            flip_count_holds(p, k, is_test);
            p->used = 1; g_flip_key = k; g_flip_pin = p;
            fprintf(stderr, "[stretto-pin] rank %d: applied flip key=%d test%s\n", g_rank, k, seen_ident(p, id, sizeof id));
            return 1;
        }
        int cr = to_comm_rank(comm, p->src);
        if (cr == MPI_UNDEFINED || (src != MPI_ANY_SOURCE && src != cr)) continue;   /* another call of the loop */
        int t = tag == MPI_ANY_TAG ? p->tag : tag;
        if (tag != MPI_ANY_TAG && p->tag != MPI_ANY_TAG && p->tag != tag) continue;
        flip_count_holds(p, k, is_test);
        p->used = 1; g_flip_key = k; g_flip_pin = p;
        *fsrc = cr; *ftag = t;
        fprintf(stderr, "[stretto-pin] rank %d: applied flip key=%d src=%d (world %d) tag=%d%s\n", g_rank, k, cr, p->src, t,
                seen_ident(p, id, sizeof id));
        return 1;
    }
    return 0;
}

HIDDEN void stretto_pin_flip_matched(int key, int src, int tag) {
    char id[96] = "";
    if (g_flip_pin && g_flip_key == key) seen_ident(g_flip_pin, id, sizeof id);
    fprintf(stderr, "[stretto-pin] rank %d: matched flip key=%d src=%d tag=%d%s\n", g_rank, key, src, tag, id);
}


static void chains_finalize(void);
HIDDEN void stretto_pin_finalize(void) {
    chains_finalize();
    for (int i = 0; i < g_npins; i++)
        if (g_pins[i].idx >= g_nwild)
            fprintf(stderr, "[stretto-pin] rank %d: pin widx=%d unused: the run posted %d wildcard receive(s)\n", g_rank, g_pins[i].idx, g_nwild);
    for (int i = 0; i < g_nppins; i++)
        if (g_ppins[i].idx >= g_nprobe && !g_ppins[i].used)
            fprintf(stderr, "[stretto-pin] rank %d: probe pin pidx=%d unused: %d wildcard probe(s) found a message\n", g_rank, g_ppins[i].idx, g_nprobe);
    for (int i = 0; i < g_nepins; i++)
        if (!g_epins[i].used) {
            const struct pin *p = &g_epins[i];
            int n = 0;
            for (int j = 0; j < g_nenv; j++) if (g_env[j].comm == p->comm && g_env[j].otag == p->otag) n = g_env[j].n;
            fprintf(stderr, "[stretto-pin] rank %d: probe pin pidx=%d unused: %d wildcard probe(s) of comm %d tag %d found a message\n",
                    g_rank, p->idx, n, p->comm, p->otag);
        }
    for (int i = 0; i < g_nfpins; i++)
        if (!g_fpins[i].used)
            fprintf(stderr, "[stretto-pin] rank %d: flip pin key=%d unused: %d call(s) other than failed polls, no call of its identity forced\n", g_rank, g_fpins[i].idx, g_calls);
}

static void delay_at(int is_send, int k) {
    for (int i = 0; i < g_nat; i++) if (g_at[i].is_send == is_send && g_at[i].k == k) msleep(g_at[i].ms);
}
HIDDEN void stretto_pin_before_send(void) {
    pthread_mutex_lock(&g_pmu); int k = g_nsend++; pthread_mutex_unlock(&g_pmu);
    msleep(g_send_delay_ms); delay_at(1, k);
}
HIDDEN void stretto_pin_before_recv(void) {
    pthread_mutex_lock(&g_pmu); int k = g_nrecv++; pthread_mutex_unlock(&g_pmu);
    msleep(g_recv_delay_ms); delay_at(0, k);
}

/* ---- several threads (STRETTO_PIN_CHAIN, STRETTO_PIN_ORDER; docs/threads.md, sections 4 and 11).
   A chain is named by the tracer (stretto_chain_name); each chain counts its receives and sends
   as they are posted and its probes as they find a message, and the gates of an operation wait
   for the operations of other chains named before it. The counters live per chain name, not per
   thread: an OpenMP thread runs another chain in each parallel region. */
const char *stretto_chain_name(void);   /* tracer/mpitrace.c; NULL in the replay library alone */

#define CHAIN_NAME 160
/* soft: a gate beyond the horizon ("~" after the token), which follows the recorded order where
   the run still does and gives way where it does not (docs/threads.md, section 19) */
struct gate { char wchain[CHAIN_NAME]; int wkind, word; char pchain[CHAIN_NAME]; int pkind, pord; int reached, soft; };
/* sdead: a soft gate waited for this chain in vain, and the soft gates that wait for it are open */
struct cstate { char name[CHAIN_NAME]; int next[3], posted[3]; int sdead; };
static struct pin *g_cpins = NULL;             /* this rank's chain pins, by (chain, idx) */
static int g_ncpins = 0;
static struct gate *g_gates = NULL;            /* this rank's gates, by (waiting chain, kind, ordinal) */
static int g_ngates = 0;
static struct cstate *g_cs = NULL;
static int g_ncs = 0, g_capcs = 0;
static int g_chains_on = 0;
static long g_gate_ms = 10000;
static pthread_mutex_t g_gmu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_gcv = PTHREAD_COND_INITIALIZER;
static const char KIND[3] = {'r', 's', 'p'};

static int by_chain_idx(const void *a, const void *b) {
    const struct pin *x = a, *y = b;
    int c = strcmp(x->chain, y->chain);
    return c ? c : (x->idx < y->idx ? -1 : x->idx > y->idx);
}
static int gate_cmp(const char *c1, int k1, int o1, const char *c2, int k2, int o2) {
    int c = strcmp(c1, c2);
    return c ? c : k1 != k2 ? (k1 < k2 ? -1 : 1) : o1 != o2 ? (o1 < o2 ? -1 : 1) : 0;
}
static int by_waiter(const void *a, const void *b) {
    const struct gate *x = a, *y = b;
    return gate_cmp(x->wchain, x->wkind, x->word, y->wchain, y->wkind, y->word);
}
static int kind_of(char c) { return c == 'r' ? 0 : c == 's' ? 1 : c == 'p' ? 2 : -1; }

/* A run of several threads ends at its first gate that times out or pin that falls on another
   operation: the first thread to get there aborts, and the others wait for the abort, since two
   threads in MPI_Abort at once can crash Open MPI's error reporting. The gates open first: Open
   MPI's abort takes a mutex in a component library (mca_pmix_ext3x.so) that the tracer counts as
   the program's, and its lock gate held the abort until the gate timed out, so that a misapplied
   pin read as a gate timeout of the aborting chain waiting for its own attempt. */
static int g_locks_on;
static void threads_abort(int code) {
    static int aborting = 0;
    if (__atomic_exchange_n(&aborting, 1, __ATOMIC_ACQ_REL))
        for (;;) pause();
    __atomic_store_n(&g_locks_on, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&g_chains_on, 0, __ATOMIC_RELEASE);
    PMPI_Abort(MPI_COMM_WORLD, code);
}
static void bad_token(const char *name, const char *tok) {
    fprintf(stderr, "[stretto-pin] rank %d: %s: token '%s' does not parse\n", g_rank, name, tok);
    PMPI_Abort(MPI_COMM_WORLD, 3);
}

/* ---- waiting at a gate (docs/threads.md, sections 11, 14 and 19). A gated thread holds its
   thread: an OpenMP task that waits keeps its thread from the team, and a runtime's thread that
   waits may be the one the operation it waits for needs (premise P-T7). A gate that stays closed
   while nothing opens any gate of the rank therefore gives way: once STRETTO_GATE_YIELD_MS have
   passed without an operation posted or a lock taken by any chain, the thread that has waited
   longest goes on as if its gate had opened ("gate yielded"), and the others wait on, the period
   starting again. Whether the replay still is the alternative is then for its own trace to say
   (every required pin taken), as for any replay; the run is not ended. A soft gate (beyond the
   horizon) also gives way after STRETTO_GATE_SOFT_MS of waiting, progress or not, and then
   stops following the chain it waited for. A hard gate still closed after STRETTO_GATE_TIMEOUT_MS
   ends the run as before. */
struct waiter { unsigned long ticket; struct timespec since; struct waiter *next; };
static struct waiter *g_waiters = NULL;      /* the threads waiting at gates (caller holds g_gmu) */
static unsigned long g_ticket = 0;
static struct timespec g_progress;           /* when a gate could last have opened */
static long g_yield_ms = 0, g_soft_ms = 0;   /* 0: never */
static int g_yields = 0, g_soft_yields = 0;
static long ms_between(const struct timespec *a, const struct timespec *b) {
    return (b->tv_sec - a->tv_sec) * 1000L + (b->tv_nsec - a->tv_nsec) / 1000000L;
}
static void progress_locked(void) { clock_gettime(CLOCK_REALTIME, &g_progress); }
static void waiter_add(struct waiter *w) {
    w->ticket = g_ticket++;
    clock_gettime(CLOCK_REALTIME, &w->since);
    w->next = g_waiters; g_waiters = w;
}
static void waiter_del(struct waiter *w) {
    for (struct waiter **p = &g_waiters; *p; p = &(*p)->next)
        if (*p == w) { *p = w->next; return; }
}
static int waiter_oldest(const struct waiter *w) {
    for (const struct waiter *o = g_waiters; o; o = o->next) if (o->ticket < w->ticket) return 0;
    return 1;
}
/* One slice of a wait on g_gcv (caller holds g_gmu, w registered), the gate still closed after
   it: 0 wait on, 1 give way (soft: as a soft gate), -1 the hard deadline has passed. */
static int gate_slice(struct waiter *w, const struct timespec *until, int soft) {
    struct timespec now, dl;
    clock_gettime(CLOCK_REALTIME, &now);
    long slice = 1000;
    if (g_yield_ms > 0 && g_yield_ms / 4 < slice) slice = g_yield_ms / 4 > 5 ? g_yield_ms / 4 : 5;
    if (soft && g_soft_ms > 0 && g_soft_ms / 4 < slice) slice = g_soft_ms / 4 > 5 ? g_soft_ms / 4 : 5;
    dl = now;
    dl.tv_sec += slice / 1000; dl.tv_nsec += (slice % 1000) * 1000000L;
    if (dl.tv_nsec >= 1000000000L) { dl.tv_sec++; dl.tv_nsec -= 1000000000L; }
    if (dl.tv_sec > until->tv_sec || (dl.tv_sec == until->tv_sec && dl.tv_nsec > until->tv_nsec)) dl = *until;
    pthread_cond_timedwait(&g_gcv, &g_gmu, &dl);
    clock_gettime(CLOCK_REALTIME, &now);
    if (soft && g_soft_ms > 0 && ms_between(&w->since, &now) >= g_soft_ms) return 1;
    if (g_yield_ms > 0 && ms_between(&g_progress, &now) >= g_yield_ms && ms_between(&w->since, &now) >= g_yield_ms
        && waiter_oldest(w)) {
        g_progress = now;                     /* the next waiter waits a period of its own */
        return 1;
    }
    if (now.tv_sec > until->tv_sec || (now.tv_sec == until->tv_sec && now.tv_nsec >= until->tv_nsec)) return soft ? 1 : -1;
    return 0;
}
static int nwaiting_locked(void) {
    int n = 0;
    for (const struct waiter *o = g_waiters; o; o = o->next) n++;
    return n;
}
/* the report of a gate that gave way (caller holds g_gmu) */
static void yielded(const char *name, int soft, const struct waiter *w, const char *what) {
    struct timespec now;
    clock_gettime(CLOCK_REALTIME, &now);
    if (soft) g_soft_yields++; else g_yields++;
    fprintf(stderr, "[stretto-pin] rank %d chain %s: %sgate yielded after %ld ms: %s; %d thread(s) wait at gates\n", g_rank, name,
            soft ? "soft " : "", ms_between(&w->since, &now), what, nwaiting_locked());
}


/* ---- lock gates (STRETTO_PIN_LOCK; docs/threads.md, sections 14 and 19). The tracer numbers
   each chain's attempts to take a lock (its sync log's ordinals) and asks before each one
   (stretto_pin_lock) and after it (stretto_pin_locked). An attempt a token names waits until
   the attempt before it in the token has taken the lock; one no token names waits, if its lock
   is one a token names, until all of that lock's listed acquisitions of the horizon have
   happened. The entries from one that starts with '~' on are the lock's tail beyond the horizon
   (soft): they wait in the same way, but a soft entry that falls on another lock than the
   token's, or that gives way (gate_slice), ends the following of that lock's tail ("lock tail
   abandoned") instead of the run. */
struct lacq { int chain; long n; int lock, pos, pchain; long pn; char flag; char soft, bad; };
/* len: the listed acquisitions, hard of them the horizon's (the rest the soft tail); done and
   hdone: how many of them, and of the horizon's, have happened; dead: the tail is no longer
   followed */
struct lseq { int len, hard, done, hdone, dead; const void *addr; };
static struct lacq *g_lacq = NULL;           /* by (chain, n) */
static int g_nlacq = 0;
static struct lseq *g_lseq = NULL;
static int g_nlseq = 0;
static char (*g_lname)[CHAIN_NAME] = NULL;   /* the chains the tokens name */
static long *g_ldone = NULL;                 /* per such chain: attempts finished (the latest ordinal + 1) */
static int g_nlname = 0, g_caplname = 0;
static struct { const void *addr; int lock; } *g_laddr = NULL;   /* address -> lock, open addressing */
static int g_laddr_cap = 0;
static int g_locks_on = 0, g_ltails_dead = 0;

static int lname_find(const char *name) {
    for (int i = 0; i < g_nlname; i++) if (strcmp(g_lname[i], name) == 0) return i;
    return -1;
}
static int lname_add(const char *name) {
    static int last = -1;
    if (last >= 0 && strcmp(g_lname[last], name) == 0) return last;
    int i = lname_find(name);
    if (i < 0) {
        if (g_nlname == g_caplname) {
            g_caplname = g_caplname ? 2 * g_caplname : 64;
            g_lname = realloc(g_lname, sizeof *g_lname * g_caplname);
            g_ldone = realloc(g_ldone, sizeof *g_ldone * g_caplname);
        }
        i = g_nlname++;
        snprintf(g_lname[i], CHAIN_NAME, "%s", name);
        g_ldone[i] = 0;
    }
    return last = i;
}
/* the calling thread's chain among them (-1: none), cached per thread by the name's address */
static int lname_cur(void) {
    static __thread const char *t_name = NULL;
    static __thread int t_idx = -1;
    const char *name = stretto_chain_name();
    if (!name) return -1;
    if (name != t_name || t_idx < 0 || strcmp(g_lname[t_idx], name) != 0) {
        t_name = name;
        t_idx = lname_find(name);
        if (t_idx < 0) { t_name = NULL; return -1; }
    }
    return t_idx;
}
static int by_lacq(const void *a, const void *b) {
    const struct lacq *x = a, *y = b;
    return x->chain != y->chain ? (x->chain < y->chain ? -1 : 1) : x->n < y->n ? -1 : x->n > y->n;
}
static struct lacq *lacq_find(int chain, long n) {
    int lo = 0, hi = g_nlacq;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (g_lacq[mid].chain < chain || (g_lacq[mid].chain == chain && g_lacq[mid].n < n)) lo = mid + 1; else hi = mid;
    }
    return lo < g_nlacq && g_lacq[lo].chain == chain && g_lacq[lo].n == n ? &g_lacq[lo] : NULL;
}
static size_t laddr_slot(const void *addr) {
    size_t i = ((uintptr_t)addr >> 3) & (g_laddr_cap - 1);
    while (g_laddr[i].addr && g_laddr[i].addr != addr) i = (i + 1) & (g_laddr_cap - 1);
    return i;
}

/* "[~]name#n[t|!]" at s: the chain, the ordinal, the flag and whether the tail starts here */
static int parse_lacq(char *s, struct lacq *a, int *tail) {
    *tail = *s == '~';
    if (*tail) s++;
    char *hash = strrchr(s, '#'), *end = NULL;
    if (!hash || hash == s) return 0;
    *hash = '\0';
    a->n = strtol(hash + 1, &end, 10);
    if (end == hash + 1 || (*end && ((*end != 't' && *end != '!') || end[1]))) return 0;
    a->flag = *end;
    a->chain = lname_add(s);
    return 1;
}
static void locks_init(int rank) {
    char *spec = pin_spec("STRETTO_PIN_LOCK", rank), *save = NULL;
    int cap = 0;
    g_nlacq = g_nlseq = 0;
    for (char *tok = spec ? strtok_r(spec, ", \n", &save) : NULL; tok; tok = strtok_r(NULL, ", \n", &save)) {
        char *colon = strchr(tok, ':');
        if (!colon) bad_token("STRETTO_PIN_LOCK", tok);
        if (atoi(tok) != rank) continue;
        int lock = g_nlseq++, pos = 0, soft = 0, hard = -1;
        g_lseq = realloc(g_lseq, sizeof *g_lseq * g_nlseq);
        memset(&g_lseq[lock], 0, sizeof g_lseq[lock]);
        char *s2 = NULL;
        for (char *seg = strtok_r(colon + 1, ">", &s2); seg; seg = strtok_r(NULL, ">", &s2), pos++) {
            struct lacq a;
            int tail = 0;
            memset(&a, 0, sizeof a);
            if (!parse_lacq(seg, &a, &tail) || (a.flag == '!' && pos > 0)) bad_token("STRETTO_PIN_LOCK", seg);
            if (tail && !soft) { soft = 1; hard = pos; }
            a.soft = (char)soft;
            a.lock = a.flag == '!' ? -1 : lock;
            a.pos = pos;
            a.pchain = pos ? g_lacq[g_nlacq - 1].chain : -1;
            a.pn = pos ? g_lacq[g_nlacq - 1].n : -1;
            if (g_nlacq == cap) { cap = cap ? 2 * cap : 64; g_lacq = realloc(g_lacq, sizeof *g_lacq * cap); }
            g_lacq[g_nlacq++] = a;
        }
        g_lseq[lock].len = g_lacq[g_nlacq - 1].flag == '!' ? 0 : pos;
        g_lseq[lock].hard = hard < 0 ? g_lseq[lock].len : hard;
    }
    free(spec);
    if (g_nlacq > 1) qsort(g_lacq, g_nlacq, sizeof *g_lacq, by_lacq);
    for (g_laddr_cap = 16; g_laddr_cap < 2 * g_nlseq + 16; g_laddr_cap *= 2) {}
    g_laddr = calloc(g_laddr_cap, sizeof *g_laddr);
    g_locks_on = g_nlacq > 0;
    if (g_locks_on)
        fprintf(stderr, "[stretto-pin] rank %d: %d lock gate(s) on %d lock(s) active\n", rank, g_nlacq, g_nlseq);
}

HIDDEN int stretto_pin_locks_on(void) { return g_locks_on; }

/* the deadline of a wait that starts now */
static struct timespec deadline(void) {
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_sec += g_gate_ms / 1000;
    until.tv_nsec += (g_gate_ms % 1000) * 1000000L;
    if (until.tv_nsec >= 1000000000L) { until.tv_sec++; until.tv_nsec -= 1000000000L; }
    return until;
}
/* the soft tail of lock L is no longer followed (caller holds g_gmu) */
static void tail_dead(struct lseq *L, const char *name, long n, const char *why) {
    if (L->dead) return;
    L->dead = 1;
    g_ltails_dead++;
    fprintf(stderr, "[stretto-pin] rank %d chain %s: lock tail abandoned at a#%ld: %s\n", g_rank, name, n, why);
    pthread_cond_broadcast(&g_gcv);
}

HIDDEN int stretto_pin_lock(long n, const void *key, int is_try, int poll) {
    static __thread long t_pn = -1;               /* a polled attempt: when its polling began */
    static __thread struct waiter t_pw;
    if (!g_locks_on) return STRETTO_LOCK_GO;
    int ci = lname_cur();
    const char *name = stretto_chain_name();
    pthread_mutex_lock(&g_gmu);
    struct lacq *e = ci >= 0 ? lacq_find(ci, n) : NULL;
    if (e && e->flag == '!') {
        pthread_mutex_unlock(&g_gmu);
        return is_try ? STRETTO_LOCK_FAIL : STRETTO_LOCK_GO;
    }
    struct lseq *L = NULL;
    if (e) L = &g_lseq[e->lock];
    else {
        int li = g_laddr[laddr_slot(key)].addr ? g_laddr[laddr_slot(key)].lock : -1;
        if (li < 0 || g_lseq[li].hdone >= g_lseq[li].hard) { pthread_mutex_unlock(&g_gmu); return STRETTO_LOCK_GO; }
        L = &g_lseq[li];
    }
    if (e && e->soft && L->dead) { pthread_mutex_unlock(&g_gmu); return STRETTO_LOCK_GO; }
    int soft = e && e->soft;
    /* ready: the token's previous acquisition has happened (for an attempt no token names, all of
       the horizon's); a soft entry also once its tail is abandoned */
#define LOCK_READY() (e ? e->pos == 0 || g_ldone[e->pchain] > e->pn || (e->soft && L->dead) : L->hdone >= L->hard)
#define LOCK_WHAT(buf) (e ? snprintf(buf, sizeof buf, "lock attempt a#%ld waits for a#%ld of chain %s, which has finished %ld", n, e->pn, \
                                     g_lname[e->pchain], g_ldone[e->pchain]) \
                          : snprintf(buf, sizeof buf, "lock attempt a#%ld (beyond the gates) waits for %d of its lock's %d listed acquisitions", \
                                     n, L->hard - L->hdone, L->hard))
    if (!LOCK_READY()) {
        char what[CHAIN_NAME + 128];
        int gave_way = 0;
        if (poll) {
            /* a condition wait asks again every few ms (cond_turn); it counts as waiting from its first ask */
            if (t_pn != n) { t_pn = n; t_pw.ticket = g_ticket++; clock_gettime(CLOCK_REALTIME, &t_pw.since); }
            struct timespec now;
            clock_gettime(CLOCK_REALTIME, &now);
            long waited = ms_between(&t_pw.since, &now);
            if ((soft && g_soft_ms > 0 && waited >= g_soft_ms)
                || (g_yield_ms > 0 && waited >= g_yield_ms && ms_between(&g_progress, &now) >= g_yield_ms)
                || (soft && waited >= g_gate_ms)) {
                if (!soft) g_progress = now;
                LOCK_WHAT(what);
                yielded(name, soft, &t_pw, what);
                gave_way = 1;
            } else if (waited >= g_gate_ms) {
                LOCK_WHAT(what);
                fprintf(stderr, "[stretto-pin] rank %d chain %s: gate timed out: %s; %d thread(s) wait at gates\n", g_rank, name, what,
                        nwaiting_locked() + 1);
                fflush(stderr);
                pthread_mutex_unlock(&g_gmu);
                threads_abort(STRETTO_PIN_GATE_TIMEOUT);
            } else {
                pthread_mutex_unlock(&g_gmu);
                return STRETTO_LOCK_NOTYET;
            }
        } else {
            struct timespec until = deadline();
            struct waiter w;
            waiter_add(&w);
            while (!LOCK_READY()) {
                int rc = gate_slice(&w, &until, soft);
                if (rc == 0 || LOCK_READY()) continue;
                LOCK_WHAT(what);
                if (rc < 0) {
                    waiter_del(&w);
                    fprintf(stderr, "[stretto-pin] rank %d chain %s: gate timed out: %s; %d thread(s) wait at gates\n", g_rank, name, what,
                            nwaiting_locked() + 1);
                    fflush(stderr);
                    pthread_mutex_unlock(&g_gmu);
                    threads_abort(STRETTO_PIN_GATE_TIMEOUT);
                }
                yielded(name, soft, &w, what);
                gave_way = 1;
                break;
            }
            waiter_del(&w);
        }
        if (gave_way && soft) tail_dead(L, name, n, "its turn did not come");
    }
#undef LOCK_READY
#undef LOCK_WHAT
    t_pn = -1;
    if (e && !L->addr && !(soft && L->dead)) {   /* the lock's first acquisition (or the first to come) names its address */
        L->addr = key;
        size_t i = laddr_slot(key);
        g_laddr[i].addr = key;
        g_laddr[i].lock = e->lock;
    }
    if (e && L->addr != key) {
        if (soft) {
            e->bad = 1;
            tail_dead(L, name, n, "it takes another lock than the trace's");
            pthread_mutex_unlock(&g_gmu);
            return STRETTO_LOCK_GO;
        }
        fprintf(stderr, "[stretto-pin] rank %d chain %s: lock pin a#%ld misapplied: it takes another lock than a#%ld of chain %s\n",
                g_rank, name, n, e->pn, e->pchain >= 0 ? g_lname[e->pchain] : "?");
        fflush(stderr);
        pthread_mutex_unlock(&g_gmu);
        threads_abort(STRETTO_PIN_MISAPPLIED);
    }
    pthread_mutex_unlock(&g_gmu);
    if (e && e->flag == 't') return STRETTO_LOCK_TIMEDOUT;
    return e && is_try ? STRETTO_LOCK_BLOCK : STRETTO_LOCK_GO;
}

HIDDEN void stretto_pin_locked(long n, int ok) {
    if (!g_locks_on) return;
    int ci = lname_cur();
    pthread_mutex_lock(&g_gmu);
    progress_locked();
    if (ci >= 0) {
        if (g_ldone[ci] < n + 1) g_ldone[ci] = n + 1;
        struct lacq *e = lacq_find(ci, n);
        if (e && e->lock >= 0 && ok && !e->bad) {
            g_lseq[e->lock].done++;
            if (!e->soft) g_lseq[e->lock].hdone++;
        }
    }
    pthread_cond_broadcast(&g_gcv);
    pthread_mutex_unlock(&g_gmu);
}

static void locks_finalize(void) {
    if (!g_locks_on) return;
    int reached = 0, listed = 0, treached = 0, tlisted = 0;
    pthread_mutex_lock(&g_gmu);
    for (int i = 0; i < g_nlseq; i++) {
        reached += g_lseq[i].hdone; listed += g_lseq[i].hard;
        treached += g_lseq[i].done - g_lseq[i].hdone; tlisted += g_lseq[i].len - g_lseq[i].hard;
    }
    pthread_mutex_unlock(&g_gmu);
    fprintf(stderr, "[stretto-pin] rank %d: lock gates: %d of %d listed acquisitions reached\n", g_rank, reached, listed);
    if (tlisted)
        fprintf(stderr, "[stretto-pin] rank %d: lock tails: %d of %d acquisitions beyond the horizon followed, %d tail(s) abandoned\n",
                g_rank, treached, tlisted, g_ltails_dead);
}

static void chains_init(int rank) {
    char *spec = pin_spec("STRETTO_PIN_CHAIN", rank), *save = NULL;
    int cap = 0;
    g_ncpins = 0;
    for (char *tok = spec ? strtok_r(spec, ", \n", &save) : NULL; tok; tok = strtok_r(NULL, ", \n", &save)) {
        struct pin p;
        memset(&p, 0, sizeof p);
        char *eq = strchr(tok, '=');
        if (eq) *eq = '\0';                       /* the harness's part: the message to be taken */
        char *at = strchr(tok, '@');
        if (at) *at = '\0';
        int r, k = sscanf(tok, "%d:%159[^:]:%d:%d:%d", &r, p.chain, &p.idx, &p.src, &p.tag);
        if (k < 4) bad_token("STRETTO_PIN_CHAIN", tok);
        if (r != rank) continue;
        if (k < 5) p.tag = MPI_ANY_TAG;
        if (at) parse_ident(at + 1, &p);
        if (g_ncpins == cap) { cap = cap ? 2 * cap : 16; g_cpins = realloc(g_cpins, sizeof *g_cpins * cap); }
        g_cpins[g_ncpins++] = p;
    }
    free(spec);
    if (g_ncpins > 1) qsort(g_cpins, g_ncpins, sizeof *g_cpins, by_chain_idx);
    spec = pin_spec("STRETTO_PIN_ORDER", rank); save = NULL; cap = 0;
    g_ngates = 0;
    for (char *tok = spec ? strtok_r(spec, ", \n", &save) : NULL; tok; tok = strtok_r(NULL, ", \n", &save)) {
        struct gate g;
        char pk, wk;
        int r, used = 0;
        memset(&g, 0, sizeof g);
        if (sscanf(tok, "%d:%159[^#]#%c#%d<%159[^#]#%c#%d%n", &r, g.pchain, &pk, &g.pord, g.wchain, &wk, &g.word, &used) != 7
            || (g.pkind = kind_of(pk)) < 0 || (g.wkind = kind_of(wk)) < 0 || (tok[used] && strcmp(tok + used, "~") != 0))
            bad_token("STRETTO_PIN_ORDER", tok);
        g.soft = tok[used] == '~';               /* beyond the horizon: follows the run where it can */
        if (r != rank) continue;
        if (g_ngates == cap) { cap = cap ? 2 * cap : 16; g_gates = realloc(g_gates, sizeof *g_gates * cap); }
        g_gates[g_ngates++] = g;
    }
    free(spec);
    if (g_ngates > 1) qsort(g_gates, g_ngates, sizeof *g_gates, by_waiter);
    const char *ms = getenv("STRETTO_GATE_TIMEOUT_MS");
    if (ms) g_gate_ms = atol(ms);
    if ((ms = getenv("STRETTO_GATE_YIELD_MS"))) g_yield_ms = atol(ms);
    g_soft_ms = g_yield_ms;
    if ((ms = getenv("STRETTO_GATE_SOFT_MS"))) g_soft_ms = atol(ms);
    progress_locked();
    locks_init(rank);
    g_chains_on = g_ncpins > 0 || g_ngates > 0 || g_locks_on || getenv("STRETTO_PIN_CHAIN") || getenv("STRETTO_PIN_ORDER")
                  || getenv("STRETTO_PIN_LOCK");
    if (g_chains_on && !stretto_chain_name()) {
        fprintf(stderr, "[stretto-pin] rank %d: STRETTO_PIN_CHAIN, STRETTO_PIN_ORDER and STRETTO_PIN_LOCK need the tracer's chain names: "
                "preload libstretto-trace.so, not the replay library\n", rank);
        PMPI_Abort(MPI_COMM_WORLD, 3);
    }
    if (g_ncpins || g_ngates)
        fprintf(stderr, "[stretto-pin] rank %d: %d chain pin(s), %d gate(s) active\n", rank, g_ncpins, g_ngates);
    if (g_yield_ms > 0 && (g_ngates || g_locks_on))
        fprintf(stderr, "[stretto-pin] rank %d: gates give way after %ld ms without progress (soft gates after %ld ms)\n", rank,
                g_yield_ms, g_soft_ms);
}

/* The state of a chain, created at its first use (caller holds g_gmu). */
static struct cstate *chain_state(const char *name) {
    for (int i = 0; i < g_ncs; i++)
        if (strcmp(g_cs[i].name, name) == 0) return &g_cs[i];
    if (g_ncs == g_capcs) { g_capcs = g_capcs ? 2 * g_capcs : 16; g_cs = realloc(g_cs, sizeof *g_cs * g_capcs); }
    struct cstate *c = &g_cs[g_ncs++];
    memset(c, 0, sizeof *c);
    snprintf(c->name, sizeof c->name, "%s", name);
    return c;
}

/* Is gate g open? A soft gate also when the chain it waits for is no longer followed. */
static int gate_open_locked(const struct gate *g) {
    const struct cstate *p = chain_state(g->pchain);
    return p->posted[g->pkind] > g->pord || (g->soft && p->sdead);
}

HIDDEN int stretto_pin_gate(int kind, int skip) {
    if (!g_chains_on || skip) return -1;
    const char *name = stretto_chain_name();
    if (!name) return -1;
    pthread_mutex_lock(&g_gmu);
    struct cstate *c = chain_state(name);
    int ord = kind == STRETTO_OP_PROBE ? c->posted[kind] : c->next[kind]++;
    /* the gates of (name, kind, ord): contiguous in g_gates */
    int lo = 0, hi = g_ngates;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (gate_cmp(g_gates[mid].wchain, g_gates[mid].wkind, g_gates[mid].word, name, kind, ord) < 0) lo = mid + 1; else hi = mid;
    }
    struct timespec until = deadline();
    for (int i = lo; i < g_ngates && gate_cmp(g_gates[i].wchain, g_gates[i].wkind, g_gates[i].word, name, kind, ord) == 0; i++) {
        struct gate *g = &g_gates[i];
        g->reached = 1;
        if (gate_open_locked(g)) continue;
        struct waiter w;
        waiter_add(&w);
        while (!gate_open_locked(g)) {
            int rc = gate_slice(&w, &until, g->soft);
            if (rc == 0 || gate_open_locked(g)) continue;
            struct cstate *p = chain_state(g->pchain);
            char what[2 * CHAIN_NAME + 64];
            snprintf(what, sizeof what, "%c#%d waits for %c#%d of chain %s, which has posted %d", KIND[kind], ord, KIND[g->pkind], g->pord,
                     g->pchain, p->posted[g->pkind]);
            if (rc < 0) {
                waiter_del(&w);
                fprintf(stderr, "[stretto-pin] rank %d chain %s: gate timed out: %s; %d thread(s) wait at gates\n", g_rank, name, what,
                        nwaiting_locked() + 1);
                fflush(stderr);
                pthread_mutex_unlock(&g_gmu);
                threads_abort(STRETTO_PIN_GATE_TIMEOUT);
            }
            yielded(name, g->soft, &w, what);
            if (g->soft) { p->sdead = 1; pthread_cond_broadcast(&g_gcv); }
            break;
        }
        waiter_del(&w);
    }
    pthread_mutex_unlock(&g_gmu);
    if (kind == STRETTO_OP_RECV) t_cord = ord;
    return ord;
}

HIDDEN int stretto_pin_gate_open(int kind, int skip) {
    if (!g_chains_on || skip) return 1;
    const char *name = stretto_chain_name();
    if (!name) return 1;
    pthread_mutex_lock(&g_gmu);
    struct cstate *c = chain_state(name);
    int ord = kind == STRETTO_OP_PROBE ? c->posted[kind] : c->next[kind], open = 1;
    int lo = 0, hi = g_ngates;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (gate_cmp(g_gates[mid].wchain, g_gates[mid].wkind, g_gates[mid].word, name, kind, ord) < 0) lo = mid + 1; else hi = mid;
    }
    for (int i = lo; open && i < g_ngates && gate_cmp(g_gates[i].wchain, g_gates[i].wkind, g_gates[i].word, name, kind, ord) == 0; i++)
        open = gate_open_locked(&g_gates[i]);
    pthread_mutex_unlock(&g_gmu);
    return open;
}

HIDDEN void stretto_pin_posted(int kind, int ord) {
    if (!g_chains_on || ord < 0) return;
    const char *name = stretto_chain_name();
    if (!name) return;
    pthread_mutex_lock(&g_gmu);
    struct cstate *c = chain_state(name);
    if (c->posted[kind] < ord + 1) c->posted[kind] = ord + 1;
    progress_locked();
    pthread_cond_broadcast(&g_gcv);
    pthread_mutex_unlock(&g_gmu);
}

HIDDEN void stretto_pin_use_ord(int ord) { t_cord = ord; }

static int find_cpin(const char *name, int idx) {
    int lo = 0, hi = g_ncpins;
    while (lo < hi) {
        int mid = (lo + hi) / 2, c = strcmp(g_cpins[mid].chain, name);
        if (c < 0 || (c == 0 && g_cpins[mid].idx < idx)) lo = mid + 1; else hi = mid;
    }
    return lo < g_ncpins && strcmp(g_cpins[lo].chain, name) == 0 && g_cpins[lo].idx == idx ? lo : -1;
}

/* A chain pin on the receive of ordinal cord of the calling thread's chain: its identity is
   checked, an ANY_SOURCE (ANY_TAG) receive takes the pinned source (tag). Returns the handle
   -(i + 1) of pin i, 0 if none. */
static int chain_pin_recv(int *src, int *tag, MPI_Comm comm, int cord) {
    const char *name = stretto_chain_name();
    int i = name ? find_cpin(name, cord) : -1;
    if (i < 0) return 0;
    struct pin *p = &g_cpins[i];
    char id[96];
    current_ident(comm, *tag, p);
    if (!ident_holds(p)) {
        char seen[96], want[96], c[16] = "x", t[16] = "x", s[24] = "x";
        if (p->has_comm) snprintf(c, sizeof c, "%d", p->comm);
        if (p->has_otag) snprintf(t, sizeof t, "%d", p->otag);
        if (p->has_site) snprintf(s, sizeof s, "%lx", p->site);
        snprintf(want, sizeof want, "%s:%s:%s", c, t, s);
        fprintf(stderr, "[stretto-pin] rank %d chain %s: pin ridx=%d misapplied: the run's operation has%s, the trace's id=%s\n",
                g_rank, name, cord, seen_ident(p, seen, sizeof seen), want);
        fflush(stderr);
        threads_abort(STRETTO_PIN_MISAPPLIED);
    }
    if (*src == MPI_ANY_SOURCE) {
        int cr = to_comm_rank(comm, p->src);
        if (cr == MPI_UNDEFINED) {
            fprintf(stderr, "[stretto-pin] rank %d chain %s: pin ridx=%d names world rank %d, not a member of the communicator: not applied\n",
                    g_rank, name, cord, p->src);
            return 0;
        }
        *src = cr;
    }
    if (*tag == MPI_ANY_TAG && p->tag != MPI_ANY_TAG) *tag = p->tag;
    p->used = 1;
    fprintf(stderr, "[stretto-pin] rank %d chain %s: applied ridx=%d src=%d (world %d) tag=%d%s\n", g_rank, name, cord, *src, p->src,
            *tag, seen_ident(p, id, sizeof id));
    return -(i + 1);
}

static void chain_pin_matched(int i, int comm_src) {
    if (i < 0 || i >= g_ncpins) return;
    char id[96];
    struct pin *p = &g_cpins[i];
    fprintf(stderr, "[stretto-pin] rank %d chain %s: matched ridx=%d src=%d%s\n", g_rank, p->chain, p->idx, comm_src,
            seen_ident(p, id, sizeof id));
}

static void chains_finalize(void) {
    locks_finalize();
    pthread_mutex_lock(&g_gmu);
    int hy = g_yields, sy = g_soft_yields;
    pthread_mutex_unlock(&g_gmu);
    if (hy || sy) fprintf(stderr, "[stretto-pin] rank %d: gates yielded: %d hard, %d soft\n", g_rank, hy, sy);
    for (int i = 0; i < g_ncpins; i++)
        if (!g_cpins[i].used) {
            pthread_mutex_lock(&g_gmu);
            int n = chain_state(g_cpins[i].chain)->next[STRETTO_OP_RECV];
            pthread_mutex_unlock(&g_gmu);
            fprintf(stderr, "[stretto-pin] rank %d chain %s: pin ridx=%d unused: the chain posted %d receive(s)\n", g_rank,
                    g_cpins[i].chain, g_cpins[i].idx, n);
        }
    for (int i = 0; i < g_ngates; i++)
        if (!g_gates[i].reached)
            fprintf(stderr, "[stretto-pin] rank %d chain %s: gate %c#%d<%c#%d unused: the operation was not reached\n", g_rank,
                    g_gates[i].wchain, KIND[g_gates[i].pkind], g_gates[i].pord, KIND[g_gates[i].wkind], g_gates[i].word);
}
