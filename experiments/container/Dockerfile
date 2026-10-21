# Experiment environment: Ubuntu 24.04 with Open MPI 4.1.6, MPICH 4.2.0, gcc 13 and
# Python 3.12, at the versions the results were produced with.
# Build:  docker build -t stretto-exp:24.04 experiments/container
# Check:  experiments/container/check.sh stretto-exp:24.04
#
# The base image is pinned by digest, and the packages come from the Ubuntu archive as it was
# at SNAPSHOT (snapshot.ubuntu.com), when the image the results ran in got its packages. The
# packages that decide what a run does are also pinned by version (versions.txt lists them
# with their dependencies), so a build that cannot get one of them fails instead of drifting.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
ARG SNAPSHOT=20261007T095400Z
# The snapshot service answers over https only, so the certificates come first, from the
# current archive; they serve the download and nothing else.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && apt-get update --snapshot "$SNAPSHOT" \
 && apt-get install -y --no-install-recommends --snapshot "$SNAPSHOT" \
      build-essential make bc time util-linux procps curl \
      gcc-13=13.3.0-6ubuntu2~24.04.1 g++-13=13.3.0-6ubuntu2~24.04.1 \
      gfortran=4:13.2.0-7ubuntu1 gfortran-13=13.3.0-6ubuntu2~24.04.1 \
      openmpi-bin=4.1.6-7ubuntu2 libopenmpi-dev=4.1.6-7ubuntu2 \
      mpich=4.2.0-5build3 libmpich-dev=4.2.0-5build3 \
      python3=3.12.3-0ubuntu2.1 python3.12=3.12.3-1ubuntu0.17 \
 && rm -rf /var/lib/apt/lists/*
# What third_party/fetch.sh needs when it runs in the image: git and wget for the inputs, and
# the Python packages and clang-format of MPI-BugBench's program generator (clang-format at the
# version that formatted the paper's programs). The experiments use none of them.
RUN apt-get update --snapshot "$SNAPSHOT" \
 && apt-get install -y --no-install-recommends --snapshot "$SNAPSHOT" \
      git wget python3-pandas python3-tqdm python3-typing-extensions \
      clang-format=1:18.0-59~exp2 clang-format-18=1:18.1.3-1ubuntu1 \
 && rm -rf /var/lib/apt/lists/*
