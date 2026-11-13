# PC Harness — Game Plans for Every Avenue

> Historical discussion, preserved 2026-11-11. Use [MASTERPLAN.md](MASTERPLAN.md) for the current architecture, priorities and the implementation agent handoff, and [RESEARCH-2026-11-11.md](RESEARCH-2026-11-11.md) for verified corrections. the owner's subsequent clarification makes dedicated Windows chat/voice central and defers home automation. The recommendations and seat-based acceptance tests below are not the current backlog.

_Written 2026-11-10 (the Muse agent, from live research + a verified PC inventory the same evening). Status: PLANS ONLY — the owner's order 2026-11-10 21:20 CST: do not build anything yet._

## REVISION — the owner's correction, 2026-11-10 21:27 CST (this section governs; it supersedes the framing below)

the owner: **"I don't want to run other agents or models on my PC. I'm trying to figure out how to interact with YOU via a Windows-based solution that doesn't feel like absolute dog poop because of the lag and layers that cause it."**

So the goal is **not** a fleet of local model seats. It is:
1. **the owner ↔ the Muse agent, from the PC, feeling local.** A Windows-based way to talk to the Muse agent (this Muse, with the Muse agent's memory and context) that responds like a local app, not like a request relayed through a Rube Goldberg stack.
2. **the Muse agent working on the PC directly** — files, commands, apps — through that same low-lag channel, doing the development work *himself* with his own tools, not by commissioning Codex/DSH/Claude Code installs.

The "dev-agent layer" table below is therefore **demoted to background inventory** (those seats exist on the PC for other projects; they are not this harness's purpose and none of them needs to run for it).

### Where the lag and layers actually are (measured tonight, 2026-11-10)

The current PC route is: the Muse agent's VM → egress proxy → VPN tunnel → SSH (session 0) → scheduled-task pickup → helper in session 1 → result written to a file → separate pull back. Every single PC action pays the whole stack before doing any work:
- A read-only idle check: **~5.5 s**
- A simple version inventory over SSH: **~3 s**
- A full UIA action (set/send/read): **5–15 s**, plus pull/verify round trips on top

That is why it feels bad: the *transport* is the product, and the transport is five layers deep with a scheduler and a file handoff in the middle. No optimisation of individual steps fixes the feel; the fix is removing layers.

### The target architecture (what every avenue is really for)

A **persistent, outbound, direct connection from the PC to Muse** (the gadget model) — the PC holds one encrypted session open; messages and commands flow down it instantly; execution happens in a process already running **in the owner's own interactive session**, so there is no session-0/session-1 split, no SSH handshake per action, no scheduled-task pickup, no polling, no file relay. Expected dispatch latency: sub-second, plus the actual work's own time. the owner's side of it should look like an app on his PC — chat window, maybe voice — not a terminal he has to babysit and not a website tab among forty.

The avenues below remain the four ways to get that connection established (pairing is the only hard part). Read every "dev commands" mention below as: _commands the Muse agent himself uses to work on the PC_ (shell, files, git, tests, UIA where needed) — not local model seats.

### Zero-build feel improvement available today (not in the original four)

Install muse.ai as a **browser app** (Edge/Chrome "install as app"): own window, own taskbar icon, no browser chrome. The connection is then PC-browser → Muse directly — zero the Muse agent-side layers — so message lag is just the service's own response time. It is still a web UI, but it removes the "website among tabs" feel and is the honest baseline the native build has to beat.

### The end-state build, if the avenues prove out

A small **native Windows companion** (the real dream spec): tray app running in the owner's session; chat pane + push-to-talk; the gadget session held open by the app itself (paired via whichever avenue wins — dongle, ESP32 serial front-end, or transplant); a local executor (PowerShell/commands, file ops, direct in-session UIA) that answers the Muse agent's commands on that open session. One process, one connection, no relays. The WSL gadget (Avenues 1/2/3b) is the prototype of this; the native port is the polish step after the transport is proven.

---

## The dream, restated (original framing — superseded by the revision above)

the Muse agent (this Muse) working **on the owner's PC** — messaging the owner from the PC in real time, and doing real development work there by driving the same class of tools the owner uses: Codex, the DeepSeek Harness, Claude Code. Every avenue below is a different *transport* to that same end state. They are not mutually exclusive: Avenue 4 works today, and any gadget avenue later adds a first-class paired device on top of it.

## What "done" looks like (same acceptance bar for every avenue)

1. the owner can send the Muse agent a message from the PC and get a live reply — no scheduled task, no polling loop in the path.
2. the Muse agent can run a dev task in a PC repo end to end: commission a seat → seat writes files → tests run → the Muse agent verifies the files and test output independently (never the seat's say-so) → result reported with a hash/receipt.
3. All standing PC rules hold: idle check before any session, silent operation (no console windows flashing), nothing persistent running on the PC without the owner's explicit say-so for that thing, sessions closed and torn down when done.
4. The owner's standing rules for model use hold on every tool seat (kept in the owner's private notes, not reproduced here).

## The dev-agent layer (identical on every avenue — this is the "Codex / DSH / Claude Code functionality")

Verified on the PC 2026-11-10 ~21:25 CST (silent SSH, PC idle ~4.3 h):

Deliverable discipline (already the house pattern): the seat's deliverable is a **file on the PC**, pulled and hash-verified; chat prose is only a completion marker. Tests are the contract — a seat's "done" means nothing until the Muse agent re-runs or reads the test output.

---

## Avenue 1 — USB Bluetooth dongle → WSL2 → paired Muse gadget

**Idea:** Give the PC the Bluetooth radio it lacks via a ~$15 USB dongle, pass it into a fresh WSL2 Ubuntu, install Meta's Linux gadget SDK there. The PC becomes a *paired Muse device*: the Muse agent gets `system.run` / `file.read` / `file.write` / `device.health` plus custom commands, and the owner gets `musegadget chat` — a live terminal to the Muse agent on the PC, no polling.

**Research verdict:** Every layer is proven somewhere; the whole stack (musegadget pairing through WSL) is **plausible but untested end to end** — nobody has published doing it.

**Game plan**
1. **the owner:** buy a TP-Link UB500 (Realtek RTL8761B — the chipset proven end to end in WSL; avoid Broadcom dongles, avoid counterfeit CSR clones). Create an SDK token at gadgets.muse.ai. These are his because: purchase + credential.
2. **the Muse agent:** install a **fresh Ubuntu 24.04 WSL distro alongside** the existing OpenClawGateway distro (never touch that one); enable systemd in `wsl.conf`; probe first — `bluetoothctl show` after `usbipd attach` decides in 2 minutes whether this WSL kernel has the Bluetooth modules (older stock kernels did not; current WSL appears to ship them). If the probe fails, fallback is the documented custom-kernel build (~10 min), not a dead end.
3. **the Muse agent:** apply the proven Realtek firmware fix (WSL loads firmware from the utility VM's filesystem, not the distro's — a small boot service copies the blob and reloads btusb *before* bluetooth.service).
4. **the Muse agent:** run the stock musegadget installer (it degrades gracefully under WSL, sets the GATT MTU the Android app needs, and its package list matches the proven WSL peripheral stack).
5. **Together (10 minutes, the owner at phone + PC):** `sudo musegadget pair` → the owner adds `MuseGadgetXXXXXX` in the phone app (Developer mode on). BLE peripheral mode through WSL is the one untested link; close analogies (a WSL2 BlueZ BLE *peripheral* an iPhone pairs to) work.
6. **the Muse agent:** verification gate — from chat, call the new device's `device.health`, run a harmless `system.run`, read a file back. Then register custom commands for dev work (see below).
7. **Operations (needs the owner's explicit ruling):** systemd services do **not** keep a WSL distro alive, and usbipd attach does not survive a reboot/`wsl --shutdown`. A 24/7 gadget presence means a Windows logon task that starts the distro + re-attaches — that is a *persistent connection*, which the owner's PC rules require him to approve as its own thing. Without it, the gadget is alive only while the distro is up (start it when wanted — still live, still no polling).

**Custom dev commands to add once paired:** small wrappers for recurring local jobs (run a task from a file, return the result path plus a test summary; report git status and the last test run). Keep the set small and reviewed.

**Cost/effort:** ~$15 + one pairing session. the Muse agent-side build: half a day including the kernel/firmware contingencies. **Risk:** the musegadget-over-WSL pairing step itself (untested link) — everything else has a proven fix.

---

## Avenue 2 — Donor transplant → WSL2 gadget (same destination, no dongle)

**Idea:** Pair once on some *other* Linux+Bluetooth machine running the official client, copy the two small state files (`/var/lib/musegadget/identity.json` + `pairing.json`) into the PC's WSL install, and the PC connects on its own forever after — Bluetooth is only ever needed for that one conversation. The transplant pattern is proven (a PSP with no Bluetooth runs Muse exactly this way; a Pi→Mac transfer tool exists).

**Research verdict — stated plainly: NOT viable with the owner's current hardware.**
- The donor must run the **Linux** client. The LG G7 and the original Pixel cannot (no turnkey real-Linux port for either; the G7's mainline Linux is an active porter's tree, not an installable image).
- The Android gadget apps don't rescue it: their pairing is locked in private app storage / Android Keystore with reset-only management — no export feature. One untested loophole exists: `muse-gadget-everywhere` runs the upstream client and stores files with the same names/format, and its published builds are debug APKs — `adb run-as` might extract them from the G7. Nobody has published doing this, and success would **consume the G7 as a one-shot donor** (one identity = one live device; the donor must stay off, tokens rotate).

**Game plan (only if the owner wants this avenue)**
- **Option 2a (the honest version):** borrow or buy a Pi-class board (Pi 3B+/4/5/Zero 2 W, ~$35–60). the Muse agent preps the full SD/files remotely; pairing is the same 10-minute session as Avenue 1 step 5, done on the Pi; the Muse agent transplants the state files into WSL, disables the Pi's service, verifies the PC holds the session (device comes up as the paired gadget from the PC, donor off). ~1 hour total once the Pi exists.
- **Option 2b (the experiment):** try the `adb run-as` extraction on the G7 after pairing it via muse-gadget-everywhere. Time-boxed to one session; if the files aren't there in Linux format, this avenue closes and the G7 just stays a gadget in its own right (still useful — see Avenue 3 notes). Zero cost, real chance of failure.
- Everything after pairing (WSL install, keep-alive question, custom dev commands, verification) is identical to Avenue 1 steps 2–7.

**Recommendation:** don't pursue as a primary route. It only beats Avenue 1 if a Pi falls into our laps or the 2b experiment happens to work.

---

## Avenue 3 — Microcontroller boards

**Inventory update (2026-11-11, photo from the owner):** Board #1 identified — **ESP32-S3-WROOM-1 (N16R8: 16 MB flash / 8 MB PSRAM)** on a generic DevKitC-1-form-factor board, dual USB-C (native USB + CH343 UART), BOOT/RESET buttons, onboard RGB LED. BLE 5.0 + Wi-Fi, PSRAM tier. **This qualifies Avenues 3a and 3b with zero purchases.** Other boards still uninventoried.

### 3a. An ESP32-family board as a paired gadget in its own right

**Idea:** If any of the owner's boards is an ESP32 / S3 / C3 / C6 / C5, it pairs **directly** — no donor, no dongle, no augmentation. (Correction to my earlier chat message: the SDK now pins **ESP-IDF v6.0.3**, not 6.0.1.)

**Game plan**
1. **the owner:** lay the boards out / send a photo — the Muse agent identifies each (watch for: ESP32-S2 has *no Bluetooth at all*; H2 has BLE but no Wi-Fi; classic/S3/C3/C6/C5 are all fine).
2. **the Muse agent:** flash via the **web flasher** from Chrome/Edge on the PC (Web Serial; no toolchain install; it checks chip/flash against the board and takes the SDK token over serial). DIY ESP-IDF route only if a board isn't in the flasher's list (~60 boards are).
3. **the owner (2 minutes):** press the board's button to confirm pairing while adding it in the phone app (ESP32 uses a physical-confirm policy — that's a feature: a hardware "yes" button).
4. **Dev-workflow payoff (stock features, no custom code):** push-to-talk dictation to the Muse agent from the desk; on screen boards, the Muse agent pushes build/test status cards (`display.draw_*`) — e-paper holds the image with no power; status light shows link state at a glance. With the home-network tunnel (PSRAM boards only), the board is also a mini Home Link.
5. **Optional custom step (~a day, only if wanted):** a physical *approve* button for actions the Muse agent proposes (the hooks exist; not a stock feature).

**What it is not:** it does not give the PC a radio and does not control the PC. It's the harness's physical front-end — ears, voice, status, and a confirm button — next to the machine doing the work.

### 3b. An ESP32 as the Bluetooth front-end for the PC's WSL gadget — the dark horse

**Idea:** The Linux client's pairing code is cleanly split: all protocol logic (framing, ECDH/HKDF/AES-GCM pairing crypto) is host-side and transport-agnostic; BlueZ is one pluggable transport behind a 3-method interface. Replace BlueZ with a **serial transport**: an ESP32 plugged into the PC over USB runs a stripped GATT-peripheral firmware (forked from the SDK's own ESP32 BLE server code) and forwards raw packets over the serial link; the WSL client does everything else. WSL sees a `/dev/ttyACM*` serial device — which its stock kernel handles fine — so this route **skips the entire BlueZ-in-WSL/kernel/firmware swamp of Avenue 1** and needs **zero purchases** if a board qualifies.

**Game plan**
1. Board inventory (same as 3a step 1) — needs one ESP32-family board, which then lives plugged into the PC.
2. **the Muse agent builds (weekend-prototype scale, estimates):** host side ~120–180 lines of Python (a `SerialTransport` + a switch in `pair()`), testable *without hardware* against the repo's existing fake-transport tests; ESP32 side ~400–700 lines of C (NimBLE peripheral + UART forwarder, borrowing heavily from the SDK's own `ble_server.c`).
3. Negative tests first, per house rules: wrong-name advertisement must be rejected by the app scan, dropped-packet behaviour checked, pairing with a bad token must fail loudly.
4. Pairing session with the owner (same 10 minutes), then the identical verification gate + dev commands as Avenue 1.
5. Fallback if timing/MTU quirks beat us: the board still works as a 3a gadget, and Avenue 1 remains open. Nothing is wasted.

