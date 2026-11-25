# The timeouts, cell selection and run times of the thread campaigns' scripts (series.sh,
# traced.sh, traced_cells.sh, apps.sh; sourced by them). The uniform campaign
# (experiments/protocol, RULE.md amendment 20) calibrates a T per cell and passes it here; with
# none of these settings a script runs as it was committed.
#   TIMEOUTS     "name=T ..." per cell (apps.sh: "native:name=T" for its runs without MUST)
#   CAP          T of every cell TIMEOUTS does not name (a calibration's cap)
#   ONLY         the names of the cells to run (default all)
#   VARIANTS     the variants to run (default "plain fixed"; a calibration runs "fixed")
#   SECONDS_OUT  a file that gets one row per run: name,variant,run,seconds,rc
#   ANALYSE      0: traced runs are not analysed (a calibration times the traced run only)

cell_timeout() {   # cell_timeout NAME DEFAULT: the cell's T
  local kv
  for kv in ${TIMEOUTS:-}; do
    [ "${kv%=*}" = "$1" ] && { echo "${kv##*=}"; return; }
  done
  echo "${CAP:-$2}"
}

cell_wanted() {    # cell_wanted NAME: whether ONLY selects the cell
  [ -z "${ONLY:-}" ] && return 0
  case " $ONLY " in *" $1 "*) return 0 ;; esac
  return 1
}

variants() {       # the variants to run, in order
  echo "${VARIANTS:-plain fixed}"
}

timed() {          # timed NAME VARIANT RUN T [--kill] CMD ...: CMD under timeout T, its time noted
  local name=$1 v=$2 i=$3 t=$4 k=() s e rc; shift 4
  [ "${1:-}" = --kill ] && { k=(-k 5); shift; }
  s=$(date +%s%N)
  timeout "${k[@]}" "$t" "$@"; rc=$?
  e=$(date +%s%N)
  if [ -n "${SECONDS_OUT:-}" ]; then
    echo "$name,$v,$i,$(( (e - s) / 1000000000 )).$(printf '%03d' $(( (e - s) / 1000000 % 1000 ))),$rc" >> "$SECONDS_OUT"
  fi
  return $rc
}

note_seconds() {   # note_seconds NAME VARIANT RUN SECONDS RC: a run timed by its own script
  [ -n "${SECONDS_OUT:-}" ] && echo "$1,$2,$3,$4,$5" >> "$SECONDS_OUT"
  return 0
}
