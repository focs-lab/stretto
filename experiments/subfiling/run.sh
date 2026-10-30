#!/usr/bin/env bash
# HDF5's Subfiling VFD before and after its wrong-worker fix (docs/threads.md, section 10): build
# testpar/t_subfiling_vfd at both commits, run it traced, and count what L1 predicts.
#
# The bug: an I/O concentrator receives the header of each write request on its main thread
# (MPI_Iprobe(ANY_SOURCE, ANY_TAG)) and hands the request to a worker thread, which sends an ACK
# and receives the data with MPI_Recv(client, WRITE_INDEP_DATA); two write requests of one
# client are two worker threads receiving with one envelope, so either can take either data
# message. The fix (ffc254983, 17 Nov 2021, on the subfiling branch that 27bb358 squashed into
# develop on 22 Jul 2022) sends each worker's own tag in the ACK. Its parent 9975eaeb2 is the
# version with the bug; develop's parent of 27bb358 has no Subfiling VFD at all.
#
# Run inside a container of the image built from Dockerfile here (the experiment image plus
# cmake and libtool), with the repository at /w and the tracer built for Open MPI
# (make -C tracer MPI=openmpi):
#   experiments/subfiling/run.sh WORK
# WORK holds the HDF5 clone (WORK/hdf5, cloned if absent, or HDF5_SRC=<clone>), the two trees,
# the builds and the runs; a build is skipped when its test binary exists. Settings: JOBS
# (build jobs, 4), RUNS (traced runs per version, 5), NP (ranks, 4), SCRATCH (where the test runs and
# writes its files, emptied after each run; default the run's directory: a tmpfs of a few GB keeps
# the test's files off the disk). The runs record the lock order (STRETTO_SYNC_ORDER=1, for
# replay.sh; docs/threads.md section 14). Writes WORK/results.csv:
# version, run, the test's exit status and passes, records, and the L1 findings on receives of
# tag WRITE_INDEP_DATA (0xd8da02), with the other findings by class.
set -u
W=${1:?usage: run.sh WORK}
JOBS=${JOBS:-4}; RUNS=${RUNS:-5}; NP=${NP:-4}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
LIB=$REPO/tracer/bin/openmpi/libstretto-trace.so
SRC=${HDF5_SRC:-$W/hdf5}
mkdir -p "$W"
[ -d "$SRC/.git" ] || git clone -q --filter=blob:none https://github.com/HDFGroup/hdf5.git "$SRC"
git -C "$SRC" fetch -q origin pull/1464/head:pr1464 2>/dev/null || true
export OMPI_MCA_rmaps_base_oversubscribe=1
echo "version,run,test_rc,passed,records,l1_data_tag,l1_data_tag_1a,findings_by_class" > "$W/results.csv"
for v in pre:9975eaeb2 fix:ffc254983; do
  name=${v%%:*}; commit=${v#*:}
  tree=$W/hdf5-$name; build=$W/build-$name
  [ -d "$tree" ] || git -C "$SRC" worktree add --detach "$tree" "$commit" >/dev/null
  if [ ! -x "$build/bin/t_subfiling_vfd" ]; then
    cmake -S "$tree" -B "$build" -DCMAKE_C_COMPILER=/usr/bin/mpicc -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DHDF5_ENABLE_PARALLEL=ON -DHDF5_BUILD_SUBFILING=ON -DBUILD_TESTING=ON -DHDF5_BUILD_TOOLS=OFF \
      -DHDF5_BUILD_EXAMPLES=OFF -DHDF5_BUILD_CPP_LIB=OFF -DHDF5_BUILD_FORTRAN=OFF -DHDF5_BUILD_HL_LIB=OFF \
      -DHDF5_ENABLE_Z_LIB_SUPPORT=OFF -DHDF5_ENABLE_SZIP_SUPPORT=OFF > "$W/build-$name.cfg.log" 2>&1 \
      && make -C "$build" -j"$JOBS" t_subfiling_vfd > "$W/build-$name.make.log" 2>&1 \
      || { echo "build of $name failed: $W/build-$name.*.log"; exit 1; }
  fi
  for i in $(seq 1 "$RUNS"); do
    d=$W/runs/$name/run$i; rm -rf "$d"; mkdir -p "$d/trace"
    # the single-copy transport needs ptrace across processes, which the container does not allow
    run=${SCRATCH:-$d}; mkdir -p "$run"
    (cd "$run" && timeout -k 5 300 /usr/bin/mpirun.openmpi -np "$NP" --mca btl_vader_single_copy_mechanism none -x STRETTO_SYNC_ORDER=1 \
       -x STRETTO_TRACE_DIR="$d/trace" -x LD_PRELOAD="$LIB" "$build/bin/t_subfiling_vfd" > "$d/out.log" 2>&1)
    rc=$?
    # the test's files (subfiles of up to 128 MB each, several GB per run) are not needed
    find "$run" -mindepth 1 -maxdepth 1 ! -name out.log ! -name trace -exec rm -rf {} +
    PYTHONPATH=$REPO/analysis python3 -m stretto analyze "$d/trace" > "$d/report.txt" 2> "$d/report.err"
    f=$(grep '^\[' "$d/report.txt")
    data=$(echo "$f" | grep ' L1 ' | grep -c 'tag=14211586')
    data1a=$(echo "$f" | grep ' L1 ' | grep 'tag=14211586' | grep -c '^\[ *1a\]')
    classes=$(echo "$f" | sed -E 's/^\[ *([^]]*)\] *([A-Za-z0-9]+).*/\1 \2/' | sort | uniq -c | awk '{printf "%s%s:%s=%s", (NR > 1 ? " " : ""), $2, $3, $1}')
    echo "$name,$i,$rc,$(grep -c PASSED "$d/out.log"),$(cat "$d"/trace/rank-*.jsonl | wc -l),$data,$data1a,$classes" >> "$W/results.csv"
  done
done
cat "$W/results.csv"
