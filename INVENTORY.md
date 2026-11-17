# Inventory — PC and hardware on hand

> Historical hardware/software inventory. See [2026-11-11 source review](RESEARCH-2026-11-11.md) for board-profile and capability qualifications, and [MASTERPLAN.md](MASTERPLAN.md) for current use. Photo identification is not live hardware verification. The owner clarified on 2026-11-11 that he has no home automation devices currently; that work is deferred in favor of the PC harness.

_All PC facts verified live by the Muse agent over silent SSH on 2026-11-10 (idle checks first: PC idle ~4–4.3 h at the time). Hardware facts from the owner's messages and photo identification._

## The PC

- Windows 11 Home, build 10.0.26200
- **No Bluetooth adapter present** (`Get-PnpDevice -Class Bluetooth` returned nothing) — this single fact drives the whole pairing problem.
- WSL2 present; only distro: `OpenClawGateway` (Stopped, version 2). Avenues assume a **fresh Ubuntu 24.04 distro alongside it**; OpenClawGateway is never to be modified.
- Disk: ~100 GB free on C:
- GPU (from standing records): NVIDIA GTX 1070, 8 GB VRAM.

### Software present (version-checked 2026-11-10)

| Item | Version / state |
|---|---|
| Claude Code | 2.1.318 (installed) |
| Codex CLI | 0.163.0 (installed) + Codex desktop app |
| DeepSeek Harness (dsh) | 0.1.0-rc.6 (installed) |
| Git | 2.55.0 |
| Node | v24.18.0 |
| Python | 3.14.6 |
| uv | 0.12.3 |
| Muse Code CLI (Meta) | **Not installed** (install = the owner's billing decision) |

_Note: per the owner's 2026-11-10 correction, the agent seats above are background inventory for other projects — the MuseBridge harness does not depend on any of them._

### Current Muse agent→PC route (the lag being fixed)

VM → egress proxy (HTTP CONNECT) → the VPN → SSH (session 0) → scheduled task `the UIA helper task` → UIA helper in session 1 → result file → separate pull back.
Measured 2026-11-10: idle check ~5.5 s; trivial SSH command ~3 s; full UIA action 5–15 s.

## Phones (the owner's, currently unused)

| Device | Status | Radio | Notes |
|---|---|---|---|
| LG G7 ThinQ (believed; exact model unconfirmed) | In hand | Bluetooth 5.0 BLE (SD845) | Best phone candidate as a gadget in its own right via community Android clients. BLE peripheral/advertising support can't be proven from spec sheets — definitive test is the gadget app's own capability check on-device. As a pairing *donor* for the PC: not viable via any supported route (see game plan Avenue 2). |
| Google Pixel (original, 2016) | "Somewhere" — location TBD | Bluetooth 4.2 LE | Backup candidate; a 2018 BLE project ran `BluetoothLeAdvertiser` on an original Pixel, suggesting advertising works. |

## Microcontroller boards

### Board #1 — IDENTIFIED (photo 2026-11-11, file: docs/board-1-esp32-s3.jpg)

- **Module:** Espressif **ESP32-S3-WROOM-1**, variant **N16R8** — 16 MB flash, **8 MB PSRAM** (stamped variant code on the can; FCC ID 2AC7Z-ESP32S3WROOM1 — module is genuine Espressif).
- **Board:** generic ESP32-S3 DevKitC-1-form-factor board (clone PCB). Dual USB-C: one native USB (S3 USB-OTG/serial-JTAG), one CH343 USB-UART. BOOT + RESET buttons, onboard RGB LED (WS2812-class), AMS1117 regulator, full pin headers (GPIO labels visible in photo).
- **Radios:** Wi-Fi 4 (2.4 GHz) + **Bluetooth 5.0 LE** (incl. BLE Mesh).
- **Harness relevance:** fully in the gadget SDK's supported family; PSRAM tier (supports the richer features incl. home-network tunnel class); ideal candidate for Avenue 3a (direct paired gadget) and Avenue 3b (serial BLE front-end for the WSL gadget client — native USB gives WSL a clean `/dev/ttyACM*` with no BlueZ/kernel work). Zero purchase required.
- **Caveat:** clone board ≠ Espressif-branded DevKitC-1; if the web flasher's named-board list is picky, the ESP-IDF route (SDK pins v6.0.3) covers it.

### Boards #2+ — not yet inventoried

The owner has several small boards; models unknown. Classification rules from research: ESP32-S2 = **no Bluetooth at all, unsupported**; ESP32-H2 = BLE but no Wi-Fi; classic ESP32 / S3 / C3 / C6 / C5 = fine. Non-Bluetooth boards are a dead end for augmentation (HM-10-class modules can't advertise the custom GATT service/name the Muse app scans for) — photos are the fast path to classification.

## Not on hand (would-be purchases — the owner's call only)

- TP-Link UB500 BLE dongle (Realtek RTL8761B) — ~$15, Avenue 1 only; unnecessary if Avenue 3b works.
- Pi-class Linux+Bluetooth donor (Pi 3B+/4/5/Zero 2 W) — ~$35–60, Avenue 2 only.
