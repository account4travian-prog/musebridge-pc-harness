# MB-25 historical baseline survey

For the controlled comparison, use [v1-baseline-complete](eval/v1-baseline-complete-2026-11-12.md) and its JSON provenance. The historical survey below remains a separate read-only observation.

2026-11-12. Read-only SQLite backup into a temporary directory, then aggregate queries on the copy. No live-file migration or write. This history includes development fixtures and is not the controlled eval baseline. Old rows lack payload-size/preamble/error-text measurements; error classes are used where available.

```text
Calls per tool
Tool                | Calls
--------------------+------
file.write          | 17   
file.read           | 15   
plan.update         | 12   
bridge.context      | 8    
job.wait            | 8    
artifact.read       | 6    
terminal.read       | 4    
git.diff            | 2    
git.status          | 2    
git.worktree.create | 2    
job.start           | 2    
mcp.read            | 2    
mcp.tools           | 2    
project.handoff     | 2    
project.note        | 2    
task.update         | 2    
terminal.start      | 2    
terminal.write      | 2    
vision.observe      | 2    
bridge.capabilities | 1    
browser.click       | 1    
browser.observe     | 1    
browser.open        | 1    
change.list         | 1    
file.edit           | 1    
hook.status         | 1    
job.status          | 1    
task.create         | 1    
task.verify_job     | 1    
Failure rate per tool
Tool                | Calls | Failures | Failure rate
--------------------+-------+----------+-------------
file.write          | 17    | 0        | 0.0         
file.read           | 15    | 0        | 0.0         
plan.update         | 12    | 0        | 0.0         
bridge.context      | 8     | 0        | 0.0         
job.wait            | 8     | 0        | 0.0         
artifact.read       | 6     | 0        | 0.0         
terminal.read       | 4     | 0        | 0.0         
git.diff            | 2     | 0        | 0.0         
git.status          | 2     | 0        | 0.0         
git.worktree.create | 2     | 0        | 0.0         
job.start           | 2     | 0        | 0.0         
mcp.read            | 2     | 0        | 0.0         
mcp.tools           | 2     | 0        | 0.0         
project.handoff     | 2     | 0        | 0.0         
project.note        | 2     | 0        | 0.0         
task.update         | 2     | 0        | 0.0         
terminal.start      | 2     | 0        | 0.0         
terminal.write      | 2     | 0        | 0.0         
vision.observe      | 2     | 0        | 0.0         
bridge.capabilities | 1     | 0        | 0.0         
browser.click       | 1     | 0        | 0.0         
browser.observe     | 1     | 0        | 0.0         
browser.open        | 1     | 0        | 0.0         
change.list         | 1     | 0        | 0.0         
file.edit           | 1     | 0        | 0.0         
hook.status         | 1     | 0        | 0.0         
job.status          | 1     | 0        | 0.0         
task.create         | 1     | 0        | 0.0         
task.verify_job     | 1     | 0        | 0.0         
Top 10 sanitized errors
(no data)
Median and p90 duration per tool (seconds)
Tool                | Samples | Median | P90  
--------------------+---------+--------+------
artifact.read       | 6       | 0.008  | 0.433
bridge.capabilities | 1       | 0.072  | 0.072
bridge.context      | 8       | 0.016  | 0.031
browser.click       | 1       | 0.028  | 0.028
browser.observe     | 1       | 0.047  | 0.047
browser.open        | 1       | 0.815  | 0.815
change.list         | 1       | 0.013  | 0.013
file.edit           | 1       | 0.06   | 0.06 
file.read           | 15      | 0.009  | 0.018
file.write          | 17      | 0.047  | 0.362
git.diff            | 2       | 0.144  | 0.144
git.status          | 2       | 0.209  | 0.213
git.worktree.create | 2       | 0.439  | 0.457
hook.status         | 1       | 0.016  | 0.016
job.start           | 2       | 0.034  | 0.044
job.status          | 1       | 0.008  | 0.008
job.wait            | 8       | 0.008  | 0.029
mcp.read            | 2       | 0.016  | 0.016
mcp.tools           | 2       | 1.864  | 2.059
plan.update         | 12      | 0.009  | 0.015
project.handoff     | 2       | 0.011  | 0.011
project.note        | 2       | 0.009  | 0.009
task.create         | 1       | 0.018  | 0.018
task.update         | 2       | 0.036  | 0.057
task.verify_job     | 1       | 0.052  | 0.052
terminal.read       | 4       | 0.008  | 0.008
terminal.start      | 2       | 0.014  | 0.014
terminal.write      | 2       | 0.008  | 0.009
vision.observe      | 2       | 0.056  | 0.067
Calls per run
Runs: 18; mean: 5.778; median: 3.5; p90: 18
Run detail
Run ID                               | Calls
-------------------------------------+------
<run-id> | 19   
<run-id> | 18   
<run-id> | 14   
<run-id> | 8    
<run-id> | 8    
<run-id> | 6    
<run-id> | 5    
<run-id> | 4    
<run-id> | 4    
<run-id> | 3    
<run-id> | 3    
<run-id> | 3    
<run-id> | 2    
<run-id> | 2    
<run-id> | 2    
<run-id> | 1    
<run-id> | 1    
<run-id> | 1    
Preamble bytes per send
Sends | Median bytes | P90 bytes
------+--------------+----------
0     | None         | None     
Result byte coverage
Measured calls | Missing | Total bytes
---------------+---------+------------
0              | 104     | 0          
```

