#!/usr/bin/env python3
"""Marker calls for MPI-PRV's buffer properties, inserted into C sources by text.

MPI-PRV observes a buffer access only through a marker: a call to a function that the
property's MOP file turns into an event. Its shipped buffer properties use three:
usebuf2() ("Use", receive buffer used before the receive completes: Irecv Use Isend),
writebuf1() ("Write", send buffer modified before the send completes: Isend Write Recv) and
writebuf2() ("write", persistent receive buffer modified: Recvinit Start write Wait).

blind(src) puts markers where a script that does not read the labels would: it walks the
statements of each function in text order, keeps the nonblocking operations that are still
pending (from MPI_I* or MPI_Start of a persistent request to the MPI_Wait, MPI_Test or
MPI_Request_free of its request, or the end of the function), and before every statement that
names a pending operation's buffer it calls usebuf2() for a receive buffer (any access), and
writebuf1() for a send buffer or writebuf2() for a persistent receive buffer when the statement
writes it. Control flow is ignored, except that a statement in a sibling of the block that
started the operation (typically the other rank's `if`) does not count as an access.

oracle(src, labelled) puts the same markers only before the labelled statements (the lines a
benchmark marks as the error), and twin(src, labelled) moves each labelled access after the
call that completes the operation it conflicts with: the corrected program, with the markers
at the moved statements.

usage: markers.py blind|oracle|twin FILE.c   (the new source on stdout; a summary on stderr)"""
from __future__ import annotations
import re
import sys

MARKERS = {"use": "usebuf2", "write_send": "writebuf1", "write_precv": "writebuf2"}
DEFS = "void usebuf2(void) {}\nvoid writebuf1(void) {}\nvoid writebuf2(void) {}\n"

# nonblocking calls: name -> (send buffer argument, receive buffer argument); the request is
# the last argument. Ibcast's buffer is sent by the root and received by the others.
NONBLOCKING = {
    "MPI_Isend": (0, None), "MPI_Ibsend": (0, None), "MPI_Issend": (0, None), "MPI_Irsend": (0, None),
    "MPI_Irecv": (None, 0), "MPI_Imrecv": (None, 0),
    "MPI_Ibcast": (None, 0),
    "MPI_Iallreduce": (0, 1), "MPI_Ireduce": (0, 1), "MPI_Iscan": (0, 1), "MPI_Iexscan": (0, 1),
    "MPI_Ireduce_scatter": (0, 1), "MPI_Ireduce_scatter_block": (0, 1),
    "MPI_Igather": (0, 3), "MPI_Iscatter": (0, 3), "MPI_Iallgather": (0, 3), "MPI_Ialltoall": (0, 3),
    "MPI_Igatherv": (0, 3), "MPI_Iscatterv": (0, 4), "MPI_Iallgatherv": (0, 3), "MPI_Ialltoallv": (0, 4),
}
PERSISTENT = {"MPI_Send_init": "send", "MPI_Bsend_init": "send", "MPI_Ssend_init": "send", "MPI_Rsend_init": "send",
              "MPI_Psend_init": "send", "MPI_Recv_init": "precv", "MPI_Precv_init": "precv"}
COMPLETE = ("MPI_Wait", "MPI_Test", "MPI_Request_free")
COMPLETE_ALL = ("MPI_Waitall", "MPI_Testall", "MPI_Waitany", "MPI_Waitsome", "MPI_Testany", "MPI_Testsome")
KEYWORDS = {"void", "const", "int", "char", "double", "float", "long", "short", "unsigned", "signed", "struct", "sizeof"}
WRITERS = ("memset", "memcpy", "memmove", "strcpy", "strncpy", "sprintf", "snprintf", "fread", "free",
           "MPI_Recv", "MPI_Irecv", "MPI_Mrecv", "MPI_Imrecv", "MPI_Recv_init", "MPI_Sendrecv_replace")


def blank_comments(src: str) -> str:
    """The source with comments and string/char literals replaced by spaces (offsets kept)."""
    out, i, n = list(src), 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def statements(code: str):
    """(start, end, blocks) of each statement of a comment-free source: text ending at a ';'
    outside parentheses, or at a '{' or '}'. blocks is the tuple of the ids of the braces that
    enclose the statement's start (empty outside functions). Preprocessor lines are skipped."""
    res, stack, nblocks, paren, start, i, n = [], [], 0, 0, None, 0, len(code)
    while i < n:
        c = code[i]
        if start is None:
            if c.isspace():
                i += 1
                continue
            if c == "#":                       # preprocessor line
                j = code.find("\n", i)
                i = n if j < 0 else j + 1
                continue
            start, sblocks = i, tuple(stack)
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif paren == 0 and c in ";{}":
            if c == "}" and i == start:
                if stack:
                    stack.pop()
                start = None
                i += 1
                continue
            res.append((start, i + 1, sblocks))
            if c == "{":
                nblocks += 1
                stack.append(nblocks)
            elif c == "}" and stack:
                stack.pop()
            start = None
        i += 1
    return res


