"""Static first-read analysis: where a program may first read the result of a receive.

The horizon's rule H1 cuts the rank of a changed receive right after the receive completes,
since the program may branch on what it returned. A program reads that result later, if at
all: the buffer, the status, a test's flag, a Waitany index. This package reads the LLVM IR
that clang emits for the program's C sources and writes a map (JSON) that says, per call that
delivers a receive's result, which MPI call sites the program can only reach before reading
it. stretto.reads turns the map into read bounds on a trace; docs/firstread.md has the rules
and the argument that the bounds are sound.

    python3 -m stretto.firstread -o reads.json prog.c [more.c ...] [--cflags "-DX -Iinclude"]

The package needs clang (clang-18 by default) only to produce the IR; the analysis itself is
standard-library Python like the rest of stretto, and lives here so that it shares the
package's test suite."""
from .analyze import FORMAT, Program, analyse_ir  # noqa: F401
