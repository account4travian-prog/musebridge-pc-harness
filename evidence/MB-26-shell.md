# MB-26 shell execution - implementation checkpoint

2026-11-12. Core implementation: `4b035fe`; future-cursor repair: `0bb7f60`.
The [live P-4 shell comparison](MB-26-shell-fluency.md) is complete: PowerShell
and Git Bash each passed all five jobs with zero failed calls/commands. The tie
retains PowerShell as the default; Git Bash remains selectable.

`shell.run` starts a conversation-owned, journaled Windows job and returns its
output/exit code in the same invocation when it finishes within 25 seconds.
The cap follows the actual P-1 result. Longer or explicitly background commands
return their job handle; `shell.job` waits, inspects, lists, or cancels owned jobs.
Nonzero command exits remain successful tool responses with the actual exit code.
Legacy `job.*` names continue to work; consolidation and ID removal remain later
plan items.

The driver prefers PowerShell 7 when installed, then Windows PowerShell. It also
supports cmd and Git Bash found relative to Git for Windows. Unavailable shells
return an actionable error before a job is admitted. Shell names and cached
versions are exposed through context. `cmd` built-ins have an observed non-ASCII
code-page limitation; PowerShell and UTF-8 child output passed Unicode checks.

Process creation stays hidden and suspended until assignment to a kill-on-close
Windows Job Object. Stdout/stderr share one pipe and incremental UTF-8 decoding.
The old bounded tail/cursor API is preserved; an additive, transaction-protected
migration adds a durable bounded head and total character count. A copied legacy
database migration preserves rows and never invents a head discarded by the old
ring. Shell result shaping strips CSI/OSC escapes, collapses progress updates,
keeps beginning/end under the JSON budget, and offers retained-log pagination.
Discarded middle output is explicitly unrecoverable; the hint recommends narrowing
or redirecting future output.

Validation observed:

- Full suite: 285 passed, 4 skipped, before the subsequent future-cursor regression.
- Root shell integration: output plus nonzero exit in one call, UTF-8, deferred
  handle/wait, ownership, read-only refusal, prompt Stop Work during a blocked
  wait, actual grandchild termination, two-megabyte head/tail output under 24 KB,
  Unicode log pagination, and unavailable-shell refusal.
- Independent review found oversized cursors could move backward. The driver now
  refuses them; targeted shell/job/fluency tests passed after the repair.
- Driver tests exercise installed Git Bash, cmd/Python Unicode output, merged
  stream order, legacy default-shell retry compatibility, and shell-aware request
  deduplication. Existing Job Object, timeout, restart and cancellation checks pass.

No visible terminal is opened. Native development windows stay on the secondary
display; these shell checks use hidden processes and disposable temporary projects.
