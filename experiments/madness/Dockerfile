# The image of the atomics work (experiments/atomics: the experiment image plus clang 18 and
# cmake) with what MADNESS's runtime and numerical library need: LAPACK and BLAS (reference,
# sequential), nlohmann-json and googletest (found instead of fetched), and gdb for hangs.
FROM atm-clang:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      liblapack-dev libblas-dev nlohmann-json3-dev libgtest-dev gdb file \
    && rm -rf /var/lib/apt/lists/*
