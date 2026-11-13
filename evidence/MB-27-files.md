# MB-27 file tools v2

2026-11-12. Implementation and independent local checks; live P-3 results are
recorded separately in `MB-27-edit-probes.json` and `MB-27-edit-probes.md`.

The file tools now show numbered text pages (400 lines by default), preserve
UTF-8/UTF-16 BOMs and original line endings, and keep file-version receipts per
conversation and canonical project root. Receipts survive restart through an
additive schema-3 migration. A copied schema-2 database retained its draft and
passed integrity checking; an injected migration failure rolled back completely.
Hashes and base64 are absent from the new read/edit results. A legacy supplied
hash is still validated, but cannot replace this chat's read receipt.

Exact and inclusive line-range edits share one in-memory multi-edit transaction
and one guarded atomic publication/checkpoint. Failed later edits leave the file
and checkpoint history untouched. Exact matching normalizes line endings; a
unique trailing-whitespace fallback is reported, while misses show numbered
nearest text with visible whitespace. Successful results use final line numbers.
The edit/checkpoint limit is 4 MiB. Large text reads instead stream bounded chunks
and retain bounded line previews while hashing all observed bytes. Binary reads
return explicit image/document guidance. Image inspection still needs the planned
screen.look surface; its reference is guidance, not a claim that MB-35 exists.

New text writes create parent folders and use LF, with CRLF for PowerShell/batch
scripts. Existing files require a current read or explicit overwrite. Checkpoints
restore the file only: new parent folders are retained, which the response states
along with the number created. Their creation is separately journaled. Shell,
external and directory effects are not represented as reversible file snapshots.

Ignore-aware tree/search tools use bounded traversal, nested .gitignore handling,
whole result records, and actionable narrowing hints. Ripgrep is preferred; the
Python fallback uses operation deadlines, regex timeouts and entry/file caps.
Private paths remain guarded. Capped scans report unknown omissions honestly.
Escaped Unicode/control characters and path metadata count toward encoded output
limits; shortened display paths are explicitly labeled as previews.

Local checks include per-chat/per-root isolation, restart receipts, owner edits,
UTF-16 BOM/CRLF multi-edits, range insertion, two-megabyte edit and restore,
five-megabyte streaming read with refused oversized edit, binary guidance,
private/.git/outside write refusal, long display paths, ignore/search limits,
malformed codecs, adversarial regex deadlines, and uncertain outcomes after a
publication succeeds but a later hook raises. The inherited injection test now
targets the actual atomic-publication boundary; UI restore no longer expects a
hash returned to the model.

MB-28 still owns global response envelopes, error normalization, removal of
model-supplied bookkeeping, and the universal 24,000-byte budget. MB-29 owns
registration consolidation and contract version 2. Runtime version remains 0.6.1.

Validation: the complete MB-27 suite passed with 365 passed, 5 skipped in
91.89 seconds. The next-stage, not-yet-integrated MB-28 binding/result test files
were excluded explicitly; no MB-27 tests were excluded. Focused new file, stream
and index checks passed 44 tests with 1 skip. No destructive real-profile test,
desktop input or visible terminal was used.

Live P-3: exact replacement and line-range modes each completed all three
requested edits with zero failed edit calls, zero substitute tools and exact
final CRLF/tab bytes. Both settled as remote_finished. Retain exact mode first
on the tie and keep both modes available. Source checkpoint: `9787bce`.
