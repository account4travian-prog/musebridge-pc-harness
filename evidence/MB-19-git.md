# MB-19 — Git worktrees

Implementation: `musebridge/git_tools.py` and the project Changes interface. Tools inspect status, diffs, staged diff, branches and worktrees, and create a fresh isolated worktree/branch linked to a separate project conversation. No commit or push action is provided. Worktree operations preserve existing work; verify repository status and target before editing.

The live probe reported a usable worktree and preserved original work (`worktree_usable`, `original_work_preserved` true). Probe report: `.local/workbench-probe-1794455635/report.json`.
