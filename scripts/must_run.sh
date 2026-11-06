#!/usr/bin/env bash
# Run MUST on one MPI program via the MPI-BugBench container (MUST 1.9.2 + MPICH inside),
# because MUST 1.11.2 built against this host's Open MPI hangs in MPI_Init under PnMPI.
# usage: scripts/must_run.sh <np> <source.c> [timeout-seconds]
# MUST_IMAGE (default mbb-must:latest) and MUST_CPUS (optional cpuset) select image and processors;
# MUST_PREFIX is MUST's installation in the image (default /MBB-builds/MUST192; the MUST 1.11.2
# image of experiments/must/ has /MBB-builds/MUST1112). MUST_TSAN=1 adds MUST's ThreadSanitizer
# analysis: the program is compiled with must-tsan-cc --wrap-target mpicc.mpich and run with
# --must:tsan, and a data race counts as an error, whether MUST's report has it
# (MUST_WARNING_DATARACE, which MPI-BugBench's harness maps to no error class) or only
# ThreadSanitizer's own report does. The latter is the case here: as MUST's own test suite does
# for clang 14 and later, the run sets MUST_DISABLE_TSAN_ONREPORT=1, since with clang 14 MUST's
# hook into ThreadSanitizer's reports misreads them and the run hangs after the first race.
# MUST_CACHE is a host directory for MUST's user cache, where MUST builds the tool layouts it
# has no prebuilt one for (the TSan ones), so that each is built once. It is created here, as the
# user: a directory docker creates for a volume belongs to root, and MUST, which runs as the user,
# then fails every run with "mkdir: cannot create directory '/cache/must'".
# prints one line: MUST:<verdict> where verdict is OK | ERROR:<summary> | TIMEOUT | CE:<msg> | RE:<msg> (a MUST run
# that ended without its report) | HARNESS-ERROR:<msg> (no MUST run to judge: docker failed, the
# container was killed before the timeout, MUST stopped before it started the program, or the run
# hung after MUST had finished); exit status 1 on a harness error
# The run inside the container is scripts/must_once.sh.
# A run that hangs after MUST has finished is no verdict on the program. MUST's tool layer shuts
# down only after every rank of the program has reached MPI_Finalize; some runs then hang, either
# in the tool's shutdown, where the backtrace the timeout's SIGTERM prints shows GTI's
# shutdown(GTI_FLUSH_TYPE, ...) under the tool place's destructor (with ThreadSanitizer), or
# after it, when the tool layer has written gti_layer_1.profile and the processes hang in MPICH's
# teardown, where the SIGTERM finds MPI_COMM_WORLD gone ("MPI_Abort(MPI_COMM_WORLD, errorcode=143)
# failed ... Invalid communicator"). After the frozen rerun of 23 Oct 2026 this happened to MUST
# 1.11.2 in about one run in two of some correct programs. A program that deadlocks never gets
# there: its tool place is still in its event loop (ThreadedMPIPlace::run), and no profile is
# written. ("rank -1 (of -1) ... caught signal nr 15" in run.out is no sign of either: one of MUST's
# tool processes prints it in every run the timeout ends.)
set -uo pipefail
np="$1"; src="$(readlink -f "$2")"; to="${3:-120}"
img="${MUST_IMAGE:-mbb-must:latest}"
if [ -n "${MUST_CACHE:-}" ] && { ! mkdir -p "$MUST_CACHE" || [ ! -w "$MUST_CACHE" ]; }; then
    echo "MUST:HARNESS-ERROR:MUST_CACHE $MUST_CACHE cannot be created or is not writable"
    exit 1
fi
work="$(mktemp -d)"
mkdir -p "$work/src"; cp "$(dirname "$src")"/*.h "$(dirname "$src")"/../*.h "$work/" 2>/dev/null; cp "$src" "$work/src/prog.c"
cp "$(dirname "$(readlink -f "$0")")/must_once.sh" "$work/"
tsan_env=(); [ "${MUST_TSAN:-0}" = 1 ] && tsan_env=(-e MUST_DISABLE_TSAN_ONREPORT=1)
out=$(docker run --rm --memory 4g ${MUST_CPUS:+--cpuset-cpus "$MUST_CPUS"} --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$work:/w" -w /w/src \
  -e STRETTO_MUST_DIR="${MUST_PREFIX:-/MBB-builds/MUST192}" -e STRETTO_MUST_TSAN="${MUST_TSAN:-0}" \
  ${tsan_env[@]+"${tsan_env[@]}"} \
  ${MUST_CACHE:+-v "$MUST_CACHE:/cache" -e XDG_CACHE_HOME=/cache} "$img" bash /w/must_once.sh "$np" "$to" 2> "$work/docker.err" | tail -1)
rc=$?
# a container that did not start or was killed (docker's own failure, the 4 GB cap) gave MUST no
# run to judge: a harness error, never MUST's verdict
status=0
if [ $rc -ne 0 ] || [ -z "$out" ]; then
    echo "MUST:HARNESS-ERROR:docker exited with status $rc: $(tail -c 160 "$work/docker.err" | tr '\n' ' ')"
    status=1
else
    echo "MUST:$out"
    case "$out" in HARNESS-ERROR:*) status=1;; esac
fi
[ -n "${MUST_KEEP:-}" ] && echo "kept $work" >&2 || rm -rf "$work"
exit $status
