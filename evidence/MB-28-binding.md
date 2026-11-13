# MB-28 binding and response boundary

2026-11-12. Implementation receipt. Full integrated suite: 396 passed, 5 skipped
in 90.03 seconds. Helper/probe-focused suite: 27 passed. Policy/hook integration
also verifies that generated operation IDs do not restart identical pre-checks.

Ordinary remote calls no longer require run/revision/retry bookkeeping from the
model. The callback copies arguments, resolves an explicit conversation or the
unique active conversation, validates the independent selected-chat boundary,
and admits the operation under current local policy. Explicit legacy run and
revision fields still validate. Optional retry keys keep their old meaning;
omitted keys receive new operation UUIDs. Historical run.calls inspection uses
history_run in registration so it cannot be confused with execution attribution.

Safety checks cover no-ID Stop Work refusal, profile changes on the next call,
different-project conversations, callback/chat mismatch, queued revocation,
legacy stale runs/revisions, and no change to caller-owned argument dictionaries.
Rejected calls with a resolved conversation are counted in the local journal.

Conservative refinement to the plan's local-run fallback: remote mutations with
no active local request are refused. Only local UI operations can create a
standalone mutation run. Review showed that otherwise a delayed callback after
Stop/Resume could silently become a new run. This changes no owner rule.

The pinned SDK callback supplies command, parameters and timeout, without a
trusted remote-turn identifier. An ID-free invocation cannot prove which remote
turn originated it if an earlier turn is replaced while still running. Scope,
current-policy and Stop gates remain enforced. Explicit legacy IDs retain their
stale-turn checks. Parallel/overlapping turn lifecycle work remains MB-36; this
receipt does not claim cryptographic or provider-level turn attribution.

The response boundary removes internal call/execution/lifecycle envelopes,
retains journal data locally, and measures the actual shaped wire bytes. Error
responses always include actionable hints with private paths/credential patterns
redacted. Three identical consecutive failures in one run add a different-approach
hint; only a SHA-256 signature and count are kept in bounded memory, not arguments.
Large output keeps marked beginning/end previews, whole list records and stable
handles where possible; encoded JSON escaping counts toward 24,000 bytes. Image
base64 is omitted whole with image-turn guidance rather than returned damaged.

Capability discovery now lists all tool effects/availability without repeating
the already-registered full schemas. This prevents the old 59-schema response
from consuming the budget and discarding substantive workflow/policy metadata.
Surface consolidation and contract version 2 remain MB-29. App remains 0.6.1.
