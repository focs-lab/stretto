"""Keep the traces a run records (KEEP_TRACES), for an archive of a whole rerun.

The experiment drivers delete or overwrite the traces they analyse: a replay's trace goes as
soon as it has been judged, a traced run's once its alternatives are drawn, and a trace kept in
place is replaced by the next run of the same step. With KEEP_TRACES set to a directory, every
driver hands each trace it records to keep() as soon as the run is over (after the analysis it
times, where it times one), and keep() writes the directory as one compressed tar,

    <KEEP_TRACES>/<campaign>/<name>.tar.xz

and appends a line to <KEEP_TRACES>/MANIFEST.tsv. Without KEEP_TRACES, keep() does nothing and
no driver does anything it did not do before. The campaign is the driver's table (campaign_of:
its path under experiments/results without the extension, such as hypre/predict or
valid/nekbone, or the step's name), the name is the run as the table's rows name it (program,
process count, run index; the alternative's pin and "replay" or "control" for a replay). An
archive is never overwritten: a name already taken gets ~2, ~3, ... (a step run twice keeps the
traces of both). A relative KEEP_TRACES is taken from the repository root, so that drivers that
run from an application's directory, and the containers, which mount the repository at /w, all
find the same directory; an absolute one must be visible inside the containers
(experiments/container/run.sh and experiments/mpiprv/prv.sh mount it at its own path).

The tar holds the directory's files under <name>/, and for a replay a note with its command,
pins, outcome and output (stretto.confirm.run_checked: the pin lines that confirmation reads
are in its stderr). It is compressed with xz at preset 1: on these JSON-lines traces 20 to 160
times smaller, at 30 to 80 MB/s on one processor (measured on AMG, HYPRE, NPB MG and LU,
Nekbone and deal.II traces; gzip -6 gives 11 to 33 times at about 1.6 times the speed, xz -6
files at most a quarter smaller at a tenth of the speed or less). The manifest's columns:
campaign, name, archive (relative to KEEP_TRACES), source (the trace directory, relative to the
repository root when inside it), files, bytes (before), archive_bytes, sha256 (of the archive),
commit (of the code that recorded the run, experiments/provenance.py), time (UTC). Drivers in
several processes and containers append to it at once, each line under an exclusive lock
(flock).

A trace that cannot be kept raises KeepError, which stops the driver: a rerun that is to keep
every trace must not lose one silently.

usage: python3 -m stretto.keep <trace dir> <campaign> <name>
       (experiments/harness.sh keep_trace; silent unless it fails, with status 1 and KEEP-ERROR
       on stderr)
"""
import fcntl
import functools
import hashlib
import importlib.util
import io
import os
import re
import sys
import tarfile
import tempfile
import time
from typing import Dict, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENV = "KEEP_TRACES"
EXT = ".tar.xz"
PRESET = 1
MANIFEST = "MANIFEST.tsv"
FIELDS = ["campaign", "name", "archive", "source", "files", "bytes", "archive_bytes", "sha256", "commit", "time"]


class KeepError(RuntimeError):
    """A trace that could not be kept."""


def directory() -> Optional[str]:
    """The archive directory KEEP_TRACES names (a relative one from the repository root), or
    None when it is not set."""
    d = os.environ.get(ENV, "")
    if not d:
        return None
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def enabled() -> bool:
    return directory() is not None


_UNSAFE = re.compile(r"[^A-Za-z0-9._+=-]+")


def safe(part) -> str:
    """part as one file name: every run of characters other than letters, digits and ._+=- is
    one "_", a leading dot is replaced, and a long name keeps its start and a hash of the whole."""
    s = _UNSAFE.sub("_", str(part)).strip("_") or "_"
    if s.startswith("."):
        s = "_" + s[1:]
    if len(s) > 150:
        s = s[:130] + "-" + hashlib.sha1(str(part).encode()).hexdigest()[:12]
    return s


def campaign_of(table: str, root: str = ROOT) -> str:
    """The campaign of a driver's table: its path under experiments/results without the
    extension (hypre/predict, valid/nekbone); for a table elsewhere in the repository its path
    from the root, and outside the repository its file name."""
    base = os.path.splitext(os.path.abspath(table))[0]
    for top in (os.path.join(root, "experiments", "results"), root):
        rel = os.path.relpath(base, top)
        if rel != os.pardir and not rel.startswith(os.pardir + os.sep):
            return rel.replace(os.sep, "/")
    return os.path.basename(base)


