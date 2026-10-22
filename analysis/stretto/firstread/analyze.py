"""The first-read analysis over the LLVM IR of a program (docs/firstread.md).

For every call that delivers the result of a receive (a blocking receive, a wait or test, a
probe) and the objects that result lands in (the receive's buffer, the status, the flag, the
index, the message handle), it computes the MPI call sites the program can reach from the
point where the call returns *only* along paths that do not read those objects. A record of
the trace whose call site is in that set was issued before the program read the result, so
the result cannot have decided it.

The objects must be local variables of the function that makes the call whose address never
leaves it (stored, passed to a function other than MPI, returned): then every access to them
is an instruction of that function on a pointer derived from them, or an MPI call they are
passed to, and the analysis sees each one. Anything else (a parameter, a global, heap memory, a
local whose address escapes, MPI_BOTTOM) gives the empty set, which is today's rule H1.

Reachability is interprocedural and context-insensitive: a call to a function of the program
goes to its entry and its returns go back after every call that may reach it; a call through
a pointer, and every call into code outside the program (MPI included), may call back any
function whose address the program takes. Every real path is a path of this graph, which is
what soundness needs; spurious paths only make the sets smaller."""
from __future__ import annotations
import os
import re
from collections import deque
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from . import ir
from .mpi import (BLOCKING_RECV, COMPLETION, NONBLOCKING_RECV, OUTPUT_ONLY, PROBES, RECV_BUFFER, RESULTS, captures,
                  mpi_name)

FORMAT = "stretto-firstread 1"
UNKNOWN = "?"                       # a root the analysis cannot name: a loaded pointer, a parameter, a call's result
DERIVE = ("getelementptr", "bitcast", "addrspacecast", "freeze")
TERMINATORS = ("br", "switch", "indirectbr", "ret", "unreachable", "resume", "invoke", "callbr",
               "catchswitch", "catchret", "cleanupret")
NONLOCAL_JUMPS = {"@setjmp", "@_setjmp", "@__setjmp", "@sigsetjmp", "@__sigsetjmp", "@longjmp", "@_longjmp",
                  "@siglongjmp", "@__longjmp_chk", "@savectx", "@vfork", "@getcontext", "@setcontext", "@swapcontext"}
BENIGN_INTRINSICS = ("@llvm.dbg.", "@llvm.lifetime.", "@llvm.invariant.", "@llvm.assume", "@llvm.experimental.noalias")
MEMCPY = ("@llvm.memcpy", "@llvm.memmove", "@llvm.memcpy.inline")
MEMSET = ("@llvm.memset",)

Root = Tuple  # ("a", function index, name) for an alloca, ("g", name) for a global, (UNKNOWN,)


