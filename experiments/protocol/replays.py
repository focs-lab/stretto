#!/usr/bin/env python3
"""The replays of the uniform campaign (experiments/protocol/RULE.md, Section 6), for the
configurations whose traces predict the bug: every distinct alternative, by its set of pins, over
the configuration's kept traces (KEEP_TRACES/protocol/<subject>/<config>/bug/run*.tar.xz, which
run.py keeps; those of the current rows of RESULTS/<subject>/stretto-traced.csv), a sample of 40 drawn with a recorded seed if there are more; each replayed with its
control. Two steps, both jobs of experiments/protocol/schedule.py:

  enumerate  (run 0) takes, for every kept trace of the configuration, the pins run.py wrote when
             it analysed the run (STATE/alternatives/), or else analyses the trace again as run.py
             does: the findings of the subject's classifier (subjects.py, `stretto`), and for each its pins
             as stretto confirm takes them (stretto.confirm: the pins of the alternative's whole
             horizon, or, for a finding that holds in some execution only, the finding's pin and
             its control pin); the distinct pin sets, in the order of the traces, are written to
             RESULTS/<subject>/replay-alternatives-<config>.csv with the seed and how many there
             were. With more than 40 a sample of 40 is drawn by random.Random(SEED:<subject>:<config>).
  replay     (run k) replays alternative k with its pins (stretto.confirm.run_checked, under the
             tracer, which judges the replay a run of the program or not) and its control, each
             judged by the subject's judge (run.py). A replay is confirmed by the committed
             criterion: every required pin matched, the replay shows the bug's symptom (the predicted
             outcome) and the control ends correctly; inconclusive when a pin fell on another
             operation (the replay diverged before it) or the control did not end correctly;
             not-confirmed otherwise.

The denominator is the distinct alternatives replayed; replays per trace are not counted.

usage: replays.py enumerate --subject S --config C --row FILE [--seed K] [--results DIR]
       replays.py replay --subject S --config C --index K --timeout T --row FILE [--results DIR]
"""
import argparse
import csv
import json
import os
import random
import shutil
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
sys.path.insert(0, HERE)
import rows  # noqa: E402
import run as runner  # noqa: E402
import subjects as subjects_mod  # noqa: E402

SAMPLE = 40
SEED = "20261124"
ALT_FIELDS = ["index", "run", "cls", "level", "pin", "pins", "control_pins", "required", "distinct", "findings",
              "sampled", "seed"]


def alternatives_path(results, subject, config):
    return os.path.join(results, subject, "replay-alternatives-%s.csv" % config)


def pins_of(t, f, rep):
    """(pins, control pins, required matches) of a finding, as stretto.confirm.confirm takes them."""
    from stretto.confirm import _horizon, control_pin, with_identity
    hp = _horizon(t, f) if f.certainty != "some" else None
    if hp is not None:
        pins, ctl, required = hp
        return list(pins), list(ctl), sorted(list(x) for x in required) if required is not None else None
    pin = f.pin
    if pin is None:
        parent = next((g for g in rep.findings if g.pin and g.recv == f.recv and g.send == f.send), None)
        pin = parent.pin if parent else None
    if pin is None:
        return None
    cp = control_pin(t, pin)
    return with_identity(t, [pin]), [cp] if cp else [], None


