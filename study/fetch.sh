#!/usr/bin/env bash
# Fetch the WP0a corpus: the three ECP proxy apps that use MPI_ANY_SOURCE, plus
# MPI-BugBench for idiom sources. Shallow clones into study/apps (git-ignored).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p apps && cd apps
clone() { [ -d "$2" ] && { echo "have $2"; return; }; git clone --depth 1 "$1" "$2"; }
clone https://github.com/LLNL/AMG.git            AMG
clone https://github.com/LLNL/MACSio.git         MACSio
clone https://github.com/Nek5000/Nekbone.git     Nekbone
clone https://gitlab.com/MpiBugsInitiative/MpiBugsInitiative.git MBI          # MPI Bugs Initiative (1,730 programs)
clone https://github.com/tudasc/MPI-CorrBench.git                     MPI-CorrBench
# MPI-BugBench (EuroMPI'24) merges MBI + MPI-CorrBench + RMARaceBench; its public location
# was not found under github.com/tudasc or RWTH GitLab on 2026-09-05. Confirm with the
# paper's artifact link and add the clone here.
