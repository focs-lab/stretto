#!/usr/bin/env python3
"""Summarise Stretto prediction CSVs (scout.py and history_predict.py formats) for the key.
usage: summ.py file.csv [filter column=value ...]"""
import csv, sys
from collections import Counter, defaultdict

path = sys.argv[1]
flt = [a.split('=', 1) for a in sys.argv[2:]]
rows = [r for r in csv.DictReader(open(path)) if all(r.get(k) == v for k, v in flt)]
if not rows:
    print(path, 'no rows'); sys.exit()
if 'driver' in rows[0]:     # scout.py
    g = defaultdict(list)
    for r in rows:
        g[(r['driver'], r['np'])].append(r)
    for (d, np), rs in sorted(g.items()):
        runs = sorted(set(r['run'] for r in rs))
        alts = [r for r in rs if r.get('pin')]
        print(f'{path}: driver={d} np={np} runs={len(runs)} traced={dict(Counter(r["traced"] for r in {x["run"]: x for x in rs}.values()))}')
        print('  alternatives replayed:', len(alts), 'kinds', dict(Counter(r['kind'] for r in alts)),
              'replay', dict(Counter(r['replay'] for r in alts)), 'matched', Counter(r['matched'] for r in alts)['True'],
              'valid', Counter(r['run_valid'] for r in alts)['True'],
              'differs_control', Counter(r['differs_control'] for r in alts)['True'],
              'control', dict(Counter(r['control'] for r in alts)))
        for run in runs:
            r0 = next(r for r in rs if r['run'] == run)
            print('  run', run, 'findings', r0['findings'][:300])
            break
else:                       # history_predict.py
    g = defaultdict(list)
    for r in rows:
        g[(r['version'], r['np'])].append(r)
    for (v, np), rs in sorted(g.items()):
        byrun = {}
        for r in rs:
            byrun.setdefault(r['run'], r)
        clean = [r for r in byrun.values() if r['traced_ok'] == 'True']
        cross_runs = [r for r in clean if 'cross' in (r.get('kinds') or '')]
        alts = [r for r in rs if r.get('pin')]
        print(f'{path}: version={v} np={np} traced={len(byrun)} clean={len(clean)} clean_with_cross={len(cross_runs)}')
        print('  kinds per run:', '; '.join(sorted(set(r.get('kinds', '') for r in clean)))[:400])
        print('  replayed:', len(alts), 'roles', dict(Counter(r['role'] for r in alts)), 'kinds', dict(Counter(r['kind'] for r in alts)),
              'replay', dict(Counter(r['replay'] for r in alts)), 'replay_ok', dict(Counter(r['replay_ok'] for r in alts)),
              'matched', Counter(r['matched'] for r in alts)['True'], 'valid', Counter(r['run_valid'] for r in alts)['True'],
              'verdict', dict(Counter(r['verdict'] for r in alts)), 'strict', dict(Counter(r['strict'] for r in alts)),
              'control_ok', dict(Counter(r['control_ok'] for r in alts)))
