# The experiment image with what Athena++'s MPI regression tests and AMR examples need beyond
# it: FFTW with its MPI interface (Ubuntu builds it for Open MPI only), parallel HDF5 for each
# MPI (the examples write HDF5), and gdb for hangs.
FROM stretto-exp:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libfftw3-dev libfftw3-mpi-dev libhdf5-openmpi-dev libhdf5-mpich-dev gdb file \
    && rm -rf /var/lib/apt/lists/*
