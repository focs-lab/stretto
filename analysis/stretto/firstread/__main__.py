"""python3 -m stretto.firstread [-o OUT] [--clang CLANG] [--mpi openmpi|mpich|none] [--cflags FLAGS] [--ir] FILE...

Compiles each C file to LLVM IR (clang -O0 -g -S -emit-llvm, with the MPI implementation's
include flags), analyses the units together and writes the first-read map as JSON, with the
SHA-256 of every source its sites come from ("sources"), against which a trace's program is
checked when the map is used. --ir takes .ll files instead. Exit status 0, or 2 if a file does
not compile."""
from __future__ import annotations
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from .analyze import analyse_ir
from ..reads import sha256_of


def mpi_flags(mpi: str) -> list:
    """The -I/-D flags of the MPI implementation's compiler wrapper."""
    if mpi == "none":
        return []
    wrapper, ask = ("mpicc.openmpi", "-showme:compile") if mpi == "openmpi" else ("mpicc.mpich", "-show")
    if shutil.which(wrapper) is None:
        raise SystemExit(f"firstread: {wrapper} not found (use --mpi none and --cflags)")
    out = subprocess.run([wrapper, ask], capture_output=True, text=True, check=True).stdout
    return [f for f in shlex.split(out) if f.startswith(("-I", "-D"))]


def compile_ir(clang: str, src: str, flags: list, outdir: str) -> str:
    out = os.path.join(outdir, f"{len(os.listdir(outdir))}.ll")
    r = subprocess.run([clang, "-O0", "-g", "-S", "-emit-llvm", "-w", *flags, "-o", out, src], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{src}: {r.stderr.strip()[-600:]}")
    with open(out) as fh:
        return fh.read()


def dumps(res: dict) -> str:
    """JSON with one site or result per line."""
    keys = list(res)
    lines = ["{"]
    for i, k in enumerate(keys):
        v, end = res[k], ("," if i < len(keys) - 1 else "")
        if isinstance(v, list) and v and isinstance(v[0], dict):
            lines.append(f" {json.dumps(k)}: [")
            lines += [f"  {json.dumps(x)}" + ("," if j < len(v) - 1 else "") for j, x in enumerate(v)]
            lines.append(" ]" + end)
        else:
            lines.append(f" {json.dumps(k)}: {json.dumps(v)}{end}")
    return "\n".join(lines + ["}"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m stretto.firstread", description="first-read map of a C program")
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", default="-")
    ap.add_argument("--clang", default="clang-18")
    ap.add_argument("--mpi", default="openmpi", choices=["openmpi", "mpich", "none"])
    ap.add_argument("--cflags", default="", help="more compiler flags, one string")
    ap.add_argument("--ir", action="store_true", help="the files are LLVM IR already")
    args = ap.parse_args(argv)
    texts = []
    if args.ir:
        for f in args.files:
            with open(f) as fh:
                texts.append((f, fh.read()))
    else:
        flags = mpi_flags(args.mpi) + shlex.split(args.cflags)
        with tempfile.TemporaryDirectory() as d:
            for f in args.files:
                try:
                    texts.append((f, compile_ir(args.clang, f, flags, d)))
                except RuntimeError as e:
                    print(f"firstread: {e}", file=sys.stderr)
                    return 2
    res = analyse_ir(texts)
    res["compiler"] = args.clang if not args.ir else None
    # the SHA-256 of every source the sites come from: a map is used only with a trace of a
    # program built from these very sources (stretto.reads.check_sources)
    files = dict.fromkeys([*res.get("units", []), *(s.get("file") for s in res.get("sites", []))])
    res["sources"] = {f: sha256_of(f) for f in files if f and os.path.isfile(f)}
    text = dumps(res)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
