# Experiment environment: the userland of the development host (Ubuntu 24.04), so that
# traces, replays and analysis behave the same wherever the experiments run.
# Build:  docker build -t stretto-exp:24.04 experiments/container
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential gfortran make bc time util-linux procps \
      openmpi-bin libopenmpi-dev mpich libmpich-dev python3 \
 && rm -rf /var/lib/apt/lists/*
