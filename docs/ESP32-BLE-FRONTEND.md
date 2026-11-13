# The ESP32 Bluetooth front end: a workaround guide

How to give a Windows PC a paired Muse gadget connection when the PC has
no Bluetooth adapter, no Windows gadget SDK exists, and gadget pairing
only happens over Bluetooth. The trick is to stop asking the PC to do
Bluetooth at all. A small ESP32 board plugged into a USB port does the
radio work, and the Linux gadget client running under WSL2 does
everything else.

This guide is written to be followed end to end. It also records why each
simpler idea does not work, so nobody has to rediscover the dead ends.

## Contents

1. The three blockers
2. The idea in one paragraph
3. Hardware
4. Architecture
5. Step 0: read the upstream source first
6. Step 1: prepare WSL2
7. Step 2: build the ESP32 firmware
8. Step 3: build the host serial transport
9. Step 4: the pairing session
10. Step 5: the verification gate
11. Negative tests (run these before you trust it)
12. Day-two operations
13. Troubleshooting
14. Fallbacks and related routes
15. Security rules that are part of the build

## 1. The three blockers

**No Windows SDK.** The Muse Gadget SDK (upstream:
github.com/facebookincubator/muse-gadget-sdk, Apache License 2.0) ships
two targets: an ESP32 SDK and a Linux SDK. There is no Windows target.
A Windows PC cannot pair as a gadget with stock tooling.

**Pairing is Bluetooth-only.** In the Linux client's source, the pairing
module's own description is: pair this device with the Muse app over
Bluetooth and save the pairing. There is no USB, serial, adb, or network
pairing transport in the code. The phone app discovers the gadget by
scanning for its advertised Bluetooth name and service.

**The PC may have no Bluetooth.** The machine this was designed against
reported no Bluetooth adapter at all. That one fact rules out pairing
in place, and it is common on desktops.

Any working route has to answer all three at once.

## 2. The idea in one paragraph

The Linux client's pairing code is split cleanly. All of the protocol
logic (framing, the ECDH, HKDF, and AES-GCM pairing cryptography, and the
saved pairing state) lives on the host and is transport-agnostic. BlueZ,
the Linux Bluetooth stack, sits behind a small transport interface with
three methods. Replace that one transport with a serial transport: an
ESP32 board on USB runs a stripped-down BLE peripheral firmware, forked
from the SDK's own ESP32 BLE server code, and forwards raw packets
between the Bluetooth link and the USB serial link. The WSL2 client does
the rest. WSL2 sees a serial device, which its stock kernel handles
without any Bluetooth stack, kernel rebuild, or firmware blobs. The
board becomes the client's Bluetooth front end.

Why this beats the obvious alternatives:

- **No Bluetooth stack inside WSL2.** The dongle route needs the WSL
  kernel to carry Bluetooth modules, a USB passthrough that has to be
  re-attached after every WSL shutdown, and a vendor firmware fix for
  the proven dongle chipset. The serial route needs none of that.
- **No purchase if a board is on hand.** Any supported ESP32-family
  board with Bluetooth LE qualifies.
- **The radio code is upstream's own.** The board side starts from the
  ESP32 SDK's BLE server, which already negotiates with the same phone
  app successfully. The new code is the forwarder, not a new stack.

## 3. Hardware

### The reference board

The board this design was validated against:

- Module: Espressif ESP32-S3-WROOM-1, variant N16R8
- Memory: 16 MB flash, 8 MB PSRAM
- Board form: generic ESP32-S3 DevKitC-1 style board, dual USB-C
  (one native USB port, one USB-to-UART port through a CH343 chip),
  BOOT and RESET buttons, onboard RGB LED, full pin headers
- Radios: Wi-Fi 4 (2.4 GHz) and Bluetooth 5.0 LE
- Photo: see `board-1-esp32-s3.jpg` in this folder

The PSRAM tier matters if the same board is later used as a direct
gadget (avenue 3a in the game plan): richer gadget features, including
the home-network tunnel class, want PSRAM. For the front-end job alone,
PSRAM is comfortable headroom rather than a requirement.

