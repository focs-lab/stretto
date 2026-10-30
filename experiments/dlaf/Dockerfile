# The SLATE image (experiment image + CMake, OpenBLAS, LAPACK, gdb) with what pika and
# DLA-Future need from Ubuntu: Boost headers, hwloc, fmt and spdlog. pika, Umpire, blaspp,
# lapackpp and DLA-Future are built from source by build.sh.
FROM hunt-slate:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libboost-dev libhwloc-dev libfmt-dev libspdlog-dev \
    && rm -rf /var/lib/apt/lists/*
