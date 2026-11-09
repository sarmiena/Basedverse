# forge — the board server

One stdlib Python server (`board/server.py`) that every agent session and your browser read: a live
view of the configured GitHub repos (`../.env`: `REPO_OWNER`, `REPO_NAMES`), the agent roster, and the
messages between agents. It runs on this machine (`python3 board/server.py`, started by `bin/start`; no Docker), on port 3001 of this machine only (127.0.0.1).
Signing in comes first ("Signing in", below).

## Signing in

The board asks who you are before anything else: every page and route checks a session first (`board/auth.py`),
with the sign-in inside basedverse's **`visit`** (2.0: one program on this machine, started by `bin/start`), on
127.0.0.1, where only the board talks to it. Its accounts, sessions and the agents' key live in `visit`'s own
database (`VISIT_DATA_DIR`), in a schema of their own, as a role of their own.

- **Your account** is made in a terminal, never in a browser (there is no sign-up page): `bin/start` asks for its
  name the first time, from a terminal, and `visit auth` asks for the password twice (at least 12 characters).
  Later: `../bin/auth user add <name>`; a new password: `../bin/auth passwd <name>` (it signs you out everywhere).
- **Signed out**, a page shows the sign-in page (`board/login.html`, from basedverse's `auth/web/login.html`) and
  comes back where it was once you are in; an API call gets 401. A session lasts 30 days at most, and ends after 7
  unused; **sign out** is in the kanban's header and the room's corner. A write from another site's page is
  refused, signed in or not.
- **The agents** send their API key (`REPO_BOARD_KEY` in `../.env`, made once by `bin/start`): `forge.py` reads it.
  `../bin/auth key revoke agents` ends it, and the next `bin/start` makes another.
- **Each key reaches only what its holder uses** (`server.KEY_ROUTES`); everything else is your session's alone, on your
  own page. The agents' key: the board, moving a card, refreshing, messages (a message as "owner" through it, as
  `forge.py --owner` sends, also lands in your unread, so one you did not send is seen; and only your page marks your
  messages read). The engine's: the room's server page, its scripts and pictures, the board, the tarot reader's routes,
  the room's settings. bin/consoles': taking jobs and answering them. A key of any other name reaches nothing. Every
  page refuses to be framed by another site's (`X-Frame-Options`, `frame-ancestors`).
- **Who is signed in**, from where, and the last sign-ins tried: `../bin/auth sessions`; end one with
  `../bin/auth revoke <session>`.
- visit (its sign-in) not answering: the board refuses everyone (503), never lets anyone in for it; `bin/start` again.

## From other devices

The board, the room and the agents' consoles work from your laptop or phone too, over HTTPS only (your password
and session would otherwise cross the network readable by anyone on it), through a proxy on the board's port 3002,
where every request counts as another device's (the `__Host-session` cookie, its page's origin `https://` and the
name you used). Visit stays on this machine: its panel is not there.

`bin/start` asks once which (`REMOTE` in `../.env`: `lan`, `tailscale`, or both, `lan,tailscale`), and at the end
prints every address the board is at.

- **Your network** (`lan`): Caddy, at `https://<this machine's LAN address>:3443` and `https://<its name>.local:3443`,
  to private addresses only. Its certificate is Caddy's own: trust `../.remote/board-ca.crt` on each device once, and
  never click through a certificate warning for the board: one there is not Caddy's (someone on the network between you
  and it, who would have your password and session, and with them your agents' consoles).
- **Your tailnet** (`tailscale`), from anywhere your devices are: `tailscale serve`, at
  `https://<machine>.<tailnet>.ts.net:9443` (`REMOTE_TS_PORT` in `.env`, 9443 when blank; never 443, which a
  project's own dev server may need on this machine), with Tailscale's certificate (one every device trusts), from your tailnet
  only (never funnel); with `lan` too, Caddy at `https://<its tailnet address>:3443` as well (Caddy's certificate,
  as on your network). Tailscale lets only root or its operator serve: the first time, bin/start offers to make you
  its operator (`sudo tailscale set --operator=<you>`, once; no sudo after that). `bin/down` takes the board off the
  tailnet again (only its own serve, nothing else Tailscale serves).

The console works there because bin/consoles connects out to the board (its key, `CONSOLES_KEY`) and takes the
room's calls from it: it listens on nothing, and the board, which checks your session first, is its only way in.

## The kanban — <http://localhost:3001>

The columns are **`workflow.json`** in the project root (beside `.env`; `REPO_WORKFLOW` points
elsewhere). The board re-reads it on its next poll after you save it, and a broken edit keeps the last
good workflow running, with the error on the status line. The one shipped is for a repo whose labels
are all `status: …` (exclusive, like `severity: …`); an issue with no status label is Ready:

| column | rule | agents pick here | drag a card here |
|---|---|---|---|
| Later | the `status: later` label | | sets `status: later` |
| Up next | the plan: the body of the `RESUME…` issue in `REPO_HOME`, the list a bare `/go` starts from | first | reorders the plan |
| Ready | the default: every open issue no rule below takes | yes | takes the status labels off |
| In progress | `status: in progress`, or claimed by an agent (and an older `In Progress` label, until it is retired) | | sets `status: in progress` |
| In review | `status: in review`, or an open PR names the issue | | sets `status: in review` |
| Needs input | `status: needs input`: waiting on a person's answer | | sets `status: needs input` |
| Blocked | `status: blocked` (waiting outside GitHub), or an open `blocked by` edge | | sets `status: blocked` |
| Closed | rows that left the board in the last 30 minutes | | |

**Claims show on GitHub too:** the workflow's `claim_label` (`status: in progress`) is put on an issue
when an agent claims it (`forge.py agent claim`) and taken off when it releases it, exits, or you remove
a dead agent with the ×. Which agent holds it is on the roster: every agent posts as the same GitHub
user, so neither a label nor an assignee could say.

**How a card's column is decided:** it sits in the RIGHTMOST rule column whose rule it meets, else in
the default column. Further right is further along: a claimed issue with an open PR reads In review, and
a blocked one reads Blocked whatever else is true. Within a column, cards are ordered by `REPO_RANK`.

**A column** has a `key` and `name`, and is one of: `plan` (Up next, at most one), `closed` (at most
one), `default` (exactly one), or a rule column with `when` and/or `label`:

- `when` — conditions that must all hold: `label` (a name or a list, any of), `claimed`, `pr`,
  `blocked`, `epic` (true/false), `any: [ ... ]`, `not: { ... }`. A column with a `label` and no `when`
  holds the issues carrying that label.
- `label` — makes it a drop target: dragging a card here (or `forge.py move <ref> <column>`) sets this
  label and takes every other column's label off, in one write on GitHub. The label must exist in the
  repo (`forge.py newlabel`). Whether the card then shows there is still the rules' call: a claimed
  issue stays In progress, and the board says so.
- `pick: true` — agents are offered work from it (`forge.py board`, `/go`, the SessionStart hook).
  A card you or an agent claims is yours to resume wherever it shows.
- `limit` — a WIP limit: the header shows `count / limit` and turns red when over.
- `hint` — the line under the column's name.

A plain status-label board, for example: a `Todo` default column with `pick: true`, then
`{"key": "doing", "name": "Doing", "label": "status: doing", "limit": 3}`,
`{"key": "review", "name": "Review", "label": "status: review"}`, and a `closed` column.

You set priority by adding cards from a pick column to Up next (drag, or `+`) and reordering them (drag,
or the arrows). Each change rewrites the RESUME issue body, the same list `forge.py resume` reads and
`/stop` maintains; a write is refused if a session rewrote the plan after the page loaded. A claimed
card wears its agent's identicon. Any card opens in a quick view (body, comments, edges) without
leaving the board.

**One board per repo the agents work in.** The home board is the `.env`'s project (`REPO_OWNER`, `REPO_NAMES`),
whichever of its checkouts an agent is in. Every other GitHub repo an agent's directory is a checkout of is a
board of its own, named in `REPO_BOARDS` (`owner/repo`, comma-separated; `bin/init-agent` adds one when it sets up such
a directory, and the board picks it up at its next poll, no restart): that repo's open issues and PRs, in the same
columns, ranked the same way. With more than one, tabs beside the title pick which this page shows (kept in its address,
`?board=owner/repo`, and in the browser), and an agent of another board wears its board's name in the strip. Each board
has a colour (`common.js` `boardColor`, the home board's purple, then teal, orange, pink, olive, brown; an agent on no
board grey): a dot on its tab, a stripe along the top of each of its agents' rows, and in the Agent Room a band down the
side of each of its agents' name cards with the board's name in it, the wall's board framed in it, and a dot for each
board in the room's legend. Another
board's bookkeeping stays in the home repo, so a public repo carries none of it: its agents are on the one roster (each
entry says its `board`), and its Up next is a RESUME issue of its own there, `RESUME [owner/repo] …`, made when you first
add a card to it. `/api/board` serves the home board as ever; `?board=owner/repo` another; `?board=*` every one at once
(the Agent Room's): all their rows, PRs and gone, and `plans` by board. A board that cannot be read keeps its last good
read and says why on its tab; the others go on.

Above the columns:

- **Agents**: your agents as a party, as the Agent Room's list draws them (one `board/party.js`, both pages'): a row per
  session on the roster (the `AGENTS…` issue in `REPO_HOME`, one comment per agent, written by `forge.py agent`), in the
  order they joined, so nothing moves under the pointer. Each is its head as the room makes it (its face, its halo of
  context, slumped when idle, grey when dead, dressed for the room's environment) with what it is up to drawn on it, and
  its card: name, seat and board, what it waits on you for, what it is doing, what it is up to between turns, a command
  running (from `bin/shells`), its subagents, what it holds (each opens its quick view) or held last, and how long since it
  last acted. ⏳ in the header counts those waiting on you. Hover a row for its picture's legend (as in the room, below),
  with the directory it runs in and its machine. **Click a row for its focused view**, as a click on it in the room: its
  ticket (the one it holds, or its last) in the quick view, its terminal beside it, and its row ringed while that is up.
  Its **×** (two taps) takes a crashed session off the roster and frees its claims. The roster also heals itself:
  a session that exits takes its own entry off, and one that crashed or was closed without `/stop` is
  taken off (its claims freed) by the next agent on the same machine to report, once Claude Code no
  longer runs it and it has been quiet 10 minutes. This machine turned off with its agents still running (none of them
  ran its SessionEnd): the board takes their entries off itself, claims freed, at its first read after `bin/start`,
  each proven ended (this machine's, its transcript here, last seen before the machine started, and no Claude Code
  running it now: `roster.rebooted`), and says so in `.board/board.log`. An entry from another machine waits for *remove*.
- **PRs**: every open PR, with the agent that owns it (the claim on the issue it names, else its
  session link), or *orphaned* when that agent has left.
- **Messages**: every message between agents and you, newest first, with a badge for yours; send to
  everyone or to one agent. Agents use `forge.py tell` / `forge.py inbox`. Messages live in the board's
  own SQLite file (`BOARD_DB`: `.board/board.db`), never on GitHub. Its tables are `board/schema.sql`, which the board
  runs at each start: a new database gets them all, an existing one keeps its rows.

## The Agent Room — <http://localhost:3001/room>

The same snapshot as a 3D room: the board wall with one section per workflow column, one desk per
agent, and between snapshots the agents walk to the wall to copy a card down (a claim), move it on, or put
their notes back (a release or a close): below. The desks stand four to a row with wide aisles between them; the room is 32 m wide
(`ROOM_R` in `room.html` sets its open right side, and the board, the desks and the overview follow it)
and as deep as its rows need.

With more than one board (one per repo the agents work in, above), the wall shows one at a time, and every desk holds
its agent's cards whatever board they are on. A switch on the wall's right end, past its last column, cycles it: a
plate with ▲ and ▼, the board's name over it and which of how many. Click it (yours alone: a visitor's click does
nothing) and the wall turns to the board before or after; the room keeps it (`room.board`, kept by the board like the
environment), so every page and your visitors see the same wall. An agent walking up for a card on another board than
the wall shows goes to the switch first, reaches up and presses it (the button lights), and the wall turns to its
board; then it goes along the wall to the card's column, as ever. The room reads every board at once
(`/api/board?board=*`).

