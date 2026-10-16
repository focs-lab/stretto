# 13 derived types across a wildcard

Ranks: 4. Rank 0 receives four `MPI_INT` from `ANY_SOURCE` (observed: rank 1, four
`MPI_INT`), then four `MPI_INT` from rank 2, which sent one element of
`contiguous(4, MPI_INT)`. Rank 3 sends a `struct {int; double}` on another tag.

Alternative matching: the wildcard takes rank 2's message.

Expected reports:
- L1: the alternative is **1f benign**: one `contiguous(4, MPI_INT)` has the signature
  `INT INT INT INT`, the same as four `MPI_INT`. The trace carries the flattened signature
  `MPI_INT*4`; comparing the type names alone would have reported a 1a mismatch.
- Rank 3's message is on tag 14, so it is no alternative for the tag-13 wildcard. Its record
  carries `MPI_INT,MPI_DOUBLE`, which `types.compatible` would reject against `MPI_INT*4`.
