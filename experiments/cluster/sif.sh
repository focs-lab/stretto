#!/usr/bin/env bash
# Convert the experiment images to Apptainer images (.sif) for a cluster without Docker. This
# machine has Docker and no Apptainer, so Apptainer runs in a container of its own image: each
# Docker image is saved as an archive and built into a .sif from it (docker-archive:), which
# needs no privilege. The .sif holds the image's file system as it is: the same Ubuntu packages,
# MPI libraries and compilers (experiments/container/versions.txt).
#   IMAGES     the Docker images (default "stretto-exp:24.04 stretto-must-u:24.04"; one that is
#              not on this machine is skipped with a note)
#   OUT        where the .sif files go (default build/sif, under the repository), with
#              manifest.tsv: Docker image, its id, the .sif, its sha256, Apptainer's version
#   APPTAINER  the image Apptainer runs from (default ghcr.io/apptainer/apptainer:1.3.6, pinned
#              by digest below)
#   CPUS       the conversion's cpuset (default 28-43)
# Copy OUT to the cluster (scp, or whatever its data transfer allows) and check the sha256 there.
# usage: experiments/cluster/sif.sh
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGES="${IMAGES:-stretto-exp:24.04 stretto-must-u:24.04}"
OUT="${OUT:-$R/build/sif}"
APPTAINER="${APPTAINER:-ghcr.io/apptainer/apptainer:1.3.6@sha256:1320d13fa26d962cfaccfeca7a67c8d9c5b8ad946b6a815a3e3ca60e7ee1a2b9}"
CPUS="${CPUS:-28-43}"
mkdir -p "$OUT"
run() {     # Apptainer in its container, as this user (who needs an account there), with OUT at /out
    docker run --rm --cpuset-cpus "$CPUS" --ulimit core=0 --user "$(id -u):$(id -g)" ${SECURITY:-} \
        -v "$OUT/etc/passwd:/etc/passwd:ro" -v "$OUT/etc/group:/etc/group:ro" \
        -e HOME=/out/home -e APPTAINER_TMPDIR=/out/tmp -e APPTAINER_CACHEDIR=/out/cache \
        -v "$OUT:/out" "$APPTAINER" "$@"
}
mkdir -p "$OUT/home" "$OUT/tmp" "$OUT/cache" "$OUT/etc"
printf 'root:x:0:0::/root:/bin/sh\nbuild:x:%s:%s::/out/home:/bin/sh\n' "$(id -u)" "$(id -g)" > "$OUT/etc/passwd"
printf 'root:x:0:\nbuild:x:%s:\n' "$(id -g)" > "$OUT/etc/group"
[ -f "$OUT/manifest.tsv" ] || printf 'image\timage_id\tsif\tsha256\tapptainer\n' > "$OUT/manifest.tsv"
version="$(run apptainer --version)"
for img in $IMAGES; do
    if ! docker image inspect "$img" > /dev/null 2>&1; then
        echo "sif.sh: $img is not on this machine; skipped" >&2
        continue
    fi
    name="$(echo "$img" | tr ':/' '__')"
    echo "== $img -> $OUT/$name.sif"
    docker save -o "$OUT/$name.tar" "$img"
    run apptainer build --force "/out/$name.sif" "docker-archive:///out/$name.tar"
    rm -f "$OUT/$name.tar"
    # the check: the .sif's file system (its squashfs partition, unpacked as data: apptainer exec
    # and its sandbox need namespaces that a container does not get) has every package of the
    # Docker image at its version
    run sh -c 'id=$(apptainer sif list "$0" | awk -F"|" "/Squashfs/ {print \$1+0}") && apptainer sif dump "$id" "$0" > "$1.sqfs" \
               && unsquashfs -q -n -d "$1" "$1.sqfs" var/lib/dpkg > /dev/null && rm "$1.sqfs"' "/out/$name.sif" "/out/check-$name"
    list='dpkg-query --admindir="$0" -W -f "\${Package} \${Version}\n" | sort'
    if diff <(docker run --rm --cpuset-cpus "$CPUS" --ulimit core=0 --entrypoint "" "$img" sh -c "$list" /var/lib/dpkg) \
            <(docker run --rm --cpuset-cpus "$CPUS" --ulimit core=0 --entrypoint "" -v "$OUT:/out:ro" "$img" sh -c "$list" "/out/check-$name/var/lib/dpkg"); then
        echo "$name.sif: the packages of $img, every one at its version"
    else
        echo "sif.sh: $name.sif differs from $img (< the image, > the .sif)" >&2; exit 1
    fi
    chmod -R u+w "$OUT/check-$name"; rm -rf "$OUT/check-$name"
    printf '%s\t%s\t%s.sif\t%s\t%s\n' "$img" "$(docker image inspect -f '{{.Id}}' "$img")" "$name" \
        "$(sha256sum "$OUT/$name.sif" | cut -d' ' -f1)" "$version" >> "$OUT/manifest.tsv"
done
rm -rf "$OUT/tmp" "$OUT/cache" "$OUT/etc" "$OUT/home"
cat "$OUT/manifest.tsv"
