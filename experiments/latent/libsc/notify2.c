/* notify2: consecutive sc_notify calls on one communicator, nothing between them.
 *
 * A minimal driver over libsc's public API (not a shipped test; libsc's own
 * test_notify.c puts an MPI_Barrier before every call and skips the NBX type).
 *
 * usage: notify2 <type> [ncalls [ntop nint nbot [barrier [sorted]]]]
 *   type     nary | nbx | nbxv (sc_notify_payloadv with type NBX) | pex | native
 *   ncalls   consecutive calls (default 2)
 *   ntop..   SC_NOTIFY_NARY widths (default 3 2 2)
 *   barrier  1: MPI_Barrier between calls (the control) (default 0)
 *   sorted   the sorted argument (default 1; nbxv: 0, its own code path)
 *
 * Call c (0-based): rank s notifies the ranks R(s, c) below and sends them the
 * payload 1000 * (c + 1) + s. After all calls each rank checks every call's
 * senders and payloads against what it computes for itself, and prints one
 * line: "notify2: OK" or "notify2: FAIL rank r call c ...".
 */
#include <sc.h>
#include <sc_notify.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int
cmp_int (const void *a, const void *b)
{
  int                 x = *(const int *) a, y = *(const int *) b;
  return x < y ? -1 : x > y;
}

/* the receivers of rank s in call c, sorted and uniqued, never s itself */
static int
receivers_of (int s, int c, int P, int *out)
{
  int                 cand[3], n = 0, i, j;
  cand[0] = (s + 1 + c) % P;
  cand[1] = (s + 3 + 2 * c) % P;
  cand[2] = (5 * s + c + 2) % P;
  for (i = 0; i < 3; ++i) {
    if (cand[i] == s)
      continue;
    for (j = 0; j < n; ++j)
      if (out[j] == cand[i])
        break;
    if (j == n)
      out[n++] = cand[i];
  }
  qsort (out, n, sizeof (int), cmp_int);
  return n;
}

typedef struct
{
  sc_array_t         *senders, *payload, *offsets;
} result_t;

