Rows by code commit: 31e57af (256).

| corpus | reference | judged (alternative, B) | all ranks: same | weaker | other | changed rank only: same | weaker | other |
|---|---|---|---|---|---|---|---|---|
| idiom | single-path | 4 | 2 | 2 | 0 | 2 | 2 | 0 |
| idiom | twin | 12 | 12 | 0 | 0 | 10 | 2 | 0 |
| mbi | single-path | 216 | 178 | 38 | 0 | 178 | 38 | 0 |
| mbi (E7 twins) | twin | 24 | 24 | 0 | 0 | 24 | 0 | 0 |

| program | label | observed run | L1 alternatives | L1 | L1+SP | L2 (twin) | L2 (re-execution) | runs | seconds | L2 findings by re-execution |
|---|---|---|---|---|---|---|---|---|---|---|
| 01_master_worker_anysource |  | FINISHED | 1 | 1f | 1c 1d | 1e | 1e | 7 | 2.2 | the continuation diverges from the trace in every execution |
| 02_waitany_loop |  | FINISHED | 0 | waitany-order | - | - | - | 0 | 0.0 |  |
| 03_head_to_head_send |  | FINISHED | 0 | - | - | - | - | 0 | 0.0 |  |
| 04_orphan_send |  | FINISHED | 1 | 1f | 1c 1d | 1d | 1d | 2 | 0.5 | deadlock in every execution |
| 05_count_mismatch |  | FINISHED | 1 | 1a | 1a 1c 1d | 1d | 1d | 2 | 0.6 | deadlock in every execution |
| 06_collective_after_anysource |  | FINISHED | 1 | 1f | 1c 1d | 1d 1e | 1d 1e | 2 | 0.6 | deadlock in every execution; the continuation diverges from the trace in every execution |
| 07_isend_buffer_reuse |  | FINISHED | 0 | - | - | - | - | 0 | 0.0 |  |
| 08_anytag_cross_purpose |  | FINISHED | 1 | 1b | 1c 1d | 1d | 1d | 2 | 0.5 | deadlock in every execution |
| 09_two_wildcards_chain |  | FINISHED | 1 | 1b | 1c 1d | 1d | 1d | 2 | 0.5 | deadlock in every execution |
| 10_halo_exchange |  | FINISHED | 0 | - | - | - | - | 0 | 0.0 |  |
| 11_iprobe_polling |  | FINISHED | 0 | probe-alt | - | (no twin) | - | 0 | 0.0 |  |
| 12_split_comm_pin |  | FINISHED | 1 | 1f | 1c 1d | (no twin) | - | 1 | 0.3 |  |
| 13_derived_type_signature |  | FINISHED | 1 | 1f | 1c 1d | (no twin) | 1d | 2 | 0.5 | deadlock in every execution |
| MessageRace_Allgather_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Allgather_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.9 |  |
| MessageRace_Allgather_Send_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Allgather_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Allgatherv_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Allgatherv_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Allgatherv_Send_Irecv_nok | ERROR MessageRace | FINISHED | 1 | 1f | 1c 1d | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Allgatherv_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Allreduce_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Allreduce_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Allreduce_Send_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Allreduce_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Alltoall_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Alltoall_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Alltoall_Send_Irecv_nok | ERROR MessageRace | FINISHED | 1 | 1f | 1c 1d | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Alltoall_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Alltoallv_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Alltoallv_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Alltoallv_Send_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Alltoallv_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 1 | 0.3 |  |
| MessageRace_Barrier_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Barrier_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Barrier_Send_Irecv_nok | ERROR MessageRace | FINISHED | 1 | 1f | 1c 1d | (no twin) | 1d | 2 | 0.6 | deadlock in every execution |
| MessageRace_Barrier_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Bcast_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Bcast_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Bcast_Send_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Bcast_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Exscan_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Exscan_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Exscan_Send_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Exscan_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Gather_Isend_Irecv_nok | ERROR MessageRace | FINISHED | 1 | 1f | 1c 1d | (no twin) | 1d | 2 | 0.5 | deadlock in every execution |
| MessageRace_Gather_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Gather_Send_Irecv_nok | ERROR MessageRace | FINISHED | 1 | 1f | 1c 1d | (no twin) | 1d | 2 | 0.6 | deadlock in every execution |
| MessageRace_Gather_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.9 |  |
| MessageRace_Irecv_Isend_nok | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.5 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Irecv_Send_nok | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.5 | the program aborts on its own check in some executions |
| MessageRace_Loop_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 6 | 1f | 1c 1d | (no twin) | 1d | 122 | 33.3 | deadlock in every execution; deadlock in some executions |
| MessageRace_Loop_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 25 | 6.8 |  |
| MessageRace_Loop_Isend_Recv_nok | ERROR MessageRace | TIMEOUT | 6 | 1f | 1c 1d | (no twin) | 1d | 122 | 33.6 | deadlock in every execution; deadlock in some executions |
| MessageRace_Loop_Isend_Recv_ok | OK | FINISHED | 3 | 1f | - | (no twin) | - | 25 | 6.9 |  |
| MessageRace_Loop_Send_Irecv_nok | ERROR MessageRace | FINISHED | 7 | 1f | 1c 1d | (no twin) | 1d | 121 | 33.7 | deadlock in every execution; deadlock in some executions |
| MessageRace_Loop_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 25 | 6.9 |  |
| MessageRace_Loop_Send_Recv_nok | ERROR MessageRace | FINISHED | 6 | 1f | 1c 1d | (no twin) | 1d | 121 | 33.5 | deadlock in every execution; deadlock in some executions |
| MessageRace_Loop_Send_Recv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 25 | 6.9 |  |
| MessageRace_Recv_Isend_nok | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.7 | the program aborts on its own check in some executions |
| MessageRace_Recv_Send_nok | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.8 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Reduce_Isend_Irecv_nok | ERROR MessageRace | FINISHED | 1 | 1f | 1c 1d | (no twin) | 1d | 2 | 0.6 | deadlock in every execution |
| MessageRace_Reduce_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.9 |  |
| MessageRace_Reduce_Send_Irecv_nok | ERROR MessageRace | FINISHED | 1 | 1f | 1c 1d | (no twin) | 1d | 2 | 0.6 | deadlock in every execution |
| MessageRace_Reduce_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Scan_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Scan_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Scan_Send_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Scan_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Scatter_Isend_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Scatter_Isend_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Scatter_Send_Irecv_nok | ERROR MessageRace | TIMEOUT | 1 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_Scatter_Send_Irecv_ok | OK | FINISHED | 2 | 1f | - | (no twin) | - | 3 | 0.8 |  |
| MessageRace_tag_1_2_Send_Recv_ok | OK | FINISHED | 0 | - | - | (no twin) | - | 0 | 0.0 |  |
| MessageRace_tag_1_ANY_TAG_Send_Recv_ok | OK | FINISHED | 0 | - | - | (no twin) | - | 0 | 0.0 |  |
| MessageRace_tag_2_2_Send_Recv_nok | ERROR MessageRace | TIMEOUT | 0 | - | - | (no twin) | - | 0 | 0.0 |  |
| MessageRace_tag_ANY_TAG_1_Send_Recv_nok | ERROR MessageRace | TIMEOUT | 1 | 1b | - | (no twin) | - | 3 | 0.9 |  |
| MessageRace_tag_ANY_TAG_ANY_TAG_Send_Recv_ok | OK | FINISHED | 1 | 1b | - | (no twin) | - | 3 | 0.8 |  |

