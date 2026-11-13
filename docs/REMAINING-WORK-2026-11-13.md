# Remaining work assessment - November 13, 2026

Authority: [HARNESS-UPGRADE-PLAN.md](../HARNESS-UPGRADE-PLAN.md). This assessment describes that backlog; it does not create a second implementation track.

The execution foundation is working. MB-24 through MB-31b are complete. The 0.7.0 context milestone passed 12/12 tasks versus the baseline's 9/12, with 139 tool calls versus 206. Context overhead fell from 35,429 to 6,543 bytes across fourteen sends. The latest finish-line checks and local-suite counts are recorded in [MB-31b evidence](../evidence/MB-31b-finish-line.md). Live Main-chat context and hostname execution now work in the owner-selected PC workspace. A separate live negative check confirms Muse receives readable workspace errors.

The major work remaining is stall feedback, broader file recovery, richer browser/desktop control, and concurrent chat routing. Owner skill confirmation and the header-memory probe are complete. Edit diagnostics, optional after-edit checks and finish-line verification feedback are complete. The next implementation item is MB-31c; seven plan items remain.

| Order | Remaining work | Practical benefit and acceptance |
|---|---|---|
| Completed: MB-30 | Skill confirmation, P-5, `v2-context` and 0.7.0 | All gates passed; first header 527 bytes and thirteen unchanged follow-ups. Twelve-task success retained. |
| Completed: MB-31a | Syntax feedback and optional owner-configured after-edit checks | Tested publication, warning output, Stop responsiveness, timing/size limits and bounded glob matching. |
| Completed: MB-31b | Revision-bound changed-file feedback and one opt-in queued finish-line prompt | Local tests cover exactly-once continuation, failed/old checks, holds/restart, late revocation, chat/workspace isolation and five-round goals. Shell-only change discovery remains MB-32. |
| 2: MB-31c | Repeat and stall hints | Help Muse recognize attempts that make no progress, without blocking its tools. |
| 3: MB-32 | Separate shadow-Git snapshots around runs | Review and restore project files changed through shell tools as well as file tools. Preserve the project's real Git data and refuse restore after later owner edits. Test large-tree performance and missing-Git fallback. |
| 4: MB-33 | Browser element references, stale-reference refusal, richer actions and snapshot diffs | More reliable multi-step browser work in the owned headless browser. Verify local form interactions, large pages and reference invalidation. Signed-in personal browsers remain outside this plan. |
| 5: MB-34/35 | Desktop references/actions/diffs; OCR fallback if supported; resized screenshots, reference marks and crops | Improve visible Windows work and visual grounding. Test owned windows, user activity, elevation, multiple DPI settings, 4K capture and image delivery; then an owner-observed Notepad task and `v2-eyes` evaluation for 0.8.0. |
| 6: MB-36a | Live subscriptions/routing for up to three project chats plus Main; per-project write ownership | Allow reading one chat while another keeps working. Highest routing risk: test cross-chat isolation and Stop, then a real two-chat check on one identity. The plan allows parking this feature if two attempts are unstable. |
| 7: MB-36b | Final evaluation, dependency/test checks, documentation/skill refresh and acceptance receipt | Establish a reviewable 1.0 release with measured results and explicit remaining limits. Remove compatibility names only when usage evidence supports doing so. |

## Current limitation discovered by owner testing

Main's shared chat stream currently supplies completed replies and task status without request-parent linkage. Its command execution is verified, but the harness cannot safely certify which Main request finished. It preserves that uncertainty. Dedicated workspace chats support attributed completion and are the current route for automatic queue/goal continuation. Parallel-chat work must retain this distinction; it cannot treat an unrelated phone/web reply as proof of local completion.

## Owner involvement

The owner has confirmed VM skill version 1. A new Teach confirmation is needed only when that skill version changes. The immediate development step is MB-31c stall hints. Finish-line prompts are off by default; configure them through Conversation actions -> Finish-line verification. Held queues must be explicitly resumed.

Later acceptance needs one real desktop task, a spoken request with tool use including interruption/hang-up, and ordinary day-to-day use. These prove different things from automated fixtures. No ESP32 reflash or re-pair is required for the current work.

The two highest-risk engineering items are shadow snapshots/restores and concurrent chat routing. Desktop input also needs real DPI/monitor acceptance. The code foundation and fixture score support continued development, while those stages still need implementation and evidence before calling the whole upgrade complete.
