// Two parallel meshes built one after the other from different serial meshes. Each mesh then
// gets an H1 space, and the integral of 1 over it (the area) and its global true size are
// printed by rank 0.
// usage: twomesh [nx1 ny1 nx2 ny2]
#include "mfem.hpp"
#include <iostream>
#include <cstdlib>
using namespace mfem;

static void report(ParMesh &pm, int k, double sx)
{
   H1_FECollection fec(1, 2);
   ParFiniteElementSpace fes(&pm, &fec);
   ParLinearForm b(&fes);
   ConstantCoefficient one(1.0);
   b.AddDomainIntegrator(new DomainLFIntegrator(one));
   b.Assemble();
   HypreParVector *B = b.ParallelAssemble();
   HypreParVector ones(&fes);
   ones = 1.0;
   double area = InnerProduct(*B, ones);
   HYPRE_Int n = fes.GlobalTrueVSize();
   if (pm.GetMyRank() == 0)
      std::cout << "mesh " << k << ": true dofs " << n << " area " << area
                << (std::abs(area - sx) < 1e-9 ? " ok" : " WRONG") << std::endl;
   delete B;
}

int main(int argc, char **argv)
{
   MPI_Init(&argc, &argv);
   int nx1 = argc > 4 ? atoi(argv[1]) : 16, ny1 = argc > 4 ? atoi(argv[2]) : 16;
   int nx2 = argc > 4 ? atoi(argv[3]) : 30, ny2 = argc > 4 ? atoi(argv[4]) : 7;
   Mesh *m1 = new Mesh(nx1, ny1, Element::QUADRILATERAL, 1, 1.0, 1.0);
   Mesh *m2 = new Mesh(nx2, ny2, Element::QUADRILATERAL, 1, 2.0, 1.0);
   ParMesh *p1 = new ParMesh(MPI_COMM_WORLD, *m1);
   ParMesh *p2 = new ParMesh(MPI_COMM_WORLD, *m2);
   delete m1;
   delete m2;
   report(*p1, 1, 1.0);
   report(*p2, 2, 2.0);
   delete p1;
   delete p2;
   MPI_Finalize();
   return 0;
}
