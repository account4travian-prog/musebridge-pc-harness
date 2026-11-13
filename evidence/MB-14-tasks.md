# MB-14 structured tasks and verification — 2026-11-11

Implemented task/dependency records, pending/active/blocked/awaiting_review/complete states, explicit job-exit versus manual verification, bounded task tools and a Tasks view. Context and goal prompts include task summaries; plan completion refuses unfinished or stale structured tasks. Remote tools cannot issue local-owner review acceptance.

Task receipts inspect actual conversation-owned JobManager outcomes. File observations alone cannot complete a task. File-bound check jobs capture inputs before process admission and retain that original snapshot through verification and duplicate request-ID handling. Changed inputs cannot be re-baselined to bytes an older check never examined. Reads are bounded and root/reparse identity is checked. Later changes invalidate receipts; a completed goal with now-stale tasks is projected as paused/unverified while its original report is retained.

Default job-exit evidence establishes that the check process succeeded and the selected pre-run inputs stayed unchanged, not an independent semantic judgment of arbitrary prose criteria. Manual-review tasks require an explicit local Tasks-view action; ordinary automated tasks do not introduce an approval dialog.

A helper model supplied TaskStore and targeted tests; root integrated checked job admission, schemas/policy, context/GoalRunner, goal-state projection and Tasks UI. Local tests include dependency/cross-chat isolation, missing or failed receipts, root/size bounds, stale content, job retries, restart and actual Windows JobManager integration. A post-job/pre-receipt file-change case was added before live acceptance.

**Live acceptance passed:** `.local/task-probe-1794395291/report.json`. Actual Muse used task.create/update, job.start/wait with prepared input.txt, task.verify_job and plan.update. Independent local inspection found one completed task with a passed receipt. The probe then changed input.txt; the receipt became stale, the task awaited review and the goal context became paused. Zero unknown tool calls were recorded. No private project was modified; the test used a disposable project and chat.

Whole-program reliability, speech and desktop acceptance remain MB-23/05/06/07 work. Task/check evidence is not a claim of universal program correctness.