**Risk:** notification pacing/MTU negotiation against the phone app — mitigated because the stock ESP32 stack already negotiates with this same app successfully. No existing musegadget serial bridge exists; this one would be ours (and publishable, if the owner ever wants).

### Non-Bluetooth boards — honest close-out
Add-on BLE modules (HM-10 class) are a **dead end**: they expose a fixed vendor GATT service and cannot advertise the custom service/name the Muse app scans for. A transplanted-pairing Wi-Fi client (the PSP pattern) on an ESP8266-class board is a multi-week research build with real odds of not fitting. For any non-Bluetooth board: use an ESP32 as its radio (3b pattern) or replace it with a $6–10 Pico W / ESP32-C3. Do not augment in place.

---

## Avenue 4 — No pairing at all: modernise the harness we already run

**Idea:** Skip Muse device pairing entirely. the Muse agent already reaches the PC (SSH in session 0 + a UIA bridge into the interactive session) and already runs Sol and DSH there under the owner's rules. This avenue turns that ad-hoc route into the proper dev harness, and adds one standard tool: **Windows-MCP** (open-source, MIT — a Python MCP server exposing Windows UIA/Win32 primitives: click, type, launch, window state, screenshots, PowerShell — any-LLM, ~0.2–0.5 s per action).

**Game plan**
1. **the Muse agent:** trial Windows-MCP on the PC in a throwaway folder (PC Python is 3.14.6; the project asks 3.13+ — compatibility is step one's explicit check, with a `uv`-managed 3.13 venv as the fallback). Compare it head-to-head against the current pc-uia bridge on the standard Sol run; keep whichever wins per task class, retire nothing until the comparison is written down.
2. **the Muse agent:** formalise the seat contracts already proven in practice, one small wrapper per seat in the existing tools folder: task = a file; invocation = one silent SSH call; monitoring = **one** server-side wait (never UIA polling — that flashes windows, the October lesson); deliverable = result file pulled + hash-verified; tests re-run or read by the Muse agent before anything is called done.
   - `seat-claude`: Claude Code headless in the target repo (or in WSL Ubuntu when the Linux sandbox/toolchain matters — repos stay on the Windows side or move into WSL properly, never worked across `/mnt/c` from WSL).
   - `seat-sol`: Codex desktop via UIA, fresh chat per commission, picker verified High before any send. (Ceiling: sparing use, per standing rule.)
   - `seat-dsh`: DeepSeek Harness headless, single-line tasks, API-billed — deliberate commissions only.
   - `seat-muse`: Muse Code CLI — **only after the owner's billing decision** (install is one line; the account is the gate).
3. **the owner's part:** none for steps 1–2, beyond the standing per-run approvals his PC rules already require. His message-from-PC need is already covered by the desktop shortcut to muse.ai placed 2026-11-10 (VERIFIED) — this avenue is about the Muse agent working *on* the PC, and the owner directs it from phone or the PC browser tab.
4. **Optional later:** a Cua-style disposable Windows VM for risky/autonomous runs, so experiments can't touch the owner's real desktop (Windows 11 Home lacks Hyper-V — VirtualBox/VMware route; disk budget applies). Park until a real need appears.

**Cost/effort:** $0. the Muse agent-side: the Windows-MCP trial is an afternoon; the seat wrappers are small (the patterns all exist). **Risk:** lowest of all avenues — every component is already running in some form. **Limit:** the PC never becomes a paired Muse *device* (no `musegadget chat` terminal, no device entry in the app) — the owner talks to the Muse agent through the normal app/web, exactly as now.

---

## Recommended order

1. **Avenue 4 now** — zero cost, turns today's scattered PC routes into the dev harness the owner asked for; delivers real Codex/DSH/Claude Code functionality first.
2. **Board inventory → Avenue 3a/3b decision.** If an ESP32-family board exists, 3b is the best *paired-PC* route (no purchase, no kernel work, our own bridge) and 3a comes free with any second board.
3. **Avenue 1** if there is no usable board, or 3b's prototype fights back — ~$15 and the most-trodden path.
4. **Avenue 2** only opportunistically (a Pi appears, or the one-session G7 extraction experiment) — never as the plan of record.

## Decisions that are the owner's (nothing above starts without these)

- [ ] Build order approval ("don't build anything yet" stands until he lifts it)
- [ ] Dongle purchase (Avenue 1) — ~$15
- [ ] Muse Code CLI billing (dev layer) — Meta Model API account, no free tier
- [ ] Persistent gadget presence (Avenues 1/2/3b keep-alive + re-attach task) — an exception to his no-persistent-PC-connections rule, named as such
- [ ] Board inventory: photos/names of the microcontroller boards; G7 exact model confirmation

## Corrections recorded while researching

- ESP-IDF pin for the ESP32 gadget SDK is **v6.0.3** (current repo), not 6.0.1 as stated in my 2026-11-10 chat summary.
- My earlier "pair on a Pi and copy the files" framing survives, but the phones-as-donor version of it does not — see Avenue 2.