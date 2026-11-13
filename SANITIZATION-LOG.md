# Sanitization log

This repository is a public copy of a private working tree, reviewed before
publication. This file records the classes of change made, not the values
removed.

## Included

- Application source (`musebridge/`), tests (`tests/`), development tools
  and the evaluation suite (`tools/`), ESP32 provisioning firmware
  (`firmware/serial-ble/`, build outputs excluded), design documents
  (`docs/`, plus the root planning documents), dated acceptance receipts
  (`evidence/`), dependency lockfile, and the board photograph.

## Excluded entirely

- The upstream Muse Gadget SDK checkout (`vendor/muse-gadget-sdk/`): a
  large separate project under its own license that also contains a device
  signing key. `vendor/PINNED.md` records the exact upstream revision to
  fetch.
- Local runtime state (`.local/`), the owner's PC workspace folder,
  virtual environments, test caches, firmware build outputs (`.pio/`).

## Replaced with generic terms

- The owner's name (kept only in the LICENSE copyright line) and the names
  of the owner's AI assistants and internal model seats, replaced in prose
  with role descriptions ("the owner", "the Muse agent", "the
  implementation agent", "a helper model"). Development-history documents
  are otherwise preserved as written, including dates and work-item codes.
- Personal account addresses, the home network name, private IP addresses
  and the paired device's identifier suffix.
- Local absolute paths in test fixtures (a fictional example user).
- Conversation and run identifiers in evidence tables and in one probe
  script, replaced with placeholders.
- A synthetic form-fixture name in the evaluation tasks.

## Checks run before publication

- Automated secret scanning (gitleaks) over the full tree: no findings.
- Denylist greps for names, addresses, device and account identifiers and
  internal terms: no hits outside the LICENSE.
- The board photograph was re-encoded to strip EXIF metadata (the original
  carried a phone model and timestamp).
- Python sources byte-compile clean after edits.

Fixture strings inside tests (for example a value that looks like a token,
used to verify redaction) are synthetic test data, reviewed individually.
