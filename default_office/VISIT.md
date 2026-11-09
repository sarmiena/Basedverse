# Visit in this workroom

How this workroom (the Agent Room, `forge/board/room.html`) uses basedverse's Visit, and every way it differs from
Visit's vanilla experience (basedverse's `visit/INTEGRATING.md`, "The vanilla experience"). An upgrade reads this
first and keeps what it says. basedverse's `/basedverse:visit` skill keeps it.

**Integrated at:** basedverse v5.2.0 (released 2026-11-06; 5.1.1: PR #85; 5.2.0: PR #86; basedverse #42, #57, #58, #59, #60; 4.0.1: PR #66; 4.1.0: #67, PR #68; 4.2.0: #71, PR #75; 4.3.0: PR #77; 4.3.1: PR #78; 4.3.2: PRs #80, #81; 5.0.0: #82, PR #83; 5.1.0: PR #84): the lobby is `wss://basedverse.app/`. **No Docker, no basedverse checkout**:
`bin/start` runs everything on this machine as programs of their own. `visit` is the release `VISIT_VERSION` names
(`.env`), downloaded once into `.visit/bin/visit` and checked against the release's SHA256SUMS (`VISIT_PATH` overrides
it with one of your own); it holds the board's sign-in, the room's engine (the headless Chrome inside it) and their
database (Postgres inside it, its data in `VISIT_DATA_DIR`, `~/.local/share/basedverse/<your office's name>`); its log
`.visit/visit.log`. The board is `python3 forge/board/server.py` (`bin/lib/board.sh`; its data `.board/`, its log
`.board/board.log`; `bin/board-restart` after a server change), and Caddy for the LAN is its official release in
`.remote/` (pinned checksum; its authority in `.remote/data/`, the same root devices already trust). The room's page
runs in **the engine** (always), and your browser is one more client of it (engine/INTEGRATING.md): `/room` is the
person's page, `/room?role=host` the room's (an API key only: the engine's, made by visit at each start),
`/visit/ws` the board's relay of your page to visit (no token in any page), `/visit/visit.js` its library,
`/visit/mode` what runs. Tokens each start: `.visit/token` the room's, `.visit/user-token` yours, `.auth/token` the
board's for the sign-in. Before that: 1.0.0, with visit-client, auth-client, the engine and Postgres (`visit-db`) as
four services in Docker beside the board's and Caddy's containers; its database was imported (`visit import`,
2026-10-29: 1 account, 3 keys, 460 chat lines, 33 pictures and voice messages); the Docker volumes (`forge_visitdb`,
`forge_boarddata`, `forge_caddydata`) are kept to go back with (git tag `before-2.0`).

## Features

| feature | state | how, in this room |
|---|---|---|
| connect, host, visit | vanilla | `startVisit`, by `ROLE`: the engine's page (`window.VISIT_ENGINE`) connects as the host and `v.host`s the room's scene (`startRoom`: `spawn` puts the owner at the owner's spot, a guest beside it; `move` = `visitorMove`, the room's walls and its land, the owner free to walk off it like a watcher); your page connects as the user through `/visit/ws` (`startPerson`): no room of its own, `v.world({ three, renderer })` draws it as the engine's page sends it, and you where it has you (`stepMe`: `world.frame`, on your own plot like any); with no visit-client at all (no lobby in `.env`: `/visit/mode`), both, as before |
| panel, stage | vanilla | `Visit.panel` and `Visit.stage` (the person's page; 4.0: with your world drawn here the stage shows nothing) |
| knocks, links | gone (Visit 4.0, basedverse#57) | worlds are the way into a room: you walk onto a friend's plot, they walk onto yours |
| talk, huddles (4.0, basedverse#58) | vanilla | the panel's chat window: 🎧 follows its box (`/w someone`: talk with them, it rings for them; `/g world`: that world's huddle; `/room`: the room's voice); nothing in the room |
| world | vanilla | `v.world`; the land (110 × 110 m) is the plot (the engine's page says it); the person's page draws your dude over the world, uncut, into the same depth (`drawRoom`) |
| map | vanilla | the panel's Map |
| where friends put you | vanilla | the panel's notes |
| avatars | vanilla | `meAvatar()`: your dude, with walk and idle (4.0: and sit, lie, drink, eat, throw, swat, wave as the room's walkers do them, and a `hand` on its right arm: MODELS.md); visitors as their own model (Visit's default since 0.16), or ours, set in the panel; ours tagged with the name they go by (4.3: `visitor.name`, its tag alone made again when it changes) and with a random look picked from their office's key (`visitor.key`, kept as they rename themselves) |
| interactions | vanilla | the engine's page's: `VISITOR_TYPES`: sit, banana, water, coffee, cocktail, wake, swat, poke, and the tarot reader's spread (2026-11-02: `tarotcards` on the cards on her table, "look at the cards"; `tarotcard`, a card up in the air; `tarotscroll`, its scroll, a click its next page and anyone's wheel scrolling it, `wheel: true`, `spreadWheel`; `tarotshut`, its seal), and your own clicks come to it as a visitor's points (`world.point()`), your body there the doer; a card or a name card you click opens its ticket or console on your page (found by name in the room as sent: `ticket:`, `console:`), and the wall's switch (`boardswitch:up`/`down`, with more than one board) turns the wall to another board, yours alone, never a Visit interaction; the tarot reader (`tarot:reader`, `tarot:deck`) becomes your target, her card on your page (New reading, and questions: requests to the board, `/api/tarot`), and your click on her cards (`tarot:cards`) opens them on your page with her reading, yours alone; a visitor's (Visit's `tarotcards`), or yours with **As a visitor** on (the legend's switch: your clicks do only what a visitor's do), brings them up in the room, in 3D, for everyone there (`spreadAt`, `spreadDo`: the cards fly up over her table, a scroll of her reading unrolls in front of it; it asks the board nothing); `deck` (2.1.1): your wheel over a board's deck scrolls it, yours alone (`wheel: true`, `who: "owner"`, `deckWheel`); every one cosmetic (the user: "DONT IMPLEMENT ANY INTERACTIONS OTHER THAN COSMETIC ONES UNLESS SPECIFICALLY AGREED UPON BY THE USER"), none reaches a ticket, an agent's console, a request or anything kept; every click through the room's own gate (`visitorTyped` into `visitorPoint`), checked by `board/test_visitors.mjs`. 4.0 (basedverse#59, #60): what the room does to a visitor's body (a seat, a lounger or a float, a drink, a banana, a peel thrown, a swat) is played on their own model too (`actOut`: `visit.perform`, a seat once they are down on it and again when Visit lets it go as a float drifts), and what is in its hand goes into theirs as a copy (`visit.hold`: the room's own figure keeps the thing), checked by `board/test_acts.mjs`; on a friend's plot your dude does what its room has you do (`actedOn`, from the world's frame: `act`, `holding`) |
| voice | vanilla | the panel's 🎙; your position from the world. Over WebRTC since 4.2.0: your page connects to visit (and to the offices you speak to) on UDP, any free port (`VISIT_MEDIA_PORT` to pin one); the lobby's STUN and TURN where no direct way goes |
| 🔊 over who is speaking | vanilla | Visit's own |
| phone | off | none: the room never calls `visit.phone(true)`, so it takes no calls (see Deviations) |
| a figure of the room's own that talks (`sayAs`) | vanilla | Madame Vesna, the tarot reader in the room's front right corner (her object `visit:npc:vesna`): the engine's page says her reading as she gives it, and then her answers to what you ask her aloud, `visit.sayAs("vesna", "Madame Vesna", line)`, Visit's bubble over her and the line in the room's chat; each line cut so Visit's bubble shows it whole (`tarotLines`: three rows as Visit wraps them, 96 characters at most; Visit cuts a fourth row's end with "…"), one every few seconds; with Visit not taking it (no Visit, its limit), her own bubble in the scene (`vesnaBubble`). What was asked in a whisper (a whispered reading, or turn) she whispers to its whisperer alone (3.3: `sayAs(…, { to })`, "owner" or the visitor's visit id, no bubble; never her own bubble: dropped if Visit does not take it). Described for anyone in the room (3.3: `v.figures`, `talk: "everyone"`, no actions): a visitor who clicks her has Visit's card, her name and calling, Say and Whisper, and talks with her as you do (see Interactions agreed) |
| chat bubbles | vanilla | Visit draws the bubbles |
| a line of yours to a figure of the room's; targets (3.3, basedverse#48) | own | your target is the room's own: a click on her (`INTERACTIONS` "tarot" → `setTarget`) makes her your target, in Visit too (`v.setTarget`; Visit picks none itself on your page: `v.targeting(false)`), and her own card shows (her paper doll: Visit's card is off, `card: false`: see Deviations); one Visit lets go (you left the room) closes it. Her card's Say is `v.say("@Madame Vesna …", { target })` and its Whisper `v.say(…, { whisper: her })`: the engine's page hears them (`addressed`) and asks the board for whoever said them (`tarotAddressed`: you, or a visitor as a visitor, by name; see Interactions agreed). A line you type in the chat window `@Madame Vesna …` is meant for her the same way (Visit's `@` offers her); one that starts `@Vesna` or `Vesna,` your page asks her itself (`tarotHeard`, skipping any already meant for her). New reading's line is plain ("A new reading, please, Madame Vesna"): the deal is asked from her card, not sorted again |
| its database (0.20: chat history, pictures and links shared, bans) | vanilla | Postgres as the compose service `visit-db` (postgres:17-alpine, the `visitdb` volume, 127.0.0.1:`VISIT_DB_PORT` or 7432), its password made once in `.visit/db-password`; `visit_up` builds visit-client, brings `visit-db` up and runs `visit-client migrate` before starting it; `visit_down` stops both |
| your name, shown to others (0.20.1) | vanilla | `startVisit` and the Who-are-you form call `visit.setName(me.name)`: your lines, voice and panel row go by it, not your office's name |
| pictures and links in chat; a room's voice (🎧); bans; voice across the seam; @ mentions | vanilla | the panel's chat window and person rows (0.20) |
| the mailbox (📬 what came while nobody looked), voice messages (🎙), whispers left for someone away (⏳), a time before each line, Tab to where a line goes (0.22.0) | vanilla | the panel's chat window; nothing in the room. Its database at schema 2 (`visit_up`'s `visit-client migrate`: "applied: 2 mail") |
| chat window (say, whisper, a room, a world, the lobby) | vanilla | the panel's; at the bottom left, the room's buttons being at the top (`--visit-chat-bottom` in the room's CSS: 12px, over the panel on a narrow screen) |
| mounts | vanilla | the room's own (Visit has none: what they are is the room's; the user: "default_office (and other sims) would dictate what items should be"), M or the panel's "your mount": the lowrider alone (`lowrider()`, given to `v.possessions` in `startVisit`; three times walking pace, lift 0, it flies; clips walk, run, and fly with its wheels folded flat; the user: "make this object for me and allow me to drive around in it", then "ok want it to be able to fly. i don't want carpet or horse though"), in which you sit at its wheel (`atWheel` in `stepMe`, the avatar's `ride` clip), A/D steer it and never sidestep, its nose and tail felt by what is in the way (`CAR.reach`), nothing in the way over the treetops (`skyline`); visitors drawn at their pose's height, astride when riding (`stepVisitors`); `eye: VISITOR_EYES` so mounts stand on the floor; at home your dude lifted onto yours (`rideLift`), at its pace (`rideSpeed`), and on a flying one Space/X fly you in place of the jump (`riding()` in `stepMe`) |
| physics (falling, flying, riders) | own | the room's (Visit 0.19 has none): `visitorMove` walks visitors at 3 m/s (riders at their mount's pace, `walk.mounts`), their eyes VISITOR_EYES up (riders their mount's `lift` higher); one arriving in the air (`spawn` honours `at.air`) floats down at 1.5 m/s; on a mount that flies, Space/X fly them (4 m/s, at most 40 m up), and over the treetops (`skyline()`) nothing stops them, as your own lowrider at home |
| telemetry (⏱) | vanilla | the panel's ⏱ (a tool: nothing in the room) |
| the lobby (3.1): in it only while signed in; users and worlds (a world public or with a password; 3.3: its members are your guests, in through your open door; whose rooms sit round yours you pick, 🗺 + in the panel, at most 8; a world's chat `/g`); ignoring someone in chat (3.3: a click on a name, `/ignore`); your line in the list of who is here | vanilla | the panel (worlds: Find, New, the owner's ✕ ⛔ ♛ and password; your line; your door) and its chat window; the board's sign-in is what counts (signed out: out of the lobby). Nothing in the room |
| the lobby's page in a browser, as this workroom (3.1: linked browsers; 3.2: a code confirmed both ways) | vanilla | the panel's 🌐 Lobby (a tab, linked), 📱 Phone (a QR code for a phone), Link a browser (the code `https://basedverse.app/` shows, then the room's QR code, scanned by your phone to confirm that browser, or its 6 digits typed there; the browsers linked, each to unlink); 📱 On my phone puts the lobby on your phone. Nothing in the room |
| the room's sounds | own, and Visit's for visitors (3.4, basedverse#36) | your page plays its own, as before (Web Audio, made in the page: its mixer, its echo, never repeating), and Visit's for your room is off on it (`v.ownSounds(false)`: see Deviations). For everyone else in the room the engine's page renders the same makings once (OfflineAudioContext, `vsRoomSounds`: each loop 20 s with its end folded over its start, the DJ one drop's cycle exactly, on the dancers' beat as a visitor draws them; the beds day and night; the bursts; each environment's echo in them, made again when it changes) and gives them to Visit (`v.sounds`, 30 WAVs): the marks (`sound:<kind>`, `soundMark`) carry `userData.visitSound`, the bed is one `bed: true` mark (day or night, as room.dark), the DJ's is hidden while the music is off (the room's), and each burst goes as it pops (`vsPop`: `v.soundAt`). Your visitors hear it by distance and side, by Visit's 🔈, at your mixer's levels (the user: "my actions on this slider should control the sound for my visitors"): the mixer's levels and mutes are the room's (`room.mix`, the board's, every page of yours plays by them), and each loop's Visit mark (`vsMark`, a child of its `sound:` mark) has its channel's `volume`, made again when that changes (Visit reads it as the mark comes in), hidden while muted (`vsMix`); the bed at the ambient's. The bursts go as they are, as on your page |
| landscapes, offline plots | vanilla | found by the room itself, not marked by hand: every mesh of the environment's building (`scenery`) marked `visitStatic` as it is built (`markLand`), but those marked `userData.live` (they move from the start: the pool club's woofers, decks, waterfall, sparks, stage beams); one seen to move or change is taken out for good (`stepLand`, once a second); a theme change re-baselines and calls `landscapeChanged()` (`landRebase`) |
| teleports (5.1, 5.2, basedverse SPEC 8.15) | vanilla, with the room's own popup and rule | a pad of the room's own, `teleportPad` (2026-11-06; the user: "the sim/room should be able to styleize the teleportation pad any way they want"): the tarot booth's corner mirrored, front left, three metres in from the left wall (clear of the jungle sweatshop's boards on it) and three back (`padPlace`), facing into the room toward its front middle; a low round step with two rings of light, a glowing middle and three marks, in each environment's look (`PAD_STYLE`: steel and cyan in the office, mossy stone and green in the jungle, rock and embers in the cave, pearl and neon pink at the pool club), its glow breathing (`stepPad`, `userData.live`); pets go round it (`petBlocked`), people walk over it. The engine's page gives it to Visit (`teleport: { pad, allow }` in `startRoom`'s `v.host`): arrivals stand on it (`spawn` puts them where `at` says). Who may come: the room's rule (the popup's Who can come, `room.teleport` on the board; unset, everyone in your worlds: the user, "right now i want it set to 'everyone in this world'"). How anyone looks teleporting is their own animation (5.2): yours is the beam (`teleport-beam.js`, its "out" 2 s), and the room plays whatever anyone teleporting here or away brought. Your page's teleport widget is the reference UI's (its ✦ and T, in this room's copy, 5.1.1), and the room opens it its own ways too (2026-11-06; the user: "i should be able to invoke it without having to touch my teleport pad (so a button somewhere in my UI or a hotkey). also clicking on my teleport pad should do the same"): **Teleport** in the top bar (lit while the list shows) and a click on your pad (INTERACTIONS' `teleport`: yours only, on your screen; `padAt`, `openTeleports`, the panel's `ui.teleports`). A visitor's click on it does nothing. Checked headless in all four environments (screenshots, and through a toJSON round trip) |

State is one of:
- **vanilla**: Visit's default. This includes the room's own choices where Visit has none: what its landscape is,
  where the phone stands, which interactions it has, its avatar. Those go in "how", not under Deviations.
- **own**: the room replaces or turns off a Visit default (its own phone, `speaking: false`, its own panel). See
  Deviations.
- **off**: not used.

## Widgets

Each piece of this room's UI on Visit's API (basedverse's `visit/widgets/`): the reference UI's, this room's copy
(`forge/board/visit-ui.js`, served at `/visit-ui.js`, your page's alone, as it came from v5.2.0), but teleport's.

| widget | whose | where, and what differs |
|---|---|---|
| panel | reference | top right (`.visit-box`), the board's Sign out as a row under it |
| chat window | reference | bottom left, `--visit-chat-bottom` 12px |
| target card | none (Visit's off: `card: false`) | the tarot reader's own card instead (Deviations) |
| map | reference | the panel's Map |
| teleport | own: a popup | the room's Teleport popup (2026-11-06; the user: "i would prefer a popup where i can select where i want to go"): opened by the top bar's Teleport, T, and a click on your pad; Where to (your office first, onto your pad from anywhere on your land) and Who can come (the room's rule). The panel's own list off: `VisitUI.panel(..., { teleport: false })` |
| ⏱ corner | reference | the panel's ⏱ |
| stage | reference | shows nothing here: this page draws a world |

## Sign-in (inside visit)

- Offered 2026-10-28; the user: "go ahead and implement on my default_office project, too pls" (yes).
- Public routes (work signed out): none but the sign-in page (`GET /login`, `POST /login`).
- Where the server listens: 127.0.0.1:3001 only (`BOARD_HOST`, bin/lib/board.sh). The sign-in API inside
  visit, on 127.0.0.1:7403 (the board's `AUTH_HOST=127.0.0.1`); `/v1/visit` is not used: the board relays.
- The engine: an API key of its own, `engine`, made by visit at each of its starts (the one before gone), with which it
  fetches `/room?role=host` and the board's API; a session never gets that page.
- The agents: an API key, `agents` (`REPO_BOARD_KEY`, sent by `forge.py`).
- The console: bin/consoles connects out to the board (its key, `consoles`) and takes the room's calls from it
  (auth/INTEGRATING.md step 9): no port, no token in any page; `/api/console`'s token route is gone.
- Other devices (step 10): the board's proxy listener on 127.0.0.1:3002; `REMOTE=lan` serves it with Caddy
  (https://<LAN address>:3443, its own certificate), or `tailscale serve`. Visit works there too now: your page there is
  relayed to visit-client by the board (`/visit/ws`, the proxy's origins in visit-client's `--origins`), the room
  itself staying in the engine at home.

## Interactions agreed (beyond cosmetic)

- **Visitors talk with the tarot reader** (2026-11-01; the user: "i also want my visitors to be able to interact with
  madame vesna", then of her table: "i think the shared reading is fine. we want it to act like a real shared world.
  not an instanced reading", and of the limit: "i think we rate limit someone if they do more than 10 readings in a
  minute. the cooloff would be 3 mins. that way we make sure they don't spam our AI GPU"). What it reaches: the board's
  `/api/tarot/ask` through the engine's page (`tarotAddressed`), which calls the model for them; her table, shared
  (a visitor's reading replaces yours on it, and its talk). Held by the board: more than 10 readings in a minute (or
  questions) rests anyone but you 3 minutes (tarot.py, `RATE_N`, `RATE_WINDOW`, `RATE_COOL`); a whisper stays its
  whisperer's (an answer aloud never sees one; your page has a visitor's without its words). Not a Visit interaction
  type: lines meant for a figure (`addressed`), the room's own to answer. 2026-11-03 (the user: "can we have a single
  card mode for madame vesna? Feel free to use my qwen 3.8 local inference server to interact with the user who is
  asking for a reading. if they want a single card reading or triple card reading. obv be mystical when having a that
  dialog. max 15 inference calls per minute per user/visitor"): asked for a reading without its number, she asks one
  card or three (the model writes it, and sorts the answer), so a visitor's reading costs a few more calls; held by the
  board: the model called at most 15 times a minute for anyone, you too (`INFER_N`, `INFER_WINDOW`), and while one
  person chooses, anyone else but you waits (`CHOOSE_TTL`).

## Deviations from vanilla

Only what replaces or turns off a Visit default, each as the user asked for it. An upgrade re-applies it; if a
release changes it, ask the user.

- **The phone: none** (2026-11-01; the user: "remove the phone from my office hah"). The room never calls
  `visit.phone(true)` (it stood on a stand beside your spot at the front); an upgrade must not put it back.

- **Visit's target card: off on your page** (3.3, `Visit.panel(box, v, { card: false })`; 5.0: `VisitUI.panel(box, v,
  { card: false })`, this room's copy of the reference UI, `forge/board/visit-ui.js`, otherwise as it came; the user: "i like the NPC
  paper doll/player card", and "everyone does what they want in their own world. that's the beauty. allows people to
  build stuff that might even be better than what we think. we just build the plumbing."). The tarot reader's card is
  the room's own. `card: false` is all or nothing: a person you target here (were the room to let you) has no card.
  And Visit picks no target itself on your page (`v.targeting(false)`): your click on her does. Visitors' pages are
  theirs: they have Visit's card.

- **Visit's room sounds: off for your own room on your page** (3.4, `v.ownSounds(false)`). Your page keeps playing the
  room's sounds itself, as before: its mixer (music, ambient, crowd, each its level and mute in this browser), each
  environment's echo, and a synth that never repeats. Visit plays the room's sounds, rendered from the same makings, for
  your visitors (and every other room's for you). Were Visit's on here too, you would hear everything twice.

- Not deviations, the room's own beyond Visit: toasts for errors (the room's `toast`; 4.0 has no knocks nor answers); the camera's
  see-through fade (`stepFade`, worn only while this page draws).

Switched to vanilla on 2026-10-26 (they were the room's own, made before Visit had them): the phone (its own model
and its click-open phone box), the 🔊 marks, and the toast of where a friend's world put your room. To have the room's
own back, it is a deviation: write it here first. (The phone has since gone altogether: Deviations.)

## Open requests for basedverse

- 2026-10-26: a visitor's jump (the user noticed they cannot see their visitor jump). Visit's input has no jump, and
  visit-client drops unknown input fields: it needs a `jump` in Visit's input (SPEC 8.2), Space in the stage's and
  the world's input, and the host raising the pose's y.

## History

- 2026-11-06: teleports the user's way (basedverse 5.2.0, PR #86, released; `VISIT_VERSION=v5.2.0`, bin/lib/visit.sh's
  default too, and `bin/start`: visit and the engine report 5.2.0, the room hosting). Their asks: a popup to
  pick where to go ("an easier UI to navigate since it gives more space"), with a tab for who may teleport into their
  room ("right now i want it set to 'everyone in this world'"); teleporting to their own room from anywhere on its land
  ("whenever i am far away and want to get to my office"); and "the animation should play for 2s and then execute the
  teleport". And Visit forces no animation on anyone: "if i'm doing a teleport in my own room, my default_office should be
  handling the animation. if my visitor is teleporting out of my room, my room would be responsible for playing the
  animation that they provided during their visit (same as they shipped their other animations & mounts, etc)". So:
  the room's own teleport widget, a popup (Widgets); who may come, the board's `room.teleport` (`teleportRule`: worlds,
  people by key, anyone; unset, everyone in your worlds), answered by the room's page (`teleportAllowed`); your beam
  (`teleport-beam.js`, the room's copy of basedverse's example: its "out" 2 s) given Visit as your teleport animation,
  beside your lowrider; and the room's page plays what anyone teleporting here or away brought (`TeleportBeam.play`).

- 2026-11-06: the reference UI's 5.1.1 (basedverse's widgets: `visit/widgets/`, one page per piece of a room's UI a
  feature needs, after Keyvan's room upgraded to 5.1 without the teleport list). This room's copy is the v5.1.1
  reference as it came: its ✦ list opens from T and from `ui.teleports(on)` now, so the room's own T and its own
  handle went (it was a deviation for an hour); the top bar's Teleport and the pad's click call `ui.teleports`. The
  binary stays 5.1.0 until 5.1.1 is released (nothing of this needs it). The Widgets section is new.

- 2026-11-06: 5.0.0 → 5.1.0: teleports (basedverse PR #84; `VISIT_VERSION=v5.1.0`, bin/lib/visit.sh's default too;
  the lobby at basedverse.app needs its 5.1 redeploy, basedverse's `bin/deploy-lobby`, for asks to other rooms). The CHANGELOG's steps: visit and its visit.js together; the
  pad (done before the release, below); who may come: Visit's own rule, nothing of the room's; `spawn` already puts
  someone who steps in where `at` says, so arrivals stand on the pad; the copy of the reference UI has ✦ (brought
  over before, below); the beam in the pad's colour. 5.1 also fixes a plot missing from your world after your page
  reloads while the room watches it (its scene came before the page made its world).

- 2026-11-06: teleports, built in basedverse for 5.1.0. The user asked for a
  "teleport" animation Visit plays as it takes someone to another connected world, played in the other user's world
  too ("if you teleport out of their world, the animation plays as well"); an inbound teleport pad in each room; a
  teleport button in the visit HUD listing only the rooms that answer "can I teleport to you?" with true, by each
  room's own rule ("independent of basedverse/visit client"); and each room styling its pad as it likes. This room has
  its pad (Features: teleports) and the ✦ Teleport rows in its copy of the reference UI; they come alive with
  `VISIT_VERSION=v5.1.0` (and the lobby redeployed with 5.1's), then `bin/start`.

- 2026-11-05: 4.3.2 → 5.0.0: Visit draws no UI in the page (basedverse #82, PR #83). The user and Keyvan agreed that
  the HUD, the chat window, the ⏱ corner and the rest should not be Visit's code at all, so a room that changes them
  is never broken by an upgrade. They now live in a reference UI, basedverse's `visit/examples/ui/visit-ui.js`, which a
  room copies and owns. This room's copy is `forge/board/visit-ui.js`, unchanged from the v5.0.0 reference (a header
  says where it came from). The board serves it at `/visit-ui.js`, and only your page loads it (the room's engine page
  has no UI). `Visit.panel(box, v, { card: false })` became `VisitUI.panel(...)` and `Visit.stage(v)` became
  `VisitUI.stage(v)`; the classes are the same, so `.visit-box`, its sign-out foot and `--visit-chat-bottom` fit as
  before. The card stays off (Deviations). To take a later release's UI changes: diff the reference between the two
  tags, and bring over what the user wants. The board's new route needs it started again (`bin/start` does).

- 2026-11-04: 4.3.1 → 4.3.2: voices heard near each other in a huddle, and as soon as someone steps in (basedverse
  PRs #80, #81). The user said: "if i walk into keyvan's sim, he cannot hear me until he restarts" and "if we are in
  using the huddle or whatever, and you get close to me, our voice cuts out until you get out of range", and asked
  whether it was basedverse or one of the sims. Basedverse, all of it, on the listener's side: a watcher stepping in
  was told to the room's page alone (a person on a page of their own, as here, had them outside until a reload);
  near someone in a huddle, its office held their huddle voice back for their voice in person, which a page sends
  only with 🎙 on (a huddle runs the microphone with it off); the page played the first way by route even when it
  could not hear it; 🎙 toggled while a huddle or call ran the microphone took effect only later (off: still heard in
  person meanwhile); and a huddle's voice could go in person in its starter's room. Now near huddle-mates are heard
  from where they stand at the huddle's volume (no fading with distance). Each fix is the listener's: a friend hears
  this room's people right once they run 4.3.2 too. Nothing to change here: step 2 (the relay's 64 MiB, hung up with
  no close frame) was done already (df675e3, 8eb521f); two comments of the relay's still said 4408, now right.

- 2026-11-03: 4.3.0 → 4.3.1: the sky drawn for less, and ⏱ counts draw calls (basedverse PR #78). The user asked
  whether the room could be made cheaper to draw. Measured on your page at home: 6,611 objects (5,023 meshes, 4,093
  casting shadows); a frame drew 557 colour and 683 shadow draw calls; the shadow pass took 1.9 ms of about 6 ms; the
  CPU does it, while the GPU (an RTX PRO 6000) sits near 7%. Visit's sky pass walked the whole room for nothing
  (1.2 ms a frame): gone. The ⏱ now says "draw calls a frame: colour + shadows". Nothing to change here. What is left
  is the room's own (its agent's): shadows redrawn on a beat (Visit 4.3.1's sky pass leaves `needsUpdate` for the
  room's pass), fewer small casters, and fewer colour draws than the batches leave.

- 2026-11-03: 4.2.0 → 4.3.0: people named by the name they go by (basedverse PR #77). A host's `visitor.name` is now
  the visitor's own name, not their office's, and it can change while they are here; `visitor.office` is their office's.
  The room was ready for this before the upgrade (52c5637): a visitor's tag is made again for a new name, and their
  random look comes from their office's key. Your own room is no longer listed among the rooms round yours. 4.2 and 4.3
  offices meet as before, and the lobby needed no redeploy.

- 2026-11-03: no Visit upgrade. Madame Vesna reads one card or three, and asks which first (Interactions agreed): her
  asking is said as any of her lines (`sayAs`, aloud or whispered back); `v.figures`' about says "One card, or three".
  A visitor answers in chat as they talk to her; your card has One card and Three cards. One card lies in her table's
  middle and hangs alone in the 3D spread.

- 2026-11-03: 4.1.0 → 4.2.0: voice over WebRTC (basedverse #71, PR #75; the user: "i don't want 2 different ways of
  doing anything ... i do care about lower bandwith options"). Voice was choppy (the page played packets as they came,
  60 ms behind, and a relayed link ran over TCP); now each page connects by WebRTC to the offices it speaks to or hears
  from, the browser's jitter buffer and loss concealment play it, and the lobby carries STUN and TURN. Nothing to change
  here (the panel's 🎙, calls, talk and huddles as before). Every office in the lobby upgrades (a 4.1 office and a 4.2
  one hear each other no more), and the lobby is deployed again for its TURN (UDP 3478, 49152-65535 open on its
  server): until then, voice between offices only goes where a direct way does.

- 2026-11-02: no Visit upgrade. The tarot reader's reading in 3D for your visitors (the user: "if you're a visitor or
  {view-as-visitor-world-switch-for-me}, render a 3d version of the tarot reading ... i still want animations and
  such"). A visitor's click on the cards on her table brings them up over it, in the room, for everyone there: they fly
  up and turn over, a scroll of her reading unrolls in front of the table (a card clicked: its meaning on it; the seal
  rolls it up). Four more cosmetic types (Interactions), anyone's; a whisper's words never on it. Your page: **As a
  visitor** in the legend, your clicks then a visitor's (no console, ticket or wall switch; her target stays). Her
  bubble rises over the cards while they are up (a hidden mark in her, since Visit puts it over all she is made of).
  Then (the user: "the 'as visitor' should be a choke for all interactions in my world ... basically a way to debug"):
  As a visitor is the room's (`room.guest`, the board's), and `owns` the room's one check of you against a visitor:
  your clicks, your body's points on the engine's page, the decks' wheel, your lines to her; walking unchanged.
  The scroll in pages (a canvas redrawn reaches visitors at most once a second, so scrolling juddered): two more types,
  `tarotprev`, `tarotnext` (its gilt ▲ ▼); the seal a gilt ✕, "close"; the scroll longer, just under the cards.
  Her paper doll card as a standing 3D card by her booth (with New reading for visitors, `tarotnew`) was tried and taken
  out (the user: "i don't like that static thing by her booth tbh. let's remove that in general"): don't put it back.

- 2026-11-02: 4.0.1 → 4.1.0: a scene's big pictures and meshes by name, once (basedverse #67, PR #68; the user, on
  #37's reporter having to shrink their room: "open a new ticket and start hammering it out"). Nothing to change
  here: this room's drawn boards and signs (26 `CanvasTexture`s) go to visitors once each, by hash, a piece at a time,
  and are kept, so the whole scene a page joins with is its structure; small ones go inline as WebP. A board redrawn
  goes whole with each redraw, as before.

- 2026-11-02: 4.0.0 → 4.0.1: a room's figures heard near them, in the room alone (basedverse PR #66; the user's
  report: "NPCs' /s are going across world boundaries"). A figure's line (`sayAs`) went to every world member whose
  world showed the plot, wherever they stood; now it goes as a person's `/s`: to those in the room within 45 m of her
  object (`visit:npc:vesna`, which Visit measures from). Nothing to change here. Your own page has her lines only
  within 45 m of her too: out in the yard (the plot is 110 m) a reading's lines come as "said_far", which this page
  does not listen for, so her portrait stays still and the lines are not in your chat.

- 2026-11-02: 3.4.3 → 4.0.0 (the user: "go ahead and implement the upgrade to default_office"). Knocks and links are gone
  (basedverse#57): the room's toasts for them came out (they never fire now), and the stage's comment; nothing else
  here called them, and a visitor walks in from a world as before. 🎧 follows the chat box (#58): talk and world
  huddles from the panel's chat window, nothing in the room. The room acts out what it does to a visitor on their own
  model (#59, #60): `actOut` beside the room's own acts (`visit.perform`; `visit.hold` with a copy of the cup, the
  banana or the peel), your avatar's clips for the acts and its `hand`, your dude posed on a friend's plot by the
  world's frame (`actedOn`); `board/test_acts.mjs` checks them (48 checks). Friends' plots cost less (#42): nothing
  to do here (the engine ticks at 30 a second with a visitor, Visit's sample rate). Needs the lobby deployed at 4.0
  for talk and huddles, and the user's `bin/start`.

- 2026-11-01: no Visit upgrade. The mixer's levels and mutes are your visitors' too (the user: "my actions on this
  slider should control the sound for my visitors"): the room's (`room.mix` on the board), given to Visit as each
  loop's `volume` (SPEC 8.13), within today's API.
- 2026-11-01: 3.4.2 → 3.4.3: security fixes (calls, the panel, pictures sent over a link, the lobby's broadcasts;
  the user: "fix 1,2,3 & 4"). Nothing to change here: this page answers calls only through Visit's phone, which
  answers only a ringing one.

- 2026-11-01: 3.4.1 → 3.4.2: a line said aloud is heard within 45 m of its sayer, as their voice is
  ([#54](https://github.com/sarmiena/Basedverse/issues/54); the user: "let's fix the newest "say" issue"). Nothing to
  change here: the tarot reader hears your own lines (`mine`) and hers (`sayAs`), neither of them filtered by distance;
  visitors ask her through Visit's targets (`addressed`), which 3.4.2 leaves alone. A visitor's line said far from you
  now comes as "said_far" (this page does not listen for it).

- 2026-11-01: 3.3.0 → 3.4.1 (3.4.0 never released): the room's own sounds, heard by your visitors too ([#36](https://github.com/sarmiena/Basedverse/issues/36),
  landed; the user: "we need plumbing to allow sound to be played for visitors in my room", then "implement the sound
  into default_office"). The engine's page renders the room's sounds once and gives them to Visit; your page keeps its own
  (Deviations).

- 2026-11-01: no Visit upgrade. Visitors talk with the tarot reader (Interactions agreed): `talk: "everyone"`; their
  lines meant for her asked of the board for them, by name; her whispered answers back to their visit alone; her
  table shared; the board's limit for anyone but you; your card and scroll say whom the reading was for and when.

- 2026-11-01: 3.2.2 → 3.3.0 (3.2.3 on the way: friends' plots out of view not drawn; nothing in the room). Worlds are
  your guests (the ★ list gone; the room never used it), you pick whose rooms are round yours (🗺 +, at most 8), and
  ignoring in chat: all the panel's. Targets ([#48](https://github.com/sarmiena/Basedverse/issues/48), landed): her
  card's Say and Whisper go as lines meant for her, the engine's page asking the board (`tarotAddressed`, yours
  alone); her whispered answers to you alone (`sayAs` with `to`); she is described for visitors (`v.figures`, `talk:
  "owner"`); Visit's card off on your page and its own picking off (Deviations), your click setting the target.

- 2026-11-01: no Visit upgrade. The tarot reader is your target on a click, her card on your page, and takes questions
  about her reading: aloud (your line said in the room's chat, `@Madame Vesna …`, her answer with `sayAs`) or
  whispered (never through Visit); a line of yours in the chat window that starts with her name asks her too (the
  user: "i also want the room to see my comms with her if it's not whisper").

- 2026-11-01: no Visit upgrade. The phone taken out of the room (the user: "remove the phone from my office hah");
  and a figure of the room's own that talks: Madame Vesna, the tarot reader, says her readings with `sayAs`, each line
  cut to fit Visit's bubble whole (the user: "she should even /s her reading. and it should be a bubble over her head.
  make sure it doesn't truncate").

- 2026-11-01: 3.2.1 → 3.2.2: the room's QR code is scanned by your phone to confirm the browser waiting on your
  computer, which then goes in by itself; the phone is not linked (the user: "i thought doing the scan on my phone
  would let my computer browser know that i successfully scanned the QR code"). Nothing in the room.

- 2026-11-01: 3.2.0 → 3.2.1: scanning the room's QR code from a phone links it however the phone opens it (the
  user: "this didn't go away after i scanned the QR code"). The lobby's fix; nothing in the room.

- 2026-11-01: 3.1.0 → 3.2.0: the lobby's page is a world browser (the user: "the list of worlds should kind of be the
  focal point"), and linking a browser by its code takes a last step (the user: "a final step where we scan a QR code
  to automatically have the browser verify the connection"): the panel shows a QR code and 6 digits that browser
  shows back. Nothing in the room. `bin/lib/visit.sh` defaults to v3.2.0 (`VISIT_VERSION=v3.2.0` in `.env`).

- 2026-11-01: 3.0.0 → 3.1.0, users and worlds (the user: "there should only be users & world. and world is either
  public or password protected"), and the lobby's page in a browser this workroom links (🌐 Lobby, 📱 Phone's QR code,
  or the code the page shows, typed in Link a browser). Nothing in the room: the panel has it all. 3.0's door open to
  anyone and its list of rooms are gone. `bin/lib/visit.sh` defaults to v3.1.0 (`VISIT_VERSION=v3.1.0` in `.env`).

- 2026-10-31: 2.1.1 → 3.0.0, the lobby in Rust at `wss://basedverse.app/` (the user: "make it its own rust project...
  an interactive web service... like diablo ii lobby", then "the lobby should be lobby of world groups"). Nothing in
  the room: the panel and chat window bring world groups, the list of rooms and `/g`; the sign-in it now needs is the
  board's own. `bin/lib/visit.sh` defaults to v3.0.0 (`VISIT_VERSION=v3.0.0` in `.env`); `bin/start` moves the old
  lobby address (the server's bare IP) to `wss://basedverse.app/` by itself, and offers basedverse's lobby when none
  is set.

- 2026-10-30: the room's sounds (the user: "do we have the ability to add sound? it's so quiet given these
  environments. even something ambient for background. interactions too"; synthesized, "just for me for now"). Room
  only, no Visit change, nothing new for a visitor to reach: the engine's page adds empty `Object3D`s named
  `sound:<kind>` (the scene carries them, names and all; Visit filters materials and images, not object types) and
  names its burst sprites `burst:<kind>`; the person's page plays them (Web Audio, by distance and side from you). The
  pool club's beat is the wall clock's now (`stepCrowd`), so your page's DJ is in time with the dancers it is sent.
  Visitors hear nothing: basedverse#36.

- 2026-10-30: a fourth environment, the Vegas pool club (the user: "make me another world. i want to model it after a
  vegas day pool club. waiters and NPCS having a good time. having drinks at the grotto pool bar."). Room only, no Visit
  change: one more interaction type, `cocktail` ("a cocktail", cosmetic, through `visitorTyped` like the rest), on
  both its bars (the tiki bar in the room, the grotto's swim-up counter); its free loungers, cabana sofas and bar
  stools are `sit`. The pool is the shell's `ground` (its floor 1.1 m down), so visitors wade it as you do
  (`visitorMove` through `groundUnder`). Its crowd (some fifty) lives in the shell: their pivots move, their meshes stay
  landscape under them; what moves of itself is `userData.live`, never landscape. Measured headless: 144 objects moved
  per 33 ms scene tick, the jungle sweatshop's 129.

- 2026-10-30: the engine's debug hook off unless asked (the user: "can you update my ./bin/start to have a --debug
  mode?"): `bin/start --debug` passes `ENGINE_DEBUG=1` (`visit_up debug`), `bin/start` alone does not. It had been on
  since 2.0 (TEMPORARY, proving it at the desk): /debug/eval on 127.0.0.1:7405 runs code in the room's page with no
  password, for any program on this machine.

- 2026-10-30: 2.1.0 → 2.1.1: the board's decks scroll again from your page, with the wheel: the engine's page gives
  them Visit's interaction `deck` (`wheel: true`, `who: "owner"`: yours alone, never a visitor's; cosmetic), your
  page sends each notch over the deck as a point (`world.point(hit.point, { wheel })`), and `deckWheel` moves the
  cards, which come back as sent (room.html, `visitAtLeast("2.1.1")`: off before 2.1.1). Also in 2.1.1, nothing to
  do here: the engine's pipe on Windows (#27), visit as a library (#29). `VISIT_VERSION=v2.1.1` in `.env`. Run
  before the release was published (the user asked): the release workflow's own Linux build, put in
  `.visit/bin/visit` (2.0.0 kept beside it, `visit-2.0.0`), and `bin/start`. Checked live: visit and the engine's
  `/health` say 2.1.1, the room hosting, back in the lobby, the linked office let in at the open door.

- 2026-10-30: 2.0.0 → 2.1.0 (the user: "this whole hud should be collapsible"): the panel folds on its caret (to its
  first line, with a knock or a ringing call), says Visit's version at its foot (and in the ⏱ report), and its
  "Enter to chat" line is gone. The room's own sign-out row under the panel folds away with it
  (`.visit-box.visit-folded > .visit-foot`, room.html). `VISIT_VERSION=v2.1.0` in `.env`; the user's `bin/start`
  fetches it.

- 2026-10-29: 1.0.0 → 2.0.0: one `visit` (the release) in place of the visit, auth, engine and visit-db services; the
  1.x database imported; then the board and Caddy out of Docker too (the board's data and Caddy's authority copied
  out of their volumes). No Docker left. `ENGINE_DEBUG=1` still on (temporary, proving 2.0 at the desk). Not yet
  started on the release: the user left before its first run (`bin/start` when back).

- 2026-10-26: 0.8 → 0.9.1: typed interactions, the room's own phone, avatars both ways.
- 2026-10-26: 0.9.1 → 0.10.0 (default_office's own session): its 🔊 marks and seen toasts.
- 2026-10-26: 0.10.0 → 0.13.0: chat (the panel's Say box, Visit's bubbles); the landscape (`markLand`, `stepLand`,
  `landRebase`); the phone, the 🔊 marks and the seen toasts switched to Visit's vanilla ones (the room's own taken
  out). Checked with a smoke test (a local lobby, a second visit-client, the basedverse test room as a friend): all
  passed, no page errors.
- 2026-10-26: at 0.13.0, by basedverse:visit's rule that a deviation replaces or turns off a Visit default: the
  interactions and the landscape, choices Visit leaves to every room, are vanilla (their choices in "how"); no
  deviations.
- 2026-10-26: 0.13.0 → 0.14.0: the panel's ⏱ (telemetry). The jungle's three boards (usage, safety, tally),
  repainted every second, are marked changed only when their pixels changed (`stepBoards`): each change went to
  every visitor and watcher, and their encoding stalled this page some 200 ms a second; Visit 0.14 also encodes a
  redrawn canvas in the background. The landscape's check compares numbers, not strings (`landSame`). Measured in
  the jungle with a visitor: Visit's slowest round 220 ms → 7 ms, no stalls.
- 2026-10-26: 0.14.0 → 0.14.1: the ⏱ shows the room's drawing apart from Visit's own work. The safety board's
  "time since last accident" counts minutes, not seconds (`since`): it changed, and went to every watcher, every
  second; now once a minute.
- 2026-10-26: 0.14.1 → 0.15.0: the chat window (Enter; say, `/w` a linked room, `/1` the lobby), placed over the
  legend (`--visit-chat-bottom`). The room's keys already left a text box alone, and Enter and `/` to Visit.
- 2026-10-26: 0.15.0 → 0.16.0: `/w` whispers to a person (`/room` for a linked room); the panel shows one row a
  person (their look, Kick, Unlink inside); visitors look like their own model by default (the user asked: "default
  should be 'their model'"). Nothing in the room changed.
- 2026-10-26: 0.16.0 → 0.17.0: flying (Keyvan's PR #7), the room's choice: left off here (vanilla). Nothing in the
  room changed.
- 2026-10-26: 0.17.0 → 0.18.0: mounts. Visitors' figures at their pose's height (they were always on the floor),
  astride when riding; `eye`; your own riding at home (lift, pace, carpet flight on Space/X). Smoke-tested: a
  visitor's horse under them in the jungle, their figure on it; your carpet under your feet at home.
- 2026-10-26: at 0.18.0: your own mount, the lowrider (a white, gold-trimmed voxel car the user showed and asked to
  drive), given to `v.possessions`; your dude sat at its wheel, at home and (its avatar's `ride`) in other rooms.
  Checked headless (the model, its JSON and clips, its pace, steering, what stops it) and with a smoke test (Visit
  draws it under you at home, a visitor sees it, a room you visit asks for it and draws you in it).
- 2026-10-26: 0.18.0 → 0.18.1: Visit's own horse and carpet gone from Visit (it is the plumbing; items are each
  room's); this room's own instead, beside the lowrider (`horse()`, `carpet()`, one `v.possessions` list of the
  three), their moves as clips so every room plays them. Worlds draw rooms farthest first (a name over a head no
  longer cut by a plot's edge). Smoke-tested: the three listed, a visitor's own pony under them, your carpet under
  you at home.
- 2026-10-26: at 0.18.1: the lowrider flies (Space up, X down, wheels folded flat in the air), and is the room's only
  mount: the horse and the flying carpet taken out (the user: "ok want it to be able to fly. i don't want carpet or
  horse though").
- 2026-10-27: 0.18.1 → 0.19.0: physics are the room's (the user: "i want each room to dictate its own physics"):
  Visit no longer floats arrivals down, flies anyone, or lifts and paces riders. `visitorMove` does it now (as
  Visit did: float down at 1.5 m/s, riders lifted and paced, flying mounts on Space/X, clear of the room over the
  treetops); `spawn` honours `at.air`; `walk: { speed: 3, mounts: true }`.
- 2026-10-27: at 0.19.0, positions are feet (the user: "cleanup the concept of positioning so that it's consistent"):
  `spawn` and `visitorMove` return the feet (0 on the ground, a rider at its mount's `lift`, a sitter at the seat),
  not the eyes; `VISITOR_EYES` is only the camera height of a visitor drawn as one of the room's dudes (`eye`).
  Visit draws visitors' own models now: `stepVisitors` hides its dude (its name tag kept) where `look` is
  "theirs", instead of putting `avatar` in it. Not yet checked live: the room was stopped for the night.
- 2026-10-28: 0.20.1 → 0.21.0 (the user: "go ahead and implement on my default_office project, too pls"): the board's
  sign-in (board/auth.py: one check before every route; /login from auth/web/login.html; sign out in the kanban and
  the room; writes from other sites refused; /api/visit from auth-client's /v1/visit, /api/console for the owner's
  session only; the agents' API key, REPO_BOARD_KEY, sent by forge.py); the database moved over (the superuser
  renamed basedverse, visit-client's tables into its schema, 340 lines of chat kept). Checked live: signed out, every
  page goes to /login and every API route answers 401; the agents' key reads the board; each role is refused the
  other's schema. Tests: board/test_auth.py (a stand-in auth-client), test_consoles.py signed in. Not yet: the
  owner's account (bin/auth user add, by the user, in a terminal).
- 2026-10-28: 0.21.0 → 0.21.1 (the user: "update the INTEGRATING.md and then run the changes on default_office"): other
  devices (auth/INTEGRATING.md steps 9 and 10). bin/consoles connects out to the board (its key, `consoles`) and takes
  the room's console calls from it, listening on nothing (`/api/console`'s token route gone); the board's second
  listener, 3002, for an HTTPS proxy (`__Host-session`, the origin from `Host`, the last X-Forwarded-For entry, no
  `/api/visit`); `REMOTE=lan`: Caddy at https://<its LAN address>:3443 and https://<name>.local:3443, private addresses
  only, its root in `.remote/board-ca.crt`. Checked live: signed out refused, here and over the LAN; `/api/visit`
  refused over the LAN; bin/consoles asking the board. Tests: test_consoles.py (bin/consoles' jobs, then the whole
  way round through the board, here and through the proxy's listener), test_auth.py (signing in through it).
- 2026-10-28: 0.21.1 → 0.21.2 (the user: "ok fix it", after a restart of the board alone left auth-client in a
  network that was gone, every route refused): auth-client on the compose network (`AUTH_LISTEN=0.0.0.0`, no port
  published) and the board asking it by name (`AUTH_HOST=auth`), in place of `network_mode: service:board`. Checked
  live: the board restarted alone and auth-client restarted alone, each signing in again within 2 s; nothing on
  7403 from this machine or the LAN; bin/auth's commands. Built from basedverse's working tree (branch
  `auth-listen`), before its release.
- 2026-10-28: 0.21.2 → 0.22.0 (the user: "let's upgrade my default_office to the latest release"): the release of what ran
  here already (auth-client on the compose network) and the chat window's mailbox, voice messages, whispers left for
  someone away, times and Tab, all Visit's own: nothing in the room changed. Done as bin/start does it (`auth_up`,
  then `visit_up`), from the checkout at the tag, with a backup first (`.visit/backup-20261028-192350.sql`);
  `visit-client migrate` applied "2 mail". Checked live: both `/health` say 0.22.0; `migrate --status` up to date;
  the room page reconnected and hosts; a guest's link came up direct.
- 2026-10-28: 0.22.0 → basedverse's `engine` branch (#17; the user: "ok plow all steps in #17"): the room split into
  the engine's page and the person's (`ROLE` in room.html: host, user, or both with no visit-client), the board's
  relay and routes (`server.py`: `/visit/ws` with the relay preface, 1 MiB then 4408, one a session; `/visit/visit.js`;
  `/visit/mode`; `/room?role=host` key-only; `/api/visit` gone), compose (visit-client on the network with
  `VISIT_LISTEN`, two tokens, the `engine` service built from basedverse's `engine/`), `bin/lib/visit.sh` (the tokens,
  the engine's key and mark, `VISIT_ORIGINS` with the proxy's), `bin/down`. Tests: `test_auth.py` (a stand-in
  visit-client; 52 ok), `test_consoles.py`. Checked live from this machine: the engine hosting with no reload, visit-client
  on the network and nothing on the host's 7401, the gates (signed out 401; the host page refused a session, given
  to the engine's key; `/api/visit` 404), the proxy reaching `/visit/mode`. Not yet checked: the person's page in a
  browser (needs the owner's session; a test account was not made), so the first look at the desk and from a laptop
  is the user's. The old page is at tag `before-engine`. Known follow-ups: your jump at home (the room walks you now:
  no jump for a visitor), the camera's see-through fade (your scene is empty: nothing of the room's to fade).
- 2026-10-28, the first look at the desk: three things seen, fixed. You stood twice (the owner's visit had an id a
  side: visit-client says `home` to both pages now). The map and the panel counted your own room among the plots round
  yours (visit.js). The environment was the engine's page's own (its `localStorage`: office) while the dropdown changed
  this page's alone, drawn over it: the choice is the board's now (`POST /api/room {env}`, `room.env` in the snapshot,
  kept in `room.json` beside the messages; `setEnv` on every page from its poll; no `?env=` and no per-browser memory),
  and the cave's lid is lifted on your page too (its objects named `lid:` by the shell, found in the replica). The
  dropdown shows on your page (the `hidden` it was given lost to `.hud label.env`'s display). Tests: `test_auth.py`
  (57 ok).
- 2026-10-28, the desk again: nothing could be clicked and no jump. A click went to the room as where your body faced
  (a point is a direction from the eyes), not where the mouse was: your page now casts its own ray into the room as
  sent, and `world.point(hit)` aims from your eyes at what it met (visit.js's `point(target)`). The jump was this
  page's own physics, and the room walks you now: Space goes to the room as `up` always (a flying mount's up, else a
  jump), and `visitorMove` jumps a body once a press (4.6 m/s up, 14 m/s² down, as yours was): yours, and any visitor's.
- 2026-10-28, later: no hand over a seat, no sitting: the hover looked for the room's things in this page's own scene
  (empty now): `typedAt` finds what is marked for a visitor's click (`userData.visitPoint`) in the room as sent, the
  hand shows for it, a click points at it (an INTERACTIONS entry, `typed`, yours only; `metInRoom` for both). The
  cave's boulders arrived as round balls: a primitive travels as its parameters (toJSON), so the pulled icosahedron is
  a plain BufferGeometry now (visit/INTEGRATING.md says it). compose: `ENGINE_DEBUG: "1"` for the proving, TEMPORARY
  (eval on the engine's port, this network only): remove when #17 is proven. Lesson: never `docker compose up -d
  engine` (it recreates `visit` without the launcher's environment: a crash loop, the room gone); `bin/lib/visit.sh`'s
  `visit_up`, or `docker compose restart engine` for the page alone.
- 2026-10-28, later still: the room's night and day follow your theme again (the engine's page has none: your page tells
  the board `dark` with `/api/room`, the engine's page takes it as its `data-theme`, so the office's CSS colours, the
  palettes and the lights are yours; the floor and walls were the day's, white, under your dark theme). The division of
  clicks kept on the engine: `interact` runs everything for you (this page's click, or your own body's point on the
  room's page: `doer.owner`, from Visit's `visitor.owner`), `visitors: true` alone for a visitor, and `screen` entries
  (console, ticket, typed: your screen's) never for a point. Each point the room's page gets is in the engine's log
  (`visit point … -> what`). Your dude at home is drawn inside the room as sent (the room's sun shadows it: a scene's
  shadows fall on that scene alone), then the rest of this page over it. The hover finds small marked things (a
  mosquito) within the click's slack, and the point goes at the thing. test_visitors.mjs follows the gate.
- 2026-10-28, the bananas: the engine's log showed your points crossing and running (`-> thing`, then `-> throw`: a
  banana eaten, its peel thrown), unseen: they happened to the room's copy of your body, which your page hid for a
  dude of its own that knows nothing of them. At home you are the room's copy of you now (`v.world({ self: "room" })`,
  Visit's; this page's dude hidden there, shown off your land; in first person neither): a seat, a drink, the peel,
  your lowrider's wheel (`stepVisitors` sits a lowrider's rider at its wheel as you sat, `sitPose` and `WHEEL_ARMS`),
  the room's sun's shadow. The reach rule ("You are too far away.") is said by your page too, since the room's word
  does not reach a visitor's screen.
- 2026-10-28, the sit: your body sat on the engine (its legs bent) and you saw it standing: with "visitors look like
  their model", Visit drew your avatar with its own clips (walk, idle, ride; no sit, no drink), and the room's dude,
  which the room poses, was hidden under it. The room draws its owner itself now (`look: who => who.owner ? "mine" :
  undefined`, a `visit.host` hook of this branch), with your own look: the person's page tells the board `me.look`
  (`/api/room {look}`, `room.look`), the room's page builds your dude from it (`ROOM_LOOK`; made again when it
  changes). The typed path names the visitor (your points are yours: the division), and the room's word to a visitor
  ("too far away") is in the engine's log.
