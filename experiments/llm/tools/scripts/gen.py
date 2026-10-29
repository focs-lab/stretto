#!/usr/bin/env python3
"""Build the blind packets from staged source trees and an item spec.

usage: gen.py spec.json stage_dir out_packets_dir out_meta.json

spec.json: list of items; each item has
  id, lang (fence language), program, driver, options, procs, mpi, input (optional),
  root (tree directory under stage_dir, i.e. the item id), parts: list of
    {"file": path relative to the tree, "sel": [selector, ...]}
  selector:
    {"fn": regex}            the function whose signature line matches (first match after
                             "after" if given), to its end; with its preceding comment block
    {"fn": regex, "nth": k}  the k-th match (0-based)
    {"lines": regex}         every matching line
    {"block": [ra, rb]}      from the first line matching ra through the next matching rb
    {"range": [a, b]}        lines a..b (1-based, inclusive)
"""
import json, re, sys, os

QUESTION = ("Consider runs of this program with the stated driver, options and process count, on a "
            "standard MPI implementation with default settings. Can the MPI message matching (which "
            "message a receive or probe with MPI_ANY_SOURCE/MPI_ANY_TAG, or a test/probe poll, takes) "
            "differ between runs in a way that changes the program's behaviour (a hang, an abort or "
            "crash, or a different/wrong result), including deadlocks that depend on whether sends are "
            "buffered? Answer in JSON: {\"verdict\": \"race\" | \"no-race\", \"confidence\": 0..1, "
            "\"location\": \"file:line of the receive/probe that can take the wrong message, or null\", "
            "\"consequence\": \"hang\" | \"crash\" | \"wrong-result\" | \"none\", \"conditions\": "
            "\"short\", \"reasoning\": \"at most 150 words\"}.")

RULE = ("The excerpt follows one rule for every packet: the functions that contain the "
        "receive, probe and poll calls with MPI_ANY_SOURCE or MPI_ANY_TAG (or test/probe polls) "
        "of the code under study; the functions that call them, up to two caller levels (a "
        "function that only forwards its arguments to the next one is shown but not counted as a "
        "level), which hold the code that runs between consecutive invocations; the functions of "
        "the same files that these call and that make the sends and collectives of the same "
        "exchanges, and the code that makes the matching sends where that is elsewhere; the "
        "definitions of the tags and macros they use; and the driver's own code when the driver is "
        "a test or a program written for this run (the whole file up to 250 lines, otherwise the "
        "function that makes the calls). Each function is shown with the comment block just above "
        "it; a function longer than 300 lines is shown as its first 16 and last 4 lines and the "
        "lines around every MPI call and every call of another function of the excerpt (20 on each "
        "side, or 10 or 5 when the excerpt would otherwise pass 1500 lines). Line numbers are the "
        "file's own; `...` marks omitted lines.")

def is_comment(line, fortran):
    s = line.strip()
    if not s:
        return False
    if fortran:
        return s.startswith('!') or (line[:1] in 'cC*' and not line[:1].isspace())
    return s.startswith('//') or s.startswith('/*') or s.startswith('*') or s.endswith('*/')

def c_end(lines, start):
    """index of the line closing the brace block opened at or after start (0-based)"""
    depth = 0; opened = False
    in_block = False
    for i in range(start, len(lines)):
        s = lines[i]
        j = 0; n = len(s)
        while j < n:
            if in_block:
                k = s.find('*/', j)
                if k < 0:
                    j = n; break
                in_block = False; j = k + 2; continue
            c = s[j]
            if s.startswith('//', j):
                break
            if s.startswith('/*', j):
                in_block = True; j += 2; continue
            if c == '"' or c == "'":
                q = c; j += 1
                while j < n and s[j] != q:
                    j += 2 if s[j] == '\\' else 1
                j += 1; continue
            if c == '{':
                depth += 1; opened = True
            elif c == '}':
                depth -= 1
                if opened and depth == 0:
                    return i
            elif c == ';' and not opened and i > start + 40:
                pass
            j += 1
    raise ValueError('no end of block from line %d' % (start + 1))

