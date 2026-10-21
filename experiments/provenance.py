"""The code version an experiment row comes from, written into every CSV row so that a
table can be traced to the commit that produced it. The copy on the experiment machine has
no .git: there the runner sets STRETTO_COMMIT, or the deployment writes the commit into
COMMIT at the root, which every script reads, not only those that export the variable.
Otherwise git is asked, and a tree with uncommitted changes to the code is marked "-dirty"."""
import functools
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE = ["analysis", "tracer", "replay", "experiments", "scripts"]


@functools.lru_cache(maxsize=1)
def code_commit() -> str:
    env = os.environ.get("STRETTO_COMMIT")
    if env:
        return env
    try:
        with open(os.path.join(ROOT, "COMMIT")) as fh:
            deployed = fh.read().strip()
        if deployed:
            return deployed
    except OSError:
        pass
    try:
        head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", *CODE], capture_output=True, text=True, check=True).stdout.strip()
        return head + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