int
main (int argc, char **argv)
{
  int                 mpiret, P, me, c, i, j, k, n, ncalls = 2;
  int                 ntop = 3, nint = 2, nbot = 2, barrier = 0, sorted = 1;
  int                 type, fails = 0, rec[3];
  const char         *tname;
  result_t           *res;
  sc_notify_t        *notify;
  char                msg[4096];
  size_t              pos = 0;

  mpiret = sc_MPI_Init (&argc, &argv);
  SC_CHECK_MPI (mpiret);
  sc_MPI_Comm_size (sc_MPI_COMM_WORLD, &P);
  sc_MPI_Comm_rank (sc_MPI_COMM_WORLD, &me);
  sc_init (sc_MPI_COMM_WORLD, 1, 1, NULL, SC_LP_ERROR);

  tname = argc > 1 ? argv[1] : "nary";
  if (argc > 2)
    ncalls = atoi (argv[2]);
  if (argc > 5) {
    ntop = atoi (argv[3]);
    nint = atoi (argv[4]);
    nbot = atoi (argv[5]);
  }
  if (argc > 6)
    barrier = atoi (argv[6]);
  if (!strcmp (tname, "nbxv"))
    sorted = 0;
  if (argc > 7)
    sorted = atoi (argv[7]);
  if (!strcmp (tname, "nary"))
    type = SC_NOTIFY_NARY;
  else if (!strcmp (tname, "nbx") || !strcmp (tname, "nbxv"))
    type = SC_NOTIFY_NBX;
  else if (!strcmp (tname, "pex"))
    type = SC_NOTIFY_PEX;
  else if (!strcmp (tname, "native"))
    type = SC_NOTIFY_BINARY;
  else {
    fprintf (stderr, "unknown type %s\n", tname);
    return 2;
  }

  res = (result_t *) calloc (ncalls, sizeof (result_t));
  notify = sc_notify_new (sc_MPI_COMM_WORLD);
  sc_notify_set_type (notify, (sc_notify_type_t) type);
  if (type == SC_NOTIFY_NARY)
    sc_notify_nary_set_widths (notify, ntop, nint, nbot);

  /* one barrier before the first call, as a user would start from any state */
  sc_MPI_Barrier (sc_MPI_COMM_WORLD);
  for (c = 0; c < ncalls; ++c) {
    sc_array_t         *r = sc_array_new (sizeof (int));
    n = receivers_of (me, c, P, rec);
    for (i = 0; i < n; ++i)
      *(int *) sc_array_push (r) = rec[i];
    res[c].senders = sc_array_new (sizeof (int));
    res[c].payload = sc_array_new (sizeof (int));
    if (!strcmp (tname, "nbxv")) {
      /* each receiver gets (c + 1) ints, all equal to the payload */
      sc_array_t         *inp = sc_array_new (sizeof (int));
      sc_array_t         *inoff = sc_array_new (sizeof (int));
      res[c].offsets = sc_array_new (sizeof (int));
      *(int *) sc_array_push (inoff) = 0;
      for (i = 0; i < n; ++i) {
        for (k = 0; k <= c; ++k)
          *(int *) sc_array_push (inp) = 1000 * (c + 1) + me;
        *(int *) sc_array_push (inoff) = (i + 1) * (c + 1);
      }
      sc_notify_payloadv (r, res[c].senders, inp, res[c].payload, inoff,
                          res[c].offsets, sorted, notify);
      sc_array_destroy (inp);
      sc_array_destroy (inoff);
    }
    else {
      sc_array_t         *inp = sc_array_new (sizeof (int));
      for (i = 0; i < n; ++i)
        *(int *) sc_array_push (inp) = 1000 * (c + 1) + me;
      sc_notify_payload (r, res[c].senders, inp, res[c].payload, sorted,
                         notify);
      sc_array_destroy (inp);
    }
    sc_array_destroy (r);
    if (barrier)
      sc_MPI_Barrier (sc_MPI_COMM_WORLD);
  }
  sc_notify_destroy (notify);

  /* check */
  for (c = 0; c < ncalls; ++c) {
    int                 exp[1024], ne = 0, got[1024], gp[1024], ng, s;
    for (s = 0; s < P; ++s) {
      n = receivers_of (s, c, P, rec);
      for (i = 0; i < n; ++i)
        if (rec[i] == me)
          exp[ne++] = s;
    }
    ng = (int) res[c].senders->elem_count;
    for (i = 0; i < ng && i < 1024; ++i) {
      got[i] = *(int *) sc_array_index_int (res[c].senders, i);
      if (res[c].offsets != NULL) {
        int                 lo =
          *(int *) sc_array_index_int (res[c].offsets, i);
        gp[i] = *(int *) sc_array_index_int (res[c].payload, lo);
      }
      else if ((int) res[c].payload->elem_count > i)
        gp[i] = *(int *) sc_array_index_int (res[c].payload, i);
      else
        gp[i] = -1;
    }
    /* sort (sender, payload) pairs by sender for the comparison */
    for (i = 1; i < ng; ++i)
      for (j = i; j > 0 && got[j - 1] > got[j]; --j) {
        int                 t = got[j];
        got[j] = got[j - 1];
        got[j - 1] = t;
        t = gp[j];
        gp[j] = gp[j - 1];
        gp[j - 1] = t;
      }
    k = (ng == ne);
    for (i = 0; k && i < ng; ++i)
      k = (got[i] == exp[i] && gp[i] == 1000 * (c + 1) + exp[i]);
    if (!k) {
      ++fails;
      pos += snprintf (msg + pos, sizeof (msg) - pos,
                       "notify2: FAIL rank %d call %d expected", me, c);
      for (i = 0; i < ne && pos < sizeof (msg) - 64; ++i)
        pos += snprintf (msg + pos, sizeof (msg) - pos, " %d:%d", exp[i],
                         1000 * (c + 1) + exp[i]);
      pos += snprintf (msg + pos, sizeof (msg) - pos, " got");
      for (i = 0; i < ng && pos < sizeof (msg) - 64; ++i)
        pos += snprintf (msg + pos, sizeof (msg) - pos, " %d:%d", got[i],
                         gp[i]);
      pos += snprintf (msg + pos, sizeof (msg) - pos, "\n");
    }
  }
  {
    int                 total = 0;
    sc_MPI_Allreduce (&fails, &total, 1, sc_MPI_INT, sc_MPI_SUM,
                      sc_MPI_COMM_WORLD);
    if (fails)
      fputs (msg, stdout);
    fflush (stdout);
    sc_MPI_Barrier (sc_MPI_COMM_WORLD);
    if (me == 0)
      printf (total ? "notify2: FAIL %d\n" : "notify2: OK\n", total);
  }
  for (c = 0; c < ncalls; ++c) {
    sc_array_destroy (res[c].senders);
    sc_array_destroy (res[c].payload);
    if (res[c].offsets)
      sc_array_destroy (res[c].offsets);
  }
  free (res);
  sc_finalize ();
  sc_MPI_Finalize ();
  return 0;
}