### Which boards qualify

- ESP32-S3, ESP32-C3, ESP32-C6, ESP32-C5, and classic ESP32: fine.
- ESP32-H2: has Bluetooth LE but no Wi-Fi. Usable as a radio, limited
  as a direct gadget.
- ESP32-S2: no Bluetooth radio at all. Unsupported, full stop.

### Which boards to prefer, and why

Use the board's native USB port for the serial link where one exists
(the S3 has one). Native USB shows up in WSL2 as a clean `/dev/ttyACM*`
device. A UART-bridge port (CH343, CP210x, CH340) also works; it shows
up as `/dev/ttyUSB*` and needs the matching USB serial driver on the
Windows side before passthrough.

### What does not work

- **Add-on Bluetooth modules (HM-10 class).** They expose a fixed vendor
  GATT service and cannot advertise the custom service UUID and device
  name the Muse app scans for. They cannot pair, and no firmware trick
  on the host side changes that.
- **Asking the phone to be the radio.** Android gadget apps keep pairing
  state in private app storage and the system keystore, with no export
  in the Linux client's format. A phone can be a gadget in its own
  right; it is not a supported radio donor for a PC.

## 4. Architecture

```text
  Muse phone app
       |
       |  Bluetooth LE (advertising, GATT)
       v
  ESP32-S3 board  ---- stripped BLE peripheral firmware
       |                (advertise, connect, forward; no protocol logic)
       |
       |  USB serial (native USB preferred)
       v
  Windows host  ---- usbipd-win attaches the USB device to WSL2
       |
       v
  WSL2 Ubuntu  ---- Linux gadget client (upstream, unmodified protocol)
       |                SerialTransport replaces the BlueZ transport
       |                pairing state saved under /var/lib/musegadget/
       v
  Built-in commands: system.run, file.read, file.write, device.health
  Custom commands: JSON definitions in the client's commands folder
```

Responsibility split, stated once and kept everywhere:

- The **board** is a radio and a pipe. It advertises the right name and
  service, holds the BLE connection, negotiates MTU, and forwards bytes
  in both directions. It makes no protocol decisions.
- The **host client** is the gadget. It owns identity, pairing
  cryptography, saved state, command definitions, and execution.
- The **phone app** only ever sees a normal Bluetooth gadget. Nothing
  about the app or the pairing ceremony changes.

## 5. Step 0: read the upstream source first

Clone the upstream SDK and read two areas before writing anything:

- The Linux client's pairing module: confirm the transport interface
  (three methods) and where the transport is selected in the pairing
  entry point. The host side of this workaround is a new class next to
  the BlueZ transport, plus a switch at that selection point.
- The ESP32 SDK's BLE server source: the board firmware forks its
  advertising, service, and characteristic setup. Do not re-derive the
  GATT layout from packet captures; the app expects what this code
  already produces.

Also note the ESP-IDF version the ESP32 SDK currently pins: v6.0.3.
Secondary write-ups may cite an older pin. Trust the repository.

## 6. Step 1: prepare WSL2

1. Install a fresh Ubuntu 24.04 WSL2 distribution alongside any existing
   distributions. Do not modify an existing distribution to make room
   for this; the gadget install should be the only tenant of its own.
2. Enable systemd in the distribution's `wsl.conf`. The gadget client
   installs as a service.