class Program:
    """The functions of every unit, the interprocedural graph over their instructions, the
    MPI call sites and, per function, the roots of every pointer value."""

    def __init__(self, modules: List[ir.Module]):
        self.modules = modules
        self.funcs: List[ir.Function] = []
        self.fmod: List[ir.Module] = []
        for m in modules:
            for f in m.functions:
                self.funcs.append(f)
                self.fmod.append(m)
        self.external: Dict[str, int] = {}
        self.local: Dict[Tuple[str, str], int] = {}
        for i, f in enumerate(self.funcs):
            if f.internal:
                self.local[(f.unit, f.name)] = i
            else:
                self.external.setdefault(f.name, i)
        self.notes: List[str] = []
        self._number()
        self._address_taken()
        self._edges()
        self._roots = [None] * len(self.funcs)
        self._uses: Dict[int, list] = {}
        self.sites: List[dict] = []
        self.site_node: List[int] = []
        self._sites()

    # ------------------------------------------------------------------ structure
    def resolve(self, fid: int, callee: Optional[str]) -> Optional[int]:
        """The function a direct call names, if the program defines it."""
        if callee is None or not callee.startswith("@"):
            return None
        i = self.local.get((self.funcs[fid].unit, callee))
        return i if i is not None else self.external.get(callee)

    def _number(self) -> None:
        self.node_of: List[Tuple[int, int, int]] = []          # node -> (function, block, instruction)
        self.first: Dict[Tuple[int, str], int] = {}             # (function, block name) -> node
        self.entry: List[Optional[int]] = []
        for fi, f in enumerate(self.funcs):
            self.entry.append(len(self.node_of) if f.blocks and f.blocks[0].insts else None)
            for bi, b in enumerate(f.blocks):
                self.first[(fi, b.name)] = len(self.node_of)
                for ii in range(len(b.insts)):
                    self.node_of.append((fi, bi, ii))

    def inst(self, n: int) -> ir.Inst:
        fi, bi, ii = self.node_of[n]
        return self.funcs[fi].blocks[bi].insts[ii]

    def _address_taken(self) -> None:
        """Functions whose address the program takes: named anywhere but as a direct callee."""
        taken: Set[int] = set()
        for m in self.modules:
            for g in m.global_refs:
                i = self.local.get((m.path, g), self.external.get(g))
                if i is not None:
                    taken.add(i)
        for n in range(len(self.node_of)):
            fi = self.node_of[n][0]
            x = self.inst(n)
            toks = self.fmod[fi].tokens(x.text)
            if x.callee is not None and x.callee in toks:
                toks.remove(x.callee)
            for tok in toks:
                if tok.startswith("@"):
                    i = self.resolve(fi, tok)
                    if i is not None:
                        taken.add(i)
            if x.callee in NONLOCAL_JUMPS:
                self.notes.append(f"{x.callee[1:]} in {self.funcs[fi].name[1:]}: control flow that the graph does not show")
        self.taken = sorted(taken)

    def _edges(self) -> None:
        N = len(self.node_of)
        self.succ: List[List[int]] = [[] for _ in range(N)]
        rets: Dict[int, List[int]] = {}
        conts: Dict[int, List[int]] = {}                 # function -> nodes its returns go back to
        taken_entries = [(g, self.entry[g]) for g in self.taken if self.entry[g] is not None]
        for n in range(N):
            fi, bi, ii = self.node_of[n]
            x = self.inst(n)
            block = self.funcs[fi].blocks[bi]
            targets = [self.first[(fi, s)] for s in x.succs if (fi, s) in self.first]
            if x.op == "ret":
                rets.setdefault(fi, []).append(n)
                continue
            if x.op in ("call", "invoke", "callbr"):
                after = targets if x.op != "call" else [n + 1]
                g = self.resolve(fi, x.callee)
                if g is not None and self.entry[g] is not None:
                    self.succ[n].append(self.entry[g])
                    conts.setdefault(g, []).extend(after)
                    continue
                self.succ[n].extend(after)
                if x.callee is None or not x.callee.startswith("@llvm."):
                    # a call through a pointer, or into code outside the program, may call back
                    # any function whose address the program takes
                    for g, e in taken_entries:
                        self.succ[n].append(e)
                        conts.setdefault(g, []).extend(after)
                continue
            if x.op in TERMINATORS:
                self.succ[n].extend(targets)
                continue
            if ii + 1 < len(block.insts):
                self.succ[n].append(n + 1)
        for g, rs in rets.items():
            back = sorted(set(conts.get(g, ())))
            for r in rs:
                self.succ[r] = back

    def _sites(self) -> None:
        for n in range(len(self.node_of)):
            x = self.inst(n)
            name = mpi_name(x.callee)
            if name is None:
                continue
            fi = self.node_of[n][0]
            loc = self.fmod[fi].location(x.dbg)
            if loc is None:
                continue
            path, line, col, sub = loc
            self.sites.append({"id": len(self.sites), "file": path, "line": line, "col": col,
                               "function": sub or self.funcs[fi].name[1:], "call": name})
            self.site_node.append(n)

    # ---------------------------------------------------------------------- roots
    def roots(self, fi: int) -> Tuple[Dict[str, FrozenSet[Root]], Set[str], Dict[str, str]]:
        """Per value of function fi the objects it may point into, the allocas whose address
        escapes, and the source names of the allocas (from llvm.dbg.declare)."""
        if self._roots[fi] is not None:
            return self._roots[fi]
        f, m = self.funcs[fi], self.fmod[fi]
        insts = [x for b in f.blocks for x in b.insts]
        roots: Dict[str, FrozenSet[Root]] = {p: frozenset({(UNKNOWN,)}) for p in f.params}
        names: Dict[str, str] = {}

        def of(tok: str) -> FrozenSet[Root]:
            if tok.startswith("@"):
                return frozenset({("g", tok)})
            return roots.get(tok, frozenset())

        def union(toks: Iterable[str]) -> FrozenSet[Root]:
            out: Set[Root] = set()
            for t in toks:
                out |= of(t)
            return frozenset(out)

        changed = True
        while changed:
            changed = False
            for x in insts:
                if x.result is None:
                    continue
                if x.op == "alloca":
                    new = frozenset({("a", fi, x.result)})
                elif x.op in DERIVE:
                    parts = ir.split_top(x.text)
                    base = parts[1] if x.op == "getelementptr" and len(parts) > 1 else (parts[0] if parts else "")
                    new = union(m.tokens(base))
                elif x.op == "select":
                    new = union(t for p in ir.split_top(x.text)[1:] for t in m.tokens(p))
                elif x.op == "phi":
                    # the incoming values and their blocks; a block name points nowhere
                    new = union(m.tokens(x.text))
                else:
                    new = frozenset({(UNKNOWN,)})
                if roots.get(x.result) != new:
                    roots[x.result] = new
                    changed = True
        escaped: Set[str] = set()
        for x in insts:
            for kind, rs in self.uses(fi, x, roots):
                if kind == "escape":
                    escaped |= {r[2] for r in rs if r[0] == "a"}
            if x.callee is not None and x.callee.startswith("@llvm.dbg.declare"):
                toks = [t for t in m.tokens(x.args[0]) if t.startswith("%")] if x.args else []
                mid = re.search(r'metadata\s+!(\d+)', x.args[1]) if len(x.args) > 1 else None
                if toks and mid:
                    nm = re.search(r'name:\s*"([^"]*)"', m.metadata.get(int(mid.group(1)), ""))
                    if nm:
                        names[toks[0]] = nm.group(1)
        self._roots[fi] = (roots, escaped, names)
        return self._roots[fi]

    def uses(self, fi: int, x: ir.Inst, roots=None) -> List[Tuple[str, FrozenSet[Root]]]:
        """What instruction x does with the objects its operands point into: (kind, roots) with
        kind read, write, escape or derive. A use the analysis does not recognise is an escape."""
        m = self.fmod[fi]
        if roots is None:
            roots = self.roots(fi)[0]

        def rt(text: str) -> FrozenSet[Root]:
            out: Set[Root] = set()
            for t in m.tokens(text):
                out |= frozenset({("g", t)}) if t.startswith("@") else roots.get(t, frozenset())
            return frozenset(out)

        op = x.op
        parts = ir.split_top(x.text)
        if op == "load":
            return [("read", rt(parts[1] if len(parts) > 1 else x.text))]
        if op == "store":
            return [("escape", rt(parts[0])), ("write", rt(parts[1] if len(parts) > 1 else ""))]
        if op in DERIVE or op in ("select", "phi"):
            return [("derive", rt(x.text))]
        if op in ("icmp", "fcmp", "alloca", "br", "switch", "unreachable"):
            return []
        if op == "atomicrmw":
            return [("read", rt(parts[0])), ("escape", rt(" ".join(parts[1:])))]
        if op == "cmpxchg":
            return [("read", rt(parts[0])), ("escape", rt(" ".join(parts[1:])))]
        if op in ("call", "invoke", "callbr"):
            callee = x.callee
            out: List[Tuple[str, FrozenSet[Root]]] = []
            if callee is not None and callee.startswith("%"):
                out.append(("escape", rt(callee)))
            name = mpi_name(callee)
            if name is not None:
                for i, a in enumerate(x.args):
                    r = rt(a)
                    if captures(name):
                        out.append(("escape", r))
                    elif i in OUTPUT_ONLY.get(name, ()):
                        out.append(("write", r))
                    else:
                        out.append(("read", r))
                return out
            if callee is not None and callee.startswith(BENIGN_INTRINSICS):
                return out
            if callee is not None and callee.startswith(MEMCPY):
                return out + [("write", rt(x.args[0]) if x.args else frozenset()),
                              ("read", rt(x.args[1]) if len(x.args) > 1 else frozenset())] + \
                    [("escape", rt(a)) for a in x.args[2:]]
            if callee is not None and callee.startswith(MEMSET):
                return out + [("write", rt(x.args[0]) if x.args else frozenset())] + [("escape", rt(a)) for a in x.args[1:]]
            return out + [("escape", rt(a)) for a in x.args]
        return [("escape", rt(x.text))]

    # ------------------------------------------------------------------- analysis
    def reachable(self, starts: Iterable[int]) -> Set[int]:
        seen: Set[int] = set()
        todo = deque(s for s in starts)
        while todo:
            n = todo.popleft()
            if n in seen:
                continue
            seen.add(n)
            todo.extend(self.succ[n])
        return seen

    def tracked(self, fi: int, texts: Iterable[str], buffer: Optional[str]) -> Tuple[Optional[FrozenSet[Root]], str, List[str]]:
        """The objects behind the result arguments texts (and the receive buffer, if given), or
        None with the reason they cannot be followed."""
        m = self.fmod[fi]
        roots, escaped, names = self.roots(fi)
        T: Set[Root] = set()
        if buffer is not None:
            rb: Set[Root] = set()
            for t in m.tokens(buffer):
                rb |= frozenset({("g", t)}) if t.startswith("@") else roots.get(t, frozenset())
            if not rb:
                return None, "the receive buffer is a constant address (MPI_BOTTOM or null)", []
            T |= rb
        for text in texts:
            for t in m.tokens(text):
                T |= frozenset({("g", t)}) if t.startswith("@") else roots.get(t, frozenset())
        for r in T:
            if r[0] == UNKNOWN:
                return None, "a result lands in memory reached through a pointer (parameter, heap or loaded address)", []
            if r[0] == "g":
                return None, f"a result lands in global {r[1][1:]}", []
            if r[1] != fi:
                return None, "a result lands in another function's local", []
            if r[2] in escaped:
                return None, f"the address of {names.get(r[2], r[2])} escapes its function", []
        return frozenset(T), "", sorted(names.get(r[2], r[2]) for r in T)

    def free_sites(self, start: int, T: FrozenSet[Root], R: Optional[Set[int]] = None) -> List[int]:
        """MPI call sites reachable from the node after start along paths without a read of T,
        and along no path with one."""
        if R is None:
            R = self.reachable(self.succ[start])
        X = [n for n in R if self._reads(n, T)]
        tainted = self.reachable(X)
        return [s["id"] for s, n in zip(self.sites, self.site_node) if n in R and n not in tainted]

    def _reads(self, n: int, T: FrozenSet[Root]) -> bool:
        fi = self.node_of[n][0]
        if not any(r[1] == fi for r in T):
            return False
        u = self._uses.get(n)
        if u is None:
            u = self._uses[n] = [rs for kind, rs in self.uses(fi, self.inst(n)) if kind == "read"]
        return any(rs & T for rs in u)

    def analyse(self) -> dict:
        """The map: every MPI call site, and for each (posting site, completing site) pair and
        each completing site alone, the sites that are free of reads of its results."""
        results = []
        by_func: Dict[int, List[Tuple[int, dict]]] = {}
        for s, n in zip(self.sites, self.site_node):
            by_func.setdefault(self.node_of[n][0], []).append((n, s))
        for fi, lst in sorted(by_func.items()):
            posts = [(n, s) for n, s in lst if s["call"] in NONBLOCKING_RECV]
            for n, s in lst:
                call = s["call"]
                x = self.inst(n)
                res_args = [x.args[i] for i in RESULTS.get(call, ()) if i < len(x.args)]
                R = None
                items = []
                if call in BLOCKING_RECV:
                    items.append((s["id"], res_args, x.args[RECV_BUFFER[call]] if RECV_BUFFER[call] < len(x.args) else ""))
                if call in COMPLETION or call in PROBES:
                    items.append((None, res_args, None))
                if call in COMPLETION:
                    for pn, ps in posts:
                        px = self.inst(pn)
                        b = RECV_BUFFER[ps["call"]]
                        items.append((ps["id"], res_args, px.args[b] if b < len(px.args) else ""))
                for post, texts, buf in items:
                    entry = {"post": post, "complete": s["id"]}
                    if self.notes:
                        entry.update(free=[], why="the program jumps non-locally (" + "; ".join(sorted(set(self.notes))) + ")")
                    else:
                        T, why, objs = self.tracked(fi, texts, buf)
                        if T is None:
                            entry.update(free=[], why=why)
                        else:
                            if R is None:
                                R = self.reachable(self.succ[n])
                            entry.update(free=self.free_sites(n, T, R), objects=objs)
                    results.append(entry)
        return {"format": FORMAT, "units": [m.path for m in self.modules], "sites": self.sites,
                "results": results, "notes": sorted(set(self.notes))}


def analyse_ir(texts: List[Tuple[str, str]]) -> dict:
    """texts: (unit name, IR text) per translation unit."""
    return Program([ir.parse(t, p) for p, t in texts]).analyse()
