/*---------------------------------------------------------------------------*\
Application
    nbxExchange

Description
    Successive calls of Pstream::exchange on Map containers (the sparse
    exchange, whose sizes go through UPstream::allToAllConsensus, the NBX
    algorithm, by default), each one checked.

    Every call uses the defaults: tag UPstream::msgType(), communicator
    UPstream::worldComm, wait. In call c rank r sends to the ranks of the
    pattern a labelList of 1 + (c % 3) entries: c, r, the destination, and
    1000*c + r for the rest. A rank checks what it received in every call:
    exactly one entry from every rank that sends to it in that call, each of
    the expected length and contents. Each wrong entry is printed by the rank
    that received it; at the end the master prints how many there were in
    all, and "ok" when there were none.

    Patterns:
      ring    rank r sends to r+1 (mod n)
      all     rank r sends to every other rank
      scatter rank 0 sends to every other rank

Usage
    nbxExchange -parallel [-calls N (default 3)] [-pattern ring|all|scatter]

\*---------------------------------------------------------------------------*/

#include "argList.H"
#include "Pstream.H"
#include "Map.H"
#include "labelList.H"
#include "IOstreams.H"

using namespace Foam;

static labelList targets(const word& pattern, label r, label n)
{
    labelList t;
    if (pattern == "ring")
    {
        t.append((r + 1) % n);
    }
    else if (pattern == "all")
    {
        for (label d = 0; d < n; ++d)
        {
            if (d != r) t.append(d);
        }
    }
    else if (pattern == "scatter")
    {
        if (r == 0)
        {
            for (label d = 1; d < n; ++d) t.append(d);
        }
    }
    else
    {
        FatalErrorInFunction << "unknown pattern " << pattern << exit(FatalError);
    }
    return t;
}

static labelList payload(label c, label src, label dst)
{
    labelList v(1 + (c % 3), 1000*c + src);
    v[0] = c;
    if (v.size() > 1) v[1] = src;
    if (v.size() > 2) v[2] = dst;
    return v;
}

int main(int argc, char *argv[])
{
    argList::noCheckProcessorDirectories();
    argList::addOption("calls", "int", "number of calls (default 3)");
    argList::addOption("pattern", "word", "ring | all | scatter (default ring)");

    #include "setRootCase.H"

    const label calls = args.getOrDefault<label>("calls", 3);
    const word pattern = args.getOrDefault<word>("pattern", "ring");
    const label n = UPstream::nProcs();
    const label me = UPstream::myProcNo();

    label wrong = 0;

    for (label c = 0; c < calls; ++c)
    {
        Map<labelList> sendBufs;
        Map<labelList> recvBufs;

        for (const label d : targets(pattern, me, n))
        {
            sendBufs.insert(d, payload(c, me, d));
        }

        Pstream::exchange<labelList, label>(sendBufs, recvBufs);

        // what this rank must have received in call c
        Map<labelList> expected;
        for (label s = 0; s < n; ++s)
        {
            if (s != me && targets(pattern, s, n).contains(me))
            {
                expected.insert(s, payload(c, s, me));
            }
        }

        forAllConstIters(recvBufs, iter)
        {
            const label s = iter.key();
            if (s == me && iter.val().empty()) continue;
            const auto e = expected.cfind(s);
            if (!e.good() || e.val() != iter.val())
            {
                ++wrong;
                Pout<< "call " << c << " rank " << me << ": from rank " << s
                    << " received " << flatOutput(iter.val())
                    << " (expected "
                    << (e.good() ? flatOutput(e.val()) : flatOutput(labelList()))
                    << ")" << endl;
            }
        }
        forAllConstIters(expected, iter)
        {
            if (!recvBufs.contains(iter.key()))
            {
                ++wrong;
                Pout<< "call " << c << " rank " << me << ": nothing from rank "
                    << iter.key() << " (expected " << flatOutput(iter.val())
                    << ")" << endl;
            }
        }
    }

    const label total = returnReduce(wrong, sumOp<label>());

    Info<< "nbxExchange: " << calls << " calls on " << n << " ranks, pattern "
        << pattern << ", " << total << " wrong answers"
        << (total == 0 ? ": ok" : "") << endl;

    return 0;
}