3. Attach the board's USB device to the distribution with usbipd-win
   (Microsoft's USB device passthrough for WSL). The bind persists;
   the attach does not. See day-two operations below.
4. Confirm the serial device exists inside the distribution:
   `/dev/ttyACM*` for native USB, `/dev/ttyUSB*` for a UART bridge.
   A stock WSL2 kernel handles both. No Bluetooth probe is needed,
   because no Bluetooth stack is involved on this side of the cable.
5. Run the upstream Linux gadget installer. It degrades gracefully
   under WSL2 and sets up the client, its data directory, and its
   service. Pairing state will live under `/var/lib/musegadget/` as
   `identity.json` and `pairing.json`. Read `SECURITY.md` in this
   repository before those files exist: they are credentials.

## 7. Step 2: build the ESP32 firmware

Starting point: the ESP32 SDK's BLE server code. The firmware's job
list is short:

1. Advertise exactly as the stock gadget does: same device name pattern
   and same custom service, so the phone app's scan finds it.
2. Accept a connection and negotiate the largest MTU the app offers.
   The host protocol is sensitive to notification pacing and MTU;
   the stock ESP32 stack already negotiates with this app, which is
   the strongest reason to fork it rather than start from a generic
   BLE example.
3. Forward bytes both ways between the GATT characteristics and the
   USB serial port, preserving message boundaries as framed by the
   host protocol. The forwarder is a pipe: no parsing beyond framing,
   no state beyond the connection.
4. Keep the physical confirm policy: pairing requires a button press
   on the board. This is a security feature of the stock design, and
   the forwarder keeps it.
5. Drive the onboard LED from link state (advertising, connected,
   paired) so the board's state is readable at a glance.

Size estimate from the design pass: 400 to 700 lines of C, most of it
adapted from the SDK's own server code, built with ESP-IDF at the
version the SDK pins (v6.0.3). Flashing options:

- The upstream web flasher (Chrome or Edge, Web Serial) covers about
  60 named boards and needs no toolchain install. A generic clone
  board may not be on its list.
- The ESP-IDF route covers any board, including clones. Use it when
  the flasher is picky, and for your own forked firmware in any case:
  the forwarder firmware is yours, so you will build it yourself.

## 8. Step 3: build the host serial transport

The host change is deliberately small:

1. Implement the client's existing transport interface against a
   serial device instead of BlueZ: open, read, and write, honoring
   the same framing the BlueZ transport presents to the protocol
   layer. Nothing above the interface changes.
2. Add a selection switch at the pairing entry point so the client
   can be told to pair over the serial transport (an environment
   variable or a command-line flag; either is fine, one is enough).
3. Write it against the repository's existing fake-transport tests
   first. The protocol layer can be exercised end to end with no
   hardware, no Bluetooth, and no Muse account. Hardware enters
   only after the fake-transport tests pass.

Size estimate from the design pass: 120 to 180 lines of Python.

## 9. Step 4: the pairing session

Pairing is a short, two-person ceremony. It is the only step that
needs the phone, the PC, and a person in the same ten minutes.

1. Get an SDK token from gadgets.muse.ai, signed in as the account
   that will own the gadget. The token is a credential. It goes to
   the client (or to the board only in the direct-gadget route), never
   into a repository, a screenshot, or a chat log.
2. On the phone: open the Muse app, go to Settings, then Devices,
   and turn on Developer mode if the gadget flow asks for it.
3. Power the board from the PC's USB port and confirm it is
   advertising (LED state).
4. In WSL2, start pairing with the serial transport selected. The
   client drives the protocol; the board forwards.
5. In the phone app, add the device when its advertised name appears.
6. Press the board's button when prompted, to physically confirm.
7. Let the exchange finish. The client saves `identity.json` and
   `pairing.json` under `/var/lib/musegadget/`. From this moment the
   Bluetooth link is no longer needed for the connection itself;
   pairing state, not the radio ceremony, is what the ongoing
   connection uses. Keep the board attached anyway if you may pair
   again or re-pair after a reset.
8. If pairing fails, stop and read the troubleshooting table before
   retrying. A failed pairing with a bad token must fail loudly. If
   it appears to succeed, something is wrong with the build, not
   with the token.

## 10. Step 5: the verification gate

Do not call it working until all of these pass, in this order:

1. From a Muse conversation, call the new device's `device.health`
   and confirm it answers as the PC gadget.
2. Run a harmless `system.run` command (print the working directory)
   and confirm the output and exit code come back.
3. Write a small test file with `file.write`, read it back with
   `file.read`, and compare contents byte for byte.
4. From the PC, send a live message to the agent with the client's
   message command, and open its chat command for a terminal
   conversation. A reply from the agent, initiated from the PC,
   with no scheduled task and no polling in the path, is the
   acceptance test for the whole harness.
5. Time a trivial command end to end. The point of the exercise is
   feel: dispatch should be sub-second, plus the work's own time.

## 11. Negative tests (run these before you trust it)

A gate that has never refused anything is not a gate.

- **Wrong name.** Change one character of the advertised name in the
  firmware. The phone app's scan must not offer the board as a
  gadget. Restore the name.
- **Bad token.** Attempt pairing with a deliberately invalid token.
  Pairing must fail loudly, and no pairing state files may be
  written.
- **Dropped packets.** Interrupt the serial link mid-pairing (pull
  the cable). The client must error, not hang, and a clean retry
  must succeed.
- **Second live copy.** If pairing state is ever copied elsewhere
  for testing, the original must stay off. Two live copies of one
  identity corrupt each other's sessions; tokens rotate and the
  loser fails confusingly.

## 12. Day-two operations

- **WSL does not stay alive on its own.** A systemd service inside
  the distribution does not keep the distribution running. If the
  gadget should be present around the clock, something on the
  Windows side has to start the distribution at logon and re-attach
  the USB device. That is a persistent connection on a personal PC,
  and it should be an explicit, separate decision by the PC's owner,
  not a default smuggled in by an installer.
- **usbipd attach is per-boot.** After a Windows reboot or a
  `wsl --shutdown`, re-attach the board's USB device before starting
  the client. The bind persists; the attach does not.
- **Re-pairing is cheap.** If pairing state is lost or the board is
  replaced, repeat the pairing session. Nothing else in the setup
  changes.
- **Updates.** The host client is upstream's; update it in its own
  distribution. Your local change is one transport class and one
  switch. Keep them as a small patch or fork so an upstream update
  does not swallow them.

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Phone app never lists the board | Wrong advertised name or service; board not advertising | Compare advertising setup against the SDK's ESP32 server code, byte for byte. Check LED state. |
| Board not in the web flasher's list | Generic clone board | Build and flash with ESP-IDF at the SDK's pinned version instead. |
| No serial device in WSL2 | USB device not attached, or wrong port on a dual-port board | Re-run the usbipd attach. Try the native USB port before the UART port. Confirm the device path (`ttyACM*` vs `ttyUSB*`). |
| Pairing starts, then stalls | MTU or notification pacing mismatch | Confirm the firmware negotiates MTU the way the stock ESP32 server does. Test with the stock direct-gadget firmware on the same board to isolate radio from forwarder. |
| Pairing succeeds with a bad token | The build is not checking what it should | Stop. Do not use the build. Re-check the host transport switch; the protocol layer, unmodified, rejects bad tokens. |
| Gadget vanishes after reboot | Distribution stopped; USB attach lost | Start the distribution and re-attach the USB device. See day-two operations. |
| Works on one board, not another | Board family mismatch | Check the family: ESP32-S2 has no Bluetooth. Classic, S3, C3, C6, C5 are fine. |
| Sessions conflict or drop after state was copied | Two live copies of one identity | Turn the copy off. One identity lives on one device. Re-pair if state has diverged. |

## 14. Fallbacks and related routes

Nothing spent here is wasted if the forwarder fights back:

- The same board works immediately as a **direct gadget** (route 3a in
  the game plan) with stock firmware: dictation, status display, and
  a physical confirm button, while PC control stays on another route.
- The **dongle route** (route 1) reuses the same WSL2 install and the
  same verification gate. Only the transport changes back to BlueZ.
- The **transplant route** (route 2) reuses the same saved state
  format: pair on any Linux machine with Bluetooth, copy the two
  state files in, retire the donor. It converges on the same client
  this guide sets up.

## 15. Security rules that are part of the build

- Pairing state files are credentials. Never commit them, copy them
  to a second live machine, or include them in a backup that leaves
  the owner's control. The repository's `.gitignore` excludes them;
  keep it that way in any fork.
- The SDK token belongs to the person pairing. Handle it like a
  password.
- Keep the physical button confirm. A board that pairs without a
  person present is a board anyone in radio range can claim.
- The client runs commands as the installing account. Choose that
  account deliberately, and keep custom commands few and reviewed.
- Full notes: `SECURITY.md` in this repository.
