# First reads: where a program may first read the result of a receive

The horizon's rule 1 (H1, `docs/model.md` §3) cuts the rank of a changed receive right after
the receive completes: the trace does not say what the program does with a result it did not
see. A program reads that result later, if at all: the buffer, the status (`MPI_SOURCE`,
`MPI_TAG`, the count through `MPI_Get_count`), a test's flag, a Waitany index, a probe's
status or message handle. Until it reads one of them, it issues the calls of the trace
whatever the result was. H1' keeps the records of the rank between the completion of a changed
receive r and read(r) in the horizon, where read(r) is any *sound lower bound* of the first
point at which the program may read a result of r; H1 is the case read(r) = completion.

`stretto.firstread` computes such a bound statically from the program's C sources;
`stretto.reads` turns it into a bound per record of a trace, which `stretto analyze --reads
FILE` uses.

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
  them;
- `sources`: the SHA-256 of every source the sites come from. Sites are matched to a trace's
  call sites by function, source and line, so a map of edited sources, or of another tree,
  would put bounds on other calls and widen the horizon unsoundly: `stretto analyze --reads`
  refuses a map whose sources differ from those the traced program names (addr2line on the
  traced objects), or that no call site of the trace lies in, unless `--reads-unchecked` is
  given (maps made before the hashes need it), and certifies records only in the sources it
  checked.

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
   passed to an MPI call that keeps it (`firstread/mpi.py`, `captures`), returned, converted
   to an integer, or used by any instruction the reader does not recognise. The MPI calls that
   keep a pointer are those whose memory a later call reads without naming it: the
   initialisation of every persistent request (`MPI_Send_init`, `MPI_Recv_init`, the
   partitioned and the collective ones; `MPI_Start` reads the buffer), `MPI_Buffer_attach`,
   `MPI_Win_create` and `MPI_Win_attach` (other processes read the window),
   `MPI_Get_address` and `MPI_Address` (a datatype with the absolute address as displacement
   lets a send of `MPI_BOTTOM` read the object), attribute values, the `extra_state` of
   keyvals, generalised requests and data representations. Otherwise (a parameter, a global,
   heap memory, a constant address such as `MPI_BOTTOM`, an escaping local) the entry has no
   free site: rule H1.
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
   outside the units (MPI, the C library, a unit of the program that was not analysed) fall
   through and may also call back every function whose address the units take (named
   anywhere but as a direct callee, global initialisers included) and every function with
   external linkage but `main`: another unit, or a library linked against the program, can
   call such a function by name, so the map stays sound when some units of the program are
   not given. Only a `static` function (internal linkage) that the units never take the
   address of is called from its own unit alone. `main` returns to nowhere. A program that
   calls `setjmp`, `longjmp` or another function that transfers control the graph cannot show
   gets no free site anywhere.
4. **Free sites.** From the instruction after the completing call: R, the nodes reachable;
   X, the nodes of R that read an object; tainted, the nodes reachable from X, X included. A
   site is free if its call is in R and not tainted.

## Soundness

Claim: if the program reaches the call at a free site s after the completing call c returned
the result of a receive r, it has read no result of r in between.

Let π be the path of the execution from the return of c to the call at s. Every step of π is an
edge of the graph: control within a function follows the IR's own edges; a call to a defined
function enters it and returns to the instruction after that call, which is one of the return
edges; a call to outside code either returns to the instruction after it or runs code of the
units, which it can enter only at a function whose address the units take (through a pointer)
or at a function with external linkage (by name), and the graph has those edges from every
such call. A read of a result object on π is an instruction of the completing
call's function, or an MPI call to which a pointer to the object is passed: the object is a
local whose address never leaves its function (rule 1), so no other code holds a pointer to
it, and every pointer to it in its function is derived from the `alloca` by the operations the
analysis follows. Recursive calls create new objects; their accesses are flagged too, which
only adds reads. So if π contained a read, s would be tainted, and it is free. The argument
rests on the premises listed below.

## Bounds on a trace

