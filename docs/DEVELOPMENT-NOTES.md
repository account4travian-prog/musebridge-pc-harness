# MuseBridge — Windows companion and local tools for Muse

## Current entry point — 2026-11-16

**The Muse agent companion:** quiet transparent idle, hover chat/voice/activity controls,
and a compact Main composer with attachments. Full Main history opens separately;
the harness keeps dedicated side conversations. Voice controls explicitly choose
a dedicated chat. Application -> Owner checks prepares Notepad/voice acceptance.
[Current checks and limits](evidence/MB-37b-compact-and-owner-checks.md).

**Current candidate: 0.8.0, contract 2, catalog schema 5.** The [upgrade plan](HARNESS-UPGRADE-PLAN.md) remains the backlog authority. Finish-line/stall feedback, private shell-change recovery, browser/desktop references, OS OCR, resized/labelled/cropped machine images and concurrent project chats are implemented. The [closeout ledger](evidence/HARNESS-1.0.md) records local checks, live evaluations and remaining acceptance gates. See [remaining work](docs/REMAINING-WORK-2026-11-13.md) and the [running guide](RUNNING.md).

Start with [MASTERPLAN.md](MASTERPLAN.md) for architecture and the current handoff. MuseBridge 0.6.0 includes project continuity/handoffs, a guarded request queue, Git/worktree tools, interactive PowerShell terminals, local hooks, MCP stdio integrations, and corresponding interface pages. See [RUNNING.md](RUNNING.md) and the MB-16–21 evidence below.

The harness extends Muse's existing remote skills/workflows with Windows execution and evidence adapters. Muse remains remote; there is no local model. The existing ESP32 pairing is retained: no re-pair or firmware reflash is needed for this expansion. Stable profile discovery supports an existing cached MSIX profile. Credentials and raw local paths are not included in evidence.

Status: functional development candidate; 1.0 release acceptance is held for the owner's real desktop, voice and everyday-use checks. The owner is now ready; the [prepared walkthrough](docs/OWNER-ACCEPTANCE-2026-11-16.md) and disposable test session are available. Earlier feature receipts retain their dated scope. Main commands work, but shared-Main completion cannot safely drive automatic continuation; use a dedicated project chat.

**New in 0.6:** bounded PDF/DOCX text reading, owner-enabled automatic screenshot continuations, before/after-tool hooks with disabled-by-default plugin import, a VT terminal screen and silent desktop notifications. See [the five-capability receipt](evidence/FIVE-CAPABILITIES-0.6.md) for tests, live proof and limits. Overview retains its work dashboard and in-app indicators.
