# MB-25 SDK probes - 2026-11-12

Real Muse; normal app closed; one existing gadget identity locked. Disposable chats and files. No base64 decoding/OCR fallback allowed for P-2.

## P-1-45

```json
{
  "finished": true,
  "state": "remote_finished",
  "send_to_settled_s": 67.936,
  "tool": "probe.sleep",
  "requested_s": 45,
  "called": true,
  "local_completed": true,
  "elapsed_s": 45.01,
  "jpeg_bytes": null,
  "requested_timeout_ms": 125000,
  "jpeg_sha256": null,
  "correct_code": true,
  "valid_observation": true,
  "calls": 2,
  "failed_calls": 0,
  "unexpected_tools": []
}
```

## P-1-150

```json
{
  "finished": true,
  "state": "remote_finished",
  "send_to_settled_s": 182.992,
  "tool": "probe.sleep",
  "requested_s": 150,
  "called": true,
  "local_completed": true,
  "elapsed_s": 150.013,
  "jpeg_bytes": null,
  "requested_timeout_ms": 125000,
  "jpeg_sha256": null,
  "correct_code": false,
  "valid_observation": false,
  "calls": 12,
  "failed_calls": 0,
  "unexpected_tools": [
    "run.calls",
    "run.list",
    "workspace.list"
  ]
}
```

## P-1-decision

```json
{
  "inline_cap_s": 25
}
```

## P-2-picture

```json
{
  "finished": true,
  "state": "remote_finished",
  "send_to_settled_s": 25.599,
  "tool": "probe.picture",
  "requested_s": null,
  "called": true,
  "local_completed": true,
  "elapsed_s": null,
  "jpeg_bytes": 7231,
  "requested_timeout_ms": 30000,
  "jpeg_sha256": "ff5d9294971b3f9049360b03d631d1a1366868a79774bf3fb7b48621f1d48aec",
  "correct_code": false,
  "valid_observation": false,
  "calls": 1,
  "failed_calls": 0,
  "unexpected_tools": []
}
```

## P-2-camera

```json
{
  "finished": true,
  "state": "remote_finished",
  "send_to_settled_s": 22.353,
  "tool": "camera.capture",
  "requested_s": null,
  "called": true,
  "local_completed": true,
  "elapsed_s": null,
  "jpeg_bytes": 7920,
  "requested_timeout_ms": 30000,
  "jpeg_sha256": "46a076cb986dd8c7b4002e28d6b02fc005b70b8448885d5e552d3ffc887b1e00",
  "correct_code": false,
  "valid_observation": false,
  "calls": 1,
  "failed_calls": 0,
  "unexpected_tools": []
}
```

## P-2-decision

```json
{
  "route": "B"
}
```

## P-3-baseline

```json
{
  "finished": true,
  "state": "remote_finished",
  "send_to_settled_s": 32.9,
  "edit_calls": 3,
  "failed_edit_calls": 0,
  "python_exact": true,
  "makefile_exact": true,
  "only_edit_mutations": true
}
```

P-4 follows MB-26. P-5 follows MB-30. Neither is claimed complete here.

## Questions, methods, observations, decisions

| Probe question | Exact method | Observation | Decision |
|---|---|---|---|
| P-1: can a blocking tool deliver its result after 45 and 150 seconds? | Temporary `probe.sleep`, descriptor timeout 200000 ms, one invocation per duration, private random code returned only after the wait, independent exact answer comparison. | 45 seconds passed: local 45.010 s and send-to-settled 67.936 s. 150 seconds completed locally in 150.013 s, but the answer was not recovered; Muse reported a timeout and made status/list inspections. SDK-supplied timeout was 125000 ms on both invocations despite the descriptor. | Use the plan's conservative 25-second inline cap. Longer commands retain an owned job handle. The 150-second failure is an end-to-end result, even though the local callback succeeded. |
| P-2: does the stock ESP32 camera payload deliver inline pixels? | Synthetic six-digit code only in a 640x200 JPEG. Returned the stock `{ok:true,payload:{format:"jpeg-base64",data_base64:...}}` envelope once as `probe.picture`, then once as `camera.capture`. Base64 decoding, OCR and other capture tools were explicitly excluded. | JPEG sizes 7231 and 7920 bytes, both under 120 KB; payload digests above identify them. Both calls completed, but neither produced the code. In both replies Muse described base64 data and inability to see the pixels. Only boolean classifications were extracted from the private replies; no raw transcript/code is reproduced. | MB-35 route B: use the existing labelled automatic image turn. This route already passed baseline task 12. Neither tool naming nor stock format was enough for inline vision. |
| P-3: how often does the existing exact edit fail on this format fixture? | CRLF nested Python plus a tab-indented CRLF Makefile; three requested changes through `file.edit`, independently compared with exact expected bytes. | Three edit calls, zero failed edit calls; exact Python and Makefile bytes matched, and no file.write/shell mutation substitute was used. | Keep this result as the old-tool baseline. Repeat both new modes after MB-27; choose and document their order from the measured counts. |

The normal app remained closed and one process held its existing identity throughout.
Probe tools were removed when the process ended. These results change no owner rule
and remove no phase: MB-35 still implements image-turn vision.
