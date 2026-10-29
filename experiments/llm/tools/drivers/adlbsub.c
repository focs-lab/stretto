/* adlbsub: data-dependent ADLB tasks that wait on container subscripts.
 *
 * Layout: N ranks, the last 2 are servers (ADLB_Init with 2 servers), the others workers.
 * Two containers, ids 1 and 2, live on the two servers (ADLB_Locate: id % servers).
 * Worker 1 puts NTASKS tasks with ADLB_Dput, task i waiting on container (1 + i % 2) at the
 * subscript key(i); worker 0 then stores every key(i) into its container. Every worker runs the
 * tasks it gets; after shutdown the workers add up their counts and worker 0 prints
 * "adlbsub: OK <n>" or "adlbsub: FAIL ran <n> of <NTASKS>". A server whose loop returns an
 * error prints "adlbsub: SERVER ERROR <rc>" and aborts.
 * usage: adlbsub [ntasks (default 40)] [keylen (default 48)]
 */
#include <mpi.h>
#include <adlb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static void
key (char *buf, int len, int i)
{
  int                 n = snprintf (buf, len + 1, "key-%06d-", i);
  for (; n < len; ++n)
    buf[n] = 'a' + (i + n) % 26;
  buf[len] = '\0';
}

int
main (int argc, char **argv)
{
  int                 types[1] = { 0 };
  int                 am_server, rank, size, ntasks = 40, klen = 48, i;
  MPI_Comm            worker_comm;
  adlb_code           rc;

  MPI_Init (&argc, &argv);
  if (argc > 1)
    ntasks = atoi (argv[1]);
  if (argc > 2)
    klen = atoi (argv[2]);
  MPI_Comm_rank (MPI_COMM_WORLD, &rank);
  MPI_Comm_size (MPI_COMM_WORLD, &size);
  rc = ADLB_Init (2, 1, types, &am_server, MPI_COMM_WORLD, &worker_comm);
  if (rc != ADLB_SUCCESS) {
    printf ("adlbsub: INIT ERROR %d\n", (int) rc);
    MPI_Abort (MPI_COMM_WORLD, 1);
  }
  if (am_server) {
    rc = ADLB_Server (1 << 26);
    if (rc != ADLB_SUCCESS) {
      printf ("adlbsub: SERVER ERROR %d on rank %d\n", (int) rc, rank);
      fflush (stdout);
      MPI_Abort (MPI_COMM_WORLD, 3);
    }
  }
  else {
    int                 wrank, ran = 0, total = 0;
    char                k[256], buf[256];
    MPI_Comm_rank (worker_comm, &wrank);
    if (wrank == 0) {
      for (i = 1; i <= 2; ++i) {
        adlb_datum_id       id;
        rc = ADLB_Create_container (i, ADLB_DATA_TYPE_STRING,
                                    ADLB_DATA_TYPE_INTEGER,
                                    DEFAULT_CREATE_PROPS, &id);
        if (rc != ADLB_SUCCESS)
          printf ("adlbsub: create %d failed %d\n", i, (int) rc);
      }
    }
    MPI_Barrier (worker_comm);    /* containers exist before anyone waits on them */
    if (wrank == 1) {
      for (i = 0; i < ntasks; ++i) {
        adlb_datum_id_sub   w;
        key (k, klen, i);
        w.id = 1 + i % 2;
        w.subscript.key = k;
        w.subscript.length = (size_t) klen + 1;
        snprintf (buf, sizeof buf, "task %d", i);
        rc = ADLB_Dput (buf, (int) strlen (buf) + 1, ADLB_RANK_ANY, rank, 0,
                        ADLB_DEFAULT_PUT_OPTS, "t", NULL, 0, &w, 1);
        if (rc != ADLB_SUCCESS)
          printf ("adlbsub: dput %d failed %d\n", i, (int) rc);
      }
    }
    MPI_Barrier (worker_comm);    /* every task is put before the first store */
    if (wrank == 0) {
      for (i = 0; i < ntasks; ++i) {
        int64_t             v = i;
        adlb_subscript      s;
        key (k, klen, i);
        s.key = k;
        s.length = (size_t) klen + 1;
        rc = ADLB_Store (1 + i % 2, s, ADLB_DATA_TYPE_INTEGER, &v,
                         sizeof v, ADLB_NO_REFC, ADLB_NO_REFC);
        if (rc != ADLB_SUCCESS)
          printf ("adlbsub: store %d failed %d\n", i, (int) rc);
      }
    }
    while (1) {
      int                 length, answer, type;
      MPI_Comm            tc;
      void               *p = buf;
      rc = ADLB_Get (0, &p, &length, (int) sizeof buf, &answer, &type, &tc);
      if (rc == ADLB_SHUTDOWN)
        break;
      if (rc != ADLB_SUCCESS) {
        printf ("adlbsub: get failed %d\n", (int) rc);
        break;
      }
      ++ran;
    }
    MPI_Reduce (&ran, &total, 1, MPI_INT, MPI_SUM, 0, worker_comm);
    if (wrank == 0) {
      if (total == ntasks)
        printf ("adlbsub: OK %d\n", total);
      else
        printf ("adlbsub: FAIL ran %d of %d\n", total, ntasks);
      fflush (stdout);
    }
  }
  ADLB_Finalize ();
  MPI_Finalize ();
  return 0;
}
