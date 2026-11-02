# The image of the atomics work (experiments/atomics: the experiment image plus clang 18, cmake
# and hwloc) with what an autotools build of StarPU from its git tree needs, and gdb for hangs.
FROM atm-clang:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      autoconf automake libtool libtool-bin m4 pkg-config file gdb \
    && rm -rf /var/lib/apt/lists/*
