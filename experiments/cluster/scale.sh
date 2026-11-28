# The scale series on a cluster (docs/cluster.md, "Scale"): E11's applications (experiments/scale/
# run.sh) and NPB MG's documented bug, at 16 to 256 ranks over several nodes. Sourced by
# experiments/cluster/job.sh, whose steps these are; one phase per job, chained by
# experiments/cluster/submit-scale.sh:
#   scalebuild    every application at every count, once
#   scaleruns     on whole nodes: per process count, RUNS plain runs and RUNS traced runs of the
#                 application, timed (the tracer's overhead), in blocks of np / nodes ranks per
#                 node; the first traced run's trace is collected to OUT, the others removed
#   scaleanalyse  on one node with the memory for it: the analysis of each collected trace in
#                 one process under /usr/bin/time, as E11 (OUT/scale.csv), and, where there are
#                 alternatives, a sample of SAMPLE of them (replay_sample.py --plan-only)
#   scalereplays  on the runs' nodes: the sample replayed with the pins of each alternative's
#                 horizon and its control (replay_sample.py --from-csv, OUT/samples/)
#   APP      hypre (HYPRE 2.10.1, ParaSails in ij), amg (AMG, 16^3 points per process, one OpenMP
#            thread per rank), lu (NPB LU class A), mg-bug and mg-fix (NPB MG 2.4.1 and 3.3.1, class A)
#   NPS      process counts (default "16 32 64 128 256")
#   RUNS     timed runs per tool and count (default 5)
#   CLASS    the NPB problem class (default A; S in the two-node emulation)
#   CORES    cores per node (default 64, the xcni nodes): more ranks than that on a node bind to
#            hardware threads, and their times are not timing claims
#   FROM     scaleanalyse, scalereplays: the scaleruns job's OUT; PLAN (scalereplays) the
#            scaleanalyse job's OUT
#   SAMPLE   alternatives replayed per trace (default 10); TIMEOUT a run's limit (default 1200 s),
#            ANALYSE_TIMEOUT an analysis's (default 21600 s), REPLAY_TIMEOUT the least limit of a
#            replay (default 120 s; ten times the plain runs' median if that is more)

scale_app() {   # scale_app <app> <np>: sets MPI, CWD and CMD (the program and its arguments)
    local hypre="$R/third_party/apps/hypre/hypre-2.10.1" mg="$R/third_party/apps/npb-mg"
    [ -f "$hypre/configure" ] || hypre="$hypre/src"
    case $1 in
        hypre) MPI=mpich; CWD="$hypre/test"; CMD=("$hypre/test/ij" -solver 8 -pout 0);;
        amg) MPI=openmpi; CWD="$R/study/apps/AMG/test"
             local p; case $2 in 4) p="2 2 1";; 8) p="2 2 2";; 16) p="4 2 2";; 32) p="4 4 2";; 64) p="4 4 4";; 128) p="8 4 4";; 256) p="8 8 4";;
                      *) echo "scale.sh: no AMG grid for $2 ranks" >&2; return 1;; esac
             # shellcheck disable=SC2086
             CMD=("$CWD/amg" -P $p -n 16 16 16 -problem 1);;
        lu) MPI=mpich; CWD="$R/third_party/apps/NPB-mpich/NPB3.4-MPI/bin"; CMD=("$CWD/lu.${CLASS:-A}.x");;
        mg-bug) MPI=mpich; CWD="$mg/NPB2.4.1/NPB2.4-MPI/bin"; CMD=("$CWD/mg.${CLASS:-A}.$2");;
        mg-fix) MPI=mpich; CWD="$mg/NPB3.3.1/NPB3.3-MPI/bin"; CMD=("$CWD/mg.${CLASS:-A}.$2");;
        *) echo "scale.sh: unknown application $1" >&2; return 1;;
    esac
    [ -x "${CMD[0]}" ] || { echo "scale.sh: ${CMD[0]} is not built (submit-scale.sh's build job)" >&2; return 1; }
}

