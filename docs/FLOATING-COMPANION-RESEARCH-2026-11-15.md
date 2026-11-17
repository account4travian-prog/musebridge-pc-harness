# Floating the Muse agent companion - research and design

Date: November 15, 2026. Requested by the owner: interact with Main only through a
floating pet/widget, keeping harness conversations dedicated side chats. The Muse agent
can supply his avatar, potentially animated.

Status: research retained below; the supplied Muse agent artwork and floating Main
companion are now implemented locally. [Implementation evidence and remaining
acceptance](../evidence/MB-37-companion.md) supersede the proposed state below.
Backlog authority: [MB-37](../HARNESS-UPGRADE-PLAN.md#mb-37-floating-the-claude-agent-companion).
The [November 16 compact revision](../evidence/MB-37b-compact-and-owner-checks.md) now supplies quiet transparent idle, hover controls, inline Main chat, dedicated voice controls and prepared owner checks. Existing 0.8.0/1.0 acceptance evidence retains its original scope.

## How ChatGPT's desktop pet works

The [official Pets documentation](https://learn.chatgpt.com/docs/pets), fetched
November 15, describes an animated overlay above other apps with controls below
the character. Hovering those controls exposes a pencil for Quick Chat. Enter
sends a request; a separate voice control starts speech. A bell opens a thread
activity tray, and selecting a thread opens its full conversation. A chevron
collapses the tray. New quick chats are outside projects.

The desktop shortcut is Windows+Alt+P on Windows, configurable in settings. It
shows the controls and focuses Quick Chat; pressing it again keeps the controls
open. Right-click Hide, a menu command, or /pet hides the overlay. Size can be
customized, position persists, and reduced motion uses a still frame. Mini keeps
the controls without displaying the character.

Activity distinguishes Running, Needs input, Ready and Blocked, prioritizing
Needs input, then Blocked, Ready and Running across chats. The tray is separate
from system notifications. On Windows, Appshots open in the main app; the
pet-directed Appshot and attached Computer Use picture-in-picture examples are
documented for macOS. These platform differences should not become promises for
MuseBridge.

The [official desktop commands reference](https://learn.chatgpt.com/docs/app/commands)
also documents installing pet artwork with sprite version 1 or 2. Public web
upload guidance currently describes the older nine-row format. The installed
Work Pets sprite contract documents both formats; distinguish that local
technical evidence from public desktop interaction documentation.

## Recommended MuseBridge interaction

This records the proposed adaptation. The implementation receipt above states
which parts are delivered and which optional refinements remain.

| Gesture or control | Result |
|---|---|
| Click the Muse agent / choose Chat | Open or focus a compact panel anchored to the avatar, permanently bound to Main. |
| Type and send | Continue the existing Main conversation; keep the selected harness chat unchanged. |
| Expand conversation | Enlarge that same Main panel, with readable history and attachments. Main stays outside the harness tabs. |
| Drag the Muse agent | Reposition the widget; save its position and clamp it to an available monitor after display changes. |
| Activity button | Inspect named side-chat activity; opening a side chat selects it in the harness. |
| Right-click | Show Main, open the harness, choose avatar/size, reduce motion, reset position, hide, and Stop Work. |
| View -> Show Atlas | Recover a hidden/off-screen widget without opening Main as a harness tab. |

Use a small native Qt overlay owned by the existing application and worker. It
shares the saved pairing and transport. The avatar is a visual representation
of the Muse agent; it does not create another agent, reasoning loop, account or process.
Keep it available when the harness is minimized. Closing its chat panel hides
the panel; quitting MuseBridge closes both. Do not silently change the harness's
existing close-to-quit behavior.

Support a text label/accessible control fallback if artwork is absent or fails
to load. Saving an avatar imports a local copy; retain existing art if decoding
fails. Keep transparent margins from becoming a large invisible input blocker.
Do not show the overlay or take focus in response to a background reply. Any
new global shortcut must be configurable and avoid the existing GPT pet key.
Development windows remain on the secondary display under the current owner
rule. Verify fullscreen behavior without interfering with a running game.

## Source findings and required engineering

- `shell_actions.py:refresh_navigation` currently puts Main into the sidebar.
  Remove that entry from normal harness navigation. All ordinary harness chat
  destinations must be dedicated side chats, including startup, archive fallback,
  overview and notification navigation. If none exists, show an explicit
  start-conversation state or create one local dedicated draft consistently.
- `app.py:Worker.send` captures the UI-selected `conversation_id`; add an
  explicit target argument so the companion can send to Main without changing
  the selected project or interrupting its work. `core.py:Core.send` already
  supports an explicit conversation target and `Core.deliver` emits chat IDs.
- Give the companion its own Main draft, attachment state, transcript and
  acknowledgment handling. Reuse the existing saved Main rows; do not rename,
  recreate or migrate the remote chat. A Main acknowledgment must not clear a
  side-chat composer, and a side-chat reply must not appear in Main.
- Preserve Main's attached workspace and policy controls in the companion;
  hiding Main in the sidebar must not orphan those controls. Stop Work stays
  global. Preserve existing uncertain-send handling and the one-identity lock.
- `voice.py:VoiceController.run` explicitly refuses Main and reads the selected
  core conversation. Pet voice therefore requires a separate routing change and
  evidence. The vendored `VoiceCall` accepts an optional thread ID, but that does
  not establish that an unbound call maps safely to Main and its PC tools. Do not
  remove the refusal without proving attribution. Until then, show the limit
  clearly rather than routing a pet call to the selected side tab.
- Main's shared stream still cannot attribute request completion reliably. Show
  observed states such as Sending, Reply received or Inspect send outcome. Do not
  invent a verified-complete signal, enable automatic continuation, or put a
  permanently uncertain Main request ahead of every side-chat activity.

## Avatar handoff for the Muse agent

The simplest useful deliverable is a transparent PNG/WebP of the Muse agent, with a
consistent silhouette readable at roughly 96-160 logical pixels. A 512-pixel
source with padding is a practical proposed starting point. Keep a separate
still frame for reduced motion and load failures. Artwork can be supplied
before animation; it does not need to be uploaded into ChatGPT Pets.

For stateful animation, supply separate transparent frame sequences or strips
with uniform frame sizes, frame order, timing/FPS, loop behavior, and a shared
anchor. Useful states are idle, working, waiting for input, reply received and
error. Listening/speaking clips can be added when real voice state is wired.
Keep character scale and ground position stable across clips, and include a
small motion preview. Describe which supplied clip corresponds to which state.

If the Muse agent already exports the GPT sprite format, support that as an import option:

Local format source: the installed `work-pets/0.1.6/references/sprite-sheet-contract.md`,
the "Supported sprite-sheet layouts" table. This is versioned bundled guidance.

- v1: 1536 x 1872, eight columns by nine rows.
- v2: 1536 x 2288, eight columns by eleven rows.
- Both use 192 x 208 cells. Rows 0-8 have frame counts
  6, 8, 8, 4, 5, 8, 6, 6, 6, respectively: idle, moving right, moving left,
  waving, jumping, failed, waiting, working, review.
- v2 adds sixteen clockwise look directions, eight per row. Unused cells are
  transparent. These details come from the installed Work Pets contract, not
  an independently observed MuseBridge renderer.

MuseBridge can accept fewer states and fall back to the still/idle frame. A
complete GPT-compatible 73-frame atlas is optional; basic Main chat must not
depend on producing one. A generic looping GIF could supply ambient animation,
but alone does not describe distinct work/attention states.

## Implementation order and acceptance

1. Build separate Main presentation/routing with static avatar import, show/hide,
   dragging and recovery. Keep the harness focused on dedicated conversations.
2. Add reliable observed status, optional activity tray and animation import.
3. Investigate Main voice independently; retain the existing refusal until a
   real attributed call/tool round trip establishes the correct behavior.

Verify with disposable profiles: simultaneous Main/side sends, reversed replies
and acknowledgments, retained/newer drafts, attachments, startup with Main as
the saved selection, archiving the last side chat, notification/overview routes,
hide/show, lost-monitor recovery, minimized harness, corrupt artwork and orderly
quit. Check Stop Work and fullscreen focus behavior. Run affected routing/UI
regressions after implementation; record native Windows observations separately.

The Muse agent artwork and the actual floating interaction remain owner-reviewed. This
research did not change the running app, saved profile, pet collection or pairing.
