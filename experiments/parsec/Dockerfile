# The SLATE image (experiment image + CMake, OpenBLAS, LAPACK, gdb) with what PaRSEC needs from
# Ubuntu: hwloc, and flex and bison for its PTG compiler.
FROM hunt-slate:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libhwloc-dev flex bison \
    && rm -rf /var/lib/apt/lists/*
