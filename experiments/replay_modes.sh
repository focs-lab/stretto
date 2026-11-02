# Replay modes of the replays of several threads (docs/threads.md, section 19), sourced by the
# replay scripts (experiments/slate/replay.sh, experiments/parsec/replay.sh): mode_env MODE
# prints the variables (NAME=VALUE ...) that stretto.confirm reads for that mode.
#   before  gates never give way, no task priorities, nothing gated beyond the horizon (section 16)
#   yield   gates give way when nothing moves, task priorities; nothing beyond the horizon
#   after   the defaults: as yield, and the control gated beyond the horizon in the run's order
#   both    as after, and the replay gated beyond its horizon too
mode_env() {
  case $1 in
    before) echo STRETTO_GATE_YIELD_MS=0 STRETTO_REPLAY_TASKS=0 STRETTO_REPLAY_TAILS=none;;
    yield) echo STRETTO_REPLAY_TAILS=none;;
    after) echo STRETTO_REPLAY_TAILS=control;;
    both) echo STRETTO_REPLAY_TAILS=both;;
    *) echo "unknown replay mode $1" >&2; return 1;;
  esac
}
