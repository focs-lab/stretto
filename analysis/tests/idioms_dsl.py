"""DSL twins of the ten idioms. Metadata branches encode the L2 skeleton; idioms 01, 02
and 06 are the ones whose communication pattern depends on metadata."""
from stretto.dsl import ANY, Coll, If, Irecv, Isend, Recv, Send, Wait, Waitany

TAG = 7


def idiom01():
    master = []
    for i in range(2):
        master += [Recv(ANY, TAG, into="st"), Send(lambda env: env["st"].src, TAG)]
    worker = [Send(0, TAG), Recv(0, TAG)]
    return {0: master, 1: worker, 2: worker}


def idiom02():
    master = [Irecv(1, TAG, req="a"), Irecv(2, TAG, req="b"),
              Waitany(("a", "b"), into="i"), Send(lambda env: env["i"] + 1, TAG),
              Waitany(("a", "b"), into="i"), Send(lambda env: env["i"] + 1, TAG)]
    worker = [Send(0, TAG), Recv(0, TAG)]
    return {0: master, 1: worker, 2: worker}


def idiom03(n=400):
    return {0: [Send(1, 3, n), Recv(1, 3, n)], 1: [Send(0, 3, n), Recv(0, 3, n)]}


def idiom04():
    return {0: [Recv(ANY, 4), Recv(2, 4)], 1: [Send(0, 4)], 2: [Send(0, 4)]}


def idiom05():
    return {0: [Recv(ANY, 5, cap=16), Recv(2, 5, cap=32)], 1: [Send(0, 5, 16)], 2: [Send(0, 5, 32)]}


def idiom06():
    master = [Recv(ANY, 6, into="st"),
              If(lambda env: env["st"].src == 1, (Recv(2, 6), Coll("bcast")), (Send("st", 6),))]
    worker = [Send(0, 6), Coll("bcast")]
    return {0: master, 1: worker, 2: worker}


def idiom07():
    return {0: [Isend(1, 8, 64, req="q"), Wait("q")], 1: [Recv(0, 8, 64)]}


def idiom08():
    return {0: [Recv(ANY, ANY), Recv(2, 22)], 1: [Send(0, 11)], 2: [Send(0, 22)]}


def idiom09():
    return {0: [Recv(ANY, 31), Recv(ANY, ANY), Recv(1, 31)],
            1: [Send(0, 31), Send(0, 31)], 2: [Send(0, 32)]}


def idiom10(np=4, iters=3):
    prog = {}
    for r in range(np):
        left, right = (r - 1) % np, (r + 1) % np
        ops = []
        for _ in range(iters):
            ops += [Irecv(left, 42, 8, req="a"), Irecv(right, 41, 8, req="b"),
                    Isend(left, 41, 8, req="c"), Isend(right, 42, 8, req="d"),
                    Wait("a"), Wait("b"), Wait("c"), Wait("d"), Coll("allreduce")]
        prog[r] = ops
    return prog


ALL = {1: idiom01, 2: idiom02, 3: idiom03, 4: idiom04, 5: idiom05, 6: idiom06,
       7: idiom07, 8: idiom08, 9: idiom09, 10: idiom10}
