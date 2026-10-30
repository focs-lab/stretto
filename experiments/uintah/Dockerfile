# The experiment image with the libxml2 headers Uintah's configure requires, and the BLAS and
# LAPACK that ARCHES links (README, "Uintah's nightly threaded tests").
FROM stretto-exp:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libxml2-dev zlib1g-dev \
    libblas-dev liblapack-dev && rm -rf /var/lib/apt/lists/*
