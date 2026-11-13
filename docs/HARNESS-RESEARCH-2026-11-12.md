# Harness research and audit - 2026-11-12

Owner: [HARNESS-UPGRADE-PLAN.md](../HARNESS-UPGRADE-PLAN.md) (MB-24 to MB-36). This file is the evidence
behind that plan's decisions. It has two halves: what the outside world has learned about agent
harnesses, and what an audit of MuseBridge 0.6.0 found when measured against it.

Written by a planning model from a read-only review. Nothing in `musebridge/` was changed.
ASCII only on purpose (Codex on Windows can corrupt non-ASCII characters).

Labels used below: **MEASURED** = I ran it or read it in this checkout. **PRIMARY** = read from the
original source. **SECONDARY** = read from a summary of the source; open the original before relying
on the exact number. **INFERRED** = my reasoning, not yet tested.

---

## 1. What "harness" means for this project

Muse's reasoning loop runs on Meta's side. MuseBridge cannot change the model, its system prompt,
its context compaction or how it plans. What MuseBridge fully controls is the **agent-computer
interface**:

1. which tools exist and what they are called,
2. what each tool description tells Muse,
3. what parameters a call needs (every extra one is a chance to fail),
4. what comes back (how much, in what shape, with what hint on failure),
5. how many round trips a routine action costs (each one goes PC -> Meta -> PC),
6. what context is injected with each message,
7. what Muse can perceive (text, UI trees, pixels) and how fast,
8. what automatic feedback Muse gets after acting (syntax checks, test results, diffs).

That list is the whole lever. The research below says it is a big lever.

---

## 2. External findings

### 2.1 The harness moves results as much as the model does

- SECONDARY: Vercel removed about 80% of an agent's tools; success went from 80% to 100% on the same
  model, tokens more than halved, latency 724 s -> 141 s. LangChain moved a coding agent from 52.8% to
  66.5% on Terminal-Bench by changing only the harness. Letta Code scored 59.1% vs Claude Code 41.6%
  on Terminal-Bench 2.0 with the same model.
  Source: https://winder.ai/ai-agent-harness-comparison/ (summarising the Vercel and LangChain posts
  and https://leehanchung.github.io/blogs/2026/05/08/hidden-technical-debt-agent-harness/).