@functools.lru_cache(maxsize=1)
def _code_commit() -> str:
    env = os.environ.get("STRETTO_COMMIT")
    if env:
        return env
    try:
        spec = importlib.util.spec_from_file_location("_stretto_provenance",
                                                      os.path.join(ROOT, "experiments", "provenance.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.code_commit()
    except Exception:  # noqa: BLE001  (a copy without experiments/: the commit is not known)
        return "unknown"


def _files(tracedir: str):
    out = []
    for dp, dns, fns in os.walk(tracedir):
        dns.sort()
        for f in sorted(fns):
            out.append(os.path.join(dp, f))
    return out


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write(tracedir: str, stem: str, notes: Dict[str, str], dest: str) -> str:
    """The archive of tracedir, its files under stem/, in a temporary file in dest, written
    through to the disk (a timed run after it then finds no write-back in progress)."""
    fd, tmp = tempfile.mkstemp(prefix=".keep-", suffix=".part", dir=dest)
    try:
        with os.fdopen(fd, "wb") as raw:
            os.fchmod(raw.fileno(), 0o644)      # mkstemp's 0600 would keep the archive from its readers
            with tarfile.open(fileobj=raw, mode="w:xz", preset=PRESET) as tar:
                tar.add(tracedir, arcname=stem, recursive=True)
                for member, text in sorted(notes.items()):
                    data = text.encode("utf-8", "replace")
                    info = tarfile.TarInfo(f"{stem}/{member}")
                    info.size, info.mtime, info.mode = len(data), int(time.time()), 0o644
                    tar.addfile(info, io.BytesIO(data))
            raw.flush()
            os.fsync(raw.fileno())
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp


def _claim(tmp: str, final: str) -> bool:
    """Give tmp the name final unless that name is taken (True if it got it), never replacing
    an existing file: a hard link fails if the name exists; without hard links, the name is
    created exclusively first and then replaced."""
    try:
        os.link(tmp, final)
    except FileExistsError:
        return False
    except OSError:
        try:
            os.close(os.open(final, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
        except FileExistsError:
            return False
        os.replace(tmp, final)
        return True
    os.unlink(tmp)
    return True


def _clean(value) -> str:
    return re.sub(r"[\t\r\n]+", " ", str(value))


def _append(base: str, row: dict) -> None:
    """One line of MANIFEST.tsv, under an exclusive lock; the header when the file is new."""
    with open(os.path.join(base, MANIFEST), "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            if os.fstat(fh.fileno()).st_size == 0:
                fh.write("\t".join(FIELDS) + "\n")
            fh.write("\t".join(_clean(row.get(k, "")) for k in FIELDS) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def keep(tracedir: str, campaign: str, name: str, notes: Optional[Dict[str, str]] = None,
         commit: Optional[str] = None) -> Optional[str]:
    """Archive the trace directory tracedir as <KEEP_TRACES>/<campaign>/<name>.tar.xz (the
    module docstring), with notes, {member: text}, as more files beside the trace's; the
    archive's path, or None when KEEP_TRACES is not set or the run left no directory. commit is
    the code that recorded the run (default: STRETTO_COMMIT, else experiments/provenance.py)."""
    base = directory()
    if base is None or not os.path.isdir(tracedir):
        return None
    parts = [safe(p) for p in campaign.split("/") if p] or ["_"]
    stem = safe(name)
    dest = os.path.join(base, *parts)
    try:
        os.makedirs(dest, exist_ok=True)
        files = _files(tracedir)
        before = sum(os.path.getsize(f) for f in files)
        k = 1
        while True:
            label = stem if k == 1 else f"{stem}~{k}"
            final = os.path.join(dest, label + EXT)
            if not os.path.exists(final):
                tmp = _write(tracedir, label, notes or {}, dest)
                try:
                    if _claim(tmp, final):
                        break
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
            k += 1
        source = os.path.abspath(tracedir)
        rel = os.path.relpath(source, ROOT)
        _append(base, {"campaign": "/".join(parts), "name": stem, "archive": os.path.relpath(final, base),
                       "source": source if rel == os.pardir or rel.startswith(os.pardir + os.sep) else rel,
                       "files": len(files), "bytes": before, "archive_bytes": os.path.getsize(final),
                       "sha256": _sha256(final), "commit": commit or _code_commit(),
                       "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    except (OSError, tarfile.TarError) as e:
        raise KeepError(f"cannot keep {tracedir} as {'/'.join(parts)}/{stem}{EXT} under {base}: {e}") from e
    return final


def main(argv=None) -> int:
    """The command line: silent when it succeeds, since some drivers write their standard
    output into a result file (experiments/guard/run.sh)."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print("usage: python3 -m stretto.keep <trace dir> <campaign> <name>", file=sys.stderr)
        return 2
    try:
        keep(*argv)
    except KeepError as e:
        print(f"KEEP-ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
