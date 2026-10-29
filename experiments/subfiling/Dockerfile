# The experiment image with what HDF5's CMake build of 2021 needs (run.sh here).
FROM stretto-exp:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cmake libtool \
 && rm -rf /var/lib/apt/lists/*
