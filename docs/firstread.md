# First reads: where a program may first read the result of a receive

The horizon's rule 1 (H1, `docs/model.md` §3) cuts the rank of a changed receive right after
the receive completes: the trace does not say what the program does with a result it did not
see. A program reads that result later, if at all: the buffer, the status (`MPI_SOURCE`,
`MPI_TAG`, the count through `MPI_Get_count`), a test's flag, a Waitany index, a probe's
status or message handle. Until it reads one of them, it issues the calls of the trace
whatever the result was. H1' keeps the records of the rank between the completion of a changed
receive r and read(r) in the horizon, where read(r) is any *sound lower bound* of the first
point at which the program may read a result of r; H1 is the case read(r) = completion.

`stretto.firstread` computes such a bound statically from the program's C sources.

## The map

```
python3 -m stretto.firstread -o reads.json prog.c [more.c ...] [--mpi openmpi|mpich|none] [--cflags "..."]
```

compiles every unit with `clang-18 -O0 -g -S -emit-llvm` and the MPI implementation's include
flags, reads the textual IR (`firstread/ir.py`; standard-library Python, like the rest of the
package, so the tool shares its tests) and writes JSON:

- `sites`: every call to an MPI function that has a debug location, with `file`, `line`,
  `col`, `function` and `call` (the MPI name);
- `results`: one entry per call that delivers a receive's result, `complete` (a site id) with
  `post` = the same id for a blocking receive (`MPI_Recv`, `MPI_Mrecv`, `MPI_Sendrecv`,
  `MPI_Sendrecv_replace`), the id of an `MPI_Irecv`/`MPI_Imrecv` of the same function for a
  wait or test, and `null` for the call's own results alone (a wait's or test's status, flag and
  index; a probe's status, flag and message handle). `free` lists the sites the program can
  reach after the call returns *only* along paths that read none of the result's objects,
  `objects` names them, and `why` says why the list is empty when the analysis cannot follow
  them.

The image `experiments/container/Dockerfile.firstread` is the experiment image plus clang 18
from the same archive snapshot (`versions-firstread.txt`); the experiment image itself is
unchanged.

## Rules

The objects of a result are what the call writes it to: the receive's buffer (an argument of
the posting call), and the status, flag, index, outcount and indices, or message handle
arguments of the completing call (`firstread/mpi.py`). `MPI_STATUS_IGNORE` and the like name no
object.

1. **Followed objects.** Every object must be a local variable (`alloca`) of the function
   that makes the completing call, whose address never escapes that function. An address
   escapes when a pointer derived from it (by `getelementptr`, casts, `phi`, `select`) is
   stored to memory, passed to a function other than an MPI function or a memory intrinsic,
   passed to an MPI call that keeps it (persistent initialisation, `MPI_Buffer_attach`),
   returned, converted to an integer, or used by any instruction the reader does not
   recognise. Otherwise (a parameter, a global, heap memory, a constant address such as
   `MPI_BOTTOM`, an escaping local) the entry has no free site: rule H1.
2. **Reads.** With the objects followed, a read is: a load or an atomic operation through a
   pointer derived from one of them; `memcpy`/`memmove` with one as the source; a call to an
   MPI function that passes one in any argument the table does not list as written only. The
   written-only arguments are the receive buffers, statuses, flags, indices and requests of
   receives, waits, tests and probes, and the outputs of `MPI_Comm_rank`, `MPI_Comm_size`,
   `MPI_Get_count` and `MPI_Get_elements`. So receiving into the same buffer again is a write,
   while sending it (forwarding), `MPI_Get_count` on the status, a collective that takes it as
   input, and every MPI function the table does not know are reads. A send whose payload was
   computed from a result needs a load first, which is a read.
3. **Paths.** The graph has a node per instruction. A call to a function defined in the
   analysed units goes to its entry, and its returns go back after every call that may reach
   it (context-insensitive). A call through a pointer, inline assembly, and a call into code
   outside the units (MPI, the C library) fall through and may also call back every function
   whose address the units take (named anywhere but as a direct callee, global initialisers
   included). `main` returns to nowhere. A program that calls `setjmp`, `longjmp` or another
   function that transfers control the graph cannot show gets no free site anywhere.
4. **Free sites.** From the instruction after the completing call: R, the nodes reachable;
   X, the nodes of R that read an object; tainted, the nodes reachable from X, X included. A
   site is free if its call is in R and not tainted.

## Soundness

Claim: if the program reaches the call at a free site s after the completing call c returned
the result of a receive r, it has read no result of r in between.

Let π be the path of the execution from the return of c to the call at s. Every step of π is an
edge of the graph: control within a function follows the IR's own edges; a call to a defined
function enters it and returns to the instruction after that call, which is one of the return
edges; a call to outside code either returns to the instruction after it or runs a callback,
which can only be a function whose address the program has taken, and the graph has those
edges from every such call. A read of a result object on π is an instruction of the completing
call's function, or an MPI call to which a pointer to the object is passed: the object is a
local whose address never leaves its function (rule 1), so no other code holds a pointer to
it, and every pointer to it in its function is derived from the `alloca` by the operations the
analysis follows. Recursive calls create new objects; their accesses are flagged too, which
only adds reads. So if π contained a read, s would be tainted, and it is free.

Premises: the map comes from the sources the traced binary was built from; the program does not
access an object through a pointer to another one (undefined behaviour in C), including
receiving more data into a local than it holds; signal handlers and other threads do not read
the result objects (they cannot reach them by rule 1).

## Known imprecision

- Objects that are globals, heap memory or parameters, and locals whose address escapes, are
  not followed (H1). A helper that receives into its caller's buffer, or a wrapper around
  `MPI_Recv` (HYPRE's `hypre_MPI_Recv`), therefore keeps H1.
- Reads are field-insensitive: reading one field of a status counts for all of it, and any
  element of an array buffer for the whole array.
- An object received into again still counts as read when the program reads the new data.
- Returns are context-insensitive and every outside call may call back every address-taken
  function, so a function called both before and after a read, and every callback, has its
  calls tainted.
- A wait that completes a receive posted in another function (a helper that waits for requests
  it is given) has no pair in the map: H1.
