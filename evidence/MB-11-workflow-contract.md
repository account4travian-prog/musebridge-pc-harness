# MB-11 Windows workflow contract — 2026-11-11

Implemented versioned discovery in `musebridge/capabilities.py`, registered `bridge.capabilities` and enhanced `bridge.context`. The contract declares Windows path/shell semantics, project/conversation, execution profile/revision, active run, schemas, current availability, practical output/job limits and result semantics. Existing skills/workflows stay on Muse's VM. The local app has no new skills/model runtime.

Local tests exercise allowed/denied effects, capability truthfulness, profile persistence, stale revisions, cross-chat/run refusal and registration requirements. A live disposable probe registered the new schemas on the existing gadget identity and actual Muse invoked them successfully.

Live receipt: `.local/expansion-probe-1794394606/report.json`. Muse called capabilities/context, wrote/read/edited PC files, ran a PowerShell check with exit 0 and called change.list. Independent checks found the expected file content and four tracked edits. Muse reported `local_delegated_verification (Delegated Verification)` in the test's workflow-used.txt. This is a reported existing workflow choice, not an independently captured VM execution trace or an audit of its private skill contents. No SSH/VPN, new firmware or local agent model was introduced.

See [workflow guide](../docs/WINDOWS-WORKFLOW-CONTRACT.md) and [owning plan](../MASTERPLAN.md). Additional VM-specific workflows should be tested as used; this receipt does not establish compatibility with every procedure or Bash script.
