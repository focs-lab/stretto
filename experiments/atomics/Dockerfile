# One image for the four applications of the atomics experiments (README here): the DLA-Future
# image (experiments/dlaf/Dockerfile: SLATE's CMake, OpenBLAS and LAPACK, pika's Boost, hwloc,
# fmt and spdlog) with clang 18 from the archive snapshot of the first-read image
# (experiments/container/Dockerfile.firstread, the same pinned packages), and what HDF5
# (libtool), Uintah (libxml2 and zlib headers) and PaRSEC (flex, bison) add. clang compiles the
# instrumented builds; ThreadSanitizer's runtime (libclang-rt-18-dev) is not installed: the
# builds link the tracer's libstretto-tsan.so instead (docs/threads.md, section 17).
# Build:  docker build --cpuset-cpus 8-15 -t atm-clang:24.04 experiments/atomics
FROM hunt2-dlaf:24.04
USER root
ARG SNAPSHOT=20261007T095400Z
RUN apt-get update --snapshot "$SNAPSHOT" \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --snapshot "$SNAPSHOT" \
      clang-18=1:18.1.3-1ubuntu1 libclang-common-18-dev=1:18.1.3-1ubuntu1 \
      libclang-cpp18=1:18.1.3-1ubuntu1 libllvm18=1:18.1.3-1ubuntu1 \
      libtool libxml2-dev zlib1g-dev flex bison \
 && rm -rf /var/lib/apt/lists/*
