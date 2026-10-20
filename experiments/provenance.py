"""The code version an experiment row comes from, written into every CSV row so that a
table can be traced to the commit that produced it. The runner on the experiment machine
sets REMATCH_COMMIT (the copy there may have no .git); otherwise git is asked, and a tree
with uncommitted changes to the code is marked "-dirty"."""
import functools
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE = ["analysis", "tracer", "replay", "experiments", "scripts"]


@functools.lru_cache(maxsize=1)
def code_commit() -> str:
    env = os.environ.get("REMATCH_COMMIT")
    if env:
        return env
    try:
        head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", *CODE], capture_output=True, text=True, check=True).stdout.strip()
        return head + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
