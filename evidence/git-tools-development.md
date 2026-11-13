# MB-19 Git/worktree service development

## Implemented

`musebridge.git_tools.GitTools(catalog, state_dir)` exposes `status(cid)`,
`diff(cid, path=None, staged=False)`, `branches(cid)`, `worktrees(cid)` and
`create_worktree(cid, branch, base=None)`. Status/diff are scoped to the selected
conversation's project root. Diff output is capped at 512 KiB and command output
is drained with a 2 MiB cap and 20 second timeout. Private `.git`, `.local`,
`local-data`, and credential path components are excluded from content access.

Worktree creation requires a fresh valid branch, resolves the base to a commit
before use, and stores the linked worktree below `state_dir/worktrees/<id>`.
It creates a corresponding project and conversation and records an additive
`git_worktrees_v1` journal row before invoking Git. Failed or uncertain adds are
marked `unknown` and reserve their branch; no automatic cleanup or retry occurs.
Hooks are disabled for the command and checkout filters are refused because
local filters can invoke arbitrary external programs. Repositories with
`.gitmodules` are refused with an explicit message; submodule setup is not
silently inferred. No commit, merge, push, delete, reset, or original-checkout
switch is performed.

The service uses argument-vector subprocess calls with `shell=False`, disables
system/global Git configuration, terminal prompts, pagers, LFS smudge, hooks,
and fsmonitor for its commands. It is a selected-project mistake-prevention
boundary, not an OS security sandbox. Git still runs with the current user's
permissions. Existing user-level local Git filters are refused for worktree
creation; inspection is read-only.

## Verification

Ran `.venv\Scripts\python.exe -m pytest tests\test_git_tools.py -q`:
**7 passed, 1 skipped**. All Git fixtures and mutations use temporary repos.
Coverage includes staged/unstaged/untracked status and preservation across a
new worktree, project/conversation registration, scoped diffs, path traversal,
private `.git` refusal, diff bounds, invalid/occupied branches, invalid base,
non-Git folders, and journaled uncertain adds. Symlink escape coverage is
skipped when the Windows host does not permit creating a symlink.

## Git references

- [`git worktree`](https://git-scm.com/docs/git-worktree): linked worktrees and
  the `worktree add`/porcelain listing behavior used here.
- [`git check-ref-format`](https://git-scm.com/docs/git-check-ref-format):
  branch/ref name validation.
- [`git rev-parse`](https://git-scm.com/docs/git-rev-parse):
  `--verify --end-of-options` commit resolution.

## Remaining integration checks

This is the standalone module and fixture suite only. Root integration should
still verify admission policy, checkpoint/task ownership binding, UI review of
worktree metadata, and behavior from Muse in a disposable project/chat. Windows
junction escape prevention is implemented but the current fixture suite only
exercises symlink creation when available; it does not create or inspect a live
user worktree.
