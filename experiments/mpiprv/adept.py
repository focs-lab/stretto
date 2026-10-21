#!/usr/bin/env python3
"""Summarise the Adept Kernel runs of experiments/mpiprv/adept.sh into one CSV row per run.

  result     the FNV hashes of a0 and a1 that every rank prints at their free() (result_id: the
             first digits of a hash over all of them), compared with the run of the normal
             algorithm (blocking receives before any computation)
  order      for each iteration, receiving rank r and neighbour s that sends to r: whether s
             posted that MPI_Isend after r's last MPI_Irecv of the iteration, which r's marker
             follows at once (after_marker: the order of MPI-PRV's report), and whether after r
             entered its first MPI_Wait, that is after r's whole interior loop (after_loop)
  guard      the buffer guard's records (STRETTO_GUARD=1), by kind and by the operation that
             owns the buffer, read from the guard runs' traces

usage: adept.py <log dir> <work dir with guard.trace/ and guard-fixed.trace/>  (CSV on stdout)"""
import collections
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import provenance  # noqa: E402

RUNS = ["normal", "overlapped-1", "overlapped-2", "fixed", "fixed-delay1", "fixed-delay2", "mutant", "mutant-delay1",
        "guard", "guard-fixed"]
BIG = 64 << 20   # a0 and a1 (270 MB per rank at -s 512); smaller freed blocks are the library's


def parse(path):
    hashes, ev, head, rc, dur = collections.defaultdict(list), collections.defaultdict(list), "", "", ""
    for line in open(path, errors="replace"):
        if line.startswith("# "):
            head = line[2:].strip()
        elif line.startswith("rc "):
            rc = line.split()[1]
        m = re.match(r"\[adept\] rank (\d+) free (\d+) fnv ([0-9a-f]+)", line)
        if m and int(m.group(2)) >= BIG:
            hashes[int(m.group(1))].append(m.group(3))
        m = re.match(r"\[adept\] rank (\d+) (isend (\d+)|irecv (-?\d+)|wait-enter|wait-exit|barrier) t (\d+)", line)
        if m:
            kind = m.group(2).split()[0]
            peer = m.group(3) or m.group(4)
            ev[int(m.group(1))].append((kind, int(peer) if peer is not None else None, int(m.group(5))))
        m = re.search(r"Duration: ([0-9.]+) s", line)
        if m:
            dur = m.group(1)
    return head, rc, dur, hashes, ev


def iterations(evs):
    """One rank's events between consecutive barriers: the first barrier precedes the
    iteration loop and every iteration ends with one."""
    its, cur, seen = [], None, False
    for e in evs:
        if e[0] == "barrier":
            if seen:
                its.append(cur)
            cur, seen = [], True
        elif seen:
            cur.append(e)
    return its


def late_sends(ev):
    """(sends after the receiver's marker, sends after the receiver's interior loop, pairs)."""
    its = {r: iterations(e) for r, e in ev.items()}
    after_marker = after_loop = total = 0
    for r, rit in its.items():
        for k, it in enumerate(rit):
            posts = [t for kind, _, t in it if kind == "irecv"]
            waits = [t for kind, _, t in it if kind == "wait-enter"]
            if not posts or not waits:
                continue
            for s in (r - 1, r + 1):
                if s not in its or k >= len(its[s]):
                    continue
                sends = [t for kind, d, t in its[s][k] if kind == "isend" and d == r]
                if sends:
                    total += 1
                    after_marker += sends[0] > posts[-1]
                    after_loop += sends[0] > waits[0]
    return after_marker, after_loop, total


def guard_summary(tdir):
    owner, recs = {}, []
    for f in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
        m = re.match(r"rank-(\d+)\.jsonl", f)
        if not m:
            continue
        for line in open(os.path.join(tdir, f)):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("op") == "guard":
                recs.append((int(m.group(1)), r))
            elif "req" in r and r.get("op") in ("isend", "irecv"):
                owner.setdefault((int(m.group(1)), r["req"]), r["op"])
    c = collections.Counter()
    for rank, r in recs:
        c[f"{r.get('what')}:{r.get('kind', '-')}:{owner.get((rank, r.get('req')), '?')}"] += 1
    return len(recs), " ".join(f"{k}*{v}" for k, v in sorted(c.items()))


def main():
    logs, work = sys.argv[1], sys.argv[2]
    ref = None
    print("run,command,rc,duration_s,ranks_hashed,result_id,same_result_as_normal,"
          "sends_after_marker,sends_after_loop,send_pairs,guard_records,guard_kinds,commit")
    for name in RUNS:
        p = os.path.join(logs, name + ".log")
        if not os.path.exists(p):
            continue
        head, rc, dur, hashes, ev = parse(p)
        rid = hashlib.sha1(repr(sorted(hashes.items())).encode()).hexdigest()[:8] if hashes else "-"
        if name == "normal":
            ref = dict(hashes)
        same = "-" if not hashes or ref is None else str(dict(hashes) == ref)
        am, al, total = late_sends(ev) if ev else ("-", "-", "-")
        g, gk = guard_summary(os.path.join(work, name + ".trace")) if name.startswith("guard") else ("-", "")
        cmd = head.split(";")[0].split(": ", 1)[-1].replace(",", " ")
        print(f"{name},\"{cmd}\",{rc},{dur},{len(hashes)},{rid},{same},{am},{al},{total},{g},\"{gk}\","
              f"{provenance.code_commit()}")


if __name__ == "__main__":
    main()
