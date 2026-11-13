# Inventory: PC and hardware on hand

_All PC facts verified live by the agent over silent SSH on November 2026 (idle checks first; the PC had been idle for several hours). Hardware facts from the owner's messages and photo identification._

## The PC

- Windows 11 Home, a current build
- **No Bluetooth adapter present** (`Get-PnpDevice -Class Bluetooth` returned nothing), this single fact drives the whole pairing problem.
- WSL2 present; one existing distribution, stopped, which the harness work was told never to modify. Avenues assume a **fresh Ubuntu 24.04 distro alongside it**.
- Disk: comfortable free space on the system drive (installs should be planned against it)

### Software present

A current Git, Node, Python, and a Python environment manager were
present and version-checked during the inventory. Several local
development tools were also installed for unrelated projects; they
are not part of this harness and are not listed here.

### The current agent-to-PC route (the lag being fixed)

Cloud VM → egress proxy → VPN tunnel → SSH (session 0) → scheduled-task pickup → UI-automation helper in the interactive session (session 1) → result written to a file → separate pull back.
Measured November 2026: idle check ~5.5 s; trivial SSH command ~3 s; full UIA action 5-15 s.

## Phones (the owner's, currently unused)

| Device | Status | Radio | Notes |
|---|---|---|---|
| LG G7 ThinQ (believed; exact model unconfirmed) | In hand | Bluetooth 5.0 BLE (SD845) | Best phone candidate as a gadget in its own right via community Android clients. BLE peripheral/advertising support can't be proven from spec sheets, definitive test is the gadget app's own capability check on-device. As a pairing *donor* for the PC: not viable via any supported route (see game plan Avenue 2). |
| Google Pixel (original, 2016) | "Somewhere", location TBD | Bluetooth 4.2 LE | Backup candidate; a 2018 BLE project ran `BluetoothLeAdvertiser` on an original Pixel, suggesting advertising works. |

## Microcontroller boards

### Board #1, identified (photo, November 2026; public copy: `board-1-esp32-s3.jpg` in this folder)

- **Module:** Espressif **ESP32-S3-WROOM-1**, variant **N16R8**, 16 MB flash, **8 MB PSRAM** (stamped variant code on the can; FCC ID 2AC7Z-ESP32S3WROOM1, module is genuine Espressif).
- **Board:** generic ESP32-S3 DevKitC-1-form-factor board (clone PCB). Dual USB-C: one native USB (S3 USB-OTG/serial-JTAG), one CH343 USB-UART. BOOT + RESET buttons, onboard RGB LED (WS2812-class), AMS1117 regulator, full pin headers (GPIO labels visible in photo).
- **Radios:** Wi-Fi 4 (2.4 GHz) + **Bluetooth 5.0 LE** (incl. BLE Mesh).
- **Harness relevance:** fully in the gadget SDK's supported family; PSRAM tier (supports the richer features incl. home-network tunnel class); ideal candidate for Avenue 3a (direct paired gadget) and Avenue 3b (serial BLE front-end for the WSL gadget client, native USB gives WSL a clean `/dev/ttyACM*` with no BlueZ/kernel work). Zero purchase required.
- **Caveat:** clone board ≠ Espressif-branded DevKitC-1; if the web flasher's named-board list is picky, the ESP-IDF route (SDK pins v6.0.3) covers it.

### Boards #2 and up, not yet inventoried

The owner has several small boards; models unknown. Classification rules from research: ESP32-S2 = **no Bluetooth at all, unsupported**; ESP32-H2 = BLE but no Wi-Fi; classic ESP32 / S3 / C3 / C6 / C5 = fine. Non-Bluetooth boards are a dead end for augmentation (HM-10-class modules can't advertise the custom GATT service/name the Muse app scans for), photos are the fast path to classification.

## Not on hand (would-be purchases, the owner's call only)

- TP-Link UB500 BLE dongle (Realtek RTL8761B), ~$15, Avenue 1 only; unnecessary if Avenue 3b works.
- Pi-class Linux+Bluetooth donor (Pi 3B+/4/5/Zero 2 W), ~$35-60, Avenue 2 only.
