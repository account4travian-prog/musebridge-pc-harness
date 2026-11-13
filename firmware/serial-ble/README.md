# MuseBridge serial BLE firmware

This is a small independent Arduino/NimBLE transport, not a build of the full Muse ESP32 SDK. Its pinned PlatformIO packages therefore replace the stock SDK's ESP-IDF v6.0.3 build requirement. Pairing cryptography stays in the unchanged official Python SDK on Windows. The firmware contains no SDK token or Wi-Fi credentials.

Use the pictured board's **USB-UART** connector (the socket connected to its CH343 USB serial chip). The other socket is the S3 native USB port, and this firmware does not route its protocol there. The host enumerates COM ports to identify the UART adapter. No LED pin is used; no PSRAM is required. The firmware uses an 8 MB compatible partition layout on the 16 MB module.

Build from this directory:

```powershell
..\..\.venv\Scripts\python.exe -m platformio run
```

Before first flashing, identify the COM port and read chip/flash information. Back up the existing flash before replacing it. Do not erase or flash a guessed port. Normal upload uses PlatformIO's generated flash offsets and bootloader, without eFuse operations.

```powershell
..\..\.venv\Scripts\python.exe -m platformio run -t upload --upload-port COM_NUMBER
```

If automatic reset fails: hold **BOOT**, tap and release **RESET**, then release **BOOT** and retry. Do not do this until asked during the physical session. No buttons are needed during normal community pairing: the official Linux pairing policy confirms in the phone app.

Protocol: newline-delimited JSON, `v:1`, max 4096 bytes per line. `hello` returns a random boot `epoch`; `configure` with that epoch sets the PC identity's advertisement. Connect/disconnect/MTU/RX events include `epoch` and a connection `gen`. ATT packet bytes use base64. Host TX includes an ID; firmware ACKs notify acceptance. This ACK is not proof that the phone application processed the packet. Host sends keepalive every 3 seconds; 15 seconds without host traffic closes pairing. Stale boot/connection commands are rejected. No advertisement begins until the host configures the bridge.

The first firmware is an experimental pairing transport. A successful compile does not prove the phone accepts its advertisement or that its BLE timing works on this board.

Physical session 2026-11-11: COM3 is this board's CH343 UART socket. Full flash was backed up and verified, then the bridge uploaded successfully. Host hello/configure/keepalive passed after adding explicit DTR/RTS reset handling. See [physical evidence](../../evidence/MB-02-physical.md). Phone pairing remains pending.