scale_bind() {   # the binding of np ranks on the job's nodes
    local nodes ppn
    nodes=$("$PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["nodes"]))' "$1/job.json")
    ppn=$(( ($2 + nodes - 1) / nodes ))
    if [ "$ppn" -gt "${CORES:-64}" ]; then echo hwthread; else echo core; fi
}

step_scaleruns() {
    local np bind
    for np in ${NPS:-16 32 64 128 256}; do
        scale_app "$APP" "$np" || continue
        bind=$(scale_bind "$OUT" "$np")
        for tool in plain traced; do
            "${J[@]}" run --out "$OUT" --name "$APP-np$np" --tool $tool --np "$np" --mpi "$MPI" --runs "$RUNS" \
                --timeout "${TIMEOUT:-1200}" --map block --bind "$bind" --cwd "$CWD" --keep-traces 1 \
                --env OMP_NUM_THREADS=1 -- "${CMD[@]}" || true
        done
    done
}

step_scaleanalyse() {
    local np name tdir log t head recs wild probes finds run
    : "${FROM:?scaleanalyse needs FROM, the OUT of the scaleruns job}"
    mkdir -p "$OUT/samples"
    # the elapsed seconds and the peak resident set in kB, as E11's /usr/bin/time -f '%e %M'; the
    # same from Python's resource module where a node has no /usr/bin/time
    local TIMER=(/usr/bin/time -f '%e %M')
    [ -x /usr/bin/time ] || TIMER=("$PYTHON" "$R/experiments/cluster/timed.py")
    for np in ${NPS:-16 32 64 128 256}; do
        name="$APP-np$np"; tdir="$FROM/$name/traced/run01/trace"; log="$OUT/samples/$name"
        ls "$tdir"/rank-*.jsonl > /dev/null 2>&1 || { echo "scale.sh: no trace in $tdir"; continue; }
        scale_app "$APP" "$np" || continue
        run=$("$PYTHON" - "$FROM/$name/runs.csv" <<'PY'
import csv, sys
print(next((r["result"] for r in csv.DictReader(open(sys.argv[1])) if r["tool"] == "traced" and r["run"] == "1"), ""))
PY
)
        # (job.sh runs under set -e: every command that may fail here says what follows)
        local rc=0
        /usr/bin/timeout -k 30 "${ANALYSE_TIMEOUT:-21600}" "${TIMER[@]}" -o "$log.time" \
            "$PYTHON" -m stretto analyze --no-sp "$tdir" > "$log.report" 2> "$log.err" || rc=$?
        t=$(tail -1 "$log.time" 2> /dev/null || true)
        [ $rc = 0 ] || t="rc=$rc ${t#* }"
        # "trace: N ranks, S sends, R receives, W wildcard receives, P probes, ..."
        head=$(grep -m1 "^trace:" "$log.report" || true)
        recs=$(cat "$tdir"/rank-*.jsonl | wc -l)
        wild=$(echo "$head" | sed -n 's/.* \([0-9]*\) wildcard receives.*/\1/p')
        probes=$(echo "$head" | sed -n 's/.* \([0-9]*\) probes.*/\1/p')
        finds=$( (grep -o '^\[ *[0-9a-z-]* *\]' "$log.report" || true) | tr -d '[] ' | sort | uniq -c | awk '{printf "%s %s;", $2, $1}')
        [ -f "$OUT/scale.csv" ] || echo "app,np,mpi,traced_run,records,wildcard_receives,probes,findings,analysis_s,peak_kb,runs_job,analysis_job,analysis_node,commit" > "$OUT/scale.csv"
        echo "$APP,$np,$MPI,$run,$recs,${wild:-},${probes:-},${finds%;},${t% *},${t#* },$(basename "$FROM"),$JOBID,$(hostname -s),$STRETTO_COMMIT" \
            | tee -a "$OUT/scale.csv"
        if [ $rc = 0 ] && [ "${wild:-0}${probes:-0}" != 00 ]; then
            /usr/bin/timeout -k 30 "${ANALYSE_TIMEOUT:-21600}" \
                "$PYTHON" "$R/experiments/replay_sample.py" --plan-only --sample "${SAMPLE:-10}" --mpi "$MPI" \
                --out "$log-plan.csv" "$tdir" "$np" "${CMD[@]}" > "$log.plan" 2>&1 || echo "scale.sh: no plan for $name"
        fi
    done
}

step_scalereplays() {
    local np name plan bind limit
    : "${FROM:?scalereplays needs FROM, the OUT of the scaleruns job}" "${PLAN:?scalereplays needs PLAN, the OUT of the scaleanalyse job}"
    mkdir -p "$OUT/samples"
    for np in ${NPS:-16 32 64 128 256}; do
        name="$APP-np$np"; plan="$PLAN/samples/$name-plan.csv"
        [ -s "$plan" ] || continue
        scale_app "$APP" "$np" || continue
        bind=$(scale_bind "$OUT" "$np")
        # a replay's limit: ten times the plain runs' median, at least REPLAY_TIMEOUT (default 120 s,
        # E11's); a replay that hangs (E11's ParaSails race) waits that long, not a run's TIMEOUT
        limit=$("$PYTHON" - "$FROM/$name/runs.csv" "${REPLAY_TIMEOUT:-120}" <<'PY'
import csv, statistics, sys
s = [float(r["seconds"]) for r in csv.DictReader(open(sys.argv[1])) if r["tool"] == "plain" and r["result"] == "FINISHED"]
print(max(int(sys.argv[2]), int(10 * statistics.median(s)) if s else 0))
PY
)
        # the job's placement and launcher for every replay and control (stretto.confirm reads them)
        (e=$("${J[@]}" env --out "$OUT" --map block --np "$np" --bind "$bind") && eval "$e" && cd "$CWD" \
            && "$PYTHON" "$R/experiments/replay_sample.py" --from-csv "$plan" --timeout "$limit" --mpi "$MPI" \
                --out "$OUT/samples/$name.csv" "$FROM/$name/traced/run01/trace" "$np" "${CMD[@]}") \
            > "$OUT/samples/$name.log" 2>&1 || echo "scale.sh: the sample of $name did not finish"
        tail -1 "$OUT/samples/$name.log" || true
    done
}

step_scalebuild() {
    # what the series runs, each built once (natively: the system's Open MPI, mpich.sh's MPICH):
    # the tracer and replay library, HYPRE 2.10.1 (the campaign's build), AMG, NPB LU class A,
    # NPB MG 2.4.1 and 3.3.1 class A at every count of NPS (NPB 2 and 3.3 fix the count at build)
    [ -x "$MPICH_PREFIX/bin/mpicc.mpich" ] || PREFIX="$MPICH_PREFIX" experiments/cluster/mpich.sh
    step_build
    local hypre="$R/third_party/apps/hypre/hypre-2.10.1"
    [ -x "$hypre/test/ij" ] || [ -x "$hypre/src/test/ij" ] || (SUBJECTS="hypre:np8"; step_buildsubjects)
    [ -x "$R/study/apps/AMG/test/amg" ] || third_party/fetch.sh amg
    [ -x "$R/third_party/apps/NPB-mpich/NPB3.4-MPI/bin/lu.${CLASS:-A}.x" ] \
        || NPB_MPIS=mpich NPB_BENCHES=lu NPB_CLASSES="${CLASS:-A}" MPICH_BIN="$MPICH_PREFIX/bin" third_party/fetch.sh npb
    MPICH_BIN="$MPICH_PREFIX/bin" CLASS="${CLASS:-A}" NPS="${NPS:-16 32 64 128 256}" experiments/npbmg/run.sh fetch build
    local app np
    for app in hypre amg lu mg-bug mg-fix; do
        for np in ${NPS:-16 32 64 128 256}; do scale_app "$app" "$np"; done
    done
    echo "scale.sh: every application is built"
}
