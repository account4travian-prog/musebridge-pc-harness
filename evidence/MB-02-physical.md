# MB-02 physical board session — 2026-11-11

the owner connected the board and authorized the previously prepared flash/setup.

## Hardware and backup

- Detected `COM3`, USB VID:PID `1a86:55d3` (CH343 USB-UART interface).
- esptool identified ESP32-S3 revision 0.2, 16 MB quad flash at 3.3 V, and 8 MB PSRAM. No eFuses were changed.
- The first full backup attempt at 921600 baud failed with a corrupt serial transfer. Retried at 460800 baud successfully; the full **16,777,216-byte** backup took 395.7 seconds.
- Backup preserved under ignored `.local/flash-backups/esp32s3-before-musebridge-2026-11-11.bin`. It may contain the previous firmware's private state; do not publish it.
- SHA-256: `732bed8498db772392c3608fe5379ee1ceb15086e36bb5cb264caa8437cd9d41`.
- Independently ran esptool `verify_flash` against the complete backup before writing: **digest matched**.

## Flash and host check

- PlatformIO upload to COM3 at 460800 baud succeeded. Written data hashes were verified. Firmware SHA-256 remains `46c615dd9d1683fcfc423a6a07dc3f0364f064d703d308e87e6f7b507d7e9f6b`.
- Initial host probe timed out. A controlled reset showed a normal application boot and firmware hello. Windows had initialized the CH343 modem-control lines in an unsuitable state.
- Updated the serial host to release BOOT, pulse RESET for 150 ms, then release RESET when opening a real device. Fake transports do not perform the hardware reset.
- Hardware diagnostic `tools/probe_bridge.py --port COM3` passed: firmware ready, reader alive, no errors. This exercises boot hello, identity configuration and periodic keepalive. It does not prove phone radio discovery, pairing crypto over actual BLE or Muse server access.
- Host tests after the serial change: **10 passed**.
- Stable PC pairing name generated in the protected local identity store: **MuseGadgetXXXXXX**. The diagnostic's temporary A1B2C3 name is not the PC identity and stops advertising after the host timeout.

## Current handoff

MuseBridge was launched using `Start-MuseBridge.ps1`. the owner needs to select Pair PC, choose COM3, enter the SDK token in the masked field, and add MuseGadgetXXXXXX from the Muse phone app with Developer mode enabled. No token was requested in chat or read from the UI.

Phone discovery/provisioning, live Muse connection/chat/health invocation, token refresh and board-unplug behavior remain pending. After pairing, resume MB-03 before adding broader tools.

## Owner follow-up

the owner subsequently reported: “Ok, so it works.” This is owner-reported success of the setup experience; no additional independent capture of live chat, bridge.health, token rotation or board-unplug behavior was collected in that follow-up. The current source still advertises only bridge.health. Do not re-pair the working setup merely to resume development. Next work is targeted live verification and MB-04 local execution, followed by voice and desktop tools.