`stretto/reads.py` names each record's call site (the return address the tracer recorded) with
`addr2line`, and matches it to the static sites with the same function, source and line
whose MPI call can produce a record of that kind (`reads.FAMILIES`, from what
`tracer/mpitrace.c` writes for each call). The source is the file of the map that the call
site's path names (`reads.select_source`): the map's only file of that name; else the one
whose path shares the most trailing directories with it (the map and the binary may come
from copies of the tree under different roots, which keep the layout below them); else the
one whose recorded SHA-256 the file at that path has. Two files of one name that none of these
tell apart (`util.c` in two directories, common in large codes) give the record no
candidate. Matching by file name alone, as before 22 Oct 2026, could give a record of one
`util.c` the free sites of another. For a record k that delivers the result of a
receive r (r's own record if r blocks, the wait or test that completed it otherwise) with
posting record p, the *bound* is the index of the first later record of the rank that is not
certified: a record is certified if it has candidate sites and every candidate is free for
every pair of a candidate site of p and a candidate site of k. A record without a call site or
without a candidate, and every record after a k whose pair of sites the map does not list, are
not certified. Without a map the bound is k + 1, rule H1; a receive that never completed in the
trace keeps that bound, since a later test of its request could deliver its result anywhere.

If every record from k + 1 to j is certified, the program issued them before reading any result
of r: the site each of them really came from is among its candidates, hence free, and the claim
above applies to it.

## Premises

The bounds are sound for a program and a trace under these premises; the first is checked,
the others are not, and a map made where one fails can certify a record issued after a read.

1. **The same sources.** The map is made from the sources the traced binary was built from.
   `stretto analyze --reads` checks it: the map records the SHA-256 of each source, and a
   source the trace's call sites name must hash the same (above).
2. **The same preprocessor flags.** The map is made with the macros and include paths of the
   traced build (`--cflags`, and `--mpi` for the MPI implementation's headers): `#ifdef`,
   macros and headers decide which code the same source compiles to, and the hash of the
   source does not see them. The MBI programs are built with `mpicc -O0 -g` and nothing more,
   which is what `experiments/mbi/reads_maps.py` gives clang.
3. **No undefined behaviour.** The program accesses no object through a pointer to another
   one (an out-of-bounds index, receiving more data into a local than it holds, pointer
   arithmetic across objects), and no signal handler or other thread reaches the result
   objects (rule 1 makes them unreachable to any code that has no undefined behaviour). The
   analysis reads the IR of the C semantics; an execution with undefined behaviour need not
   follow it.
4. **The same line table.** The traced binary's line table and clang's agree on the line of
   each MPI call (both compile with `-O0 -g`). A call whose line differs finds no candidate and
   ends the window, unless another MPI call of the same kind sits on the line it was given: then
   it takes that call's bounds.
5. **Every unit, or the callback rule.** Code outside the analysed units enters them only
   through functions whose address the units take or that have external linkage (rule 3): a
   `static` function can be reached from another unit only through its address, which its own
   unit then takes. This holds whether or not every unit of the program was given, so the map
   needs no list of the program's units. What remains is a premise: nothing calls `main` but
   the C runtime, once (C allows a program to call `main`; one that does breaks the premise).
6. **C only.** The map covers the C units. A Fortran call site is not a site of the map: its
   record has no candidate and ends the window. A C helper called from Fortran is covered by
   rule 3 like any outside caller.
7. **No merged calls.** An optimised binary may merge two MPI calls into one instruction, or
   give a call another call's line (tail merging, identical-code folding, inlining). A record of
   the merged call is then matched by one line while it may stand for the other call. The
   binaries must be built without optimisation (`-O0`, as the MBI and idiom builds are), or with
   flags that keep each call on its own line.
8. **The MPI calls the table knows.** MPI reads an object of the program only in a call that
   is given a pointer to it, until that call's request completes, or later through a call
   that `firstread/mpi.py` lists as keeping the pointer (rule 1). An extension, or a call of a
   later standard, that keeps a pointer and is missing from that list breaks this.

## Rule H1' in the horizon

With bounds, rule 1 of the horizon (`horizon.compute_cut`, and `fast.Observed.closure`, which
cuts alike) cuts the rank of a changed receive at its bound instead of right after its
completion. Probe and completion-order alternatives keep rule H1: a probe's status is read at
once in every program seen so far, and a matching probe would also re-order the queue the
window's receives draw from. The indexed acyclicity test relies on the rank ending right after
the receive, so a steal whose window is longer goes to the generic check.

The other receives of the window keep their observed matches in the alternative, and a longer
window adds their obligations: a later receive that accepts the message the changed receive
gave up, and took a later message of the same channel, makes the alternative unrealisable as
stated, although H1, which says nothing about that receive, accepts it. Both claims are sound
(H1 is a bound too), so an alternative is judged with H1 and, when H1 rejects it, with H1'; its
profile over the thresholds is the union.

H1' can also accept what H1 rejects. The containment of the H1 horizon in the H1' one carries
acyclicity and obligations over, but not the survival of a call: a send of the window can keep
a call that a rule of H1 removes, while the edges it adds reach only matches and completions,
so no cycle closes. Rank d receives r from any source (it took g's message) and then, before
reading r's result, sends x to e and receives r2 from e; rank e posts r_e1 from any source
(which took x) and r_e2 from f (which took y), sends s to d, and waits for both; f sends y.
For r taking s instead, H1 cuts d right after r: x is never sent, r_e1 stays pending, and until
26 Oct 2026 rule H:pending removed the posting of r_e2 and with it s, so r had no message. H1'
keeps x: r_e1 takes x, r_e2 takes y, s is sent, and the path c(r) -> x -> m(r_e1) -> m(r_e2) ->
c(Wait r_e2) never reaches the issue of s. Since 26 Oct 2026 rule H:pending deprives r_e2 of y
instead of removing it (a nonblocking receive whose result e reads only at its wait,
docs/model.md §2b), s is sent under H1 as well, and H1 accepts this alternative too
(`test_reads.HorizonUnion`); a call that a rule of H1 removes without an edge into the window
is what H1' needs, and the deprivation of a nonblocking receive was the one such case known.
The union stays, as both claims are sound. Nothing makes the H1' verdict monotone in B either (later
receives of the window become judged as B grows), so theorem T2, which makes the H1 profile of
a steal at a blocking receive an interval and its bisection exact, says nothing about the
union: an alternative whose bounds keep a window is judged at every breakpoint
(`report._profiles_fast`, `test_reads.HorizonUnion`).

A deadlock contained in the H1' horizon is an L1 finding (theorem T7,
`deadlock.contained_deadlock` with the cut of rule 1 at the bound): the ranks of the deadlock
stop at operations every program consistent with the trace and its bounds issues.

A changed receive that errs on its new message (a message longer than the receive, or of
another type signature: class 1a) keeps rule H1, whatever the map says: the call that
completes it returns an error code, at which the program aborts under the default error
handler and which it may test under `MPI_ERRORS_RETURN`, so the program may branch right
there. For the same reason a contained deadlock of such an alternative is stated only when
the changed receive's rank stops before completing it.

## Single-path witnesses

The single-path explorer runs the trace as a straight-line program, which reads nothing: the
single-path assumption is the assumption that no rank reads its results. The bounds say how far
that holds. Let E be an execution the explorer finds for an alternative. If for every rank
every record it issues in E, the one it stops at included, lies before the bound of each
earlier receive whose message in E differs from the trace's (and so whose source, tag, count or
payload may differ), then E is an execution of every program consistent with the trace that
respects the bounds: by induction along E, each rank has read only results equal to the
trace's when it issues a record, so it issues the trace's call, and each send's payload, a
function of what the rank has read, is the trace's too. Every message of E therefore carries
what it carried in the trace, so a receive that took the same message as in the trace returned
the same result, and MPI makes the same matches in the same interleaving. A deadlock or an
orphan message (or a truncation) that the explorer reports for an alternative is then an L1
finding when one of its witness executions passes this test (`reads.witness_respects`); the
finding says "in an execution", and its pins are that witness's.

