Rows by code commit: 31e57af (122).

| corpus | reference | judged (alternative, B) | all ranks: same | weaker | other | changed rank only: same | weaker | other |
|---|---|---|---|---|---|---|---|---|
| mbi (E7 twins) | twin | 122 | 122 | 0 | 0 | 122 | 0 | 0 |

| program | label | observed run | L1 alternatives | L1 | L1+SP | L2 (twin) | L2 (re-execution) | runs | seconds | L2 findings by re-execution |
|---|---|---|---|---|---|---|---|---|---|---|
| MessageRace_Irecv_Isend_nok.0 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.6 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Irecv_Isend_nok.1 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.7 | the program aborts on its own check in some executions |
| MessageRace_Irecv_Isend_nok.2 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.6 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Irecv_Isend_nok.3 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.6 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Irecv_Isend_nok.4 | ERROR MessageRace | TIMEOUT | 4 | 1f | - | 1e | 1e | 14 | 4.2 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Irecv_Send_nok.0 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.8 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Irecv_Send_nok.1 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.9 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Irecv_Send_nok.2 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.8 | the program aborts on its own check in some executions |
| MessageRace_Irecv_Send_nok.3 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.8 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Irecv_Send_nok.4 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.6 | the program aborts on its own check in some executions |
| MessageRace_Recv_Isend_nok.0 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.6 | the program aborts on its own check in some executions |
| MessageRace_Recv_Isend_nok.1 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.8 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Recv_Isend_nok.2 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.9 | the program aborts on its own check in some executions |
| MessageRace_Recv_Isend_nok.3 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 4.0 | the program aborts on its own check in some executions |
| MessageRace_Recv_Isend_nok.4 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.8 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Recv_Send_nok.0 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.9 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Recv_Send_nok.1 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 3.9 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Recv_Send_nok.2 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 4.0 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Recv_Send_nok.3 | ERROR MessageRace | FINISHED | 3 | 1f | - | 1e | 1e | 13 | 4.0 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
| MessageRace_Recv_Send_nok.4 | ERROR MessageRace | SIGNAL:6 | 3 | 1f | - | 1e | 1e | 13 | 3.8 | the program aborts on its own check in every execution; the program aborts on its own check in some executions |
