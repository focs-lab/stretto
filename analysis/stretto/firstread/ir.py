"""A reader for the textual LLVM IR that clang writes with -O0 -g -S -emit-llvm.

It keeps what the first-read analysis needs: the functions with their basic blocks, each
instruction with its opcode, result, operands and debug location, the declared functions, the
identified types (so that `%struct.S` is not taken for a value) and the debug metadata that
names a call's source position. An instruction it does not recognise keeps its operands, and
the analysis treats such an instruction conservatively (docs/firstread.md)."""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

NAME = r'(?:[-a-zA-Z$._0-9]+|"[^"]*")'
TOKEN = re.compile(r'([%@]' + NAME + r')')
LABEL_LINE = re.compile(r'^(' + NAME + r'):(\s*;.*)?$')
RESULT = re.compile(r'^\s*(%' + NAME + r')\s*=\s*(.*)$')
DBG = re.compile(r'!dbg\s+!(\d+)')
ATTACHMENTS = re.compile(r'(,\s*![-a-zA-Z._0-9]+\s+!\d+)+\s*$')
LABEL_REF = re.compile(r'label\s+(%' + NAME + r')')
CALLEE = re.compile(r'([%@]' + NAME + r')\s*\(')
META_LINE = re.compile(r'^!(\d+)\s*=\s*(?:distinct\s+)?(.*)$')


def unquote(name: str) -> str:
    """'%"a b"' -> '%a b'; names keep their sigil."""
    if len(name) > 2 and name[1] == '"':
        return name[0] + name[2:-1]
    return name


def split_top(text: str, sep: str = ",") -> List[str]:
    """Split at sep outside brackets, braces, parentheses, angle brackets and quotes."""
    out, depth, cur, q = [], 0, [], False
    for ch in text:
        if q:
            cur.append(ch)
            if ch == '"':
                q = False
            continue
        if ch == '"':
            q = True
        elif ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def matching_paren(text: str, start: int) -> int:
    """Index of the parenthesis that closes the one at start, or -1."""
    depth, q = 0, False
    for i in range(start, len(text)):
        ch = text[i]
        if q:
            if ch == '"':
                q = False
            continue
        if ch == '"':
            q = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


@dataclass
class Inst:
    op: str                       # opcode: load, store, call, br, getelementptr, ...
    text: str                     # after the result and the opcode, attachments removed
    result: Optional[str] = None
    dbg: Optional[int] = None     # !dbg metadata id
    lineno: int = 0               # line in the .ll file
    callee: Optional[str] = None  # call/invoke: '@name', '%value', or None (inline asm)
    args: List[str] = field(default_factory=list)   # call/invoke: argument texts
    succs: List[str] = field(default_factory=list)  # terminators: successor block names


@dataclass
class Block:
    name: str
    insts: List[Inst] = field(default_factory=list)


@dataclass
class Function:
    name: str                     # '@name'
    unit: str                     # the module (translation unit) it comes from
    internal: bool                # internal or private linkage: a name local to its unit
    params: List[str] = field(default_factory=list)
    blocks: List[Block] = field(default_factory=list)
    dbg: Optional[int] = None


@dataclass
class Module:
    path: str
    functions: List[Function] = field(default_factory=list)
    declared: Set[str] = field(default_factory=set)
    types: Set[str] = field(default_factory=set)
    globals: Set[str] = field(default_factory=set)
    global_refs: Set[str] = field(default_factory=set)   # @names used in global initializers
    metadata: Dict[int, str] = field(default_factory=dict)

    def tokens(self, text: str) -> List[str]:
        """The value names (%local, @global) in text: types, labels and metadata left out."""
        text = LABEL_REF.sub("", text)
        return [t for t in (unquote(x) for x in TOKEN.findall(text)) if t not in self.types]

    # ------------------------------------------------------------ debug locations
    def _field(self, mid: Optional[int], key: str) -> Optional[str]:
        if mid is None:
            return None
        m = re.search(r'\b' + key + r':\s*("(?:[^"\\]|\\.)*"|![0-9]+|-?[0-9]+)', self.metadata.get(mid, ""))
        return m.group(1) if m else None

    def _ref(self, mid: Optional[int], key: str) -> Optional[int]:
        v = self._field(mid, key)
        return int(v[1:]) if v and v.startswith("!") else None

    def location(self, dbg: Optional[int]) -> Optional[Tuple[str, int, int, Optional[str]]]:
        """(file, line, column, subprogram name) of a DILocation, or None."""
        if dbg is None or "DILocation" not in self.metadata.get(dbg, ""):
            return None
        line, col = self._field(dbg, "line"), self._field(dbg, "column")
        scope = self._ref(dbg, "scope")
        fmeta, sub, seen = None, None, set()
        s = scope
        while s is not None and s not in seen:
            seen.add(s)
            text = self.metadata.get(s, "")
            if fmeta is None:
                fmeta = self._ref(s, "file")
            if "DISubprogram" in text:
                n = self._field(s, "name")
                sub = n.strip('"') if n else None
                break
            s = self._ref(s, "scope")
        path = None
        if fmeta is not None:
            fn, d = self._field(fmeta, "filename"), self._field(fmeta, "directory")
            fn = fn.strip('"') if fn else ""
            d = d.strip('"') if d else ""
            path = os.path.normpath(fn if fn.startswith("/") or not d else d.rstrip("/") + "/" + fn)
        if path is None or line is None:
            return None
        return path, int(line), int(col or 0), sub


