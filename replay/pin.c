#define _GNU_SOURCE
#include "pin.h"
#include <mpi.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define HIDDEN __attribute__((visibility("hidden")))

static int g_rank = -1;
static int g_nwild = 0, g_nsend = 0, g_nrecv = 0, g_nprobe = 0;
static struct { int idx, src, tag; } *g_pins = NULL, *g_ppins = NULL;
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

HIDDEN void rematch_pin_init(int rank) {
    g_rank = rank;
    g_nwild = g_nsend = g_nrecv = 0;
    g_npins = 0;
    const char *spec = getenv("REMATCH_PIN");
    if (spec) {
        char *copy = strdup(spec), *save = NULL;
        for (char *tok = strtok_r(copy, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
            int r, idx, src, tag = MPI_ANY_TAG;
            int n = sscanf(tok, "%d:%d:%d:%d", &r, &idx, &src, &tag);
            if (n >= 3 && r == rank) {
                g_pins = realloc(g_pins, sizeof *g_pins * (g_npins + 1));
                g_pins[g_npins].idx = idx; g_pins[g_npins].src = src; g_pins[g_npins].tag = (n == 4) ? tag : MPI_ANY_TAG;
                g_npins++;
            }
        }
        free(copy);
        if (g_npins && getenv("REMATCH_VERBOSE"))
            fprintf(stderr, "[rematch-pin] rank %d: %d pin(s) active\n", rank, g_npins);
    }
    const char *pspec = getenv("REMATCH_PIN_PROBE");
    g_nppins = 0; g_nprobe = 0;
    if (pspec) {
        char *copy = strdup(pspec), *save = NULL;
        for (char *tok = strtok_r(copy, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
            int r, idx, src, tag = MPI_ANY_TAG;
            int n = sscanf(tok, "%d:%d:%d:%d", &r, &idx, &src, &tag);
            if (n >= 3 && r == rank) {
                g_ppins = realloc(g_ppins, sizeof *g_ppins * (g_nppins + 1));
                g_ppins[g_nppins].idx = idx; g_ppins[g_nppins].src = src; g_ppins[g_nppins].tag = (n == 4) ? tag : MPI_ANY_TAG;
                g_nppins++;
            }
        }
        free(copy);
    }
    g_send_delay_ms = parse_rank_ms(getenv("REMATCH_SEND_DELAY_MS"), rank);
    g_recv_delay_ms = parse_rank_ms(getenv("REMATCH_RECV_DELAY_MS"), rank);
    const char *at = getenv("REMATCH_DELAY_AT");
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

HIDDEN int rematch_pin_recv(int *src, int *tag, int *widx) {
    *widx = -1;
    if (*src != MPI_ANY_SOURCE && *tag != MPI_ANY_TAG) return 0;
    pthread_mutex_lock(&g_pmu);
    int idx = g_nwild++;
    pthread_mutex_unlock(&g_pmu);
    *widx = idx;
    for (int i = 0; i < g_npins; i++) {
        if (g_pins[i].idx == idx) {
            if (*src == MPI_ANY_SOURCE) *src = g_pins[i].src;
            if (*tag == MPI_ANY_TAG && g_pins[i].tag != MPI_ANY_TAG) *tag = g_pins[i].tag;
            if (getenv("REMATCH_VERBOSE"))
                fprintf(stderr, "[rematch-pin] rank %d: wildcard #%d pinned to src=%d tag=%d\n", g_rank, idx, *src, *tag);
            return 1;
        }
    }
    return 0;
}

HIDDEN int rematch_pin_probe(int *src, int *tag, int *pidx) {
    /* Peek only: the ordinal advances in rematch_pin_probe_commit(), called when the probe
       found a message. Failed polls (Iprobe/Improbe with flag 0) do not count, so the
       ordinal is the same in a traced run and in a pinned replay however long each busy
       loop spins. */
    *pidx = -1;
    if (*src != MPI_ANY_SOURCE && *tag != MPI_ANY_TAG) return 0;
    pthread_mutex_lock(&g_pmu);
    int idx = g_nprobe;
    pthread_mutex_unlock(&g_pmu);
    *pidx = idx;
    for (int i = 0; i < g_nppins; i++) {
        if (g_ppins[i].idx == idx) {
            if (*src == MPI_ANY_SOURCE) *src = g_ppins[i].src;
            if (*tag == MPI_ANY_TAG && g_ppins[i].tag != MPI_ANY_TAG) *tag = g_ppins[i].tag;
            static int last_reported = -1;
            if (getenv("REMATCH_VERBOSE") && last_reported != idx) {
                last_reported = idx;
                fprintf(stderr, "[rematch-pin] rank %d: probe #%d pinned to src=%d tag=%d\n", g_rank, idx, *src, *tag);
            }
            return 1;
        }
    }
    return 0;
}

HIDDEN void rematch_pin_probe_commit(int pidx) {
    if (pidx < 0) return;
    pthread_mutex_lock(&g_pmu);
    if (g_nprobe == pidx) g_nprobe++;
    pthread_mutex_unlock(&g_pmu);
}

static void delay_at(int is_send, int k) {
    for (int i = 0; i < g_nat; i++) if (g_at[i].is_send == is_send && g_at[i].k == k) msleep(g_at[i].ms);
}
HIDDEN void rematch_pin_before_send(void) {
    pthread_mutex_lock(&g_pmu); int k = g_nsend++; pthread_mutex_unlock(&g_pmu);
    msleep(g_send_delay_ms); delay_at(1, k);
}
HIDDEN void rematch_pin_before_recv(void) {
    pthread_mutex_lock(&g_pmu); int k = g_nrecv++; pthread_mutex_unlock(&g_pmu);
    msleep(g_recv_delay_ms); delay_at(0, k);
}
