# The ESP32 Bluetooth front-end, as built

How MuseBridge paired a Windows PC that has no Bluetooth adapter, using a
generic ESP32-S3 board as a provisioning-time BLE radio. This document
describes the shipped implementation in this repository. (An earlier revision
of this guide described a different, design-stage approach; it has been
rewritten from the code. Where they differ, this version is what happened.)

## The constraint

- The official Muse Gadget SDK ships an ESP32 device target and a Linux host
  SDK. There is no Windows SDK.
- Muse pairing is Bluetooth-only: the pairing exchange happens over a BLE
  GATT service.
- The reference PC runs Windows 11 and has **no Bluetooth adapter** (device
  enumeration shows no Bluetooth class devices at all).
- Windows Bluetooth APIs do not offer the peripheral-side control the SDK's
  pairing flow would need in any case.

## The idea

Keep every piece of pairing logic exactly where the SDK puts it, and move
only the radio. The board never runs the SDK, holds no credentials and makes
no decisions: it advertises as instructed and shuttles raw ATT packets
between BLE and a USB serial line. The unmodified official SDK pairing code,
running natively on Windows, does all cryptography and protocol work against
a serial transport that looks like a Bluetooth link to it.

After provisioning completes, the board's job is finished. The SDK's normal
post-pairing connection runs over the PC's own network; everyday use of
MuseBridge involves no board, no re-pair and no reflash.

## Components in this repository

### Firmware: `firmware/serial-ble/`

A small independent Arduino/NimBLE application (PlatformIO, `esp32-s3`
environment, NimBLE-Arduino + ArduinoJson), about 115 lines in
`src/main.cpp`. It is deliberately **not** a build of the upstream ESP32 SDK:
its pinned PlatformIO packages replace the stock SDK's ESP-IDF build
requirement. The firmware contains no SDK token and no Wi-Fi credentials.

Protocol (see `firmware/serial-ble/README.md` for the full contract):

- Newline-delimited JSON, protocol `v:1`, maximum 4096 bytes per line.
- `hello` returns a random per-boot `epoch`. `configure`, carrying that
  epoch, sets the PC identity's advertisement data. No advertisement begins
  until the host configures the bridge.
- Connect, disconnect, MTU and RX events carry the `epoch` and a connection
  generation counter, so stale commands from a previous boot or connection
  are rejected on both sides.
- ATT packet bytes travel base64-encoded. Host transmissions carry an ID;
  the firmware acknowledges when a notification is accepted. That ACK proves
  acceptance by the firmware, **not** that the phone application processed
  the packet; the host treats it as backpressure, nothing more.
- The host sends a keepalive every 3 seconds; 15 seconds without host
  traffic closes the pairing session.

### Host transport: `musebridge/serial_ble.py`

A bounded serial transport in the MuseBridge app: 115200 baud, the same
4096-byte frame bound, epoch and generation tracking, and acknowledged
`send_packets` so a slow BLE link applies backpressure instead of dropping
packets. `musebridge/pairing.py` wires this transport into the app's Pair PC
flow, which is where the official SDK (`musegadget`, pinned, see
`vendor/PINNED.md`) performs the pairing exchange.

### Hardware notes

- Use the board's **USB-UART socket** (the connector wired to its CH343 USB
  serial chip; the reference board enumerated as COM3). The other USB-C
  socket is the S3's native USB port; this firmware does not route its
  protocol there, and the build disables USB CDC on boot.
- Opening a serial port can assert the CH343's modem-control lines and hold
  this class of clone board in reset. The host transport handles it
  explicitly: release BOOT, pulse RESET, then leave both lines released
  before expecting application startup.
- No LED pin is assumed, no PSRAM is required, and the firmware uses an
  8 MB-compatible partition layout on the 16 MB module.
- Board qualification: the radio must be BLE-capable. ESP32-S3, C3, C6 and
  classic ESP32 qualify; ESP32-S2 has no Bluetooth and cannot be used.

## The pairing session, as it ran

From the project's physical-session receipt (`evidence/MB-02-physical.md`):

1. The board's full flash was backed up and verified **before** anything was
   written. The bridge firmware was then built and uploaded over the UART
   socket. If automatic reset fails on this board class: hold BOOT, tap
   RESET, release BOOT, and retry. Never flash a guessed port.
2. The host side passed `hello` / `configure` / keepalive after the DTR/RTS
   reset handling above was added.
3. In the app: **Pair PC**, select the board's COM port, enter the SDK token
   in the masked field (the token never touches the firmware or this
   repository), and add the named device from the Muse phone app. The
   official pairing policy confirms in the phone app; no board buttons are
   needed during pairing.
4. The owner then reported the setup working, and the bridge has been in
   daily use since, over the PC's network connection, with the original
   pairing retained through all later development.

## Security properties

- The board is a radio, not a trust boundary: it sees encrypted pairing
  traffic as opaque bytes and stores no identity material.
- Pairing identity on the PC is protected with Windows DPAPI.
- Sessions are short-lived by construction: no host keepalive, no pairing
  window (15 second timeout), and epoch/generation guards keep a reset or
  reconnected board from replaying stale state.

## What this is not

- Not a general BLE serial bridge, and not a fork of the upstream ESP32 SDK.
- Not a steady-state transport: after provisioning, the board can be
  unplugged. Re-pairing (or pairing a second PC) is when it comes back out.
- Not a workaround for an absent BLE *central*: the phone side uses its own
  Bluetooth normally.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Board never enumerates | Data cable in the native-USB socket, or a charge-only cable | Use the USB-UART socket and a known data cable |
| Firmware uploads but never starts | Modem-control lines holding the board in reset | Use a host that performs the release-BOOT / pulse-RESET sequence (the transport here does) |
| `hello` never answered | Wrong COM port, or port held by another program | Enumerate ports, close serial monitors, confirm the CH343 device |
| Pairing window closes immediately | Host keepalive not running | The app must keep the session alive during the whole pairing flow |
| Stale commands acted on after reconnect | Missing epoch/generation checks | Both sides must reject frames from an older boot or connection |
| Phone does not see the device | Advertisement not configured, or an ESP32-S2 board | Confirm `configure` was sent after `hello`; confirm the board is BLE-capable |

## Fallbacks considered

- **Pair the board as a standalone gadget** (upstream ESP32 firmware) and
  bridge at the application layer. Works, but puts a second identity and a
  second protocol on the board permanently, for a one-time provisioning job.
- **A USB Bluetooth dongle on the PC.** Rejected on the reference PC:
  adapter-class and driver questions aside, Windows still does not give the
  process the peripheral behavior the pairing flow needs.
- **Pair on another host and transplant the pairing state.** Fragile: the
  state is bound to the pairing host's identity handling. The serial
  front-end keeps the identity on the PC from the first byte, which is why
  it won.