def _parse_inst(m: Module, text: str, lineno: int) -> Inst:
    dbg = DBG.search(text)
    body = ATTACHMENTS.sub("", text).strip()
    res = RESULT.match(body)
    result = None
    if res:
        result, body = unquote(res.group(1)), res.group(2).strip()
    words = body.split(None, 1)
    op = words[0] if words else ""
    rest = words[1] if len(words) > 1 else ""
    while op in ("tail", "musttail", "notail"):
        words = rest.split(None, 1)
        op, rest = words[0], (words[1] if len(words) > 1 else "")
    inst = Inst(op, rest, result, int(dbg.group(1)) if dbg else None, lineno)
    if op in ("call", "invoke", "callbr"):
        cm = None
        for c in CALLEE.finditer(rest):
            if unquote(c.group(1)) not in m.types:
                cm = c
                break
        if cm is not None and " asm " not in " " + rest[:cm.start()] + " ":
            inst.callee = unquote(cm.group(1))
            close = matching_paren(rest, cm.end() - 1)
            inst.args = split_top(rest[cm.end():close]) if close > 0 else [rest]
            if close < 0:
                inst.callee = None
        else:
            # inline asm or an unparsed shape: no known callee, every operand an argument
            inst.args = [rest]
    if op in ("br", "switch", "indirectbr", "invoke", "callbr", "catchswitch", "cleanupret", "catchret"):
        inst.succs = [unquote(x)[1:] for x in LABEL_REF.findall(rest)]
    return inst


def parse(text: str, path: str = "<ir>") -> Module:
    m = Module(path)
    lines = text.split("\n")
    # first pass: types and metadata, which the instructions refer to
    for ln in lines:
        if ln.startswith("%"):
            tm = re.match(r'^(%' + NAME + r')\s*=\s*type\b', ln)
            if tm:
                m.types.add(unquote(tm.group(1)))
        elif ln.startswith("!"):
            mm = META_LINE.match(ln)
            if mm:
                m.metadata[int(mm.group(1))] = mm.group(2)
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if ln.startswith("@"):
            gm = re.match(r'^(@' + NAME + r')\s*=\s*(.*)$', ln)
            if gm:
                m.globals.add(unquote(gm.group(1)))
                m.global_refs.update(t for t in m.tokens(gm.group(2)) if t.startswith("@"))
            i += 1
            continue
        if ln.startswith("declare "):
            dm = re.search(r'(@' + NAME + r')\s*\(', ln)
            if dm:
                m.declared.add(unquote(dm.group(1)))
            i += 1
            continue
        if not ln.startswith("define "):
            i += 1
            continue
        hm = re.search(r'(@' + NAME + r')\s*\(', ln)
        name = unquote(hm.group(1))
        close = matching_paren(ln, hm.end() - 1)
        head = ln[:hm.start()]
        f = Function(name, path, " internal " in " " + head + " " or " private " in " " + head + " ")
        f.params = [t for t in m.tokens(ln[hm.end():close]) if t.startswith("%")]
        dm = DBG.search(ln[close:])
        f.dbg = int(dm.group(1)) if dm else None
        i += 1
        cur: Optional[Block] = None
        while i < n and lines[i].rstrip() != "}":
            raw = lines[i]
            s = raw.strip()
            if not s or s.startswith(";") or s.startswith("#dbg_"):
                i += 1
                continue
            lm = LABEL_LINE.match(raw)
            if lm and not raw[0].isspace():
                cur = Block(unquote("%" + lm.group(1))[1:])
                f.blocks.append(cur)
                i += 1
                continue
            if cur is None:
                cur = Block("<entry>")
                f.blocks.append(cur)
            start = i
            text = raw
            # an instruction continues while its brackets are open (switch tables, landingpad clauses)
            while text.count("[") > text.count("]") and i + 1 < n:
                i += 1
                text += " " + lines[i].strip()
            cur.insts.append(_parse_inst(m, text, start + 1))
            i += 1
        m.functions.append(f)
        i += 1
    return m
