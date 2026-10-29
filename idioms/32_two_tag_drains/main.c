/* 15: two drain loops of different tags through one MPI_Iprobe call site. Rank 0 alternates
   drain(TAG_A) and drain(TAG_B), each an Iprobe(MPI_ANY_SOURCE) loop that receives what it
   finds, until it has the four messages; rank 1 sends two of tag A after 50 ms, rank 2 two of
   tag B after 200 ms. The two loops share the Iprobe's call site, as VTK's particle tracker's
   drains of tags 622 and 624 do (vtkMPIController::Iprobe). A replay that delays rank 1 finds
   the tag-B messages first: the rank's ordinal of a probe that found a message then names a
   probe of the other loop, the envelope's ordinal (STRETTO_PIN_PROBE's n) does not. */
#include "../common.h"
#define TAG_A 21
#define TAG_B 22

/* the messages of tag it finds now, received; one Iprobe call site for both tags */
static int __attribute__((noinline)) drain(int tag) {
    int n = 0;
    for (;;) {
        int flag = 0, v;
        MPI_Status st;
        MPI_Iprobe(MPI_ANY_SOURCE, tag, MPI_COMM_WORLD, &flag, &st);
        if (!flag)
            return n;
        MPI_Recv(&v, 1, MPI_INT, st.MPI_SOURCE, tag, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        n++;
    }
}

int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 3, rank);
    if (rank == 0) {
        int got = 0;
        while (got < 4) {
            got += drain(TAG_A);
            got += drain(TAG_B);
            idiom_delay_ms(1);       /* a bounded trace: the two loops' failed polls alternate */
        }
    } else {
        int v = rank, tag = rank == 1 ? TAG_A : TAG_B;
        idiom_delay_ms(rank == 1 ? 50 : 200);
        MPI_Send(&v, 1, MPI_INT, 0, tag, MPI_COMM_WORLD);
        MPI_Send(&v, 1, MPI_INT, 0, tag, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
