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
   had, for the report lines. */
struct pin {
    int idx, src, tag;
    int ident, has_comm, has_otag, has_site;
    int comm, otag; long site;
    int seen_comm, seen_otag; long seen_site;
};
static struct pin *g_pins = NULL, *g_ppins = NULL;
static int g_npins = 0, g_nppins = 0;
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

HIDDEN void stretto_pin_init(int rank) {
    /* ld.so ignores a preloaded library it cannot open, and the run then goes on unpinned and
       undelayed, as a plain run. Rank 0 therefore announces the library, which the harness
       requires (stretto.confirm.run_pinned, experiments/harness.sh): always from the replay
       library, and from the tracer when it applies pins or delays. */
#ifdef STRETTO_PIN_ONLY
    int announce = 1;
    const char *what = "replay library";
#else
    int announce = getenv("STRETTO_PIN") || getenv("STRETTO_PIN_PROBE") || getenv("STRETTO_SEND_DELAY_MS")
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
    g_nppins = parse_pins("STRETTO_PIN_PROBE", rank, &g_ppins);
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

static int g_pinned_probe = -1;   /* the pidx whose pin was applied by the last peek */
static int g_misapplied_probe = -1;   /* the pidx whose pin the last peek found on another operation */

HIDDEN int stretto_pin_probe(int *src, int *tag, MPI_Comm comm, int *pidx) {
    /* Peek only: the ordinal advances in stretto_pin_probe_commit(), called when the probe
       found a message. Failed polls (Iprobe/Improbe with flag 0) do not count, so the
       ordinal is the same in a traced run and in a pinned replay however long each busy
       loop spins. A probe whose identity differs from the pin's is not pinned; if it then
       finds a message, it takes the pinned ordinal, and stretto_pin_probe_commit ends the run. */
    *pidx = -1;
    if (*src != MPI_ANY_SOURCE && *tag != MPI_ANY_TAG) return 0;
    pthread_mutex_lock(&g_pmu);
    int idx = g_nprobe;
    pthread_mutex_unlock(&g_pmu);
    *pidx = idx;
    struct pin *p = find_pin(g_ppins, g_nppins, idx);
    if (!p) return 0;
    current_ident(comm, *tag, p);
    if (!ident_holds(p)) {
        pthread_mutex_lock(&g_pmu); g_misapplied_probe = idx; g_pinned_probe = -1; pthread_mutex_unlock(&g_pmu);
        return 0;
    }
    if (*src == MPI_ANY_SOURCE) {
        int cr = to_comm_rank(comm, p->src);
        if (cr == MPI_UNDEFINED) {
            fprintf(stderr, "[stretto-pin] rank %d: probe pin pidx=%d names world rank %d, not a member of the communicator: not applied\n", g_rank, idx, p->src);
            return 0;
        }
        *src = cr;
    }
    if (*tag == MPI_ANY_TAG && p->tag != MPI_ANY_TAG) *tag = p->tag;
    pthread_mutex_lock(&g_pmu); g_pinned_probe = idx; g_misapplied_probe = -1; pthread_mutex_unlock(&g_pmu);
    return 1;
}

HIDDEN void stretto_pin_probe_commit(int pidx, int src, int tag) {
    if (pidx < 0) return;
    pthread_mutex_lock(&g_pmu);
    if (g_nprobe == pidx) {
        g_nprobe++;
        struct pin *p = find_pin(g_ppins, g_nppins, pidx);
        if (p && g_misapplied_probe == pidx) {
            pthread_mutex_unlock(&g_pmu);
            misapplied("probe pin pidx", pidx, p);
        }
        if (g_pinned_probe == pidx) {
            char id[96];
            fprintf(stderr, "[stretto-pin] rank %d: applied probe pidx=%d src=%d tag=%d%s\n", g_rank, pidx, src, tag,
                    p ? seen_ident(p, id, sizeof id) : "");
            g_pinned_probe = -1;
        }
    }
    pthread_mutex_unlock(&g_pmu);
}

HIDDEN void stretto_pin_finalize(void) {
    for (int i = 0; i < g_npins; i++)
        if (g_pins[i].idx >= g_nwild)
            fprintf(stderr, "[stretto-pin] rank %d: pin widx=%d unused: the run posted %d wildcard receive(s)\n", g_rank, g_pins[i].idx, g_nwild);
    for (int i = 0; i < g_nppins; i++)
        if (g_ppins[i].idx >= g_nprobe)
            fprintf(stderr, "[stretto-pin] rank %d: probe pin pidx=%d unused: %d wildcard probe(s) found a message\n", g_rank, g_ppins[i].idx, g_nprobe);
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