def other_branch(created, at) -> bool:
    """Whether a statement in blocks `at` lies in a block that neither encloses nor is enclosed by
    the block where an operation was created (`created`): a sibling branch, such as the other
    rank's `if`, which the same process does not run after the creation."""
    common = 0
    while common < min(len(created), len(at)) and created[common] == at[common]:
        common += 1
    return common < len(created) and common < len(at)


def split_args(s: str):
    """The top-level comma-separated arguments of the call whose '(' starts s."""
    args, depth, cur = [], 0, ""
    for c in s[1:]:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                args.append(cur.strip())
                return args
            depth -= 1
        if c == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += c
    return args


def base_name(expr: str):
    """The variable a buffer or request expression names: &buf[2] -> buf, (void*)(a0+n) -> a0."""
    if expr is None or "MPI_IN_PLACE" in expr or expr.strip() in ("NULL", "0"):
        return None
    for tok in re.findall(r"[A-Za-z_]\w*", expr):
        if tok not in KEYWORDS:
            return tok
    return None


def calls(stmt: str):
    for m in re.finditer(r"\b(MPI_\w+)\s*\(", stmt):
        yield m.group(1), split_args(stmt[m.end() - 1:])


def writes(stmt: str, name: str) -> bool:
    v = re.escape(name)
    pats = [rf"\b{v}\s*(\[[^;]*?\]\s*)*([-+*/%&|^]|<<|>>)?=(?!=)", rf"\b{v}\s*(\[[^;]*?\]\s*)*(\+\+|--)",
            rf"(\+\+|--)\s*\*?\s*{v}\b", rf"\*\s*\(?\s*{v}\b[^;=]*=(?!=)",
            rf"\b({'|'.join(WRITERS)})\s*\(\s*(\(\s*\w+\s*\*?\s*\)\s*)?&?\s*\(?\s*{v}\b"]
    return any(re.search(p, stmt) for p in pats)


def names(stmt: str):
    return set(re.findall(r"[A-Za-z_]\w*", stmt))


class Scan:
    """Walk the statements once; for each, the markers it needs and the requests it completes."""

    def __init__(self, src: str):
        self.src, self.code = src, blank_comments(src)
        self.stmts = statements(self.code)
        self.need = {}        # statement index -> set of marker kinds
        self.conflict = {}    # statement index -> statement index of the completion of the op it conflicts with
        pending, persistent, completes = {}, {}, {}   # req#kind -> (kind, buffer, blocks of the creation)
        for k, (a, b, blocks) in enumerate(self.stmts):
            s = self.code[a:b]
            if not blocks:          # outside any function: a new function starts after it
                pending.clear()
            new, done = [], []
            for fn, args in calls(s):
                if fn in NONBLOCKING and args:
                    req = base_name(args[-1]) if args else None
                    si, ri = NONBLOCKING[fn]
                    for idx, kind in ((si, "send"), (ri, "recv")):
                        if idx is not None and idx < len(args):
                            buf = base_name(args[idx])
                            if buf:
                                new.append((f"{req}#{kind}", kind, buf))
                elif fn in PERSISTENT and args:
                    persistent[base_name(args[-1])] = (PERSISTENT[fn], base_name(args[0]))
                elif fn == "MPI_Start" and args:
                    req = base_name(args[0])
                    if req in persistent:
                        kind, buf = persistent[req]
                        new.append((f"{req}#{kind}", kind, buf))
                elif fn == "MPI_Startall" and len(args) > 1:
                    req = base_name(args[1])
                    for r, (kind, buf) in persistent.items():
                        if r == req:
                            new.append((f"{r}#{kind}", kind, buf))
                elif fn in COMPLETE and args:
                    done.append(base_name(args[0]))
                elif fn in COMPLETE_ALL and len(args) > 1:
                    done.append(base_name(args[1]))
            # accesses of this statement to the buffers of operations pending before it
            ns = names(s)
            for key, (kind, buf, made) in pending.items():
                if buf not in ns or key.split("#")[0] in done or other_branch(made, blocks):
                    continue
                if kind == "recv":
                    self.need.setdefault(k, set()).add("use")
                elif kind == "precv":
                    self.need.setdefault(k, set()).add("use")
                    if writes(s, buf):
                        self.need[k].add("write_precv")
                elif kind == "send" and writes(s, buf):
                    self.need.setdefault(k, set()).add("write_send")
                if k in self.need:
                    self.conflict.setdefault(k, key)
            for key in [p for p in pending if p.split("#")[0] in done]:
                completes[key] = k
                del pending[key]
            for key, kind, buf in new:
                pending[key] = (kind, buf, blocks)
        # the statement that completes the operation each access conflicts with
        self.after = {k: completes.get(key) for k, key in self.conflict.items()}

    def line_of(self, k: int) -> int:
        return self.src.count("\n", 0, self.stmts[k][0]) + 1


