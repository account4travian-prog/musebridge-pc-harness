# Security notes

This project is about giving an AI agent a live, authenticated channel into
a personal computer. That is a powerful shape, so the security notes are
part of the design, not an afterthought.

## Pairing state is a credential

The Muse Gadget pairing produces two small state files on the Linux side
(`identity.json` and `pairing.json` in the gadget client's data directory).
Whoever holds those files can present as the paired device. Treat them like
a private key:

- Never commit them to a repository (the shipped `.gitignore` excludes them).
- Never copy them to a second live machine. One identity belongs on one
  device; if the state is moved, the old copy must stay off and be retired.
- If they are ever exposed, unpair the device in the Muse app and pair
  again from scratch so fresh state is issued.

## SDK tokens

Pairing starts from a token issued at gadgets.muse.ai. The token belongs to
the person pairing the device. Do not share it, post it, or store it in this
repository, in screenshots, or in issue reports.

## Physical confirmation

The ESP32 side of the workaround keeps the upstream gadget SDK's physical
confirm policy: a person presses the board's button to approve a pairing.
Keep that step. It is the difference between "a radio that can be paired"
and "a radio only its owner can pair".

## Scope of access on the PC

A paired Linux gadget client runs commands as the account that installed
it. Install it under an account whose access you are comfortable giving the
channel, keep the custom command set as small as the work allows, and review
custom command definitions before dropping them into the commands folder:
they execute locally with no further prompt.

## Reporting

If you find a problem in these documents, such as a step that would expose
pairing state or a command broader than its description, open an issue
describing the step and the risk. Do not include tokens, pairing files, or
personal network details in the report.
