# The experiment image with what a CPU-only build of SLATE needs: CMake, and BLAS and LAPACK
# from OpenBLAS (Ubuntu's packages).
FROM stretto-exp:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      cmake libopenblas-dev liblapack-dev gdb \
    && rm -rf /var/lib/apt/lists/*
