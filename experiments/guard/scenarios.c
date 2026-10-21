/* Cases for the tracer's buffer guard (STRETTO_GUARD=1), two ranks: scenarios <case>.
   Each prints "expect <what>" on rank 0, the guard records the trace must hold (none, or
   access:write, access:read, modified, overlap); experiments/guard/run.sh compares. A case
   that moves data checks it and aborts on a wrong value. */
#include <mpi.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int rank;
static volatile int sink;
#define PAGE 4096

static void *aligned(size_t n) { void *p; if (posix_memalign(&p, PAGE, n)) abort(); memset(p, 0, n); return p; }
static void expect(const char *what) { if (rank == 0) printf("expect %s\n", what); }
static void check(const int *b, long n, int v, const char *name) {
    for (long i = 0; i < n; i++) if (b[i] != v + (int)(i % 7)) { printf("rank %d: %s: bad data at %ld: %d\n", rank, name, i, b[i]); MPI_Abort(MPI_COMM_WORLD, 3); }
}
static void fill(int *b, long n, int v) { for (long i = 0; i < n; i++) b[i] = v + (int)(i % 7); }

int main(int argc, char **argv) {
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    int s = argc > 1 ? atoi(argv[1]) : 0, peer = 1 - rank;
    MPI_Request r[4];
    switch (s) {
    case 1: {   /* reading a pending send buffer is allowed */
        expect("none");
        int *b = calloc(1000, sizeof(int));
        if (rank == 0) { MPI_Isend(b, 1000, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); sink = b[5] + b[999]; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else MPI_Recv(b, 1000, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        break; }
    case 2: {   /* writing it is not */
        expect("access:write");
        int *b = calloc(1000, sizeof(int));
        if (rank == 0) { MPI_Isend(b, 1000, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); b[5] = 1; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else MPI_Recv(b, 1000, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        break; }
    case 3: {   /* a read inside a page that lies wholly in a pending receive buffer */
        expect("access:read");
        int *b = aligned(4 * PAGE);
        if (rank == 0) { MPI_Irecv(b, PAGE, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); sink = b[2000]; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else { usleep(100000); MPI_Send(b, PAGE, MPI_INT, 0, 0, MPI_COMM_WORLD); }
        break; }
    case 4: {   /* a small receive buffer shares its page: reads are not caught, writes are */
        expect("access:write");
        int *b = calloc(10, sizeof(int));
        if (rank == 0) { MPI_Irecv(b, 10, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); sink = b[2]; b[3] = 4; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else { usleep(100000); MPI_Send(b, 10, MPI_INT, 0, 0, MPI_COMM_WORLD); }
        break; }
    case 5: {   /* other data on the page of a pending buffer is the program's */
        expect("none");
        struct { int a[10]; int b[10]; } *x = calloc(1, sizeof *x);
        if (rank == 0) { MPI_Irecv(x->a, 10, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); x->b[3] = 1; sink = x->b[4]; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else { usleep(100000); MPI_Send(x->a, 10, MPI_INT, 0, 0, MPI_COMM_WORLD); }
        break; }
    case 6: {   /* a write after the page was opened by another access: the checksum catches it */
        expect("modified");
        struct { int a[10]; int b[10]; } *x = calloc(1, sizeof *x);
        if (rank == 0) { MPI_Isend(x->a, 10, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); x->b[3] = 1; x->a[2] = 7; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else MPI_Recv(x->a, 10, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        break; }
    case 7: {   /* two pending sends from one buffer are allowed */
        expect("none");
        int *b = calloc(100, sizeof(int));
        if (rank == 0) { MPI_Isend(b, 100, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); MPI_Isend(b, 100, MPI_INT, 1, 1, MPI_COMM_WORLD, &r[1]); MPI_Waitall(2, r, MPI_STATUSES_IGNORE); }
        else { MPI_Recv(b, 100, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE); MPI_Recv(b, 100, MPI_INT, 0, 1, MPI_COMM_WORLD, MPI_STATUS_IGNORE); }
        break; }
    case 8: {   /* a persistent request's buffer is the program's between completion and Start */
        expect("none");
        int *b = calloc(100, sizeof(int));
        if (rank == 0) MPI_Send_init(b, 100, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]);
        else MPI_Recv_init(b, 100, MPI_INT, 0, 0, MPI_COMM_WORLD, &r[0]);
        for (int it = 0; it < 5; it++) {
            if (rank == 0) fill(b, 100, it);
            MPI_Start(&r[0]); MPI_Wait(&r[0], MPI_STATUS_IGNORE);
            if (rank == 1) check(b, 100, it, "persistent");
        }
        MPI_Request_free(&r[0]);
        break; }
    case 9: case 10: {   /* a vector type: its holes are not the operation's */
        expect(s == 9 ? "none" : "access:write");
        int *b = calloc(20, sizeof(int)); MPI_Datatype v;
        MPI_Type_vector(10, 1, 2, MPI_INT, &v); MPI_Type_commit(&v);
        if (rank == 0) { MPI_Isend(b, 1, v, 1, 0, MPI_COMM_WORLD, &r[0]); b[s == 9 ? 1 : 2] = 5; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else MPI_Recv(b, 10, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        MPI_Type_free(&v);
        break; }
    case 11: {  /* MPI_IN_PLACE: the receive buffer is read and written */
        expect("access:write");
        int *b = calloc(10, sizeof(int));
        MPI_Iallreduce(MPI_IN_PLACE, b, 10, MPI_INT, MPI_SUM, MPI_COMM_WORLD, &r[0]); b[0] = 1; MPI_Wait(&r[0], MPI_STATUS_IGNORE);
        break; }
    case 12: case 13: {  /* large exchanges while polling and computing on adjacent memory (single-copy transports) */
        expect("none");
        long n = s == 12 ? 16384 : 4L << 20;    /* 64 KiB, 16 MiB */
        char *mem = malloc(3 * n * sizeof(int) + 100);
        int *out = (int *)(mem + 12), *in = out + n, *work = in + n;   /* edges share pages */
        fill(out, n, rank);
        for (int it = 0; it < 4; it++) {
            MPI_Irecv(in, n, MPI_INT, peer, it, MPI_COMM_WORLD, &r[0]);
            MPI_Isend(out, n, MPI_INT, peer, it, MPI_COMM_WORLD, &r[1]);
            int done = 0; long spins = 0;
            while (!done) { for (long i = 0; i < n; i += 1024) work[i] += 1; sink = out[n / 2]; MPI_Testall(2, r, &done, MPI_STATUSES_IGNORE); spins++; }
            check(in, n, peer, "exchange");
        }
        free(mem);
        break; }
    case 14: {  /* matched early, completed late: the receiver returns from MPI between the match and the data */
        expect("none");
        long n = 2L << 20;
        int *b = aligned(n * sizeof(int)), *work = calloc(4096, sizeof(int));
        if (rank == 0) {
            MPI_Irecv(b, n, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]);
            int done = 0;
            while (!done) { for (int k = 0; k < 4096; k++) work[k]++; usleep(200); MPI_Test(&r[0], &done, MPI_STATUS_IGNORE); }
            check(b, n, 1, "late");
        } else { fill(b, n, 1); usleep(50000); MPI_Send(b, n, MPI_INT, 0, 0, MPI_COMM_WORLD); }
        break; }
    case 15: {  /* a stack buffer is never protected; the checksum still sees the write (idiom 07) */
        expect("modified");
        int b[16] = {0};
        if (rank == 0) { MPI_Isend(b, 16, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); b[0] = 1; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else MPI_Recv(b, 16, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        break; }
    case 16: {  /* a blocking receive into the buffer of a pending send */
        expect("overlap");
        int *b = calloc(200, sizeof(int));
        MPI_Isend(b, 100, MPI_INT, peer, 0, MPI_COMM_WORLD, &r[0]);
        MPI_Recv(b + 50, 100, MPI_INT, peer, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        MPI_Wait(&r[0], MPI_STATUS_IGNORE);
        break; }
    case 17: {  /* buffered send: the attached buffer shares a page with the send buffer */
        expect("access:write");
        char *mem = calloc(1, 40 + 40 + MPI_BSEND_OVERHEAD);
        int *b = (int *)mem; MPI_Buffer_attach(mem + 40, 40 + MPI_BSEND_OVERHEAD);
        if (rank == 0) { MPI_Ibsend(b, 10, MPI_INT, 1, 0, MPI_COMM_WORLD, &r[0]); b[2] = 1; MPI_Wait(&r[0], MPI_STATUS_IGNORE); }
        else MPI_Recv(b, 10, MPI_INT, 0, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        int sz; void *p; MPI_Buffer_detach(&p, &sz);
        break; }
    case 18: {  /* Mprobe + Imrecv, then a legitimate use after the wait; freed request of a send */
        expect("none");
        int *b = calloc(10, sizeof(int));
        if (rank == 0) {
            MPI_Message m; MPI_Mprobe(1, 0, MPI_COMM_WORLD, &m, MPI_STATUS_IGNORE);
            MPI_Imrecv(b, 10, MPI_INT, &m, &r[0]); MPI_Wait(&r[0], MPI_STATUS_IGNORE); b[2] = 9;
        } else { MPI_Isend(b, 10, MPI_INT, 0, 0, MPI_COMM_WORLD, &r[0]); MPI_Request_free(&r[0]); }
        MPI_Barrier(MPI_COMM_WORLD);
        b[3] = 1;
        break; }
    case 19: {  /* waitany/testsome over many receives in a page-aligned array, written as they complete */
        expect("none");
        int *b = aligned(8 * PAGE);
        MPI_Request q[8];
        if (rank == 0) {
            for (int i = 0; i < 8; i++) MPI_Irecv(b + i * 1024, 1024, MPI_INT, 1, i, MPI_COMM_WORLD, &q[i]);
            for (int k = 0; k < 8; k++) { int idx; MPI_Waitany(8, q, &idx, MPI_STATUS_IGNORE); b[idx * 1024] += 1; }
        } else { for (int i = 7; i >= 0; i--) MPI_Send(b + i * 1024, 1024, MPI_INT, 0, i, MPI_COMM_WORLD); }
        break; }
    default:
        expect("none");
    }
    MPI_Finalize();
    return 0;
}
