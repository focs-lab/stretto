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

HIDDEN int stretto_pin_recv(int *src, int *tag, MPI_Comm comm, int *widx) {
    *widx = -1;
    if (*src != MPI_ANY_SOURCE && *tag != MPI_ANY_TAG) return 0;
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
    return 1;
}

/* A pinned receive completed: it can only have matched the pinned source, so this line is
   the evidence that the forced match happened ("applied" only says the argument was set). */
HIDDEN void stretto_pin_matched(int widx, int comm_src) {
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


HIDDEN void stretto_pin_finalize(void) {
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
