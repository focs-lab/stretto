"""The code version an experiment row comes from, written into every CSV row so that a table can
be traced to the commit that produced it, and marked when the code that ran is not that commit.

The commit is found, in this order:
  1. STRETTO_COMMIT, which a driver sets from this module (python3 experiments/provenance.py), so
     that its rows and those of the scripts it starts agree;
  2. git, when the repository is a working tree git can read: HEAD, with "-dirty" when a file of
     the code differs from it;
  3. COMMIT, which a deployment writes into a copy without .git, checked against COMMIT.files,
     the code's files at that commit as `git ls-tree -r HEAD` lists them (blob hashes): "-dirty"
     when a file differs or is missing, "-unchecked" when there is no list to check against.
The code is every tracked file under CODE except the result tables, which a run writes.

`python3 experiments/provenance.py` prints the commit; `--write` writes COMMIT and COMMIT.files
from git, as a deployment or artifact/reproduce.sh does before each step. Python 3.6 (the
experiment machine's host) runs this file too."""
import functools
import hashlib
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE = ["analysis", "tracer", "replay", "rerun", "experiments", "scripts", "idioms", "artifact"]
NOT_CODE = ["experiments/results", "experiments/generated"]   # what a run and tables.py write
MANIFEST = "COMMIT.files"


def _git(*args):
    p = subprocess.run(["git", "-C", ROOT, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, check=True)
    return p.stdout


def _pathspec():
    return ["--", *CODE, *[":(exclude)" + x for x in NOT_CODE]]


def blob_hash(path: str) -> str:
    """git's hash of a file's content (git hash-object)."""
    with open(path, "rb") as fh:
        data = fh.read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def manifest_differs(root: str = None):
    """The files of COMMIT.files whose content in root differs from the listed blob (or that are
    missing); None if there is no COMMIT.files."""
    root = root or ROOT
    path = os.path.join(root, MANIFEST)
    if not os.path.exists(path):
        return None
    out = []
    with open(path) as fh:
        for line in fh:
            meta, sep, rel = line.rstrip("\n").partition("\t")
            parts = meta.split()
            if not sep or len(parts) < 3 or parts[1] != "blob":
                continue
            f = os.path.join(root, rel)
            if not os.path.isfile(f) or blob_hash(f) != parts[2]:
                out.append(rel)
    return out


@functools.lru_cache(maxsize=1)
def code_commit() -> str:
    env = os.environ.get("STRETTO_COMMIT")
    if env:
        return env
    try:
        head = _git("rev-parse", "--short", "HEAD").strip()
        dirty = _git("status", "--porcelain", *_pathspec()).strip()
        return head + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        pass
    try:
        with open(os.path.join(ROOT, "COMMIT")) as fh:
            deployed = fh.read().strip()
    except OSError:
        deployed = ""
    if not deployed:
        return "unknown"
    differs = manifest_differs()
    if differs is None:
        return deployed + "-unchecked"
    return deployed + ("-dirty" if differs else "")


def write_commit(root: str = None) -> str:
    """COMMIT and COMMIT.files from git's HEAD, for a copy that has no .git; the commit."""
    root = root or ROOT
    head = _git("rev-parse", "--short", "HEAD").strip()
    # ls-tree takes no exclude pathspec: the results are left out here
    listing = "".join(line + "\n" for line in _git("ls-tree", "-r", "HEAD", "--", *CODE).splitlines()
                      if not any(line.partition("\t")[2].startswith(x + "/") for x in NOT_CODE))
    with open(os.path.join(root, "COMMIT"), "w") as fh:
        fh.write(head + "\n")
    with open(os.path.join(root, MANIFEST), "w") as fh:
        fh.write(listing)
    return head


if __name__ == "__main__":
    if "--write" in sys.argv[1:]:
        write_commit()
    print(code_commit())
