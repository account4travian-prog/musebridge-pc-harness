# MB-29 consolidated tool surface

2026-11-12 local. Implementation and live acceptance complete. Source checkpoint
e48ce96, launcher 79d3238. The live report uses the UTC date 2026-11-13.

The canonical surface is 20 core tools with browser (5), desktop (4), terminal
(4) and MCP (2) packs: 29 by default and 35 with all eligible packs. Settings ->
Tool packs persists discovery choices for the next connection; a running
connection retains its actual registered snapshot. Desktop requires an existing
project Desktop control profile, and MCP requires a selected pack and enabled
configured server. Profiles, grants and Stop Work remain independent effect gates.

Contract version is 2; app version remains 0.6.1 pending MB-30. Registered
descriptions include practical examples and neighboring-tool guidance. Legacy
names continue dispatching for this release. Temporary SDK probes explicitly add
their own schemas, while normal registration does not expose those probes.

Core adapters combine task creation/update, job-backed verification, change
review/restore and artifact listing/reading. Verification retains pre-job input
fingerprints, current receipts and manual-owner-review boundaries. Pending check
inspection reuses its owned job. Changes are scoped to the selected project;
restore preserves later owner edits. File writes report their actual line count.

Browser tools share the existing isolated headless worker. Selector-based
click/fill/select/check/uncheck are available; synchronous JavaScript uses a
browser-side execution deadline. Infinite-loop recovery and conversation scope
are exercised locally. Snapshot references/action diffs remain MB-33.
The evaluation deadline uses [the Runtime.evaluate timeout parameter](https://chromedevtools.github.io/devtools-protocol/tot/Runtime/#method-evaluate).

Terminal open/send/read/close reuse the existing owned ConPTY host and process
tree supervision. The host now answers terminal status/cursor queries inside
the pseudoterminal, allowing the modern Python REPL to start. Send waits for
bounded output/quiet time and reports input delivery separately from command
completion. Real REPL round-trip, Unicode/VT, ownership and cancellation tests run
without a visible terminal or input to the owner's desktop.

MCP dispatch derives read versus write from the owner's local grant. The same
mcp.call surface permits a read-granted fixture under Read only and refuses its
write-granted neighbor. The maintained SDK creates hidden, Job-Object-supervised
stdio processes on Windows; no visible server window is needed.

The desktop launcher is conservative: explicit asInvoker manifest, no uiAccess or
RUNASADMIN compatibility elevation, unlocked idle session, unchanged foreground,
current execution guard, secondary-monitor/no-activation hints and bounded
new-PID window lookup. Pointer-safe native resource inspection ran read-only on
the current Python executable; launch effects were mocked. No actual application
was launched. Windows/app launch hints may be overridden; real owner-observed
desktop acceptance remains MB-34. Native flags follow [SHELLEXECUTEINFOW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow).

screen.look uses the proven next-turn image route and existing local visual
permissions for browser, selected window or guarded project image. Inline pixels,
cropping and ref-label overlays are not claimed; the latter improvements remain
MB-35. Lean skill/header/Markdown context remains MB-30.

Integrated source checkpoint: 450 passed, 5 skipped in 81.36 seconds, before the
subsequently requested desktop/Start-menu launcher work. Review repairs include
freshness checks on already-verified task job retries, refused creation of a
new task requested as complete, failed envelopes for all-rejected task batches,
explicit partial lifecycle events, current-project checkpoint filtering, scoped
desktop inspection, and an MCP configuration-epoch fence at invocation start.
Existing optimistic file-restore checks detect later edits before publication;
they are not a strict cross-process compare-and-swap guarantee.

## Live acceptance

[v2-core](eval/v2-core-2026-11-13.md) passed all 12 tasks, against 9/12 in the
controlled [baseline](eval/v1-baseline-complete-2026-11-12.md). No task success
regressed. Both native fixtures were verified on the secondary display; browser
and desktop tasks exercised their required capabilities. Source/limits/checkers
were unchanged during the run. [Full comparison](MB-29-comparison.md).

| Metric | Baseline | v2-core |
|---|---:|---:|
| Successes | 9/12 | 12/12 |
| Tool calls | 206 | 145 |
| Failed/uncertain calls | 10 (4.85%) | 5 (3.45%) |
| Wall time | 945.327 s | 787.474 s |
| Result bytes | 222548 | 97622 |
| Preamble bytes / 14 sends | 35429 | 49842 |

Tool calls fell 29.61%, result bytes 56.14%, and measured wall time 16.70%.
Preamble bytes increased 40.68%; MB-30 must reduce this instead of describing it
as an improvement. This one run does not establish universal performance or
owner acceptance of real Notepad/voice use. The 1.0 call-reduction target of at
least 40% has not yet been reached. Local suite after launcher wiring passed
462 tests with 5 skips; locked offline dependency sync passed. The comparison CLI
was repaired to print ASCII arrows under Windows cp1252; its 8 existing tests and
the actual default-encoding comparison command passed.
