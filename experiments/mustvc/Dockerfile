# MUST with the vector-clock checks for MPI+OpenMP ("Correctness Checking of MPI+OpenMP
# Applications Using Vector Clocks in MUST", SC-W 2024), built as its artifact builds it
# (RWTH-HPC/must-omp-clock-checks e1b187e, Dockerfile): Debian 12, Debian's MPICH 4.0.2 and
# clang 15 with LLVM's OpenMP runtime (libomp, which has OMPT), MUST v1.9-omp-checks from the
# artifact's tarball, RelWithDebInfo, backward on, Fortran and TypeART off, tests on, lit 14
# and psutil in a virtual environment, the artifact's tests in /home/user/tests.
#
# Differences from the artifact's Dockerfile: the base is pinned by digest; make runs with
# JOBS jobs instead of $(nproc) (the host is shared); and a last layer, after MUST is built,
# adds what the four applications of experiments/mustvc need to build against the same MPICH
# (OpenBLAS and LAPACK for SLATE and DLA-Future, Boost 1.81 (pika needs 1.77; Debian 12 has
# 1.74 as its default), hwloc, fmt and spdlog for pika, flex
# and bison for PaRSEC) and bc, time and patch for the scripts.
# Build: experiments/mustvc/build.sh
FROM debian:12@sha256:854dd8ab51d62866dbd8f5431d15ce77b12983a2a4a1e1fe43a2395dc7ef9f61

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Berlin

RUN apt-get update \
    && apt-get -y -qq --no-install-recommends install \
    cmake curl binutils-dev make automake autotools-dev autoconf libtool zlib1g zlib1g-dev \
    libmpich-dev libatomic1 libfabric-dev libxml2-dev python3 python3-pip python3-venv \
    gfortran gcc g++ git graphviz libgtest-dev clang-15 libomp-15-dev libdw-dev libdwarf-dev \
    clang-format-15 llvm-15 lldb-15 ninja-build vim openssh-client gdb wget googletest \
    && apt-get -yq clean \
    && rm --recursive --force /var/lib/apt/lists/*

RUN ln -s /usr/bin/FileCheck-15 /usr/bin/FileCheck
RUN ln -s /usr/bin/clang-15 /usr/bin/clang
RUN ln -s /usr/bin/clang++-15 /usr/bin/clang++
RUN ln -s /usr/bin/clang-format-15 /usr/bin/clang-format

COPY requirements.txt .
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN pip3 install --no-input --no-cache-dir --disable-pip-version-check -r /requirements.txt

ENV MPICH_CC=clang
ENV MPICH_CXX=clang++
ENV OMPI_CC=clang
ENV OMPI_CXX=clang++
ENV CC=mpicc
ENV CXX=mpicxx

RUN useradd -ms /bin/bash user
WORKDIR /home/user
USER user

ARG JOBS=8
COPY MUST-v1.9-omp-checks.tar.gz .
RUN echo "37328e624e47fdfdd5c6f7d35ad1798895ab08b76c5a28d793d219760746c1c2  MUST-v1.9-omp-checks.tar.gz" | sha256sum -c -
RUN mkdir -p ~/must && tar xf MUST-v1.9-omp-checks.tar.gz
RUN cd MUST-v1.9-omp-checks && \
      mkdir BUILD-mpich && cd BUILD-mpich && \
      cmake .. -DCMAKE_BUILD_TYPE=RelWithDebInfo -DUSE_BACKWARD=ON -DENABLE_FORTRAN=OFF -DENABLE_TYPEART=OFF \
      -DCMAKE_INSTALL_PREFIX=~/must \
      -DENABLE_TESTS=ON && \
      make -j"$JOBS" install

COPY --chown=user:user tests /home/user/tests
RUN chmod +x /home/user/tests/run_tests.sh
ENV PATH="/home/user/must/bin:$PATH"

USER root
RUN apt-get update \
    && apt-get -y -qq --no-install-recommends install \
    libopenblas-dev liblapack-dev liblapacke-dev libboost1.81-dev libhwloc-dev libfmt-dev libspdlog-dev \
    flex bison bc time patch \
    && apt-get -yq clean \
    && rm --recursive --force /var/lib/apt/lists/* \
    && chmod -R a+rX /home/user
USER user
