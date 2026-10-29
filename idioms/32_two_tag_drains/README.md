# 32 two drain loops of different tags at one call site

Ranks: 3. Rank 0 alternates `drain(A)` and `drain(B)`, each a loop of
`MPI_Iprobe(MPI_ANY_SOURCE, tag)` that receives what it finds from `status.MPI_SOURCE`, until it
has four messages; both loops call the one `MPI_Iprobe` in `drain`. Rank 1 sends two messages of
tag A after 50 ms, rank 2 two of tag B after 200 ms, so the observed run finds A, A, B, B.

The shape of VTK 9.7's `vtkPLagrangianParticleTracker`, whose rank 0 drains tags 622 and 624
through `vtkMPIController::Iprobe` (docs/findings.md).

Expected:
- `probe-unflip` (L1) at each probe that found a message, `probe-flip` at failed polls; no error
  class.
- The pins of the probes carry their envelope's ordinal as the fourth field of their identity
  (`STRETTO_PIN_PROBE=0:1:-1@0:21:<site>:1` for the second tag-A probe): a replay with rank 1
  delayed (`STRETTO_SEND_DELAY_MS=1:400`) finds B, B, A, A, the rank's ordinal 1 is a tag-B
  probe there, and the pin still falls on the second tag-A probe. A pin without the fourth
  field ends that replay as misapplied (exit code 86).