F_END = re.compile(r'^\s*end(\s+(subroutine|function|program)(\s+\w+)?)?\s*$', re.I)
F_START = re.compile(r'^\s*((recursive|pure|elemental)\s+)*((integer|real|logical|double\s+precision|complex)\b.*)?(subroutine|function)\s+\w+', re.I)

def f_end(lines, start):
    for i in range(start + 1, len(lines)):
        if F_END.match(lines[i].split('!')[0]):
            return i
    raise ValueError('no END from line %d' % (start + 1))

def doc_start(lines, start, fortran):
    i = start
    while i > 0 and is_comment(lines[i - 1], fortran):
        i -= 1
    return i

LONG = 300      # a function longer than this is shown in windows
WIN = 20        # lines kept on each side of a marked line
MARK = (r'\b(hypre_|sc_)?MPI_\w+\s*\(|\bMPIX?_\w+\s*\(|\bmpi_\w+\s*\(|\bMPIU_\w+\s*\(|'
        r'\b(IPROBE|IRECV2?|SEND|RECV|MPI_TEST2|TEST)\s*\(|PetscCallMPI')

def decl_start(lines, s):
    """back up over the lines of a C/C++ declaration above the signature line"""
    while s > 0:
        p = lines[s - 1].strip()
        if (not p or p.endswith((';', '{', '}')) or (p.endswith(':') and not p.endswith('::'))
                or p.startswith(('#', '//', '/*', '*')) or p.endswith('*/')):
            break
        s -= 1
    return s

def windows(lines, s, e, marks, fortran, win):
    """the parts of lines s..e kept for a long function: its head, windows around marked lines, its end"""
    flags = re.I if fortran else 0
    rx = re.compile(MARK + ('|' + '|'.join(marks) if marks else ''), flags)
    keep = [(s, min(e, s + 15)), (max(s, e - 3), e)]
    for i in range(s, e + 1):
        if rx.search(lines[i]):
            keep.append((max(s, i - win), min(e, i + win)))
    return merge(keep)

def select(lines, sel, fortran, marks=(), win=WIN):
    out = []
    if 'fn' in sel:
        rx = re.compile(sel['fn'])
        hits = [i for i, l in enumerate(lines) if rx.search(l)]
        if 'after' in sel:
            a = re.compile(sel['after'])
            first = next(i for i, l in enumerate(lines) if a.search(l))
            hits = [h for h in hits if h > first]
        if not hits:
            raise ValueError('no match for fn %r' % sel['fn'])
        k = sel.get('nth', 0)
        m = hits[k]
        if 'end' in sel:   # an explicit end line, where #if branches unbalance the braces
            erx = re.compile(sel['end'])
            e = next(i for i in range(m + 1, len(lines)) if erx.search(lines[i]))
        else:
            e = f_end(lines, m) if fortran else c_end(lines, m)
        s = m if fortran else decl_start(lines, m)
        if sel.get('doc', True):
            s = doc_start(lines, s, fortran)
        if e - m + 1 > LONG and not sel.get('whole'):
            out += windows(lines, s, e, marks, fortran, win)
        else:
            out.append((s, e))
    elif 'lines' in sel:
        rx = re.compile(sel['lines'])
        hits = [i for i, l in enumerate(lines) if rx.search(l)]
        if not hits:
            raise ValueError('no match for lines %r' % sel['lines'])
        out += [(i, i) for i in hits]
    elif 'block' in sel:
        ra, rb = (re.compile(x) for x in sel['block'])
        k = sel.get('nth', 0)
        starts = [i for i, l in enumerate(lines) if ra.search(l)]
        if not starts:
            raise ValueError('no match for block start %r' % sel['block'][0])
        s = starts[k]
        e = next(i for i in range(s, len(lines)) if rb.search(lines[i]))
        out.append((s, e))
    elif 'range' in sel:
        a, b = sel['range']
        out.append((a - 1, min(b, len(lines)) - 1))
    else:
        raise ValueError(sel)
    return out

