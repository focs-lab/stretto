# The experiment image with what Smilei needs: parallel HDF5 for each MPI, Python's headers
# (Smilei embeds Python to read its namelists), h5py and matplotlib for the validation
# scripts, gdb for hangs.
FROM stretto-exp:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libhdf5-openmpi-dev libhdf5-mpich-dev python3-dev python3-h5py python3-matplotlib gdb file \
    && rm -rf /var/lib/apt/lists/*
