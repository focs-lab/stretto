# MUST 1.11.2 inside the experiment image, built twice: against MPICH 4.2.0 (/opt/must/mpich)
# and against Open MPI 4.1.6 (/opt/must/openmpi), both with the Fortran interface (NPB MG and
# Quantum ESPRESSO are Fortran programs), so that every tool of the uniform campaign runs a
# configuration under the same MPI library and the same application build
# (experiments/protocol/RULE.md, Section 2.3). The earlier MUST images (experiments/must/Dockerfile)
# carried their own MPICH 4.0.2 on Debian 12 and needed the applications built again inside them.
#
# The configuration is that of experiments/must/Dockerfile (Release, TypeART on, ThreadSanitizer
# support on, the prebuilt tool layouts installed) with Fortran on. MUST's compiler is the
# distribution's clang 18, from the same archive snapshot as the base image, a version that the
# TypeART of MUST 1.11.2 supports (14, 18 or 19). The MPI wrappers call clang only while MUST
# builds (MPICH_CC, OMPI_CC in configure-must.sh), so the image's wrappers still call gcc 13 for
# the applications, and one application build serves every tool.
# Build: experiments/protocol/must/build.sh (needs stretto-exp:24.04, experiments/container/)
FROM stretto-exp:24.04
ARG SNAPSHOT=20261007T095400Z
USER root
RUN apt-get update --snapshot "$SNAPSHOT" \
 && apt-get install -y --no-install-recommends --snapshot "$SNAPSHOT" \
      clang-18=1:18.1.3-1ubuntu1 llvm-18-dev=1:18.1.3-1ubuntu1 libclang-18-dev=1:18.1.3-1ubuntu1 \
      libclang-rt-18-dev=1:18.1.3-1ubuntu1 cmake libdw-dev libxml2-dev binutils-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*
ARG JOBS=8
COPY MUST-v1.11.2.tar.gz configure-must.sh /tmp/build-must/
WORKDIR /tmp/build-must
RUN echo "934d41dcf379df65c68853646344736a85d58ecc93e8fc4fe9c4077b2eca9ccb  MUST-v1.11.2.tar.gz" | sha256sum -c - \
 && tar xfz MUST-v1.11.2.tar.gz
# one install per MPI library; CMake's FindMPI is pointed at that library's wrappers
RUN bash /tmp/build-must/configure-must.sh mpich "$JOBS"
RUN bash /tmp/build-must/configure-must.sh openmpi "$JOBS" && cd / && rm -rf /tmp/build-must
WORKDIR /