def labelled(src: str):
    """Statement indices that carry a benchmark's error label: inside /*MBBERROR_BEGIN*/ ...
    /*MBBERROR_END*/ (MPI-BugBench), or on a line with an MBIERROR comment (MBI)."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"/\*\s*MBBERROR_BEGIN\s*\*/.*?/\*\s*MBBERROR_END\s*\*/", src, re.S)]
    lines = {src.count("\n", 0, m.start()) for m in re.finditer(r"MBIERROR", src)}
    sc = Scan(src)
    out = set()
    for k, (a, b, _) in enumerate(sc.stmts):
        if any(a < e and b > s for s, e in spans):
            out.add(k)
        if any(src.count("\n", 0, a) <= ln <= src.count("\n", 0, b - 1) for ln in lines):
            out.add(k)
    return sc, out


def insert(src: str, at: dict, defs=True) -> str:
    """Insert the marker calls of at (statement start offset -> kinds) and their definitions."""
    out, last = [], 0
    for off in sorted(at):
        out.append(src[last:off])
        out.append(" ".join(f"{MARKERS[k]}();" for k in sorted(at[off])) + " ")
        last = off
    out.append(src[last:])
    res = "".join(out)
    if defs:
        incs = list(re.finditer(r"^#include.*$", res, re.M))
        pos = incs[-1].end() + 1 if incs else 0
        res = res[:pos] + DEFS + res[pos:]
    return res


def _else_safe(sc: Scan, k: int) -> bool:
    return not re.match(r"\s*(else|case|default)\b", sc.code[sc.stmts[k][0]:sc.stmts[k][1]])


def blind(src: str):
    sc = Scan(src)
    at = {sc.stmts[k][0]: kinds for k, kinds in sc.need.items() if _else_safe(sc, k)}
    return insert(src, at), [(sc.line_of(k), sorted(v)) for k, v in sorted(sc.need.items())]


def oracle(src: str):
    sc, lab = labelled(src)
    at = {sc.stmts[k][0]: sc.need[k] for k in lab if k in sc.need and _else_safe(sc, k)}
    return insert(src, at), [(sc.line_of(k), sorted(sc.need[k])) for k in sorted(lab) if k in sc.need]


def twin(src: str):
    """The labelled accesses moved after the completion of the operation they conflict with,
    each with its markers; None if a labelled access has no completion to move after."""
    sc, lab = labelled(src)
    moves = [k for k in sorted(lab) if k in sc.need]
    if not moves or any(sc.after.get(k) is None for k in moves):
        return None, []
    cut, ins = [], {}
    for k in moves:
        a, b, _ = sc.stmts[k]
        text = src[a:b]
        dest = sc.stmts[sc.after[k]][1]
        ins.setdefault(dest, []).append(" ".join(f"{MARKERS[x]}();" for x in sorted(sc.need[k])) + " " + text)
        cut.append((a, b))
    out, last = [], 0
    events = sorted([(a, "cut", b) for a, b in cut] + [(d, "ins", None) for d in ins])
    for off, what, b in events:
        out.append(src[last:off])
        if what == "cut":
            last = b
        else:
            out.append(" " + " ".join(ins[off]))
            last = off
    out.append(src[last:])
    res = "".join(out)
    incs = list(re.finditer(r"^#include.*$", res, re.M))
    pos = incs[-1].end() + 1 if incs else 0
    return res[:pos] + DEFS + res[pos:], [(sc.line_of(k), sorted(sc.need[k])) for k in moves]


def main() -> int:
    mode, path = sys.argv[1], sys.argv[2]
    src = open(path).read()
    res, where = {"blind": blind, "oracle": oracle, "twin": twin}[mode](src)
    if res is None:
        print("no twin", file=sys.stderr)
        return 1
    sys.stdout.write(res)
    print(f"{mode}: markers at {where}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