A witness is not used when a rank passes, or stops at, a record outside the explorer's model:
a probe (its report could differ, and a blocking one could block), a test or
`MPI_Request_get_status` (its flag could differ), a Waitany or Waitsome (its index could), a
cancel, an abort, or the creation of a communicator (a collective the explorer does not
synchronise). A rank that finishes its recorded calls in the witness must end its trace with
`MPI_Finalize`; otherwise the program goes on beyond what the trace shows.

Nor is a witness used past an MPI error. The explorer goes on after a truncation (errors are
not terminal there, which keeps its exploration commutative), but a program aborts in the
call that returns the error under the default error handler and may test the error code under
`MPI_ERRORS_RETURN`, so it may branch at once. A deadlock or an orphan message is the end of
the witness, after all its errors, so its witness may have none: no error the explorer
records, and no receive that takes, instead of the trace's message, one that is longer than
it or of another type signature. A truncation (or a ready send without its receive) is
promoted from a witness up to the first error, which must be of that kind (every error of the
witness is), and each rank is judged only up to the completion of its first receive that errs,
which it has not passed when the first error happens.

## Known imprecision

- Objects that are globals, heap memory or parameters, and locals whose address escapes, are
  not followed (H1). A helper that receives into its caller's buffer, or a wrapper around
  `MPI_Recv` (HYPRE's `hypre_MPI_Recv`), therefore keeps H1.
- Reads are field-insensitive: reading one field of a status counts for all of it, and any
  element of an array buffer for the whole array.
- An object received into again still counts as read when the program reads the new data.
- Returns are context-insensitive and every outside call may call back every address-taken
  function and every function with external linkage, so a function called both before and
  after a read, every callback, and every non-`static` function once an outside call follows
  the read, has its calls tainted. Declaring helpers `static` keeps their calls free; a map
  that knew every unit of the program, and that no library calls the program by name, could
  drop the external functions, but nothing checks those two premises today.
- A wait that completes a receive posted in another function (a helper that waits for requests
  it is given) has no pair in the map: H1.
- Records are matched to sites by line: two MPI calls of the same kind on one line are
  certified only together. `MPI_Sendrecv` writes a wait record after its receive at its own
  site, which ends the window unless the call sits in a loop.
- In the horizon, the window's other receives keep their observed matches; an alternative that
  is realisable only if they take other messages falls back to H1.
- The explorer keeps one witness per outcome; a consequence is promoted only if that witness
  passes, and the promoted claim is existential ("in an execution") even when every
  single-path execution with the change has the consequence.