def merge(rs):
    rs = sorted(rs)
    m = []
    for s, e in rs:
        if m and s <= m[-1][1] + 2:   # join across a gap of at most one line
            m[-1] = (m[-1][0], max(m[-1][1], e))
        else:
            m.append((s, e))
    return m

def excerpt(tree, part, lang, marks=(), win=WIN):
    path = os.path.join(tree, part['file'])
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.read().split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    fortran = lang == 'fortran'
    rs = []
    for sel in part['sel']:
        rs += select(lines, sel, fortran, marks, win)
    rs = merge(rs)
    width = len(str(rs[-1][1] + 1))
    body = []
    count = 0
    for k, (s, e) in enumerate(rs):
        if k == 0 and s > 0 or k > 0:
            body.append(' ' * width + '  ...')
        for i in range(s, e + 1):
            body.append('%*d  %s' % (width, i + 1, lines[i].replace('\t', '    ')))
            count += 1
    if rs[-1][1] < len(lines) - 1:
        body.append(' ' * width + '  ...')
    head = '### `%s` (lines %s)' % (part['file'], ', '.join('%d-%d' % (s + 1, e + 1) for s, e in rs))
    return head, body, count, [(s + 1, e + 1) for s, e in rs]

def packet(item, stage):
    tree = os.path.join(stage, item['id'])
    lines_total = 0
    secs = []
    meta = []
    marks = item.get('marks', [])
    win = item.get('win')
    for w in ([win] if win else [WIN, 10, 5]):
        total = 0
        for part in item['parts']:
            total += excerpt(tree, part, part.get('lang', item['lang']), marks, w)[2]
        if total <= 1500:
            break
    item['win_used'] = w
    for part in item['parts']:
        lang = part.get('lang', item['lang'])
        head, body, n, rs = excerpt(tree, part, lang, marks, w)
        lines_total += n
        fence = {'fortran': 'fortran', 'c': 'c', 'cpp': 'cpp', 'tcl': 'tcl'}.get(lang, '')
        secs.append(head + '\n\n```' + fence + '\n' + '\n'.join(body) + '\n```\n')
        meta.append({'file': part['file'], 'ranges': rs, 'lines': n})
    run = ['- Driver: ' + item['driver'], '- Command line: `' + item['options'] + '`',
           '- Processes: %s' % item['procs'], '- MPI: ' + item['mpi'] + ', default settings']
    if item.get('input'):
        run.append('- Input: ' + item['input'])
    if item.get('build'):
        run.append('- Build: ' + item['build'])
    md = ['# Packet %s' % item['id'], '', '## Program', '', item['program'], '', '## Run', '']
    md += run
    md += ['', '## Code', '', RULE, '']
    md += secs
    md += ['## Question', '', QUESTION, '']
    return '\n'.join(md), lines_total, meta

def main():
    spec, stage, outdir, metaout = sys.argv[1:5]
    with open(spec) as f:
        items = json.load(f)
    os.makedirs(outdir, exist_ok=True)
    allmeta = {}
    only = set(sys.argv[5:])
    for it in items:
        if only and it['id'] not in only:
            continue
        md, n, meta = packet(it, stage)
        with open(os.path.join(outdir, it['id'] + '.md'), 'w') as f:
            f.write(md)
        allmeta[it['id']] = {'excerpt_lines': n, 'window': it['win_used'], 'parts': meta}
        print('%s %5d lines win %s %s' % (it['id'], n, it['win_used'], it.get('note', '')))
    with open(metaout, 'w') as f:
        json.dump(allmeta, f, indent=1)

if __name__ == '__main__':
    main()
