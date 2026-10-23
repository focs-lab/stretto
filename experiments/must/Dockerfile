# MUST 1.11.2 with MPICH, the second MUST baseline (scripts/must_run.sh with
# MUST_IMAGE=stretto-must:1.11.2 MUST_PREFIX=/MBB-builds/MUST1112).
#
# Adapted from MPI-BugBench's Dockerfile.must, which builds MUST 1.9.2 (the image
# mbb-must:latest of the first baseline): the same Debian 12 base, clang 14 as MPICH's
# compiler, Debian's MPICH, and the same MUST configuration (Release, no Fortran unless
# FORTRAN=ON, TypeART on, ThreadSanitizer support on by default, the prebuilt tool layouts
# installed). Left out: the Python environment of MPI-BugBench's own harness, which these runs
# do not use.
#
# MUST's download host refused connections on 21 and 22 Oct 2026, so the release archive comes
# from the build context, checked against the sha256 that EasyBuild's easyconfig
# MUST-1.11.2-gompi-2025b.eb records for https://hpc.rwth-aachen.de/must/files/MUST-v1.11.2.tar.gz.
# Build: experiments/must/build.sh
FROM debian:12@sha256:854dd8ab51d62866dbd8f5431d15ce77b12983a2a4a1e1fe43a2395dc7ef9f61
USER root
RUN apt-get update --fix-missing && \
    apt-get install -y apt-transport-https ca-certificates python-is-python3 git cmake && \
    update-ca-certificates && \
    apt-get autoremove -yq && apt-get clean -yq

ENV MPICH_CC=clang
ENV MPICH_CXX=clang++

RUN apt-get -y install wget cmake git clang-14 clang++-14 mpich libmpich-dev libdw-dev libxml2-dev binutils-dev && \
    update-alternatives --set mpi /usr/bin/mpicc.mpich && \
    update-alternatives --set mpirun /usr/bin/mpirun.mpich && \
    ln -s /usr/bin/clang-14 /usr/bin/clang && \
    ln -s /usr/bin/clang++-14 /usr/bin/clang++ && \
    apt-get clean -y

ARG JOBS=8
# FORTRAN=ON builds MUST with its Fortran interface (experiments/must/apps.sh: NPB MG, a Fortran
# program, which MUST without it reports MUST_ERROR_DATATYPE_UNKNOWN on and then hangs)
ARG FORTRAN=OFF
COPY MUST-v1.11.2.tar.gz /tmp/build-must/
WORKDIR /tmp/build-must
RUN echo "934d41dcf379df65c68853646344736a85d58ecc93e8fc4fe9c4077b2eca9ccb  MUST-v1.11.2.tar.gz" | sha256sum -c - && \
    tar xfz MUST-v1.11.2.tar.gz && mkdir -p build && cd build && \
    CC=clang CXX=clang++ cmake ../MUST-v1.11.2 -DCMAKE_INSTALL_PREFIX=/MBB-builds/MUST1112 -DCMAKE_BUILD_TYPE=Release -DENABLE_FORTRAN=${FORTRAN} -DENABLE_TYPEART=On && \
    make -j"$JOBS" install && \
    make -j"$JOBS" install-prebuilds && \
    cd / && rm -rf /tmp/build-must
WORKDIR /