Every card stays on the wall, in the column the board has it in, so the wall is where things are: a card an agent
holds is tagged with its name, and its desk has a copy (its notes). Between snapshots the agents act it out
(`motion.js`):
- **A claim**: the agent walks up to the card, copies it down onto a notepad (the card stays up), and walks back
  with its notes; when the claim moved the card too (Ready to In progress), it takes the card down after its notes,
  carries it along the wall's front to its new column and pins it up there first.
- **A card it holds that moves on** (In progress to In review): it walks over, takes it down, carries it across and
  pins it up in its new column; its notes stay on its desk.
- **A close**: it walks its notes to the card, puts them back, and carries the card to Closed lately; **a release**
  puts its notes back (and moves the card, when the release moved it).
- Until the agent gets there the card stays in the column it leaves; in its hands it is on no column; then it is in
  its new one. A card nobody holds (one you move on the kanban) just changes column.

Each column pins its top five cards on the wall; the rest wait in a stack on the table under it, banded in
their ranks' colours, with *+N more* on its front. Over a column with more than five, the wheel scrolls it a
card a notch (a trackpad's swipe adds up to one): the card at one end goes down onto the stack, the next
comes up off it at the other, the rest slide along, and a rail beside the cards shows how far down you are.
Elsewhere the wheel zooms as before. Click any card (on the wall, or on an agent's desk) and its ticket
opens beside the room, as the kanban's quick view shows it: the body and every comment, its labels, column
and holder, and what blocks it and what it blocks (a click on one, or on a link to another of the org's
issues, opens that one there). Esc or × closes it. It opens at the top left, or, with your agents' list open, under
it (beside it if the window is too short for that), so the list stays in sight. Drag its head to move it anywhere,
an edge or a corner to size it: the next one opens there, as big (this browser keeps it, `room.ticket.rect`); a
double click on its head puts it back where it opens by itself. Only you can open one: a visitor's
click on a card does nothing.

Subagents (an agent's Agent-tool workers) get a desk each, in rows in front of the agents'. Each has
its own colours and wears a cap in its agent's colour. While an agent has subagents working it stands over them in turn, hands on hips,
tapping a foot, and the one it is watching sweats. Each seat's hook reports them (`subs` on the
roster entry): added when one starts, removed when it stops, and checked against the background work
Claude Code lists at the end of every turn.

An agent in a turn and acting lately types at its desk. One with a command running (a test suite, a build,
a script: one it waits on, or one it ran in the background) has the Matrix falling down its screen, columns
of green katakana and digits, each led by a bright glyph, fading behind, and sits watching it rather than
typing. `bin/shells` says who: `bin/start` runs it on this machine (Linux; it reads /proc), and every 5
seconds it finds each Claude Code process's shell-tool commands (its `bash -c source
~/.claude/shell-snapshots/...` children, but not the message waiter, `forge.py inbox --wait`, which every
agent keeps running in the background), takes each one's session id from its environment
(`CLAUDE_CODE_SESSION_ID`) and writes, for each session whose oldest command has run 5 seconds or more, when
that command began, to `.shells/now.json` beside `bin/`. The board reads it (the project is mounted in its
container, `SHELLS_DIR`) and serves it as each agent's `shell`, matched by its `local` or `session` id; a
file more than 20 seconds old means `bin/shells` stopped, and then no one is shown running anything.
Agents on other machines are never shown running a command. `bin/shells --once` prints what it sees.

**Agents** at the top of the room (how many, and ⏳ how many are waiting on you; folded, a gold "!" bobs on it while one
waits, the one over that agent in the room) folds open a list of them all, top
left (it keeps that corner: the tarot reader's card goes beside it), so you need not walk over to talk to one (the
kanban's agents strip is the same list, `board/party.js`, which makes the room's figures too): a row each, those waiting on you first, the agent's head as the room
draws it (its face, its halo of context, slumped when idle, grey when dead) and its card to its right (name, seat, what
it waits on you for, what it is doing, what it is up to, its subagents, its board's colour). Its picture moves with what
it is up to: the Matrix falling behind it while a command of its runs; typing dots in a turn; between turns, as the room
plays it out, its head drooped and Z's rising as it dozes, its phone before its face with its feed scrolling, a speech
bubble as it chats (and with whom, on its card), a coffee steaming, the cooler's cup, a cocktail or a banana on an
errand, the sun out by the pool; its subagents' little heads; and the "!" bobbing when it waits on you. (The room's
own page plays those out and tells the board, `room.acts`; with reduced motion the pictures stand still.) Hover over a
row (its picture or its card) for its picture's legend, as a buff's tooltip: the directory it runs in (`~` for home: its hook writes it) and its machine, then each
thing on it, its icon beside it, and what it means (waiting on you
and for what, a command running and for how long, in a turn, dozing, its phone, chatting and with whom, the errand, out
by the pool, its subagents and what they do, its halo's context, idle or stale or dead), then what it is doing; the
card's line names both when a command runs through an errand. A click
does what a click on its card in the room does: its ticket (the one it holds, or its last) and its terminal, and its
row is lit (ringed in blue) while its terminal is open, or the ticket opened by it is. An agent waiting on you is plain in the room too: its card amber, "⏳ waiting on
you: …" under its name, and a "!" bobbing over it (a click on that opens its terminal as well).

*Waiting on you* is an agent at a question dialog or a permission prompt (until it is answered, or the turn ends), or
one whose turn **ended asking you something** in its own words ("Want me to file it as an issue, or go straight to
building it?": the last sentence ending in "?" of its reply's last paragraph; one in code, in quotes or in brackets
does not count). That one waits until you send that agent something, even through a turn a background shell or
subagent wakes after it (seat/hooks/agent_state.py).

Click an agent's name card (over its desk, or the tag over it while it walks) and its terminal opens; on the kanban,
its row in the agents strip (its focused view: its ticket in the quick view, the terminal on its left), its face on a
card it holds (the same), or **claimed by** in a quick view (one `board/console.js`, both pages'):
its herdr pane's screen as herdr shows it, colours and all, read again every second, with a box to reply in
(Enter sends it, as if typed there and Enter pressed; Shift+Enter is a new line) and keys for Claude Code's
menus (Esc, ↑, ↓, Enter, 1, 2, 3). An agent at a question dialog or a permission prompt takes the keys, not a reply;
one whose turn ended asking you something takes a reply.
Paste a picture into the reply (a screenshot, Ctrl+V), drop one on the console, or pick one with 📎 (a phone's way),
and it goes with your reply: a thumbnail of each till you send (× takes one back). Each is sent as it comes to
bin/consoles, which keeps it on the agent's machine (`.consoles/pastes`, a week), and the reply carries its path on a
line of its own, which Claude Code takes as the picture itself, as it does a file dropped on it (`[Image #1]` in its
prompt). PNG, JPEG, GIF and WebP go as they are, any other picture the browser can open as a PNG, 10 MB at most.
Esc or × closes it. Beside it opens the ticket the agent is on, as a click on its card on the wall opens it; holding
none, the one it held last (the board remembers each agent's, in `last.json` beside `room.json`, and the kanban's
strip names it too: "claims nothing · last WebApp#520"). Drag a corner to resize it (its text grows with its width: herdr's pane keeps its own
columns and rows) or its title bar to move it; it opens next time where you left it. `bin/consoles` serves it: `bin/start` runs it on this machine, listening on 127.0.0.1
only (`CONSOLES_PORT`, else 7402), and it talks to herdr's socket, finding the pane whose `display_agent`
is the card's handle (a pane with none is never reached). Typing there is typing at your keyboard, so it
answers only this board's page (`http://localhost:3001`, by its Origin), asked by this machine's name (its
Host), with the token it makes each start (`.consoles/console.json`, readable by you only), which the board
hands the room at `/api/console` to a page of this machine only: from the LAN or Tailscale the cards open
nothing, and another site in your browser can neither read a screen nor type. The panel is the page's, not
the room's scene, so Visit's visitors never see it. Scroll up for what went by: herdr gives an agent's history
only while it is idle, slowly, by scrolling its screen, so `bin/consoles` keeps its own. It reads every agent's
screen four times a second and keeps the lines that leave the top (about 5000 each, colours kept), giving back
those that come down onto the screen again when the live part under them shrinks. It goes back to when
`bin/consoles` started (the panel says when), and marks `⋯` where a redraw it could not follow went by.

A card's ticket (click a card on the wall) has **Work on it**: **Give to an agent** lists the agents between turns;
pick one and its console opens at once while it is told (bin/consoles' HAND) to claim the ticket, read all of it and
talk it through with you before it starts, so it walks to the board for the card. **New agent** asks bin/consoles where
one can start: your herdr workspaces (pick one) and the agents' directories (AGENTS in the .env, bin/init-agent's),
those with an agent in them already greyed out. Pick a directory and its console opens at once, saying it is
starting; bin/consoles opens a new tab in that workspace, in that directory, starts Claude in it, and gives it `/go
<ticket>` (the /go skill then claims it, reads it and talks it through with you). Whatever you type meanwhile is
queued and sent once it is up (a question as Claude starts, a folder to trust, you answer in that console). A
ticket someone has claimed offers **Talk to** them instead. A ticket goes only to an agent on its board: both lists
are its board's agents and directories (the board refuses a hand to another board's agent, bin/consoles a start in
another board's directory).

**New agent** at the top of the room starts one with no ticket, told a bare `/go`, in any of the agents' directories
(each with its board) or in one you type. Its herdr workspace is one of yours, or **+ new workspace…** (its name; blank,
the directory's), which herdr makes in that directory, the agent starting in its first tab. A directory you type is
never guessed for you: **Set up…** shows what setting it up would do (`bin/init-agent --plan`, changing nothing): its
board (another repo's is a board of its own; a public repo is said), and each step (the skill links, the hooks file with
its `REPO_BOARD`, what is hidden from git, the labels it would make there, the lines added to the .env). Nothing happens
until you confirm: then bin/consoles sets it up (`bin/init-agent`) and starts the agent there, and its board appears
beside the others at the board's next poll. A ticket's New agent is the same, with its board's directories only, and a
directory of another board refused. bin/consoles starts nothing but Claude, only in an AGENTS directory or one it has
just set up (an existing directory in your home, given as `/…` or `~/…`, nothing hidden on the way to it, not this
checkout), only in one of herdr's workspaces or one it makes, and tells it only `/go`, `/go <repo>#<N>` (a ticket of its board) or HAND.

Every room has a side door in its left wall toward the back: the office's white one with an EXIT sign, the jungle's
of old planks in a frame of beams where a column of its wall has gone, the pool club's teak STAFF door; in the
terrorist camp, a cage, a recruit waiting in it, a guard by it. A new agent (one the roster gains while the room
is open, however it was started) comes in by it (the door opens before it and shuts behind; the guard turns, opens
the cage and lets it out, and a new recruit is put in), walks up the left aisle to the board to meet you, waves (a
"hi! I'm …"), and sits down at its desk; a card it claimed as it came, it walks for after.

An agent between turns (not in one, not waiting on you, no subagents working, no card to walk) gets up to
something now and then, and sits idle a while between: it walks to the water cooler on the right of the
room for a drink (the water is green in the jungle ruin); in the office, to the coffee bar behind the
cooler, fills a mug at the espresso machine (it steams), sips, blows on it, sips again and carries the mug
back to its desk; at the pool club, to the tiki bar there for a cocktail, sips it through its straw, sips
again and carries it back; in the jungle ruin, to the rotten bananas, eats one and tosses the peel back on the pile;
over to another agent at its desk for a chat, speech bubbles going back and forth while the other turns
its head; or at its own desk it dozes off, Z's rising, or swivels round and scrolls on its phone. A stale
agent mostly dozes. Click one that is dozing and it wakes with a start (HUH?!), stretches, and goes off to
do something else. The moment it is wanted (a turn starts, a card to walk, subagents, a question for you)
it sits up, or hurries back to its desk first. Like the walks, it is only acted out: the board says only
who is between turns.

You are in the room too: a dude of your own at the front, where the owner stands, with your name over
your head. The room asks your name on its first start (the **You** button at the top changes it, and
**New look** there rolls you another face, hair, beard, glasses, hat and clothes); both are kept in the
browser. Walk about with World of Warcraft's controls: W/S (or the arrow keys) forward and back (Shift
held runs), A/D turn, Q/E sidestep, Space jumps; drag with the left button to swing the camera round you,
with the right to turn you as well (A/D then sidestep), both buttons to walk on; the wheel zooms, right
down to first person. Any of those keys takes over from the overview, and **Reset view** gives it back.
**Binoculars** (B, or the button at the top): raised to your eyes, first person from wherever the camera was, the
view magnified 3× to 12× (the wheel; they open at 6×), two round lenses over it; a drag looks about, slower the more
they magnify, and a click still lands where you look (a name card across the room, say). B, Esc or the button again
lowers them. Yours alone: nobody in the room sees them.
You are not kept in the room: walk out of its open sides (in the jungle ruin, jump the left wall where it
has crumbled to a block) into the jungle round it, some 55 m each way, past trees and rocks, along a dirt
path to a clearing with a campfire and a giant old tree, and to a stream with a fallen log across it; the
office has a lawn, a plaza out front with benches and lamps, and trees. In the terrorist camp the room
is a cave deep in a mountain, near its back; the mountain stands on a plateau, which you walk all round it. Out
of the room's front a rounded tunnel lit by torches winds along the room's front and out through a spur of the
mountain to the cave's mouth, a hood of rock coming out of the hillside, where two guards stand behind
sandbags, and out onto the plateau (boulders, scrub, junipers, snow-capped peaks all round past its edge). A
trail zigzags up the mountain's left shoulder to a lookout on its rim (on its last leg there, a landing, and a shallow
cave in the mountain's face, a torch either side of its mouth: Madame Vesna's, below), and on along the mountain's back up to
its top: a meadow levelled a little under the summit, with a picnic area (two picnic tables, a parasol, a
blanket spread with a basket, a grill, a sign). Up there you walk wherever the mountain is gentle enough to
stand on, and you, and your visitors, go up and down it. At the Vegas pool club the room is a cabana terrace
(a teak deck, the clubhouse's stucco wall with POOL PARTY in neon along its top, a thatched tiki bar in the back
right corner); out of its front, over a pale stone deck, a free-form lagoon whose right end runs into the grotto, a
hill of faux rock with palms on top and a waterfall off its lip, hollow over the water: a swim-up bar inside, its
stools in the pool. Round the pool, loungers under striped parasols, four cabanas on the west, the DJ's stage at
the far end, daybeds on a lawn to the room's right, palms everywhere, the Strip's towers and the desert's mountains
far off. You wade into the pool (chest-deep: you, and your visitors, walk on its floor) and into the grotto, up to
its bar. Nothing tall stands in
front of the room, so the overview stays clear (the mountain over the cave and in front of the room is lifted
off for the overview, which looks in from above; the cave is drawn from inside only, so the overview, and a
camera you swing into the rock, see in); the agents and the pets keep to the room (Visit's visitors walk where
you do).
In a world (Visit's, below), past the land's edge is the ground between plots, and your friends'. Click the water
cooler (left or right button) when you are close to it and you have a drink: a paper cup from the tube,
filled at the tap, drunk off, head back. In the office, click the coffee bar (green cabinets, a tiled
splashback with a shelf of mugs and a COFFEE sign, an espresso machine, a grinder, doughnuts) and you have
a coffee: a mug filled at the machine, a sip, blown on, another sip (*ahh*). In the jungle ruin, click the
rotten bananas when you are close and you eat one and keep the peel; then a click throws it at whatever
you clicked: it kills a mosquito it hits (SPLAT!), bonks anyone it hits on the head, startles the rat or
the snake, or lands on the floor. A drink of the jungle ruin's green water, or a banana, gives you a stomach
ache one time in five (OOF!), and for a while you clutch your stomach, walking slowly, now and then
gurgling; the office's water is clean. At the pool club, click either bar (the tiki bar in the room, or the swim-up
bar's counter in the grotto), or a waiter going by with a tray (they stop, turn to you and lower it), and you have a
cocktail: picked up, a sip through its straw, down a moment, another sip (*ahh*, or now and then WOOO!), and it is
yours to keep. You hold it wherever you go (walking, sat, lying back, on a float), a sip every 20 to 30 seconds, its
level going down, until after a few minutes it is empty; then it goes on the tray of a waiter within reach (one going
by waits for your last sip), or else you toss it on the ground, where it lies five minutes. One drink at a time:
"Finish your drink first." Click a log (in the jungle: one of the three round the campfire, or
the one over the stream), a bench (on the office's plaza), or in the terrorist camp a crate (round the
guards' fire, or at the lookout), a rock (any boulder you could get up onto) or a picnic table or the
blanket on top, or at the pool club a free lounger or daybed (you lie back on it), a cabana's sofa, a free stool at the
swim-up bar or one of its two empty floats (the flamingo and the mint donut: you lie back in it, your legs up over its
front, and drift round the pool, bobbing, till you walk off into the water; it never fades out of your way, being under
you). Hold Shift with the move keys on a float and you paddle it instead (W and S forward and back the way you face, Q
and E sideways, A and D turning you and it), at an easy 1.8 m/s; it stays in the water and bumps rather than going
through the other floats, anyone standing in the pool, the swim-up bar's stools or the waterfall, and let go, it drifts
on from there. Lying there you do something now and then: rest, put your hands behind your head, have a big stretch (sometimes
*yawn*), look about, or on a float kick your feet. The club's own loungers, daybeds and float riders stretch now and then, when you are
close to it and you sit on it, at the end of it nearer you: facing the fire, out over the plaza, back the way you
came (over the stream, or off a rock), at the table, down the lounger, out of the cabana, or at the bar.
Walking off (or jumping) gets you up, a step in front of it. While you walk about, whatever comes between the camera and you
fades out of the way, on a friend's plot in your world too; that is your view alone (your visitors are never sent it:
each one's own page fades what is in its way). You go round the desks like everyone else, and the room treats you like anyone
about: a mosquito may bite you, the monkey may bonk you.

The environment dropdown (top of the page, or `?env=office` / `?env=jungle` / `?env=cave`) changes
only how the room looks, and each browser remembers its choice.

In the jungle ruin (not the office), a rat and a snake live on the floor. The rat scurries about
between the desks, sniffing and sitting up, and bolts from anyone walking by and from the snake; the
snake slithers slowly, and freezes and rears its head at a passer-by. Each has a temper that rises over 6
to 13 minutes; the snake's only while its belly is empty (no rat going down it), and then half as fast
again. It shows from halfway up, a little more each minute: the rat's hackles rise and its eyes redden, it
lashes its tail, turns to face the snake, sits up to watch it and paws at the floor, and stops bolting from
it; the snake raises its head, its eyes redden, it flicks its tongue faster, watches the rat, rests less,
and rattles its tail. At the top of its temper one goes after the other, and once they are close they
fight in a cartoon cloud of dust: either the rat wins and the snake lies dead, X for eyes, until it sinks
away, or the snake swallows the rat whole and a lump goes down it (the bigger of the two has the better
of it). A fight starts where the camera can see it, unless they have been ready for 40 seconds. Click
either of them while they are close to each other and they fight then and there; click one that is not
near the other and it startles (and its temper goes up a little). The loser's replacement, a new rat or
snake with a coat and a size of its own, comes in from the jungle a minute or two later.

Five to eight mosquitoes are about at once, anywhere on the map: each rises out of the stream, somewhere
along it, and when one is gone another comes (at once, if fewer than five are left). A hungry one flies in
short zig-zag hops, round the building's back and left walls rather than through them, now and then
resting on a tree trunk or a wall, now and then going round the campfire. Once it smells someone within
14 m (an agent or subagent, in its chair or walking about; you or a visitor too), it stays about them,
buzzing a few metres off or resting nearby, and every minute or two comes in close and buzzes round their
head a moment. An agent or subagent notices it two times in five and waves at it: one wave in three shoos
it off (SHOO!), the rest miss (MISSED!); shooed, it is back later. Only one time in five does it bite: it
lands on the back of their head and drinks, swelling red, until they smack it (SMACK!) and it drops dead.
About one smack in four misses (MISSED!): it darts off, full, rests a while, then flies back to the stream
to lay its eggs, and is gone. One that lives out its few minutes goes back to the stream too. About one
mosquito in seven rises out of the stream carrying malaria (its stripes green, not white), and one that
drinks from someone sick picks it up. A carrier's bite passes it on: its victim turns green, shivers in a
blanket with fever rising off it, and stays sick for 3 to 5 minutes (kept in the browser, like the boards).
Close over the rat, one time in five it goes for the rat instead, which sits on unaware until it is full,
then whirls round, knocks it off, and hops away; the mosquito goes off to rest. Click a mosquito within
your reach (1.8 m of your head) and you swat at it, whatever it is doing (biting you too): one swat in two
kills it (SMACK!), else it darts off (MISSED!). The click is aimed where you saw it a moment ago, and the
nearer it is the bigger it is to hit. Click one further off while someone (or the rat) is near it and it
goes straight for the nearest, with no buzzing round them and no chance they wave it off; with nobody near,
it buzzes off. A mosquito trap hangs on a chain from the roof beam's stub over the front notice board: an
electric bug zapper, a violet UV tube glowing and humming in a wire cage. Its light now and then draws in a
hungry mosquito within 12 m (even one that was about someone), and seven times in ten the grid zaps it
(ZAP!, a flash, and it drops dead); the rest bump it and buzz off. Flies circle the bananas, now and then
settling on them.

A wild monkey lives in the jungle round the building, out to the campfire and the stream, and is always
about somewhere: it climbs a banana plant for a banana and eats it at the foot, flinging the peel; clings
to a tree trunk a while, looking about; sits by the campfire; or romps off somewhere and looks around.
Banana plants grow about the jungle (a few near the building, more further out, none in front of the
room), their bunches hanging over one side with the purple heart under them. The monkey goes round the
building, never through the room, but every two to five minutes, as often as it ever did, it comes in,
each time another way: past the open right side or up from the open front, over the crumbled left wall,
up the outside of the back or left wall and down out of a hole in it, or (about one time in four) a leap
for a vine hanging from a roof beam and a swing in on it, letting go. It sets off for its way in as long
beforehand as the walk there takes, so it comes in when it always did. Mostly it goes for the rotten
bananas: it sits by the pile and eats one, then throws the peel at someone (BONK!, and the peel sits on
their head a moment before it slides off), shrieks OOK OOK! and runs off out of the room some other way,
back into the jungle. Otherwise it romps about, looks around, scratches, and goes. It finds its way round
the desks, and round the trees and rocks outside; the rat bolts from it.

Two notice boards hang on the left wall, toward the front: nailed to it where it still stands,
jimmy-rigged on a couple of 2x4s where it has crumbled. WORKPLACE SAFETY, with the time since the last
accident (death by exhaustion: a subagent worked to death; or malaria from a carrier's bite, with a
MALARIA! over the victim), what and who it was, and the count of each kind; and a chalkboard tallying the
rat's wins against the snake's. They are kept in the browser (localStorage), so a reload goes on
counting. All of it is scenery, not the board: nothing in a snapshot says where they are, so each
viewer's room has its own.

In the terrorist camp the workers are armed, each its own way (seeded by its session, so the same at
every redraw): a balaclava or a bandana over the face, or nothing; a helmet, a boonie hat, a beret or a
beanie; a bandolier or a chest rig; a rifle, a carbine, a shotgun or a rocket launcher slung across the
back; grenades hanging from the belt. A subagent (a recruit) keeps its cap in its agent's colour and carries
only a stick or a machete. A finished one does not die of exhaustion: its boss shoots it (it stops, turns,
brings a rifle up, BANG!, a tracer, and the recruit keels over), or it blows itself up (BOOM!: a fireball,
smoke, its bits and its cap flung about, a scorch on the floor where it sat), each as likely. An incident
board on the left wall, toward the front, counts them: RECRUITS LOST over a skull and crossbones, how many
each way, and the last one and how long ago (kept in the browser, like the jungle's boards).

At the Vegas pool club the agents wear shades and a lei (its flowers seeded by the session), work at teak poolside
tables (a laptop, a cocktail, a bottle in an ice bucket) from wicker chairs, and the club is full: some fifty of its
regulars (each a dude like yours, dressed for the pool: trunks or a bikini, shades, a sun hat or a visor, a drink in
most hands) dance in the water and in front of the DJ, talk in twos and threes (speech bubbles), sit on the pool's
edge with their feet in the water, drift round on a donut and a flamingo, drink on five of the swim-up bar's seven
stools, tan on the loungers and the daybeds, and lounge in the cabanas. They move to the DJ's beat (124 a minute),
the speakers thump with it, the decks turn and the truss's four lights sweep the crowd (brighter at night); every
32 beats comes the drop, and everyone dancing, and the DJ, throws both hands up. They mingle, too: now and then (at most six
at a time) someone dancing or talking leaves their place, walks over (or wades, in the water) to someone else (a
dancer, a talker, one at the swim-up bar, or beside a lounger, a cabana's sofa or the pool's edge) and they talk, a
bubble over whoever is talking, the other turning to them; or they go off to dance somewhere on their own; then they go
back. Each goes only by a straight way that is clear (no furniture, no crossing the pool's edge, nobody standing in it),
and the waiters wait for them to be back. Now and then one sips a drink,
someone whoops (WOOO!), two at the bar clink glasses (CHEERS!). Two bartenders work the swim-up bar, each its own half:
along the counter to someone, a drink shaken, poured and set down (they drink; one with no drink gets one), the
counter wiped. Three waiters (white shirts, bowties, black aprons) take a tray of cocktails from the grotto's
service window round the deck to whoever is waiting (a lounger, someone on the pool's edge, the dancers, a cabana)
and hand a drink over; the third, the bottle girl, carries two bottles with a lit sparkler to a cabana, and its
people cheer (WOOO!). At night (dark mode) it is a night swim: the pool glows, the neon and the stage's screen
bright. All of it is acted out, like the jungle's pets: scenery, the board says nothing of it.

Beside the room, past the daybeds' lawn on its open right side, is the Lucky Token, the casino resort the pool club
belongs to: an open-air casino, in full view of the overview. No walls: ivory columns round its edges, planters with
hedges between them, a canopy over it all (its ceiling coffered in red and gold, bulbs along its edge, chandeliers down
its aisle) which is lifted off for the overview, as the cave's mountain is, so you look right down onto its floor. Walk
out of the room's right side and up the red carpet through its gate (LUCKY TOKEN over it), or in by the gap on its
left. Behind it its hotel tower (gold glass, its windows lit at night, LUCKY TOKEN at its top, two searchlights
sweeping the sky from its roof), at its foot the hotel's doors, the high-limit room's and the prize on its podium (your
lowrider: WIN THE LOWRIDER); a pylon sign by its front corner. On a loud carpet: two banks of slot machines (a few of
them played by the room's own), the progressive jackpot ticking up behind them, a round bar (LOUNGE over its bottles),
the cashier's cage; across the aisle roulette, craps and two blackjack tables, each with its dealer and its players
(cards dealt, played and swept up; dice thrown down the table and called, a 7 or an 11 cheered; the wheel spun, the
ball round it and into the called number's pocket). And they are yours to play, and your visitors', with chips:
everyone starts with 1,000 (yours and each visitor's kept by the board, in `casino.json` beside the room's settings, a
visitor's by their office's key: a restart keeps them, and a visitor back another day finds theirs), their count over
their heads on the casino's floor, a + or a - rising out of it as it changes, and the cashier tops anyone short back up to 1,000
(click its window). Sit at a free slot machine and it plays, 5 a pull: your hand on its lever, the reels off and
stopping one after another; three alike (one pull in nine) pays 20 to 150 by the symbol, its topper flashing, coins
pouring into its tray, JACKPOT!, your hands up; three 7s (about one in 170) pays the progressive jackpot itself. Click it
again as you sit for another pull. At roulette, click any spot of the layout (a number, the zero, or a box along its
near edge: 1-18, EVEN, RED, BLACK, ODD, 19-36) and 10 goes there, up to eight chips a spin, until the ball rolls; the
number called, a number pays 35 to 1, a box evens, and the croupier names the biggest winner. At blackjack sit on a
free stool (three of the six seats are free) and you are dealt in, 10 a hand: the dealer asks you by name, your total
over your cards; click HIT or STAND on the felt before you (20 s, then you stand); the dealer draws to 17; a win pays
evens, a blackjack 3 to 2, a push your bet back. At craps click the table: the dice are yours, 10 on the pass line, and
you throw (a 7 or an 11 wins on the come-out, a 2, 3 or 12 loses, anything else is the point, the puck ON at it; then
the point again wins, a 7 loses and the dice pass back); the stickman calls every throw by your name. Everything is said
in the room, in the dealers' bubbles, so your visitors see it all as you do (Visit shows them "play the slots", "10 on
this", "hit", "stand", "throw the dice" and "chips"). Click the bar for a cocktail, as at the pool club's, or sit on
one of its stools. Up to six of the pool party's own play roulette too (the near side's two places by the wheel are
always yours), each with chips of its own colour: a wallet
on the rail, chips flicked onto the layout, and over its head its name, its chips and how far up or down it is on its
buy-in, a + or a - at each call. Broke, or doubled up (cashing in at the cage), it goes back to the party to dance, and
a while later someone from the dance floor comes over, buys in at the cashier and takes the empty place.

**Casino stats** (the legend's button) is your record there: your chips, net, peak and biggest win; at blackjack the
hands played, won, lost and pushed, your blackjacks, the times the dealer's blackjack beat you, busts and streaks; at
roulette the spins, your favourite and luckiest numbers on a layout of where your chips go; craps' naturals, points and
seven-outs; the slots' pulls, wins and jackpots. And 32 achievements, bronze to platinum (a few secret till earned),
whose points rank you from Tourist to Legend of the Strip. One earned pops up over your head in the room, where everyone
sees it, and at the top of your screen with a chime. The room's page counts it all as you play and the board keeps it
(`/api/casino`). Your visitors' too: its **Player** picker shows anyone who has ever played in your room, their record
and achievements as yours are shown.

Your visitors see theirs at the **Players Club**, against the tower between the HOTEL door and the lowrider: a kiosk whose
screen shows the top players (you among them), and the card of whoever clicks it (their rank, chips, a medal for each
achievement, how each game goes) for everyone there to see. Penny, its host, is one of the room's figures in Visit: a
visitor who talks to her (or whispers) has their stats whispered back, "top" the leaderboard; she whispers each
achievement to them as they earn it, and says hello the first time on a visit they step onto the casino's floor.

The agents there work where they like. Every few minutes one that is free to (no card to walk, no subagents, not
waiting on you, not off for a drink, dozing or on its phone) may pick up its laptop and walk out of the room's front
by an aisle and round the deck by the waiters' way to somewhere free: the side of a lounger no one lies on, a
cabana's sofa, the pool's edge (feet in the water), the swim-up bar's counter by a free stool (standing in the water),
or a high table by the dance floor; later it moves on, somewhere else or back to its desk (which says "out by the
pool" meanwhile). Out there it does what it does at its desk: types on its laptop (on its lap, the counter or the
table), the Matrix on it while a script runs; dozes over it or scrolls its phone; holds up its "waiting on you" sign;
keeps its claimed cards beside it, its name card over it (a click opens its console); walks in to the board for a card
and back out; and its subagents sit round it on towels, their laptops on coolers, while it stands over each in turn,
hands on hips. Switching to another environment brings everyone back to their desks.

An intern (a subagent) that finishes at the pool club does not die: it gets up from its desk (or its towel), walks
out of the room's front and round the deck to the dance floor with a cocktail, cheers (WOOO!) and joins the party,
one of the crowd from then on: dancing, sipping, off mingling now and then like the rest. Ten at most: past that, the
one there longest walks back through the room and goes home by the side door.

The room has sound, on your own page, and (Visit 3.4, [basedverse#36](https://github.com/sarmiena/Basedverse/issues/36))
for your visitors: the room's page renders the same makings once (an OfflineAudioContext, in each environment's echo)
into files Visit plays for everyone in the room, each from where it sounds (`vsRoomSounds`, `vsPop`). On your own page
all of it is made live with Web Audio, nothing recorded (Visit's copy of your room's is off there: `v.ownSounds(false)`). Each environment has its bed: the office's air and birds outside (crickets at night); the jungle's
cicadas, birds and a far-off monkey (crickets and frogs at night); the cave's hum and drips, with a long echo; the pool
club's open air and splashes. What sounds is heard from where you are (walking about: you; the overview: the middle of
the view), louder near and to the side it is on: the DJ (Am, F, C, G at 124 a minute; the eight beats before the drop
build, and as the crowd's hands go up, a crash), on the wall clock so it is in time with the dancers; the crowd's
murmur and laughter by the dance floor and the swim-up bar; the waterfall; the jungle's stream, campfire, bug zapper,
the flies over the bananas and each mosquito in flight; the cave's fire drum, its torches and the wind out past its
mouth; anyone at work, typing. Every comic burst but WOOO! sounds as it pops: BANG! (a shot, echoing in the cave),
BOOM!, OOK OOK!, ZAP!, SMACK!, MALARIA! (a sad trombone), CHEERS! (two glasses), *yawn*... (WOOO!, the pool club's
cheer, is silent: its sung "oo" came out as a fart.) The room's page marks what
sounds (an empty object named `sound:<kind>`, a burst `burst:<kind>`: `soundMark`, `stepSound`), and your page plays
what it finds in the room Visit brings it. **Sound: on/off** by Reset view turns it off for this browser; nothing
plays before your first click or key (the browser's rule), nor while the tab is out of sight. **Mixer**, beside it,
has a level and a mute for each of three: the **music** (the DJ), the **ambient** (each place's air, and its water,
fire, wind, bugs and typing) and the **crowd** (the club's chatter and laughs, and the casino's floor: the slots'
little tunes, a win's bell, coins); the bursts have none, under Sound
alone. The levels and the ambient's and crowd's mutes are this browser's. The music's mute is the room's (`room.music`,
kept by the board): muted, the pool club's DJ takes a break (round his booth, down off the stage, over to talk with
whoever is on the dance floor), nobody dances (they stand about with their drinks, and more of them go off mingling,
sooner), and none of your pages plays it; back on, a WOOO!, the DJ goes back up to his decks, and the minglers
come back to dance.

### The tarot reader

In the room's front right corner (across the room's right side from the office's coffee bar),
Madame Vesna keeps a booth: a rug, a round table under a velvet cloth with a crystal ball and two candles flickering,
her high-backed chair, and behind her a folding screen of stars with her sign. The booth is set at an angle, facing into
the room and toward its front, where you stand and the overview looks from; you, the agents and the pets walk round it.
In the terrorist camp she is not in the room: her booth is in a shallow cave up the mountain's trail (on its last
leg before the lookout), cut into the mountain's face, just deep enough for her: her screen against its back wall, the
front of her table at its mouth, her cards up over her table under its arch; a level landing on the trail in front of
it, a torch either side of its mouth (`caveShell`'s `VESNA`; `g.userData.tarot` tells `tarotBooth` where).

- **Click her** (or her deck) and she is your target: her card shows at the top left (to the right of your agents' list, when it is open), as a game's target frame. It has
  her portrait (a little live model of her, who talks with her hands while her line shows), her name and calling, what
  she is doing, her cards (one or three), **New reading**, and the talk about the reading. Esc or × clears her.
- **New reading** opens a box for what the cards should look at (your career, a launch; or leave it blank), then
  **Deal**: first she asks, in character, whether you want **one card or three** (the model writes her asking:
  "Shall I light a single lantern for your new path, or lay out three cards to trace what was, what is, and what will
  be?"), in her bubble and on her card, which has a button for each (**One card**, **Three cards**); or answer her in
  words ("just the one", "the full spread", "you choose": the model sorts it; a plain "one card" or "three" by
  itself needs no call to it). An answer that does not choose ("what's the difference?") she answers, and asks again;
  after four of her asking, the cards choose. Ask for a reading with its number ("a new reading, one card, about money")
  and she does not ask. Then she shuffles, deals the cards face down onto her table (one card, for guidance, in its
  middle; or three: past, present, future; drawn from the whole 78-card deck, a third of them reversed, on average),
  turns them over one by one, and the model writes her reading (`board/tarot.py`, through `board/llm.py`: below);
  a single card's is shorter (about 100 words, to three cards' 160). She does not read it out:
  she bids the room look to the cards ("The cards have spoken, dear. Look closely at them: your fate is written
  there."), in a bubble over her head and in the room's chat, under her own name (Visit's `sayAs`; with no Visit, her own
  bubble); its words are on the cards' scroll, on your screen or up in the room, and on her card. (Read out whole, a
  line every few seconds, it flooded the room's chat.) A reading asked in a whisper she does whisper whole to its
  asker, the only place they can read it, and her answers after a reading she says: a line at a time, each cut, at its
  sentence, its commas, or evenly between words, to fit Visit's bubble whole (three rows as Visit wraps them, 96
  characters at most: Visit cuts a fourth row's end with "…"), one every few seconds, as long as it takes to read.
- **Save** (☆ on her card, once she has finished a reading) keeps it: the next reading no longer wipes it. **Saved (N)**
  lists the ones you kept, newest first (its cards, for whom and when, what it looked at); a click opens one on the
  cards' scroll, × removes it. A saved reading still on her table takes its talk as it comes. They are kept in the
  board's SQLite file (`board.db`, `tarot.Kept`), as your page has them (another's whispers without their words), and
  are yours alone (never As a visitor, no key reaches `/api/tarot/kept` or `/api/tarot/keep`).
- **Talk to her** from the box on her card, in plain words. What you say is sorted first (the model, a keyword
  guess if it fails): asking for new cards ("hey, I want a new reading aimed at my career path", "another one, about
  money", "draw again") deals a new reading for that focus, read toward it (she asks one card or three, unless you
  said); anything else is answered about the cards on her table; with no cards there, she reads for whatever you asked.
  While she waits for someone's answer to "one card, or three?" (two minutes from her last word to them), she is with
  them: anyone else but you is told to wait their turn. **Say** (aloud): your line goes in the room's
  chat as yours, `@Madame Vesna …`, over your head, and her answer comes in her bubble and the chat, after anything she
  is still saying: the room sees the exchange. **Whisper**: her answer on your card and the scroll, and whispered to
  you alone in Visit's chat (no bubble, nobody else); a reading asked in a whisper (or dealt with Whisper on) is dealt
  on her table for all to see, and its words are yours alone. With Visit (3.3: its targets, basedverse#48) both go as
  lines meant for her, Say at her and Whisper to her; the room's page hears them (`addressed`) and asks the board
  (`tarotAddressed`); without Visit, your page asks it. Her card's log has what asked for the reading, the reading
  itself, and the talk after it. In Visit's chat window, `@Madame Vesna …` (its `@` offers her) is a line meant for her
  the same way; one that starts `@Vesna` or `Vesna,` your page asks her itself. Answers take a second or so to write,
  and a few sentences each.
- **Her table is the room's**, shared as in a real world, not a reading each: whoever asks, you or a visitor, gets the
  next reading, and it is on her table for everyone. **Her memory** is that reading and the talk about it (its last 20
  turns; an answer is written from the last 8), kept by the board with the reading, so a restart keeps it; the next
  reading forgets it all. Her card and the scroll say whom the reading was for and when ("Read for Ann · 11:30 AM").
  A whisper stays its whisperer's: an answer given aloud never sees one, a whispered answer sees only its own
  whisperer's, a private reading's words are for whoever asked it alone, and your page shows a visitor's whispers
  without their words ("Whispered to Ann: the words are theirs").
- **Click the cards on her table** (or **The cards** on her card) and they fly up onto your screen and turn over, her
  whole reading unrolling on a scroll under them, and the talk after it (as she writes, while she is still at it).
Click a card there (or one of the three on her card) and it spins up out of the spread to the middle, its meaning
  beside it: Waite's own, from his *Pictorial Key to the Tarot*, the book for this deck (`board/tarot/meanings.json`): its
  meaning upright and reversed (the way it fell lit), what Waite says of the card, and the part of her reading about it.
  ‹ › (or ← →) spin to the others; **← The full reading**, Esc, or a click on the card or outside spins it back into
  its place. Esc again, ×, or a click outside closes the panel.
- **Her reading in 3D, for your visitors.** A visitor's click on the cards on her table (Visit's crosshair says "look
  at the cards") brings them up in the room, for everyone there, turned toward whoever asked: she lifts her arms, the
  three fly up off the table, turning over as they rise, to hang glowing over her head, motes of light drifting up
  round them, and a scroll's rods open in front of her table and its paper unrolls, her reading on it (written as she
  writes it), then the talk after it. A card clicked spins down beside the scroll, the others drawing back, and the
  scroll has its meaning (Waite's, as above); clicked again, or another, it goes back. The scroll is in pages: the gilt
  ▲ ▼ beside it turn them (so do the wheel on it and a click on it), each page drawn once so that turning one is
  instant for everyone (a canvas redrawn reaches visitors at most once a second, so scrolling one juddered). Its wax
  seal, a gilt ✕ with "close" under it, rolls it all up, and the cards fly back down onto her table. It rolls
  itself up after three minutes with nothing new on it and nobody touching it, and goes when she deals new cards. A
  whisper's words are never on it ("She read these cards in a whisper for Ann: the words are theirs"). Her bubble
  rises over the cards while they are up.
- **As a visitor** (the legend's button, yours): the room as your visitors have it, to see what they get. It is the
  room's setting (`room.guest`, the board's: every page of yours and the room's page go by it; a board restart turns it
  off), and the room's one check of you against a visitor (`owns`, room.html) says "visitor" while it is on: for your
  clicks and for your own body's in the room alike. So her cards (on her table, or **The cards** on her card) come up in
  the room in 3D rather than on your screen; an agent's name card, a ticket, the wall's switch and the wheel on a deck
  do nothing; "too far away" is not said; and what you say to her is a visitor's (by your name, the board's limit).
  Clicking her still makes her your target (a visitor has Visit's card for that). Walking is unchanged: a visitor walks
  in from their world and may walk off your land, as you may.
- **Your visitors talk to her too** (she is described for them: `v.figures`, `talk: "everyone"`). A visitor who clicks
  her has Visit's own card (her name, her calling, Say and Whisper), and their `@Madame Vesna …` in the chat window is
  meant for her: the room's page hears it (Visit's `addressed`, whether or not your page is open) and asks the board for
  them, by name. She answers aloud for the room, or whispers back to that visitor alone (Visit's `sayAs` with `to`).
  Anyone but you who asks for more than 10 readings in a minute rests 3 minutes (and so for questions): she tells them,
  privately, and your GPU is not spammed. And nobody, you included, has the model called for them more than 15
  times a minute (each sort of what they said, each line she writes, each reading: most lines to her cost two): one
  past it is told, privately, how many seconds to wait, and the model is not asked. What is yours alone: her card's paper doll and the 2D scroll (your page's
  own; a visitor's page runs Visit's); a visitor's click on the cards on her table brings up the 3D one (above).
- The reading is the room's: the board keeps the last one (`tarot.json` beside `room.json`), so every page and visitor
  sees the same cards on her table. `GET /api/tarot` serves it (`?v=N` held until it changes, as `/api/board` is);
  `POST /api/tarot` (`{question, private, by, cards}`) asks for a new one (`cards` 1 or 3: dealt at once; none: she asks
  which first, and the reading waits on her table with no cards, `choosing`, her lines asking it its `pre`),
  `POST /api/tarot/ask` (`{text, private, by}`) says something to her (`did`: "reading", "choose" or "answer"; `by`, who
  said it: from your page always you, from the engine's page whoever it says; 409 while she is writing or is with
  another querent, 429 for one resting or past the model's 15 a minute, 503 with no model: `llm.NEED`). Your page is served a
  visitor's whispers without their words (the engine's page has them, to whisper them back). The cards are the Rider-Waite deck's own pictures (Pamela Colman Smith's, 1909, public domain: scans of a 1910
  printing from Wikimedia Commons, `board/tarot/<key>.jpg`, each credited in its `SOURCES.md`), served at `/tarot/<key>.jpg`
  (signed in, as every route); the page paints a card (`tarotFace`) only if its scan will not come, and paints the back.
- No model at all (neither `LLM_KEY` nor `LLM_URL` in `.env`): her card says she needs an LLM key, like a Claude API
  key; asking her says so too (to a visitor, in her voice). The model failing or not answering: she says the mists will
  not part, and your page says why.

### The board's model

`board/llm.py` is the board's way to a model, for any feature that wants words written; the tarot reader is its first.
Bring your own. Stdlib only (plain HTTP, no SDK). In `../.env`, either:

- **`LLM_KEY` alone, a Claude API key**: the board writes with Claude through the Messages API, `claude-opus-5-5` unless
  `LLM_MODEL` names another. Claude always thinks a little: effort `low` (`high` with `think=True`), and room for its
  thinking on top of `max_tokens` (`THINK_ROOM`); no `temperature` (Claude takes only its default sampling); a request
  its safety checks decline goes to the model Anthropic names for it (`fallbacks: "default"`), and one nobody answers is
  an error. A busy or overloaded answer (429, 529, 5xx) is asked again twice.
- **or a model of your own**, any OpenAI-compatible chat API (vLLM behind a gateway, on the tailnet; Ollama; OpenAI's):
  `LLM_URL` (the API's base, `/v1` and all), `LLM_MODEL` (an id as `GET <LLM_URL>/models` lists it), `LLM_KEY` (its
  bearer key, if it wants one). No thinking unless `think=True` (`reasoning_effort: "none"`: the gateway drops vLLM's
  `chat_template_kwargs`, and Qwen3.8 ignores `/no_think`).

`LLM_URL` set means that API, whatever `LLM_KEY` is. With neither, every call fails with `llm.NEED` ("I need an LLM key,
like a Claude API key, to function: ..."). `llm.stream(messages, ...)` yields the answer as it is written, `llm.chat(...)`
returns it whole; the reasoning is never returned. Every failure is an `llm.LLMError` saying why.
From a terminal: `python3 board/llm.py "a prompt"` streams an answer, `--models` lists the models, `--think` lets it
reason first. The board reads `.env` when it starts: `bin/board-restart` after a change.

## The lobby, and Visit

The lobby and Visit are basedverse's, a repository of their own (`../basedverse` beside this one, or
`BASEDVERSE` in `.env`): its README says what they are, and its `lobby/README.md` how to run a lobby.

With `LOBBY_URL` and `LOBBY_NAME` in the `.env` (`bin/start` asks for them from a terminal), `bin/start`
runs this office's visit-client (built from basedverse's `visit/client`, in Docker): the room's one way
into the lobby. The room then shows Visit's panel at the top right:

- the offices online, each with a dot (green available, amber busy), its visitors, and **Link** (Visit 0.8; an
  office on an older Visit gets **Knock**: let in, you walk straight into its room);
- a link asked of you, with its message and the seconds left: **Link** or **Not now**, and **★ always** to make
  them a guest as you say yes;
- your links: once they say yes, their room shows in the corner, from above; click it, or the panel's buttons,
  to **Go there** (their room full-window; **Back to my room** puts it in the corner again), **Put in my world**
  (their room round your land, below, while the link lasts) or **Unlink**;
- your status (`available` / `busy`) and how many may visit at once (`VISIT_MAX_VISITORS` to start with);
- your visitors, with **Kick** and **their look** / **your model** (each as their own avatar, or as one of the
  room's dudes; "visitors look like" sets the default), and who is linked to you, seeing this room from outside
  it, with **Unlink**;
- **🎙** your microphone: on, those near you in this room hear you, the nearer the louder, and you them, within
  45 m (Visit 0.9: nothing to join; not into other rooms, nor next door in your world); "near you" says who, and
  Visit draws a 🔊 over whoever you hear speaking (Visit 0.13), here and in a room you visit;
- **📞 call** a friend linked to you whose room has a phone, and your calls: **Pick up**, **speaker** / **handset**,
  **Hang up** (your phone: below); and **Map**: your world from above, where your friends' plots sit;
- **notes** for a while: where a friend's world just put your room, and a call that ended unanswered;
- **Say**, at the bottom: a line typed there shows over your head, for everyone in the room you are in (Visit
  0.11: nobody else, not next door in your world), and theirs over theirs;
- **⏱** (Visit 0.14): how this page is doing, in a corner, each second: frames, stalls, and what Visit does (scanning
  the room for visitors, sending, your world, voice), with **copy report** for the last two minutes. On or off,
  remembered in this browser; nothing leaves the page. The jungle's boards are repainted each second but sent to
  visitors only when they change (`stepBoards`). Your page draws what of the room stands still in batches (its
  scenery, its desks and what is on them), and what moves only with what it is on merged on it (a figure's parts), a
  few hundred draws in place of thousands (`stepStill`); a box with a picture on one side (a card, a face) is two
  draws, not six (`boxApart`); the sun's shadows fall wherever you look, worked out again every 30 ms (`followSun`);
  `/room?batch=0` draws it mesh by mesh, to compare.
- **your door** (open to guests, or shut) and a star (☆) by each office, to make it a guest: through an open door
  a guest's link is let in without asking you, and a guest sees your land from theirs (below);
- **Go offline** / **Go online**: out of the lobby (nobody sees you or can link; visits end) and back.
  visit-client keeps it in `.visit/state.json`, so a restart (`bin/start`) stays offline until you come back.
  That file also holds the office's key, its lasting identity to its friends: a secret, never copied anywhere.

Let in, a visitor walks around this room with WASD and the mouse: their browser draws it from this room's
scene, from where they stand (spawning them beside the owner's spot at the front), and walks where you may:
round the desks, out of the room's open sides, and about its land, but not off it. Everyone else sees them there: a dude of their own with their name over their head in
blue, its looks picked at random from their name (the same visitor looks the same each time), striding as
they walk and tipping its head as they look up and down. The room treats them like anyone about: a
mosquito may bite them, the monkey may bonk them, the rat and the snake keep clear. Their own body is put
away while their view is drawn, so it never fills it.

A visitor can click the fun things too, as you do (Visit 0.6's points: while they look around, a crosshair,
hot over what they may click; a click there sends only where they looked, and the room casts that ray from
where it has them). Poke the rat or the snake, set off a mosquito or swat one in reach, wake a dozing agent,
drink at the water cooler, have a coffee, eat a banana and throw the peel, sit on a log or a bench (where they
stay, their view from there, till they walk off). Each is done by their own body, and everyone sees it. Every
click in the room, yours or theirs, goes through one list (`INTERACTIONS` in room.html): a visitor's is tried
against the entries marked `visitors: true` only, four a second at most, and an entry not marked is yours
alone. Name cards (an agent's console) are yours alone, and the console also opens only inside a click of your
own, as the browser reports it: never for a visitor's point, a script or a timer. `board/test_visitors.mjs`
holds the line: it follows every call each visitor entry can make through room.html and fails if one reaches a
request, the console, Visit's lobby, or what this browser keeps for you. A visitor's copy of the room carries
none of its objects' `userData` either (who a figure is, its session): Visit 0.6 sends only its own marks.
Since Visit 0.9 each fun thing is marked with its kind (`sit`, `banana`, `water`, `coffee`, `wake`, `swat`, `poke`:
`VISITOR_TYPES`), given to Visit as an interaction, every one cosmetic, so a visitor's crosshair says what a click
there does; a click right on one Visit runs through the same gate (`visitorTyped` into `visitorPoint`), and the
test holds that too.

**Your phone** (Visit's own, its vanilla one since 0.13): on a stand beside your spot at the front, where everyone
sees it. It shakes, RING RING over it, while it rings; its handset is off its cradle while you have it to your ear;
its lamp is lit on **speaker** (everyone near the phone, your visitors too, hears the call and is heard in it).
You call, pick up and hang up from the panel. A visitor's click never reaches it.

**Your lowrider** (Visit 0.18: one of your possessions, which go where you go; your only mount): white, gold
trimmed, a fan of gold wings off each side, a spinner on each wheel. **M** (or the panel's mount row) gets you in and
out. You sit at its wheel; W drives it at three times your walking pace (Shift, twice that), S backs it up, A and D
steer (it does not sidestep), and what is in the way of its nose or its tail stops it. It flies: Space up, X down,
its wheels folding flat under it, spinners up; over the treetops nothing stops it. Visit draws it under you: at
home in your scene, so your visitors see you drive and fly by; on a friend's plot or in a room you visit, that room
asks for its model once and draws it, you sat in it (your avatar's `ride`), flying unless that room keeps flyers out.

**Your landscape** (Visit 0.12): the building, your environment's walls, floor, trees and the rest that never
changes, goes to a visitor once and is kept by them (by its hash: never sent again, across visits and reloads), so a
visit or a friend's world loading your plot carries only the rest (the jungle's 2.4 MB room becomes 1.1 MB, its 0.9
MB landscape sent once). While you are away, your guests' worlds keep drawing it where your plot was, marked
offline, and walk them into its walls. room.html marks each of the building's meshes as it is built, and takes out
of it, for good, any it sees move or change (a vine swaying, the campfire, a banana taken): those go as the rest of
the room does. Your friends' landscapes are kept in `.visit/landscapes/`.

**Your look in other rooms** (Visit 0.9's avatars): your dude, as here, goes to a room that shows its visitors'
own looks (it walks and stands as yours does); in yours, a visitor whose look you show walks about as their own.
Visiting, the room you are in fills the window. `/api/visit` hands the room its visit-client's port and
token, and only to `localhost`. `bin/down` stops the board and visit-client both.

**Your world** (Visit 0.7, basedverse's `visit/WORLD.md`): star an office, and have it star yours, both doors
open, and its room appears as a plot of land round yours, drawn in your own room, with its own lights. Your land
(the room and the grounds round it, 110 × 110 m) is your plot; past its edge is the ground between plots. Walk
onto a friend's plot and you are in their room as yourself: their room walks you (by their walls), they see you
there, and you see them; walk off it and you are home. A friend walks onto yours the same way, from their world:
they appear where they stepped on and walk where a visitor may, and off the land's edge, which takes them home.
Plots sit round yours by themselves, or where you put them on the panel's **Map** (Visit 0.9): your layout is
yours alone, nobody agrees to it, and each friend is told only where their own room sits in it (Visit 0.10): the
panel notes when a friend puts yours somewhere in theirs ("Kay put your room north-east of theirs, 12 m off"),
once it settles, and when they take it out. Pick a friend on the Map for their view of the two of you: where their world has your room. Put a friend's land
flush against yours and walking off that side goes straight onto theirs. Without a visit-client there is no
world, and the land's edge is just its edge.

Two rooms on one machine, to try it: basedverse's `visit/client/tests/run-browser.sh` does that end to end
(a local lobby, two visit-clients, two browsers). By hand: a second visit-client (basedverse's
`visit/INTEGRATING.md`, step 8, on a port other than its example's 7402, which `bin/consoles` has) with its
`{port, token}` in `.visit/visit-2.json`, and the room opened as <http://localhost:3001/room?visit=2>; a third
is `visit-3.json` and `?visit=3`, for two rooms beside your own (a test page that took your own visit-client
would take over your room's tab). Star each from the other's panel for step 9's check: each sees the other's plot.

## Quota

GitHub allows 5000 API requests/hour per ACCOUNT: every token of the same user shares one budget.
The server polls every 5 s (`BOARD_POLL_S`: one listing of open issues and PRs, plus the plan and the
roster) and re-reads dependencies only for issues that changed, plus all of them every 60 s. Every read
is conditional, and an unchanged answer (304) does not count, so an idle board costs little. When fewer
than 300 requests are left (`BOARD_RATE_FLOOR`) it stops reading until the hour resets, leaving the rest
to the agents, and says so on the page.

## The token

`REPO_TOKEN` is a GitHub personal access token: the password-like string the board (and `forge.py`,
when you set it) presents to GitHub's API. Everything they write shows on GitHub as that token's user.

- **Quick:** `gh auth token` prints the token your GitHub CLI login already uses; paste it as
  `REPO_TOKEN=...`. It can do whatever your account can, on every repo, and stops working if you log
  `gh` out.
- **Better: a fine-grained token** (GitHub → Settings → Developer settings → Fine-grained tokens):
  resource owner = the org (`REPO_OWNER`; the org may have to approve it), repository access = only the
  repos in `REPO_NAMES`, permissions = Issues: read and write (issues, labels, comments, dependencies),
  Pull requests: read, Metadata: read. For agents' `forge.py merge` too, add Pull requests: read and
  write and Contents: read and write. Set an expiry; the board says so on its status line when GitHub
  starts refusing it.
- **Its own quota and its own name:** a token from a second GitHub account (a bot user added to the
  org) gives the board a separate 5000/hour and makes its writes show as the bot. A second token of
  your own account does neither.

`.env` holds it in plain text: keep `.env` out of any git repository.

## Usage limits

The kanban's header and the Agent Room show the account's usage limits as `/usage` does, a bar each: the
current session, the week for all models, and the week for each model with a limit of its own (Fable); how
much of each is used, when each resets, and (hover, or on the board) how the week is going ("On track. You
should reach Friday's reset with room to spare.", or when at this pace the limit comes first). In the jungle
they are a hand-painted board on the left wall, beside the rat-and-snake tally; in the office, a screen on the
left wall. Visitors see the room's board too, as they see the room.

`bin/usage` (`bin/start` runs it in the background on this machine) asks Claude Code for them every two
minutes: a headless `claude -p` sent one `get_usage` control request, which reads what `/usage` reads and asks
no model, so nothing is used up; it keeps no session and runs no hook. It writes the rows to
`.usage/plan.json` beside `bin/`, and the board (`server.usage`, the project mounted as `USAGE_DIR`) serves them
while they are under ten minutes old. It needs `claude` on the PATH, signed in to a Pro or Max account;
`bin/usage --once` prints what it gets, and `.usage/log` why a look failed. Claude Code marks `get_usage`
experimental, so a later release may change it. Until `bin/usage` answers again, the board falls back to the
agents' status lines: the seat's (`seat/hooks/usage_line.py`, set in each agent project by `seat/install.py`)
notes the session's and the week's limits in `.usage/<session>.json` (Claude Code hands a status line those
two, not the per-model weeks), then shows the status line you had. The board reads every session's note as
one account's: for each window the latest reset is the current one, and of the sessions on it the highest use
is the newest. Those notes are only as fresh as the agents' last answers, and say nothing until one has had
an answer.

## Run it

```sh
../bin/start                            # start, or restart: after any change to board/*.py or ../.env. Fills a blank
                                        # REPO_TOKEN from `gh auth token`, waits for the first read. Messages and the
                                        # roster are kept; agents' waiters reconnect by themselves.
../bin/down                             # stop it, and bin/shells and bin/consoles (messages and the roster are kept)
../bin/shells --once                    # who has a command running, as the board will show it (bin/start runs
                                        # bin/shells in the background; --start / --stop by hand)
../bin/consoles --start                 # an agent's terminal from its name card in the room (bin/start runs it;
                                        # --stop by hand)
```

The token needs issues read/write on every repo in scope; plan writes, card moves and the roster's
*remove* are made as its user. Pages long-poll `/api/board?v=N` and redraw as soon as anything changes.

The board signs you in ("Signing in", above), and listens on 127.0.0.1 only: other devices reach it over HTTPS
("From other devices", above), never plain HTTP on the LAN.

## Later: a GitHub Project as a column source (pinned 2026-10-23)

Today every column comes from facts on the issue itself (labels, claims, PRs, blocked-by edges). The
parked alternative: let a column read a GitHub Project's single-select field, e.g.
`"when": {"status": "In review"}`, and let a drop set that field instead of a label. It would buy a
Status that is exclusive by nature, typed fields (priority, iteration), cross-repo items and GitHub's own
board view. It would cost a token with the `project` scope (`gh auth refresh -s project`), GraphQL reads
the board cannot make conditional (so polling spends quota), and a second place a card's column lives,
apart from the issue. Revisit if the team starts tracking its issues in a Project anyway.
