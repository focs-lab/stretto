#!/usr/bin/env bash
# drop the Changes.log paragraph that describes the MG change (strip list in the key)
f="$1/Changes.log"
[ "$(sed -n 171p "$f")" = "      The use of MPI_ANY_SOURCE for MPI_Irecv inside subroutine" ] || { echo "npb strip: unexpected line 171" >&2; exit 1; }
[ "$(sed -n 175p "$f")" = "      (reported and suggested by Hideo Saito)." ] || { echo "npb strip: unexpected line 175" >&2; exit 1; }
sed -i '171,176d' "$f"
