# Security notes

## Pairing state is a credential

MuseBridge's pairing identity and tokens grant access to the owner's Muse
account from the paired PC. Treat them accordingly:

- On Windows, identity material is protected with Windows DPAPI in the user
  profile. Harness databases in the profile are not separately encrypted;
  keep the profile private.
- Never commit pairing or identity files. This repository's `.gitignore`
  excludes them, along with private keys (`*.pem`), `.env` files, and local
  runtime state (`.local/`).
- The upstream SDK checkout under `vendor/muse-gadget-sdk/` is deliberately
  not part of this repository: among other reasons, it contains a device
  signing key. Fetch it from upstream at the pinned revision instead.

## The ESP32 board

The provisioning firmware (`firmware/serial-ble/`) contains no token and no
Wi-Fi credentials, by design. Pairing cryptography runs in the official SDK
on the PC. The board holds nothing worth extracting.

## Operating the harness

- Desktop control is behind an explicit profile and must refuse to inject
  input while the owner is actively using the PC.
- Hook commands and MCP servers run as the Windows user. Review imported
  hook/plugin JSON before enabling it; imports start disabled.
- Project selection is a work scope, not an OS security sandbox. Review
  shell commands, grants and terminal input accordingly.

## Reporting

This is a personal project published for reference. Security reports are
welcome via GitHub issues; please do not include live tokens, pairing data
or private logs in a public issue.
