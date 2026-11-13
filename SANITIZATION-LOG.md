# Sanitization Log

How this public copy was produced from internal working notes.
Removed strings are described by class only; nothing personal is
quoted in this file.

## Method

The internal harness package (a design README, a game plan, an
inventory, and a board photo) was copied to a fresh staging tree.
The README and the ESP32 workaround guide were rewritten fresh for
publication. The game plan and the inventory were transformed by
scripted class replacements, then hand-edited for coherence, and the
whole tree passed a denylist verification scan (below). The original
notes were not modified. Git history starts fresh in this repository;
no internal history was carried over.

## Classes of change

1. **Personal names.** The PC owner's name and his agent's name were
   replaced throughout with role words ("the owner", "the agent").
   The owner's name appears only in `LICENSE`, as intended.
2. **Machine fingerprints.** The PC's hostname, a private network
   address, the VPN product name in the route description, an
   existing WSL distribution's name, exact OS build number, exact
   disk figures, and a GPU model were removed or generalized.
3. **Private infrastructure.** The internal route to the PC is
   described generically (cloud VM, proxy, VPN tunnel, SSH). Internal
   helper and task names were removed.
4. **Other projects' tooling and accounts.** A table of unrelated
   local development tools, their exact versions, their billing
   arrangements, and internal model rules were cut. The harness does
   not depend on them.
5. **Internal instructions and status labels.** Standing internal
   orders, per-project status tags, and chat timestamps were removed
   or rewritten as plain prose. Public copy carries no internal
   status labels.
6. **Photo metadata.** The board photo was re-encoded with all EXIF
   metadata stripped (the original carried a phone model and a
   timestamp) and renamed generically.
7. **Dates and times.** Chat-level timestamps and time zones were
   removed; research dates were generalized to month and year.

## Verification

Denylist scans over the staged tree, all clean except the items
dispositioned here:

- Secret patterns (key and token prefixes, private-key markers,
  bearer strings, credential keywords). One benign hit: the word
  "password" in a security instruction sentence in
  `docs/ESP32-BLE-FRONTEND.md`. No credential material.
- Personal identifiers: email pattern, phone pattern, the owner's
  surname, family names, home town, county, and employer. Zero hits
  outside `LICENSE`.
- Machine fingerprints: home-directory path patterns, user folder
  paths, hostnames. Zero hits.
- Em dashes and en dashes in public prose: zero.
- File inventory: no `.env` files, key files, credential files,
  logs, archives, databases, or internal documents. One image, the
  board photo, with metadata verified stripped after re-encoding.
