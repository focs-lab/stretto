#!/usr/bin/env bash
# List every wildcard / completion-index / probe site in the fetched apps with context.
# usage: study/sites.sh [app...]   (default: AMG MACSio Nekbone)
cd "$(dirname "$0")/apps" || exit 1
apps=("$@"); [ ${#apps[@]} -eq 0 ] && apps=(AMG MACSio Nekbone)
pat='MPI_ANY_SOURCE|MPI_ANY_TAG|MPI_Waitany|MPI_Waitsome|MPI_Testany|MPI_Testsome|MPI_Probe|MPI_Iprobe|MPI_Get_count|mpi_any_source|mpi_any_tag|mpi_waitany|mpi_probe|mpi_iprobe|mpi_get_count'
for a in "${apps[@]}"; do
    echo "===== $a"
    grep -rniE --include='*.c' --include='*.h' --include='*.cpp' --include='*.cc' --include='*.f' --include='*.F' --include='*.f90' --include='*.F90' -B3 -A12 "$pat" "$a" 2>/dev/null
done
