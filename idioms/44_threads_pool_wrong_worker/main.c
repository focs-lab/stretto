/* 44: a pool of worker threads serves requests from a queue, the shape of HDF5 Subfiling's I/O
   concentrator (experiments/subfiling). Rank 0's main thread receives two request headers from
   rank 1 (tag 1) and queues each under a mutex and a condition variable; whichever of three
   workers takes a request acknowledges it to rank 1 (tag 2) and receives its data with
   MPI_Recv(1, tag 3) into a buffer of the size the header gives. Rank 1 sends header 0 (8 bytes
   of data) and waits for its acknowledgement, the same for request 1 (400 bytes), then sends
   the two data messages. Observed: each worker takes its own request's data. The two workers'
   data receives are concurrent. Expected: an L1 concurrent swap with a 1a truncation (the 400
   bytes taken by the 8-byte receive). Which worker takes which request varies from run to run,
   so a replay names the right receive only with the trace's lock order (docs/threads.md,
   section 14). */
#include "../common.h"
#include <pthread.h>
#define HDR 1
#define ACK 2
#define DATA 3
#define NREQ 2
#define NWORK 3
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int queue[NREQ][2], head = 0, tail = 0, done = 0;
static void *worker(void *arg) {
    unsigned seed = (unsigned)getpid() * 31u + (unsigned)(long)arg;
    idiom_delay_ms(rand_r(&seed) % 20);      /* the workers start waiting in another order in each run */
    for (;;) {
        pthread_mutex_lock(&mu);
        while (head == tail && !done) pthread_cond_wait(&cv, &mu);
        if (head == tail) { pthread_mutex_unlock(&mu); return NULL; }
        int id = queue[head][0], size = queue[head][1];
        head++;
        pthread_mutex_unlock(&mu);
        char *buf = malloc(size);
        MPI_Send(&id, 1, MPI_INT, 1, ACK, MPI_COMM_WORLD);
        MPI_Recv(buf, size, MPI_CHAR, 1, DATA, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        if (buf[0] != id) printf("request %d: wrong data %d\n", id, buf[0]);
        free(buf);
    }
}
int main(int argc, char **argv) {
    int rank, np;
    idiom_init_thread(&argc, &argv, MPI_THREAD_MULTIPLE);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_min_np(np, 2, rank);
    if (rank == 0) {
        pthread_t w[NWORK];
        for (int i = 0; i < NWORK; i++) pthread_create(&w[i], NULL, worker, (void *)(long)i);
        for (int i = 0; i < NREQ; i++) {
            int hdr[2];
            MPI_Recv(hdr, 2, MPI_INT, 1, HDR, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
            pthread_mutex_lock(&mu);
            queue[tail][0] = hdr[0];
            queue[tail][1] = hdr[1];
            tail++;
            pthread_cond_signal(&cv);
            pthread_mutex_unlock(&mu);
        }
        pthread_mutex_lock(&mu);
        done = 1;
        pthread_cond_broadcast(&cv);
        pthread_mutex_unlock(&mu);
        for (int i = 0; i < NWORK; i++) pthread_join(w[i], NULL);
    } else if (rank == 1) {
        static char buf[NREQ][400];
        int size[NREQ] = {8, 400};
        for (int i = 0; i < NREQ; i++) {
            int hdr[2] = {i, size[i]}, ack;
            MPI_Send(hdr, 2, MPI_INT, 0, HDR, MPI_COMM_WORLD);
            MPI_Recv(&ack, 1, MPI_INT, 0, ACK, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        }
        for (int i = 0; i < NREQ; i++) {
            memset(buf[i], i, sizeof buf[i]);
            MPI_Send(buf[i], size[i], MPI_CHAR, 0, DATA, MPI_COMM_WORLD);
        }
    }
    MPI_Finalize();
    return 0;
}
