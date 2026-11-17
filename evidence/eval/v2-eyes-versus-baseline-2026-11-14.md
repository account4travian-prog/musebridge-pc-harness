Compare v1-baseline-complete → v2-eyes

Deltas are after minus before; negative call, failure, time, and byte deltas are lower.
| Task | Status | Success | Capability | Secondary display | Tool calls | Failed-call rate | Wall time (s) | Result bytes | Preamble bytes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 01_off_by_one | compared | yes → yes | N/A | N/A | -17 | +0.000000 | -6.896000 | -16016 | -1825 |
| 02_crlf_rename | compared | yes → yes | N/A | N/A | -6 | +0.000000 | -7.924000 | -9767 | -1825 |
| 03_cli_flag | compared | yes → yes | N/A | N/A | -4 | +0.000000 | +4.729000 | -5820 | -1825 |
| 04_find_log | compared | yes → yes | N/A | N/A | -4 | +0.000000 | -2.095000 | -1068 | -1825 |
| 05_make_yaml | compared | yes → yes | N/A | N/A | -3 | +0.000000 | -2.965000 | -4837 | -1825 |
| 06_powershell_spaces | compared | yes → yes | N/A | N/A | -4 | +0.000000 | -2.148000 | -5274 | -1825 |
| 07_small_project | compared | yes → yes | N/A | N/A | -8 | +0.000000 | +6.812000 | -8482 | -1825 |
| 08_long_command | compared | no → yes | N/A | N/A | -4 | +0.111111 | -1.397000 | -5013 | -1825 |
| 09_browser_setting | compared | no → yes | yes → yes | N/A | -9 | -0.300000 | -59.100000 | -45784 | -1825 |
| 10_undo_change | compared | yes → yes | N/A | N/A | -1 | +0.000000 | -2.453000 | -4383 | -4192 |
| 11_desktop_form | compared | no → no | no → no | yes → yes | +0 | +0.045455 | +86.730000 | +13436 | -1829 |
| 12_vision_code | compared | yes → yes | N/A | yes → yes | -3 | +0.000000 | +0.352000 | -3693 | -4390 |

Overall deltas (full-run totals):
- success_rate: +0.166667
- tools_calls: -63
- failed_call_rate: -0.006586
- wall_time: +13.645000
- result_bytes: -96701
- preamble_bytes: -26836