def enumerate_(a):
    from stretto import keep, trace
    from stretto.report import analyze
    s = subjects_mod.subjects()[a.subject]
    base = os.path.join(keep.directory() or "", "protocol", a.subject, a.config, "bug")
    archives = sorted(f for f in os.listdir(base) if f.endswith(keep.EXT)) if os.path.isdir(base) else []
    # the traces of the configuration's current traced runs only: a superseded run's archive stays
    # beside its rerun's, and its alternatives are not the campaign's
    current = set()
    for r in rows.current(rows.read(os.path.join(a.results, a.subject, "stretto-traced.csv"))):
        if r["config"] == a.config and r["version"] == "bug" and r.get("detail"):
            name = json.loads(r["detail"]).get("archive")
            if name:
                current.add(name + keep.EXT)
    if current:
        archives = [f for f in archives if f in current]
    cls = s["stretto"]["bug"] if isinstance(s["stretto"], dict) else s["stretto"]
    distinct, order, findings, t0 = {}, [], 0, time.monotonic()
    state = os.environ.get("PROTOCOL_STATE", "/state")
    for name in archives:
        side = os.path.join(state, "alternatives", a.subject, a.config, "bug", name[:-len(keep.EXT)] + ".json")
        if os.path.exists(side):
            # the pins run.py wrote when it analysed the traced run: the same analysis, not repeated
            with open(side) as fh:
                got = json.load(fh)
            findings += got["findings"]
            for x in got["alternatives"]:
                k = tuple(x["pins"])
                if k not in distinct:
                    distinct[k] = {"run": name[:-len(keep.EXT)], "cls": x["cls"], "level": x["level"], "pin": x["pin"],
                                   "pins": json.dumps(x["pins"]), "control_pins": json.dumps(x["control_pins"]),
                                   "required": json.dumps(x["required"])}
                    order.append(k)
            continue
        d = tempfile.mkdtemp(prefix="protocol-replay-trace-")
        try:
            with tarfile.open(os.path.join(base, name)) as tf:
                tf.extractall(d)
            sub = [os.path.join(d, x) for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
            t = trace.load(sub[0] if sub else d)
            rep = analyze(t, with_sp=s["sp"])
            for f in cls(t, rep):
                findings += 1
                got = pins_of(t, f, rep)
                if got is None:
                    continue
                k = tuple(got[0])
                if k not in distinct:
                    distinct[k] = {"run": name[:-len(keep.EXT)], "cls": f.cls, "level": f.level, "pin": f.pin or "",
                                   "pins": json.dumps(got[0]), "control_pins": json.dumps(got[1]),
                                   "required": json.dumps(got[2])}
                    order.append(k)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    chosen = list(range(len(order)))
    sampled = len(order) > SAMPLE
    if sampled:
        chosen = sorted(random.Random("%s:%s:%s" % (a.seed, a.subject, a.config)).sample(chosen, SAMPLE))
    out = []
    for i, c in enumerate(chosen, 1):
        out.append(dict(distinct[order[c]], index=i, distinct=len(order), findings=findings, sampled=sampled,
                        seed=a.seed if sampled else ""))
    dest = alternatives_path(a.results, a.subject, a.config)
    if os.path.exists(dest):
        os.replace(dest, dest + ".previous")
    rows.append(dest, out, ALT_FIELDS) if out else open(dest, "w").write(",".join(ALT_FIELDS) + "\n")
    c = s["configs"][a.config]
    row = {"subject": a.subject, "config": a.config, "np": c["np"], "mpi": c["mpi"], "version": "bug",
           "label": s["versions"]["bug"][0], "tool": "stretto", "setting": "replay-enumerate", "run": 0,
           "result": "ENUMERATED", "seconds": round(time.monotonic() - t0, 1), "says": "",
           "symptom": "", "reported": "", "failure": "",
           "detail": json.dumps({"traces": len(archives), "findings": findings, "distinct": len(order),
                                 "replayed": len(out), "sampled": sampled})}
    row.update(rows.common("", seed=a.seed if sampled else ""))
    write_row(a.row, row)
    print("%s %s: %d traces, %d findings, %d distinct alternatives, %d to replay" % (a.subject, a.config, len(archives),
                                                                                  findings, len(order), len(out)))
    return 0


def write_row(path, row):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        json.dump(row, fh)
    os.replace(path + ".tmp", path)


def replay(a):
    from stretto.confirm import all_matched, run_checked
    s = subjects_mod.subjects()[a.subject]
    c = s["configs"][a.config]
    alt = next(r for r in rows.read(alternatives_path(a.results, a.subject, a.config)) if int(r["index"]) == a.index)
    pins, ctl_pins = json.loads(alt["pins"]), json.loads(alt["control_pins"])
    req = json.loads(alt["required"])
    required = set(tuple(x) for x in req) if req is not None else None
    exe, args = subjects_mod.program(s, a.config, "bug")
    os.environ.update(runner.launcher_env(c["mpi"]))
    os.environ.update(subjects_mod.env_of(s, a.config))
    out = {}
    for role, p in (("replay", pins), ("control", ctl_pins)):
        if role == "control" and not p:
            out[role] = None
            continue
        wd = tempfile.mkdtemp(prefix="protocol-replay-")
        if s.get("base"):
            shutil.copytree(s["base"], wd, dirs_exist_ok=True, symlinks=True)
        t0 = time.monotonic()
        r = run_checked(c["np"], exe, args, p, a.timeout, c["mpi"], cwd=wd)
        r.result = runner.abort_class(r.result, c["mpi"])
        el = time.monotonic() - t0
        text = r.proc.stdout + r.proc.stderr
        ok, says = runner.judge(s, "bug", a.config, text, wd)
        S, failure = runner.symptom_of(s, r.result, ok, text)
        out[role] = {"result": r.result, "seconds": round(el, 1), "ok": ok, "says": says, "symptom": S,
                     "matched": all_matched(p, r.proc.stderr, required if role == "replay" else None, r.seen),
                     "valid": r.valid, "misapplied": "; ".join(r.misapplied)[:300]}
        if r.result != "FINISHED" or not ok:
            runner.keep_output(a, "%s-%s-replay-%d-%s" % (a.subject, a.config, a.index, role), [exe] + args,
                               r.proc.returncode, r.proc.stdout, r.proc.stderr, wd)
        shutil.rmtree(wd, ignore_errors=True)
    rp, ct = out["replay"], out["control"]
    control_ok = ct is not None and ct["result"] == "FINISHED" and ct["ok"]
    if rp["misapplied"] or (ct and ct["misapplied"]):
        verdict = "inconclusive"
    elif rp["matched"] and rp["symptom"] and control_ok:
        verdict = "confirmed"
    elif rp["matched"] and rp["symptom"]:
        verdict = "inconclusive"          # the control did not end correctly
    else:
        verdict = "not-confirmed"
    row = {"subject": a.subject, "config": a.config, "np": c["np"], "mpi": c["mpi"], "version": "bug",
           "label": s["versions"]["bug"][0], "tool": "stretto", "setting": "replay", "run": a.index,
           "result": rp["result"], "seconds": rp["seconds"], "says": rp["says"], "symptom": rp["symptom"],
           "reported": rp["matched"], "failure": "",
           "detail": json.dumps({"alternative": {k: alt[k] for k in ("run", "cls", "level", "pin")}, "replay": rp,
                                 "control": ct, "verdict": verdict}, sort_keys=True)}
    row.update(rows.common(a.timeout, seed=alt["seed"], category=verdict))
    write_row(a.row, row)
    print("%s %s replay %d: %s matched=%s symptom=%s; control %s -> %s" % (
        a.subject, a.config, a.index, rp["result"], rp["matched"], rp["symptom"], ct and ct["result"], verdict))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("enumerate", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--subject", required=True)
        p.add_argument("--config", required=True)
        p.add_argument("--row", required=True)
        p.add_argument("--results", default=os.path.join(HERE, "results"))
        p.add_argument("--outputs", default="")
    sub.choices["enumerate"].add_argument("--seed", default=SEED)
    sub.choices["replay"].add_argument("--index", type=int, required=True)
    sub.choices["replay"].add_argument("--timeout", type=int, required=True)
    a = ap.parse_args()
    return enumerate_(a) if a.cmd == "enumerate" else replay(a)


if __name__ == "__main__":
    sys.exit(main())
