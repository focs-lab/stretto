"""The commit of the analysis code, for the JSON output: the repository's own provenance
(experiments/provenance.py: STRETTO_COMMIT, else git with "-dirty" for changed code, else the
deployed COMMIT checked against COMMIT.files), or "unknown" for a package used outside it."""
import functools
import importlib.util
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@functools.lru_cache(maxsize=1)
def analysis_commit() -> str:
    path = os.path.join(ROOT, "experiments", "provenance.py")
    if not os.path.exists(path):
        return os.environ.get("STRETTO_COMMIT") or "unknown"
    spec = importlib.util.spec_from_file_location("stretto_provenance", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.code_commit()