## Controlled baseline - 0.6.1, 2026-11-12

Twelve real-Muse tasks were scored using frozen seeds and independent checks. The normal app was closed and the existing identity lock held. Browser contexts were headless; desktop and visual fixture startup verified containment on the secondary screen without activation. No re-pairing, model change, or firmware change occurred.

The comparison baseline is [the complete score report](eval/v1-baseline-complete-2026-11-12.json): 9/12 successes, 206 tool calls, 10 failed/uncertain calls (4.8544%), 945.327 seconds including independent checking, 222,548 measured result bytes, and 35,429 preamble bytes across 14 sends (about 2,531 per send). Every tool call has a measured result size.

Provenance is preserved rather than overwriting measurements. [The original run](eval/v1-baseline-2026-11-12.json) scored 7/12. Its browser turn became uncertain when the subscription/transport ended; the exact remote cause was not observed. The evaluator then attempted the final three tasks without waiting for reconnection. Read-only journal inspection proved those three original chats had zero outbox entries, zero send-context events, and zero tool calls. Only these definitely unsent cases were executed in fresh chats in [the supplemental run](eval/v1-baseline-unsent-2026-11-12.json). The composite JSON records source hashes and the three replacement decisions. The uncertain browser request was never replayed.

Observed limitations:

- Task 08 ran the 70-second job and produced an answer, but the remaining three-minute task budget was insufficient for its independent checker to rerun that script. Its timeout remains a failure; the benchmark budget was not changed.
- Task 09 exposed the current browser tools' lack of a select-option action: fill rejected a select element and option clicks timed out. Those operations and the interrupted turn remain recorded.
- Task 11 hit one conflicting-selector error and three owner-input refusals. The expected output file existed, but no desktop action succeeded, so the capability gate correctly rejected a false pass. Input guards were preserved.
- Task 10 verified an actual intermediate edit and exact undo. Task 12 read the private random code from an actual owned-window image continuation and passed independently; the code was not in the prompt or model-visible fixture state.

Evaluator repairs are distinct from product upgrades: per-task readiness now waits for reconnection and zero-admission sends are classified as not-sent; checker processes now use a suspended, assigned-before-resume Windows Job Object so timeout/parent completion terminates descendants. Runner/orchestration/checker tests: 17 passed. Full product/eval suite before the final readiness regression and checker helper: 255 passed, 4 skipped. Native desktop/vision placement smoke passed on the secondary screen.
