# The experiment image with what OpenFOAM's build needs beyond it: flex and bison (the readers of
# surfMesh, fileFormats and thermophysicalModels are flex scanners). The packages come from the
# archive snapshot of experiments/container/Dockerfile.
# Build:  docker build -t stretto-foam:24.04 experiments/latent/openfoam
FROM stretto-exp:24.04
ARG SNAPSHOT=20261007T095400Z
RUN apt-get update --snapshot "$SNAPSHOT" \
 && apt-get install -y --no-install-recommends --snapshot "$SNAPSHOT" flex libfl-dev bison \
 && rm -rf /var/lib/apt/lists/*