Ranks whose re-execution was not trusted:

- MessageRace_Allgatherv_Isend_Irecv_nok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 1; the run ended: unknown MPI_Allgatherv: not served by the re-execution shim)
- MessageRace_Allgatherv_Isend_Irecv_ok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 1; the run ended: unknown MPI_Allgatherv: not served by the re-execution shim)
- MessageRace_Allgatherv_Send_Irecv_nok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 1; the run ended: unknown MPI_Allgatherv: not served by the re-execution shim)
- MessageRace_Allgatherv_Send_Irecv_ok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 1; the run ended: unknown MPI_Allgatherv: not served by the re-execution shim)
- MessageRace_Alltoall_Isend_Irecv_nok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 2; the run ended: unknown MPI_Alltoall: the contribution differs from the recorded one, so the result may too)
- MessageRace_Alltoall_Isend_Irecv_ok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 2; the run ended: unknown MPI_Alltoall: the contribution differs from the recorded one, so the result may too)
- MessageRace_Alltoall_Send_Irecv_nok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 2; the run ended: unknown MPI_Alltoall: the contribution differs from the recorded one, so the result may too)
- MessageRace_Alltoall_Send_Irecv_ok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 2; the run ended: unknown MPI_Alltoall: the contribution differs from the recorded one, so the result may too)
- MessageRace_Alltoallv_Isend_Irecv_nok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 1; the run ended: unknown MPI_Alltoallv: not served by the re-execution shim)
- MessageRace_Alltoallv_Isend_Irecv_ok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 1; the run ended: unknown MPI_Alltoallv: not served by the re-execution shim)
- MessageRace_Alltoallv_Send_Irecv_nok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 1; the run ended: unknown MPI_Alltoallv: not served by the re-execution shim)
- MessageRace_Alltoallv_Send_Irecv_ok: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 4 calls vs 1; the run ended: unknown MPI_Alltoallv: not served by the re-execution shim)
- 12_split_comm_pin: rank 1: its re-execution does not reproduce the trace (after 0 result(s): 1 calls vs 0; the run ended: unknown MPI_Comm_split: not served by the re-execution shim)
