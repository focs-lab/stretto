/* 13: a wildcard receive of four MPI_INT; one sender uses MPI_INT x 4, the other one
   element of a contiguous(4, MPI_INT) type. Both have the signature INT INT INT INT, so
   the alternative is benign (1f). A third sender sends a struct {INT, DOUBLE} whose
   signature differs: that alternative is a type mismatch (1a). The tracer must write the
   flattened signatures for the analysis to tell the two apart; the strings
   "contiguous" and "struct" alone would make both look like mismatches. */
#include "../common.h"
#define TAG 13
int main(int argc, char **argv) {
    int rank, np;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    idiom_require_np(np, 4, rank);
    MPI_Datatype pair; int bl[2] = {1, 1}; MPI_Aint disp[2] = {0, 8}; MPI_Datatype ty[2] = {MPI_INT, MPI_DOUBLE};
    MPI_Type_create_struct(2, bl, disp, ty, &pair); MPI_Type_commit(&pair);
    if (rank == 0) {
        int buf[4]; MPI_Status st;
        MPI_Recv(buf, 4, MPI_INT, MPI_ANY_SOURCE, TAG, MPI_COMM_WORLD, &st);   /* observed: rank 1 */
        MPI_Recv(buf, 4, MPI_INT, 2, TAG, MPI_COMM_WORLD, &st);
        struct { int i; double d; } rec;
        MPI_Recv(&rec, 1, pair, 3, TAG + 1, MPI_COMM_WORLD, &st);              /* a different channel */
    } else if (rank == 1) {
        int v[4] = {1, 2, 3, 4};
        MPI_Send(v, 4, MPI_INT, 0, TAG, MPI_COMM_WORLD);
    } else if (rank == 2) {
        int v[4] = {5, 6, 7, 8};
        MPI_Datatype four;
        MPI_Type_contiguous(4, MPI_INT, &four); MPI_Type_commit(&four);
        idiom_delay_ms(100);
        MPI_Send(v, 1, four, 0, TAG, MPI_COMM_WORLD);
        MPI_Type_free(&four);
    } else {
        struct { int i; double d; } rec = {9, 9.0};
        idiom_delay_ms(200);
        MPI_Send(&rec, 1, pair, 0, TAG + 1, MPI_COMM_WORLD);
    }
    MPI_Type_free(&pair);
    MPI_Finalize();
    return 0;
}
