# MuseBridge PC Harness

A design and build guide for giving a Muse personal agent a fast, direct
harness on a Windows PC: one persistent paired connection, an executor
running in the user's own Windows session, and none of the relay layers
that make remote PC work feel slow.

The centerpiece is a workaround for a three-sided problem:

1. The open Muse Gadget SDK ships an ESP32 SDK and a Linux SDK. There is
   no official Windows SDK.
2. Gadget pairing is Bluetooth-only. The pairing code has no USB, serial,
   or network pairing transport.
3. The target PC had no Bluetooth adapter at all.

The workaround: plug an ESP32-S3 board into the PC over USB and let it act
as a dumb Bluetooth radio for the Linux gadget client running under WSL2.
The board advertises and forwards raw BLE packets over a serial link. All
pairing protocol and cryptography stays in the Linux client, which was
already written transport-agnostic. The PC gets a paired Muse device, and
nobody had to buy a dongle, fight a Bluetooth stack inside WSL, or wait
for a Windows SDK.

Full step-by-step instructions are in
[docs/ESP32-BLE-FRONTEND.md](docs/ESP32-BLE-FRONTEND.md).

## Why a harness at all

Before this design, reaching the PC meant a relay stack: cloud VM, egress
proxy, VPN tunnel, SSH into session 0, a scheduled task pickup, a helper
process in the interactive session, a result written to a file, and a
separate pull back. Measured on the original setup:

- A read-only idle check: about 5.5 seconds
- A trivial SSH command: about 3 seconds
- A full desktop UI action: 5 to 15 seconds, plus verification round trips

Every action paid the whole stack before any work happened. Optimizing one
step does not fix that feel. Removing layers does.

The target architecture is the gadget model: the PC holds one outbound
encrypted session open to Muse. Messages and commands flow down that
session with sub-second dispatch. Execution happens in a process already
running in the user's interactive session, so there is no session split,
no SSH handshake per action, no scheduler in the middle, and no polling.
From the user's side it should feel like a local app, not a website tab
and not a terminal that needs babysitting.

## What a paired gadget gives you

Once a machine is paired as a Muse gadget, the agent can use the client's
built-in commands on that machine:

- `system.run`: run a shell command and return output and exit code
- `file.read` and `file.write`: read and write files
- `device.health`: report device state

Programs on the machine can also talk back. The Linux client can send a
message to the agent, and its chat command opens a live terminal
conversation with the agent from the PC itself. Custom commands are JSON
definitions dropped into a commands folder; they appear next to the
built-ins. The model still runs on Meta's side. The hands are local, the
brain is not, and no route in this repository changes that.

## The four avenues

The game plan researched four ways to establish the connection. They are
documented in full in [docs/GAMEPLAN.md](docs/GAMEPLAN.md).

1. **USB Bluetooth dongle into WSL2.** A proven chipset passed into a
   fresh WSL2 Ubuntu, then the stock Linux gadget client. Every layer is
   proven somewhere; the full stack is plausible but was not the route
   chosen, because the parts were already on the desk for avenue 3.
2. **Donor transplant.** Pair on another Linux machine with Bluetooth,
   then copy the two pairing state files into the WSL2 install. The
   pattern is proven in community projects, but it needs a suitable donor
   machine, and phone apps do not export pairing state in the Linux
   client's format. Not viable with the hardware on hand.
3. **Microcontroller boards.** Two sub-routes:
   - **3a, direct gadget.** A supported ESP32-family board pairs directly
     and becomes a gadget in its own right: push-to-talk dictation to the
     agent, status cards on display boards, and a physical button that
     confirms pairing. It is the harness's physical front end, not PC
     control.
   - **3b, ESP32 as a serial BLE front end.** The workaround this repo is
     built around, described above and in
     [docs/ESP32-BLE-FRONTEND.md](docs/ESP32-BLE-FRONTEND.md).
4. **No pairing.** Modernize the existing SSH and UI-automation route into
   a tidy executor with formal task contracts. It works with no new
   hardware and no pairing, but the PC never becomes a paired device, and
   the relay latency stays. Useful as a baseline and a fallback.

## Repository map

- `README.md`: this overview
- `docs/ESP32-BLE-FRONTEND.md`: the ESP32 Bluetooth workaround, end to end:
  hardware, architecture, firmware side, host side, WSL2 setup, pairing
  session, verification gate, negative tests, troubleshooting, fallbacks
- `docs/GAMEPLAN.md`: the full game plan for all four avenues, with the
  acceptance bar any route has to meet
- `docs/INVENTORY.md`: the hardware this was designed against, and the
  board identification notes for the ESP32-S3 used in the workaround
- `docs/board-1-esp32-s3.jpg`: the reference board (ESP32-S3-WROOM-1,
  N16R8), photo metadata stripped
- `SECURITY.md`: pairing state handling, token handling, and scope notes
- `SANITIZATION-LOG.md`: how this public copy was produced from internal
  working notes, by class of change only
- `LICENSE`: MIT

## Current state, plainly

This repository is a design and build guide, not a finished software
release. The research, the hardware identification, the transport design,
and the build steps are complete and checked against the upstream SDK
source. The serial transport itself is original work to build, sized in
the guide at roughly 120 to 180 lines of Python on the host side and 400
to 700 lines of C on the board side. Nothing in this repository claims
that build is done. If you build it, the verification gate and the
negative tests in the workaround guide are the bar to call it working.

## Quick start: which route is yours

- Your PC has working Bluetooth and runs Linux or WSL2: install the
  upstream Linux gadget client and pair normally. You do not need the
  workaround.
- Your PC has no Bluetooth, and you have a supported ESP32 board: use
  [docs/ESP32-BLE-FRONTEND.md](docs/ESP32-BLE-FRONTEND.md).
- Your PC has no Bluetooth and no board: a USB Bluetooth dongle passed
  into WSL2 (avenue 1 in the game plan) is the most trodden path.
- You cannot pair at all: avenue 4 gives you a working executor over SSH
  and UI automation today, at relay latency.

## Upstream

The Muse Gadget SDK is Meta's open framework, at
github.com/facebookincubator/muse-gadget-sdk, under the Apache License
2.0. This repository contains none of its code. It documents how to put
its Linux client to work on a Windows PC it was not packaged for, using
the transport seam its own pairing code provides. ESP-IDF, NimBLE, WSL2,
and usbipd-win are other projects' work, credited in the guide where they
appear.

This is an unofficial, independent project. It is not affiliated with or
endorsed by Meta.

## License

MIT. See `LICENSE`. The copyright holder's name appears in the license,
as intended, and nowhere else in this repository.
