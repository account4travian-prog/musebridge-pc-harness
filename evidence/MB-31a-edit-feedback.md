# MB-31a edit diagnostics and optional checks

Implementation commit: `91996d2`. Fast syntax feedback after guarded file publication, and an owner-configured check using the existing after-file-edit hook/job machinery. App version remains 0.7.0, contract 2. The latest frozen Muse evaluation is the MB-30 `v2-context` run; this feature's evidence is local unit/integration/native startup testing.

## Behavior

Broken Python, JSON, TOML, XML and optional YAML return concise line/source/message warnings. PowerShell source goes to its parser on stdin through a fixed script; it is never executed by the diagnostic pass. HTML is not checked. Syntax problems do not reject the edit. A repaired file stops returning the parse warning. A missing warning does not certify syntax or goal success; use the project check.

In-process diagnostics skip files over 64 KiB, with YAML limited to 16 KiB. Dense 64 KiB YAML exceeded 500 ms during testing, so the smaller limit was selected; measured dense 16 KiB mapping/sequence checks took about 200/141 ms. Four-MiB input is skipped within the feedback budget. XML with entity/DOCTYPE declarations is skipped. PowerShell gets a 300 ms deadline and up to 50 ms kill cleanup; OS process creation cannot itself be preempted. Actual Windows PowerShell startup exceeded the parser deadline in the local probe, so it returned no warning; that is a tested skip, not proof of successful PowerShell syntax validation.

Conversation actions -> Checks after file edits opens owner-only command/glob/enabled settings. These are off by default, shared by project, and not configurable through remote tools or repository instructions. Saving settings does not execute the command. The reserved after-edit hook has a fixed ten-second job timeout. It runs once per admitted edit/hook key, matches project-relative paths, and reuses the existing owned JobManager/Stop policy. Failed checks expose only their last twenty lines, bounded to 8,000 characters; they leave the saved edit successful. Other configured hooks retain their behavior. The reserved hook cannot be overwritten through the generic hook editor.

## Correctness repairs made during integration

- Publication holds the execution/workspace mutation gates; parsing and check waiting release them. Stop remains responsive during a check.
- The original admission guard is rechecked under the gate before after-edit hook admission. Stop followed by Resume during parsing does not admit a late command from the retired request.
- Expected denial before publication is definitive, not an uncertain file mutation. Post-publication feedback failure returns a saved-file warning rather than turning a known publication into an unknown/replay candidate. Uncertain publication/checkpoint failures retain their existing conservative semantics.
- Accepted absolute paths inside the selected workspace are normalized before receipts/checkpoints/hooks. A regression reproduced a saved file followed by failed receipt tracking before this normalization. Absolute and relative forms now identify the same guarded file, with project-relative check filtering.
- Adversarial glob matching was demonstrated to exceed a two-second child-process bound. Matching now uses a 128-entry cache and a 20 ms timeout. A timeout explicitly skips that hook and advises simplifying the glob, while unrelated hooks can still run.

## Validation

Local tests cover broken/valid source, BOM/UTF-16, parser absence/deadline/size limits, actual failed PowerShell check output, glob filtering/deduplication, hidden reserved settings, saved edits after feedback failure, Stop responsiveness, late-hook revocation, and absolute-path receipt/check consistency. Final integrated suite: **538 passed, 5 skipped** in 120.46 seconds. The final absolute-path normalization regression failed before the fix and passed afterward, including read/edit/check consistency; the full suite includes it.

The 0.7.0 release snapshot `27181dc` separately passed native secondary-screen startup (exit 0, stderr 0) and 32 release-focused tests. The MB-31a working build also passed native secondary-screen isolated-profile startup (exit 0, stderr 0). No real owner project check was enabled during these tests.
