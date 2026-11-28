#!/usr/bin/env bash
# The image's MPICH on a cluster without containers or an MPICH of its own: Ubuntu 24.04's
# mpich 4.2.0-5build3 (ch4:ucx, PMIx, hydra), the very packages the experiment image installs,
# fetched from the image's Ubuntu snapshot (experiments/container/Dockerfile, SNAPSHOT), checked
# against their SHA256 and unpacked under PREFIX without root. The libraries it needs (UCX 1.16,
# PMIx 5.0.1, hwloc 2.10) are the system's, at the image's versions (docs/cluster.md); hydra's
# libslurm.so.40 (Slurm 23.11), which a cluster with another Slurm lacks, comes from the same
# snapshot and serves hydra's host-list parsing only.
# PREFIX/bin gets the compiler wrappers, with the package's /usr replaced by PREFIX/usr and an
# rpath to the library directory (the package's wrappers have none), so that programs and the
# preloaded tracer find this MPICH with no LD_LIBRARY_PATH; mpiexec.hydra and mpichversion run
# with PREFIX's libraries. Nothing goes on PATH: /usr/bin/mpirun stays Open MPI's.
#   PREFIX   where to unpack (default $HOME/opt/mpich-4.2.0-ubuntu); replaced if it exists
# Fetching and unpacking take seconds and run anywhere, a login node included.
# usage: experiments/cluster/mpich.sh
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
PREFIX="${PREFIX:-$HOME/opt/mpich-4.2.0-ubuntu}"
SNAPSHOT="$(sed -n 's/^ARG SNAPSHOT=//p' "$R/experiments/container/Dockerfile")"
BASE="https://snapshot.ubuntu.com/ubuntu/$SNAPSHOT/pool/universe"
# path under BASE, SHA256 (as in the archive's index)
DEBS="m/mpich/mpich_4.2.0-5build3_amd64.deb 9ecdfb2b294bf7a3de963d41548d27c2f9586b87a0cfe5bf0d72fe4d701be6e4
m/mpich/libmpich12_4.2.0-5build3_amd64.deb 9781cf10658e3c0bbde6afa716e0ca16cbdb0c55a905834e499a0165a93c6b96
m/mpich/libmpich-dev_4.2.0-5build3_amd64.deb 0983cdd49bcdeea4a318ea3f70bf9a4d1cd5c2efcbdfbd7d9dd866bd4398b1fc
s/slurm-wlm/libslurm40t64_23.11.4-1.2ubuntu5_amd64.deb 42972b5a253e3f50c20ab76ae2c68495310096786af3e68a42745bc40e023f72"
rm -rf "$PREFIX"; mkdir -p "$PREFIX/debs" "$PREFIX/bin"
while read -r path sum; do
    f="$PREFIX/debs/$(basename "$path")"
    curl -sfSL --max-time 300 -o "$f" "$BASE/$path"
    echo "$sum  $f" | sha256sum -c --quiet
    dpkg-deb -x "$f" "$PREFIX"
done <<< "$DEBS"
LIB="$PREFIX/usr/lib/x86_64-linux-gnu"
for w in mpicc mpicxx mpifort; do
    sed -e "s|^prefix=/usr\$|prefix=$PREFIX/usr|" -e "s|^exec_prefix=/usr\$|exec_prefix=$PREFIX/usr|" \
        -e "s|^includedir=/usr/|includedir=$PREFIX/usr/|" -e "s|^libdir=/usr/|libdir=$PREFIX/usr/|" \
        -e "s|^final_ldflags=\"|final_ldflags=\"-Wl,--disable-new-dtags -Wl,-rpath,$LIB |" \
        "$PREFIX/usr/bin/$w.mpich" > "$PREFIX/bin/$w.mpich"
    chmod +x "$PREFIX/bin/$w.mpich"
    grep -q "^libdir=$PREFIX/" "$PREFIX/bin/$w.mpich" || { echo "mpich.sh: $w.mpich kept the package's paths" >&2; exit 1; }
done
for x in mpiexec.hydra mpichversion; do
    printf '#!/bin/sh\nLD_LIBRARY_PATH=%s${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} exec %s "$@"\n' "$LIB" "$PREFIX/usr/bin/$x" > "$PREFIX/bin/$x"
    chmod +x "$PREFIX/bin/$x"
done
"$PREFIX/bin/mpichversion" | head -3
"$PREFIX/bin/mpicc.mpich" -show