- SECONDARY: "The Harness Problem" (http://blog.can.ac/2026/02/12/the-harness-problem/): changing
  only the edit-tool format moved one model from 6.7% to 68.3% on a real coding task. Exact-text
  replace fails when the model cannot reproduce whitespace; OpenAI-style patches failed 50.7% of
  the time on a model not trained for them. Line-anchored edits ("hashline": each line shown with a
  short content hash, edits reference the hash) matched or beat the others across 16 models.
  Read via https://dev.to/alexchen31337/the-harness-problem-is-real-and-the-edit-tool-is-where-it-starts-nff
- PRIMARY (titles/abstract only): https://arena.ai/blog/coding-agents-harness-tax finds the harness
  changes cost more than correctness for top models; https://arxiv.org/html/2605.23950v1 argues agent
  results are meaningless without naming the harness.

Takeaway for MuseBridge: Muse is not an Anthropic or OpenAI model, so we must not assume it is
fluent in either vendor's edit format. **The edit tool is the single most likely place for silent
capability loss, and we have to measure it rather than guess.**

### 2.2 Tool design (Anthropic, "Writing effective tools for AI agents")

PRIMARY: https://www.anthropic.com/research/writing-tools-for-agents and
https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools

- Build a few thoughtful tools for high-impact workflows; do not wrap every internal operation.
  "Too many tools or overlapping tools can also distract agents from pursuing efficient strategies."
- Tools should "handle frequently chained, multi-step tasks in a single tool call".
- Return meaningful context; avoid low-level identifiers (UUIDs, mime types) where a name will do.
- Paginate, filter and truncate with sensible defaults; when truncating, tell the agent what to do
  next.
- Errors should steer: say what was wrong and show a correctly formed input.
- Detailed descriptions are "by far the most important factor in tool performance": what it does,
  when to use it, when not to.
- Namespacing matters, and prefix vs suffix has measurable effects that vary by model: evaluate.

### 2.3 Agent-computer interface (SWE-agent)

PRIMARY: https://arxiv.org/html/2405.15793v3 (Yang et al., SWE-agent)

- A purpose-built interface beat a raw shell by a wide margin with the same model.
- A file viewer that shows a bounded window with line numbers beats `cat`.
- An edit command that runs a linter and **rejects edits that introduce syntax errors** raised the
  solve rate; without it agents spiral on self-inflicted breakage.
- Search output must be short: list of matching files / capped hits, never a wall of text.
- Always return something: an empty result should say "ran successfully, no output".
- Old observations should be collapsed; only recent ones need detail.

### 2.4 Perception: structure first, pixels second

- PRIMARY: Playwright MCP (https://playwright.dev/mcp/snapshots): returns an accessibility snapshot
  where every element has a short ref (`e12`); actions take the ref; **every action returns a fresh
  snapshot** so the model always sees the result. Refs beat CSS selectors because they point at the
  exact element just seen, and a stale ref errors instead of hitting the wrong element.
- SECONDARY: https://currents.dev/posts/playwright-mcp: full snapshot before every action is an
  anti-pattern (the action already returned one); offer a find-by-text tool for big pages.
- PRIMARY (abstract): UFO2 (https://arxiv.org/html/2504.14603v2): Windows agents work best with a
  hybrid: UI Automation first, visual grounding for custom-drawn UI, and native app APIs over
  clicking wherever one exists.
- Reference implementation worth reading before MB-33: https://github.com/CursorTouch/Windows-MCP
  (UIA observation + screenshots + input, Python, MIT).

### 2.5 Verification loops

- SECONDARY (LangChain harness-engineering post via the winder.ai summary): the largest gains came
  from (a) telling the agent about its environment up front instead of making it discover it,
  (b) a pre-completion check that forces a verification pass before the agent declares done,
  (c) detecting doom loops (the same failing action repeated) and nudging a change of approach.
- MuseBridge already has task receipts and hooks. What it lacks is **automatic, zero-ceremony
  feedback in the tool result itself** (did this edit parse? did the command exit non-zero?).

---

## 3. Audit of MuseBridge 0.6.0

### 3.1 State at handoff (MEASURED 2026-11-12)

| Check | Result |
|---|---|
| `pytest -q tests` | 225 passed, 4 skipped (matches the 0.6 receipt). |
| App launch | **Crashes.** `.local/runtime-0.6-error.txt`: `ValueError: dictionary update sequence element #0 has length 36` at `musebridge/jobs.py:125` (`finished_summary`). The jobs database has no `row_factory`, so `dict(row)` is applied to a tuple. It only fails when finished jobs exist, and `tests/test_attention_plugins.py:24` only checks the empty case. This is the "reopening 0.6.0" step GPT was on when it ran out of usage. |
| Git | No repository at the project root. `vendor/muse-gadget-sdk` has its own `.git`, at `9d8cca156cdec9ea06efcf854b03dccc5ca76d42`. A 27-file edit landed with no way to roll back. |
| Tool surface | 59 registered tools, 25,867 bytes of schema (`SPECS` + browser + desktop). |
| ripgrep, git | Both installed (`rg` 15.2.0, git 2.55.0). Git Bash is therefore available. |

### 3.2 Findings, ranked by expected effect on Muse's success

**F1. Too many tools, written as disclaimers.** 59 tools. Descriptions spend their words on what
the harness will not promise ("This is a summary, not a native hidden-state fork") instead of when
and how to use the tool. Several families overlap: `job.*` vs `terminal.*`; `plan.update` vs
`task.*`; `artifact.*` vs `file.*`; five `git.*` read tools that one shell command covers.
(`musebridge/tooling.py:20-103`)

**F2. Every mutation needs three bookkeeping parameters.** `conversation_id`, `run_id` and
`scope_revision` are required on every non-read tool (`tooling.py:150-157`, `187-189`), plus
`request_id` on jobs/terminals/MCP and `expected_sha256` on edits. Each is a UUID or counter Muse
must copy correctly from a JSON blob. A stale `scope_revision` or old `run_id` refuses the call.
None of these carry information Muse decides; the harness already knows all of them.

**F3. A shell command costs at least two round trips.** `job.start` returns immediately; Muse then
calls `job.wait` (max 20 s per call, `tooling.py:390`). A 50 s test run is four or more trips.
The upstream SDK's own `system.run` blocks for up to 10 minutes and registers
`"timeout_ms": 605000` in its spec (`vendor/.../executor.py:60-75`), so the service supports long
blocking invokes. INFERRED until probed (plan probe P-1).

**F4. Editing is fragile.**
- `file.edit` needs the whole-file SHA-256, so every edit is preceded by a full `file.read`.
- `file.read` is byte-offset based, has **no line numbers**, and returns the content twice
  (`text` and `data_base64`, `workspace.py:147-148`), more than doubling the payload.
- Matching is exact (`source.count(old)`, `workspace.py:231`). Windows files use CRLF; a multi-line
  `old` typed with `\n` will not match. INFERRED from the code; confirm with a test (plan MB-27).
- One edit per call; no multi-edit; a "found 0" error does not show the nearest text.
- Search is literal only (`--fixed-strings`, `workspace.py:252`): no regex, no glob, no context
  lines, no "files only" mode.

**F5. Every message carries a JSON preamble.** `Core.send` (`core.py:189-214`) prepends the harness
contract, results glossary, project record, goal, plan, up to 20 tasks, 5 notes and up to 16,000
characters of `AGENTS.md` to **every** user message. Stable guidance is re-sent each turn while the
truly useful environment facts (what is installed, what the tree looks like, git state) are absent.

**F6. Muse cannot see mid-turn.** Screenshots return a file path; pixels only arrive as a new chat
turn via `send_image` (`vision.observe`). One look costs a full turn. However the stock ESP32
firmware's `camera.capture` returns the picture **inside the tool result** as
`{"format":"jpeg-base64","data_base64":...}` (`vendor/.../esp32/main/app.c:1665-1673`). If the Muse
service turns that result shape into an image for the model, MuseBridge can give Muse inline
vision. INFERRED; cheap to test (plan probe P-2). Control messages cap at 256 KiB, so the JPEG must
be about 130 KB or less.

**F7. One live chat at a time.** Tool calls are refused unless they target the conversation
currently selected in the UI (`core.py:76`, `tooling.py:184-185`), and switching chats marks the
run interrupted (`core.py:108-109`). the owner cannot let Muse work in one project while chatting in
another.

**F8. Desktop hands are short.** `desktop.act` supports only `focus`, `invoke`, `set_text`
(`desktop.py:45-53`). No click, typing, key chords, scrolling, launching an app, or reading text
from windows with poor accessibility trees. Selectors are tree paths like `0.3.1`, which break when
the tree shifts.

**F9. Browser is selector-driven and thin.** Six tools, CSS selectors, no element refs, no
automatic snapshot after an action, no key press / select / wait / back / scroll / script
(`browser.py:34-65`).

**F10. Checkpoints only cover the file tools.** Anything Muse changes through the shell is not
restorable (`WINDOWS-WORKFLOW-CONTRACT.md`, "Files, jobs and recovery").

**F11. Results carry ledger noise.** Each result gets `execution: {call_id, run_id, state}` and
often `lifecycle` / `after_edit_hooks` blocks (`tooling.py:225-228`, `360`). Useful to the owner's
audit trail; noise to the model.

**F12. No measurement.** There is no task suite and no per-tool statistics, so nobody can say
whether a harness change helped. The live probes prove features work once; they do not compare.

**F13. Shell fit.** Jobs prefer Windows PowerShell 5.1 (`jobs.py:248`). 5.1 lacks `&&`/`||`, and its
default encodings corrupt non-ASCII output. Most models write bash far more reliably than
PowerShell. Git Bash is on this PC.

### 3.3 What is already strong (keep it)

- Honest state model: accepted / succeeded / unknown, no blind replay, post-ack tracking.
- Windows Job Objects with tree cancellation; ConPTY terminals; DPAPI credential storage.
- Atomic writes, path-escape and private-directory guards.
- Profiles (Read only / Project work / Desktop control) and Stop Work.
- Task receipts bound to file hashes; summary-seeded handoffs; durable queue.
- A real regression suite (225 tests) and a pattern for sanitized live probes.

The upgrade is about the **interface in front of** this machinery, not replacing it.

---

## 4. Constraints that shape the design (MEASURED from the pinned SDK)

| Constraint | Where | Consequence |
|---|---|---|
| Tools register once per connection (`link.register`, `commands_v2`) | `link_client.py:92-112` | Changing the tool set needs a reconnect. Tool "packs" are chosen at connect time. |
| Control message cap 256 KiB; whole registration must fit | `link_client.py:68`, `executor.py:144-170` | Schema budget is fine (26 KB today); result payloads must stay under about 180 KB. |
| 4 concurrent invokes by default; constructor accepts `invoke_slots` | `link_client.py:62`, `195-207` | A blocking shell call holds a slot. Pass a larger semaphore. |
| `link.invoke` carries `id`, `command`, `params`, `timeout_ms` and no chat ID | `link_client.py:799-803` | The harness must work out which conversation a call belongs to. |
| A spec may carry `timeout_ms` | `commands.py:88-96`, `executor.py:74` | Long blocking tools are a supported pattern upstream. |
| Images to chat: 2 MiB, JPEG/PNG/GIF/WebP, via `send_image` | `link_client.py:75-80` | Existing image-turn path; keep as fallback for vision. |
| No fork / steer / worker / compaction / usage API | `evidence/MB-22-remote-capabilities.md` | Unchanged. Do not build UI that implies them. |
| Muse learns device procedures from Markdown skills on its VM | `vendor/.../skills/README.md` | A `SKILL.md` for this PC is the right home for stable guidance, instead of a per-message preamble. |

---

## 5. Open questions only a live test can answer

These are the plan's probes. Each has a fallback so no later work depends on a guess.

| ID | Question | Why it matters |
|---|---|---|
| P-1 | Does the service wait for a tool that blocks 45 s / 150 s when its spec declares `timeout_ms`? | Decides the inline wait cap of `shell.run`. |
| P-2 | Does a result shaped `{"format":"jpeg-base64","data_base64":...}` reach the model as an image? | Inline vision vs one-turn-per-look. |
| P-3 | How does Muse behave with exact-replace edits vs line-anchored edits on CRLF and indented files? | Picks the default edit format. |
| P-4 | Does Muse write PowerShell or bash more reliably on this PC? | Picks the default shell. |
| P-5 | Does Muse keep a first-message context header in mind across a long chat? | Decides how often the header is repeated. |
