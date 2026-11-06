# Visit — the specification

**Status: built** (`visit`, the one program a workroom runs, with visit-client and visit.js, in `client/`; the
lobby, `lobby`, in `../lobby/`), and in use between machines over the internet. This is the contract they
are built to. Where it says MUST, SHOULD or MAY, those words mean what RFC 2119 says they mean.

Visit lets the people running agent-office sims ("workrooms") walk around each other's rooms. Every room
is made with three.js. A visitor moves with WASD and the mouse; the host sends its room's scene, as data,
and the visitor's browser draws it, with the host's very three.js release, as quickly as it draws its own
room. The host decides where the visitor may stand; what moves in its room reaches the visitor as it moves.

If you are integrating Visit into a sim, read [INTEGRATING.md](INTEGRATING.md) instead: it is the
how-to. This file is the contract behind it, for whoever builds or changes the shared pieces.

## 1. The pieces

```
           ┌──────────────── lobby server (wss) ─────────────────┐
           │ presence, knocks, visits, link addresses            │
           ▼                                                     ▼
 your machine                                       their machine
 ┌──────────────────────────────┐                   ┌──────────────────────────────┐
 │ your sim's page              │                   │ their sim's page             │
 │   visit.js  ◄─ localhost ─►  │                   │  ◄─ localhost ─►  visit.js   │
 │             visit-client ◄═══╪═══ the link ══════╪═══► visit-client             │
 └──────────────────────────────┘  (QUIC, iroh;     └──────────────────────────────┘
                                    via the relay if
                                    no direct path)
```

| piece | what it is | who provides it |
|---|---|---|
| **visit-client** | the local client: the heart of `visit`, the one program a workroom runs (section 4), one per sim per machine. Owns **all** networking: the lobby session, the links to other visit-clients, visit state. Listens on 127.0.0.1 only (or a container's own network, 4.1). | shared, identical for everyone |
| **visit.js** | the library, in the sim's page. Talks only to its own visit-client. Sends the room's three.js scene to its visitors and moves them with the sim's hooks; draws the scene of the room it visits; captures input. Served by the visit-client itself, so the two always match. | shared, identical for everyone |
| **lobby server** | presence, knocks, worlds, the lobby's chat and whispers, and introducing visit-clients to each other (`basedverse-lobby/4`, section 6). Anyone may run one (../lobby/README.md); a workroom joins the one its `VISIT_LOBBY` names (basedverse's: `wss://basedverse.app/`). | shared, one server per lobby |
| **relay** | an [iroh](https://iroh.computer) relay, for links between machines that cannot reach each other directly. Encrypted end to end; it cannot read what it carries. | the lobby's own (`lobby --relay-port`), for its offices only, as its welcome names it (`relays`); a lobby that runs none: iroh's public relays |
| **the sim** | its own room, made with three.js (r125 to r186); a launcher step; its scene and two host hooks (`spawn`, `move`); its UI, its own since 5.0 (UI.md: built on the library, or a copy of the reference UI). | each sim |

The rule that holds it together: **a page only ever talks to its own visit-client**, and **only
visit-clients talk to each other**. A page reload breaks nothing but the localhost hop.

## 2. Words

- **Office**: one sim's presence in the lobby, under a name. One visit-client is one office.
- **Host**: an office whose room is being visited. **Visitor**: an office walking around someone else's room.
- **Visit**: one visitor in one host's room. Directed: A visiting B is not B visiting A (both can happen at
  once, as two visits). A visitor is in at most one room at a time; a host has at most `max_visitors`
  visitors.
- **Link**: the QUIC connection between the visitor's and the host's visit-clients for one visit.
- **Channel**: a small number (1–65535) a visit-client gives each of its visits on its localhost
  connection, to label binary frames. Not shared between machines.
- **Pose**: where a visitor stands and looks in the host's room. Only the host's sim interprets it.
- **Scene**: a room's three.js scene (or the part of it the room shares), as its visitors draw it (section 8.1).
- **Hosting**: an office's page has given Visit its scene (`host`, 5.2). Only then is the office in the lobby
  to be visited, and only then can it knock: every office takes part with its room.
- **Input**: what the visitor is pressing and where it is looking (section 8.2).
- **Key**: an office's lasting identity: its link endpoint's public key, the same every run when the
  visit-client has a state file (4.1). Names are first come, first served, and change; a key cannot be taken,
  because the lobby makes every office prove it holds its key at hello (6.1), and a link makes whoever dials prove
  it again (7.1). One key is the same office in every lobby.
- **Signed in**: a workroom with a live session of its sign-in (../auth/SPEC.md 4). Only then is its office in
  the lobby (section 3, point 13).
- **World** in the lobby (6.6; the protocol's **group**): offices kept together by a lobby, public (anyone joins) or
  with a password, whose members are each other's guests. Each has a chat and an owner.
- **Guest** (3.3): anyone in a world with an office. **Door**: open (its guests come in without knocking, and may
  put its room in their worlds: the visit-client answers their knocks yes itself, 6.4) or closed (everyone knocks).
  Before 3.3 a guest was a key on the office's own list (★); the list is gone.
- **Picks** (3.3): whose rooms an office's world shows round its own (8.4), its user's choice among its guests, at
  most 8. Nobody's room is in it by itself.
- **World** (8.4): an office's own room with its friends' rooms round it, each on its **plot** (the rectangle
  of its floor that is its land), drawn by the office's own page. **Watch**: a visit that sees a plot from
  outside it, sending nothing, until the watcher **steps in** (`enter`) and is in the room as a visitor is,
  and **steps out** (`exit`). **Layout**: where an office has put each friend's plot in its world, by key: its
  own, agreed with nobody. **Seen**: where a friend's layout has put this office's plot, as that friend said.
- **Linking** (before 4.0): a knock that, let in, started a watch seen in a corner. Gone with knocks to walk in
  (4.0, #57, 8.5): worlds are the way to meet. "A link" is a visit's QUIC connection, as above.

## 3. Security

These are MUSTs for the visit-client. They exist because any web page a person opens can try to reach a
program listening on their machine.

1. **127.0.0.1 only.** The visit-client binds its HTTP/WebSocket listener to 127.0.0.1 and nothing else
   (not ::, not a LAN address), or, told `--listen 0.0.0.0` in a container, its container's own network, its port
   never published (../engine/SPEC.md 6.1). It refuses to start if asked to bind elsewhere.
2. **A token on the page connection.** The visit-client is started with a token (at least 32 hex
   characters, from a cryptographically secure source, new for every run). A page connection MUST send it
   in its first message (`hello`, section 5.1) within 5 s; the visit-client compares it in constant time
   and closes the connection with code **4401** if it is missing or wrong (after a quarter of a second:
   guessing is hopeless anyway, at 256 bits). There is no lockout: any website could trigger one.
3. **The token reaches only the sim's own page.** The launcher hands it to the sim's page by a route other
   sites cannot read: the sim's own server answering a same-origin request, a file served beside the page,
   or the page's URL fragment (INTEGRATING.md step 3). It is never written into a URL query, a log, or
   anything served to another origin. A server that hands it over gives it only to its signed-in owner (the
   sign-in API's `/v1/visit`, served by `visit` itself, ../auth/SPEC.md 3) or, without a sign-in, SHOULD answer
   only requests naming this machine (`Host: localhost` or `127.0.0.1`): that turns away DNS-rebinding sites, and
   nothing more (a program on the network can send any `Host`). What keeps the network out is that server
   listening on `127.0.0.1`.
4. **The page's origin, too (SHOULD, always).** Given `--origins` (the sim's own page origins), the visit-client
   refuses (HTTP 403) a page connection from any other. Browsers always send a WebSocket's origin and a page
   cannot change it, so a token that leaked anyway is no use to another site's page.
5. **Public, harmless endpoints only.** Without the token, the visit-client serves only `/visit.js` (the
   library, public code) and `/health` (a version string). Nothing else.
6. **Links are authenticated twice.** A visitor's visit-client dials the host's with the visit's
   **ticket** (a secret the lobby gives both sides, section 6.4), and the host's checks that the dialling
   endpoint's public key is the one the visitor published to the lobby (section 6.2). A link that fails
   either check is closed before anything else is read.
7. **A host controls its room.** Visitors only send input, and points: where they looked as they clicked
   (8.3), never what they claim to have clicked. The host's sim decides where a visitor can go (`move`), what
   it sees (its scene), and what, if anything, a point does. The host visit-client clamps every input field
   and every point to its range, drops inputs beyond 120 a second and points beyond 5, and control messages
   beyond 20 a second from either side of a link.
8. **A host's scene is data, never code** (section 8.1). The visitor's page runs only the three.js release
   its hash table names, checked as it is fetched; it reads what a host sends only as data, made safe first
   (no shaders, images only as data URLs of a sane size, bounded geometry); its visit-client unpacks each
   edit with a limit and checks it before the page sees it.

9. **Guests by key, proved by the link.** A host's visit-client lets a knocker in without asking its page
   only when its door is open and the key the lobby gives for the knocker is a guest's (in one of its worlds,
   point 14); when the lobby then agrees the visit, the visitor's link endpoint MUST be that very key, or the
   visit ends at once (`kicked`), before any link is made. A lobby that lies about whose knock it is gets nobody
   in who is not a guest.
10. **The state file is a secret.** It holds the office's secret key: the visit-client writes it readable by
    its owner only. Whoever has it can be that office to its friends.
11. **A watch is a visit's exposure, decided the same way.** A watcher gets the host's whole scene, as a visitor
    does. A host's visit-client admits a watch only from a guest, through an open door (point 9), and never asks
    its page; anyone else's is declined. Shutting the door ends every watch of the plot; a guest no longer (out of
    every world the two shared) has theirs ended. A watcher's input and points count only while it has stepped in, and stepping in counts toward
    `max_visitors`.
12. **A layout is told to nobody whole.** Each friend in it hears where its own plot sits, over a link between
    the two offices (which proves who it is), and nothing of anyone else's.
13. **Signed in to take part.** A visit-client joins its lobby only while its workroom has someone signed in: a
    live session of its sign-in, made by `/v1/login` and not yet ended (../auth/SPEC.md 4); API keys do not count.
    Without the sign-in API (no `AUTH_TOKEN_FILE`), or with nobody signed in, the office keeps to itself, as with no
    lobby, and its page is told (`signed_in`). Signed out, it leaves at once (`bye`: its visits end). The lobby
    cannot see into a workroom: this is the visit-client's rule. What the lobby can check, the key, it checks (6.1).
14. **A world's members are the lobby's word.** A host's visit-client takes a co-member of any of its worlds (6.6)
    as its guest (3.3): through its open door, they put its plot in their world and walk in without asking, on the
    lobby's word for who is in the world. The key that knocks is proved (6.1, and by the link: point 9); whether
    that key is in the world is not, beyond the lobby. Join worlds in lobbies you trust. A public world is anyone's
    to join: its members walk into your room while your door is open (shut it, or keep to worlds with a password,
    and they knock). A world's password
    is kept by the lobby only as its Argon2id hash; one office tries at most 5 a minute, one world takes at most 30
    wrong ones an hour from everyone together (keys cost nothing to make), and the lobby hashes at most 4 at once
    (`busy`). Pick a password that is not a word.
15. **A browser speaks for an office only once the office links it** (3.1, 6.11). The browser makes a key of its
    own (its secret half never leaves it: Web Crypto, not extractable) and proves it to the lobby, for that lobby.
    The office links it, at its signed-in user's asking only (the person's page): by a web pass (signed by the
    office's key, for one lobby, good once and for 10 minutes, travelling in a URL's fragment, never sent to a server
    and taken out of the address as the page reads it), or by the pairing code the browser shows, typed in the room
    (its three pictures shown on both, so a code someone sends you shows theirs, not your screen's) and then (3.2)
    confirmed by the browser: it shows the room's confirmation (its QR code, scanned by the browser's own device, or
    its 6 digits typed there). So linking by code proves both ways: the room saw the browser's screen, the browser the
    room's; a code someone talks you into typing links nothing without your screen. A linked browser
    speaks for the office while the office is in the lobby, until the office unlinks it or it unlinks itself: it
    may talk, whisper, run the office's worlds, list who is here and knock (to walk into another's room: the visit
    happens in the office's own room); never answer a knock, open the door, link another browser or touch a visit:
    what lets anyone into the room stays with the room. A pass is anyone's who has it first, within its time: a QR
    code (shown on a screen) lasts 2 minutes; the room says when a browser is linked (so one not meant is seen, and
    unlinked). A link never moves a browser already linked to another office unless its page asks its person first
    (someone else's link, sent to you, would otherwise make your browser theirs). And a visit-client passes a page
    only the lobby messages a page takes from a lobby (5.3): never a page's own answers, which a lobby could make up.
16. **A teleport is its room's word, and the door's** (5.1, 8.15). Whoever may teleport into a room is that room's
    own page's to say (`teleport.allow`), asked afresh each time: as someone makes their list (an ask through the
    lobby, which only ever says who asks), as they knock to teleport, and as they step in. A shut door, or no page
    hosting, is no without asking it. An answer to an ask lets nobody in: walking in takes a knock to teleport, which
    the door's visit-client lets in only by its page's yes, and then only the key the lobby named, proved by the link
    (point 9); and a watcher stepping in by teleport is asked about again, by the key its link proved. A room that
    takes no teleports (no `teleport` in its `host`) answers every ask no. Those its page lets in are not its guests:
    they are not let in to watch, nor see its plot, by anything else; the guest list does not end their watch, the
    door shutting does.

Visitors get the host's whole scene: a sim that shows issue titles or agent activity in its room shows them
to its visitors, wherever they stand and whether they look or not. Letting someone in is the host's
decision, one knock at a time, or once and for all for the worlds it joins (its guests, points 9 and 14).

## 4. The visit-client: `visit`

The visit-client is the heart of `visit`, the one program a workroom runs: one file per platform (Linux x86_64 and
ARM64 with glibc 2.28 or newer, macOS 11 or later on Apple silicon and Intel, Windows x86_64), with everything it
needs inside it. Besides the visit-client it serves the workroom's sign-in (../auth/SPEC.md) and runs its engine
(../engine/SPEC.md 5), and it carries a Postgres, its database (4.4), and a headless Chrome, the engine's, each
with every library it needs. Nothing is installed, nothing is downloaded as it runs, and it needs no Docker. How
the Postgres and the Chrome are built and pinned: [../deps/README.md](../deps/README.md).

### 4.1 Running it

```
visit --lobby wss://lobby.example/ --name "Magic Room" --sim my-sim/1.0 \
      --port 7401 --token-file /run/user/1000/visit.token [--max-visitors 3] [--idle-exit 0]
```

| flag | env | default | meaning |
|---|---|---|---|
| `--lobby` | `VISIT_LOBBY` | none: no lobby | the lobby's `wss://` (or, for a local test, `ws://127.0.0.1:…`) URL. None: the office keeps to itself (4.2) |
| `--name` | `VISIT_NAME` | — (required) | the office's name: 1–24 letters, digits, space, `_ . -`, starting and ending with a letter or digit |
| `--sim` | `VISIT_SIM` | `unknown/0` | `name/version` of the sim, shown to others |
| `--port` | `VISIT_PORT` | 7401 | the port for the page |
| `--listen` | `VISIT_LISTEN` | `127.0.0.1` | the address it answers the page on: `127.0.0.1`, or, in a container, `0.0.0.0`, its container's own network, its port never published (../engine/SPEC.md 6.1). Nothing else (exit 2) |
| `--token` / `--token-file` | `VISIT_TOKEN` / `VISIT_TOKEN_FILE` | — (required) | the page token (section 3): the host's, and a page's without a role (../engine/SPEC.md 6.2). `--token-file` keeps it off the process list and SHOULD be preferred |
| `--user-token` / `--user-token-file` | `VISIT_USER_TOKEN` / `VISIT_USER_TOKEN_FILE` | none | the user token, for a room on the engine: what the workroom's server relays a person with (../engine/SPEC.md 6.2, 6.3). As the page token: at least 32 hex characters, new each run, and another one. None: no user connection is taken |
| `--max-visitors` | `VISIT_MAX_VISITORS` | 3 | 0–8; 0 takes no visitors |
| `--idle-exit` | `VISIT_IDLE_EXIT` | 0 | exit after this many seconds with no page connected (0: never) |
| `--origins` | `VISIT_ORIGINS` | none | the page origins allowed to connect (`http://localhost:3000`, comma-separated); none: any page with the token (section 3, point 4) |
| `--relays` | `VISIT_RELAYS` | the lobby's | the relays for links: `none`, `default` (iroh's own), or URLs, comma-separated |
| `--media-port` | `VISIT_MEDIA_PORT` | 0: any free one | the UDP port voice comes and goes on (8.7: WebRTC, to this office's page and the pages of the offices it links with). Give one to open it on a firewall, or forward it on a router. Not to be had: the office runs without voice |
| `--data-dir` | `VISIT_DATA_DIR` | `basedverse/<the name>` in the user's data folder (4.4) | where the workroom keeps everything (4.4): its database, its state file, friends' landscapes. A launcher SHOULD give one, so a renamed office keeps its data |
| `--state-file` | `VISIT_STATE_FILE` | `state.json` in the data directory | a file where it keeps what outlasts a run: whether the office is away (`leave_lobby`, 5.2), so a restart stays away; its key (section 2), so it is the same office to its guests' hosts; its door; whose plots its world shows (its picks, 3.3) and its layout (8.4); whose look its visitors have (8.6). Readable by its owner only (section 3, point 10) |
| `--cache-dir` | `VISIT_CACHE_DIR` | `landscapes/` in the data directory | where it keeps friends' landscapes (8.10), each by its office's key and hash, at most 256 MiB (the oldest go first), and the plots it keeps to draw while their offices are away |
| `--auth-token-file` | `AUTH_TOKEN_FILE` | none: no sign-in API | the sign-in API's service token (../auth/SPEC.md 3): given, `visit` serves the API |
| `--auth-port`, `--auth-listen`, `--auth-idle`, `--auth-max-age` | `AUTH_PORT`, `AUTH_LISTEN`, `AUTH_IDLE`, `AUTH_MAX_AGE` | 7403, `127.0.0.1`, `7d`, `30d` | the sign-in API's (../auth/SPEC.md 8) |
| `--engine-room-url` | `ENGINE_ROOM_URL` | none: no engine | the room's server page: given, `visit` hosts it in its engine (../engine/SPEC.md 5) |
| `--engine-port`, `--engine-tick-idle`, `--engine-memory-max`, `--engine-window`, `--engine-log`, `--engine-debug` | `ENGINE_PORT`, `ENGINE_TICK_IDLE`, `ENGINE_MEMORY_MAX`, `ENGINE_WINDOW`, `ENGINE_LOG`, `ENGINE_DEBUG` | 7405, 10, 1024, `1280x720`, `info`, off | the engine's (../engine/SPEC.md 5.2); `ENGINE_DEBUG`, a test aid never left on in a workroom, takes `1`, `yes`, `true` (on) or `0`, `no`, `false` (off) |

A flag wins over its environment variable, and a variable set but blank counts as unset (its default), as a
`.env` file has it. [`../.env.example`](../.env.example) is the template of them all. There is no setting for a
database: the one inside `visit` is the database (4.4).

On start it prints **one JSON line** to stdout once it is ready (its database up and its page's port taken; when
asked for, the sign-in API's port taken, and the engine's port taken and its Chrome unpacked), then logs to stderr
(`RUST_LOG`, in tracing's filter syntax, replaces its default, `visit=info,auth=info,engine=<ENGINE_LOG>,iroh=warn`):

```json
{"ready": true, "port": 7401, "version": "2.0.0", "proto": "basedverse-visit-local/2"}
```

Exit codes: 0 asked to stop (4.2), or idle-exit; 2 bad flags (it says which); 3 the page's port is taken; 4 the
lobby refused it for good (`proto_unsupported`, `name_invalid`); 5 the workroom could not start, and it says why:
another `visit` runs it already (4.2), its database (Postgres did not start, its log named; or could not be made
ready), the sign-in API (its token file, its port) or the engine (its port, `ENGINE_ROOM_URL`, its Chrome).

There is no migrate step: `visit` brings its database up to date itself, at every start (4.4). The commands beside
a run (`visit auth …`, `visit import`, `visit psql`, `visit status`) are in 4.5.

### 4.2 Lifecycle

- **One per workroom.** A workroom is its data directory (4.4): a running `visit` holds its `visit.lock`, and a
  second `visit` for the same directory is refused (exit 5). Two workrooms on one machine have two data
  directories (two names, or two `--data-dir`), and two of each port.
- **Starting.** Postgres first (made at the first start, brought up to date at every one: 4.4), then the page's
  port, the lobby, the sign-in API and the engine (each when asked for), then the ready line (4.1). A first start
  takes longer: it unpacks what is inside `visit` and makes the database.
- **Without a lobby** (no `--lobby`), the office keeps to itself: it joins no lobby, so nobody visits, links or
  knocks, and it knocks on nobody; its pages, its sign-in and its engine run all the same. A room can have its
  sign-in, or its engine, before it has a lobby.
- **Hosting.** The office is in the lobby to be visited, and can knock, only while a page hosts its scene
  (`host`, 5.2): the visit-client publishes its link to the lobby then (6.1), and withdraws it when the page
  goes; visits under way carry on.
- **A page reload** closes the localhost connection only. The lobby session, the office's knocks and every
  visit and link carry on. While a host's page is gone its visitors are told `host_paused` (they go on
  drawing its scene as it was); when a page connects again it gets `ready` with every active visit, hosts
  again, and sends each visitor its whole scene.
- **One page at a time.** A new authenticated page connection takes over from the old one, which is
  closed with code **4409** (`replaced`). This is what a reload looks like from the visit-client.
- **A page the browser keeps as it goes** (its back/forward cache: Chrome's since 148 keeps one with a socket
  open) is frozen, its socket open but silent: it says `bye` (5.2) as it is hidden, and is gone as a closed one
  is, closed **1001**. Shown again, it gets that close, and connects again.
- **The lobby connection** is given up when the lobby has been silent for a minute (it pings every 20 s:
  silence means a connection that died without a word, after a laptop's sleep, say). It reconnects by
  itself (1 s, 2 s, 4 s, … up to 30 s) and resumes its session
  (section 6.1): within 30 s the office comes back with the same id, knocks and visits, and visits whose
  links stayed up never notice. Later than that, the lobby has ended the session and its visits, and the
  visit-client starts a new one (a new office id) and ends those visits' links.
- **Away.** The page can take the office out of the lobby on purpose (`leave_lobby`) while the visit-client
  goes on running: its visits end (`"gone"`), its knocks go, it says `bye` (no resume window) and stays out,
  reconnecting to nothing, until the page sends `join_lobby`, which starts a new session (a new office
  id). Its state file keeps it away across restarts too: a visit-client started away joins nothing.
- **Stopping** (SIGINT or SIGTERM; on Windows, Ctrl-C or its console closing) takes the office out of the lobby,
  which ends all its visits; then the engine stops, its browser with it, and Postgres last (a fast shutdown:
  nothing kept is lost). A `visit` killed outright (SIGKILL; on Windows, a launcher's kill) leaves its Postgres
  running: the next `visit`, or command, for the workroom stops it first.
- **`--idle-exit`** is for sims whose launcher starts `visit` again whenever the page loads. It
  counts from the last page disconnect.

### 4.3 HTTP on 127.0.0.1

| request | auth | answer |
|---|---|---|
| `GET /visit.js` | none | the library (JavaScript), the version built into this binary. `Access-Control-Allow-Origin: *` |
| `GET /health` | none | `{"ok": true, "version": "0.7.0"}` |
| `GET /ws` (WebSocket upgrade) | `hello.token` (5.1) | the page connection |
| anything else | — | 404 |

The sign-in API (../auth/SPEC.md 3) answers on a port of its own (`AUTH_PORT`, at `AUTH_LISTEN`), and the engine's
`/health` (../engine/SPEC.md 5.1) on `ENGINE_PORT`, at `--listen`'s address: each only when asked for (4.1).
Postgres answers on 127.0.0.1 alone, to `visit` and its commands (4.4).

### 4.4 What it keeps, and what it carries

Everything a workroom keeps is in its **data directory** (`VISIT_DATA_DIR`):

```
<VISIT_DATA_DIR>/
  visit.lock            held by the running visit (a second one is refused; a command uses its Postgres)
  state.json            the state file (VISIT_STATE_FILE)
  landscapes/           friends' landscapes (VISIT_CACHE_DIR)
  postgres/data/        the database (Postgres' own log: postgres/data/start.log)
  postgres/secrets      its roles' passwords (the superuser, visit, auth), readable by the user only
  postgres/pwfile       the superuser's, for initdb; readable by the user only
  engine/profile/       the engine's browser profile, made new each start
```

Without `VISIT_DATA_DIR` it is `basedverse/<the office's name>` in the user's data folder (`$XDG_DATA_HOME` or
`~/.local/share` on Linux, `~/Library/Application Support` on macOS, `%LOCALAPPDATA%` on Windows), the name made a
folder's (letters and digits kept, in lower case, anything else a `-`: "Magic Room" is `magic-room`); a name with
no letter or digit needs `VISIT_DATA_DIR`. It holds the office's key and the database's passwords: a secret, as
the state file is (section 3, point 10), never in a repository, never copied elsewhere. A copy of it, taken while
its `visit` is stopped, is a backup of the workroom.

**What `visit` carries** (its Postgres, its Chrome) is unpacked once into `basedverse/runtime/` in the user's data
folder, a directory per version (`postgresql-18.6-1-<platform>/`, `chrome-156.0.8078.73-1-<platform>/`), shared by
every workroom of that user. Each is checked against the sha256 it was built with before it is unpacked, and
marked with it (`.visit-sha256`, written last): a directory without the mark is unpacked again. Two `visit`s
starting at once each unpack into a directory of their own, and the first to move it into place wins.

**The database** is that Postgres, run by `visit` as its child, its data in `postgres/data`:

- **The first start** makes it: `initdb` with the built-in `C.UTF-8` locale (the same on every platform) and
  SCRAM passwords, the superuser's (`postgres`) and the two roles' made at random and kept in `postgres/secrets`.
- **Every start**: `pg_hba.conf` written (the roles, with their passwords, from 127.0.0.1 only); Postgres on
  127.0.0.1, on a port free at that moment, with no Unix socket. Then, as the superuser, the database `visit` and
  its two roles made right: `visit` (the visit-client's) and `auth` (the sign-in's, ../auth/SPEC.md 5), each a
  plain role owning a schema of its name, reading nobody else's. Then each schema brought up to date as its own
  role: each migration this `visit` knows that the database lacks, in order, once, each in a transaction of its
  own (recorded in `visit.visit_migrations` and `auth.migrations`). The migrations are built into the binary:
  whoever has a `visit` has its migrations. A Postgres left running by a `visit` that was killed is stopped first.
- **Stopping**: last, after everything else (4.2).
- **Its user.** Postgres won't run as root, and `initdb` asks the system for the user's name: `visit` runs as a
  user the machine knows.
- **In a container** (`--listen 0.0.0.0`), so: a user the container's `/etc/passwd` names, not root; a home it can
  write (the user's data folder, where what `visit` carries is unpacked); `VISIT_DATA_DIR` on a volume; and the C
  library Postgres and Chrome need (glibc 2.28 or newer). Chrome's own sandbox does not run there: the engine starts
  it without (../engine/SPEC.md 5.3).
- **A newer Postgres.** A `visit` carrying a new minor version (18.6 to 18.7) unpacks it and runs the same data. A
  new major version (19) would need `pg_upgrade` from one to the other: no `visit` does that yet.

What the database holds is 8.12's, and the sign-in's (../auth/SPEC.md 5).

### 4.5 The commands

| command | does |
|---|---|
| `visit auth <command>` | the sign-in's accounts, sessions and keys, in a terminal (../auth/SPEC.md 2): `user add`, `user list`, `user remove`, `passwd`, `sessions`, `revoke`, `key add`, `key list`, `key revoke` |
| `visit import <URL>` | moves a 1.x workroom's database in, once (below) |
| `visit psql [<psql's arguments>]` | a `psql` on the workroom's database (`visit`), as its superuser; what follows goes to `psql` (`visit psql -c '\dn'`) |
| `visit status` | its version, what it carries, where the workroom's data is, where the runtime is, and whether the workroom's `visit` runs |
| `visit --version` | its version, and the Postgres and Chrome it carries: `visit 2.0.0 (Postgres 18.6, Chrome 156.0.8078.73)` |
| `visit --help`, `visit auth --help` | the flags; the sign-in's commands |

**Which workroom**: `VISIT_DATA_DIR`, else the default made from `VISIT_NAME` (4.4), as a run finds it, so a command
run with the workroom's `.env` loaded finds its workroom; or `--data-dir` (or `--name`) after the command (for
`visit auth`, right after `auth`: `visit auth --data-dir <dir> user list`). Neither: exit 2.

**While the workroom's `visit` runs**, a command uses its Postgres (its port from `postmaster.pid`); otherwise the
command starts the workroom's Postgres for itself, made and brought up to date as a run does (4.4), does its work,
and stops it. A `visit` that is still starting: try again in a moment (exit 5).

**`visit import <the old database's URL, as its superuser>`** (`VISIT_DATABASE_ADMIN_URL` in a 1.x `.env`) copies
what is in its `visit` and `auth` schemas (chat, the mailbox's marks, pictures and voice messages, links, whispers
held, bans; accounts, sessions, API keys, sign-ins tried) into this workroom's database, with the `pg_dump` and
`pg_restore` `visit` carries, in one transaction; each schema's record of its migrations stays as this one has it.
Only before the workroom's `visit` runs (else exit 5: stop it first), and only into a workroom with no accounts,
chat or bans yet (else exit 1: import into a new data directory). It says what came, a count of each. The old
database must be reachable from where it runs; afterwards it can go.

| exit | `visit auth` | `visit import` | `visit psql` | `visit status` |
|---|---|---|---|---|
| 0 | done | done | once `psql` runs, its own code | always, given a workroom |
| 1 | it failed (said on stderr) | it failed (said: the workroom has data, the old database cannot be read) | | |
| 2 | bad flags, or no workroom | no workroom | no workroom | no workroom |
| 5 | the workroom's database could not be opened | its database could not start, or its `visit` runs | its database could not be opened | |

## 5. Page ↔ visit-client: `basedverse-visit-local/2`

One WebSocket, `ws://127.0.0.1:<port>/ws`, with Nagle's algorithm off at both ends (a visit-client sets
`TCP_NODELAY`, as browsers do: without it a small message waits for the last one's acknowledgement, some
20 ms). Text frames are JSON objects with a string `type`; binary frames carry a scene's deltas and (from
the host's page) its edits (5.4). The library implements all of this; a sim never sees it unless it writes its own
library (for a native sim, say).

### 5.1 Handshake

The page sends, within 5 s:

```json
{"type": "hello", "proto": "basedverse-visit-local/2", "token": "…", "sim": {"name": "my-sim", "version": "1.0"}}
```

The visit-client answers `ready` (or closes: 4401 bad token, 4400 bad `proto`):

```json
{"type": "ready", "version": "0.7.0", "office": {"id": "o1a2b3c4", "name": "Magic Room"},
 "online": true, "away": false, "status": "available", "max_visitors": 3,
 "key": "7f3a…", "door": "open", "picks": [{"key": "c41e…", "name": "Ada"}],
 "layout": [{"key": "c41e…", "x": 22, "z": 4, "turn": 0}], "seen": [],
 "visits": [{"id": "v…", "channel": 1, "role": "host", "peer": {"id": "o…", "name": "Ada", "sim": {…}, "key": "c41e…"}, "guest": true}]}
```

followed by a `lobby` snapshot and any pending `knock`s.

### 5.2 Page → visit-client

| type | fields | effect |
|---|---|---|
| `host` | `three` (the three.js revision, digits) | this page hosts the room's scene (`visit.host`): the office is now in the lobby to be visited, and may knock (4.2); sent again after each page load |
| `status` | `status`: `"available"` \| `"busy"` | set the office's status (busy: no new knocks, visits carry on) |
| `bye` | | this page is going, kept by the browser (4): gone now, as if closed, and closed 1001 |
| `max_visitors` | `n` (0–8) | change how many visitors the office takes; lower than the current count ends no visit |
| `rename` | `name` | ask the lobby for another name; taken (`name_taken`) or invalid, the office keeps its own |
| `leave_lobby` | — | take the office out of the lobby, and keep it out (away, 4.2): nobody sees it or can knock; its visits end |
| `join_lobby` | — | back into the lobby after `leave_lobby`: a new session |
| `door` | `open` (bool) | open the door (its guests, its worlds' members, come in without knocking and may put its room in their worlds) or close it (everyone knocks; their watches of its plot end). Kept in the state file; `picks` back. (3.0's `public` is gone: a public world, 6.6) |
| `about` | `about` {`text` (≤ 200), `tags` (≤ 5, each 1–20 lower-case letters, digits and `-`)} \| null | this office's line in the lobby's list of who is here (6.6), and on its public page; null: none. Kept in the state file, told to the lobby (and in each new `hello`). `about` back |
| `group_create`, `group_join`, `group_leave`, `group_remove`, `group_unban`, `group_set`, `group_owner`, `group_delete`, `groups` | as 6.2's | worlds (6.6), passed to the lobby (the user's: ../../engine/SPEC.md 3.3); what comes back, as 5.3 has it |
| `browser_code`, `browser_allow`, `browsers`, `browser_revoke` | as 6.2's | linked browsers (3.1, 6.11), passed to the lobby (the user's); `browser_pending`, `browser_confirm` (3.2) and `browsers` back |
| `admin` | as 6.2's | the lobby's operators' (6.7) |
| `lobby_pass` | `req`, `qr`? (bool) | a web pass for the lobby's page (3.1, 6.11), the person's to ask: `lobby_pass` back with `req`, `url` (the lobby's page, the pass in its fragment), `expires`, and with `qr`, `qr` (that URL as a QR code: an SVG); `offline` when not in the lobby, `no_lobby` without one |
| `pick` | `key`, `name`? (≤ 24) | 3.3, the user's: put that office's room in this office's world (8.4): an office's key, as the lobby lists it (6.3) or a visit's peer has it (5.3), and the name it is known by; again for a key already there renames it. At most 8 (`too_many`: take one out first); not this office's own key. Kept in the state file; `picks` back. (Before 3.3: `guest_add` and `guest_remove`, a guest list: gone) |
| `unpick` | `key` | out of this office's world: its watch of that plot ends, where it was placed is forgotten (`layout`), and its kept plot let go (`plot_forgotten`) |
| `world` | `on` (bool) | on: this office watches the plots its user picked, of those online whose lobby entry says `world` and whose visit-clients let it (it is their guest, their door open) (8.4), by itself, and keeps doing so (a declined or refused watch is tried again after 20 s); off: its watches end. No page for a minute: off |
| `enter` | `channel` (a watch's), `pose` {`x`, `y`, `z`, `yaw`, `air`} (in the watched room's terms; `air`: how far above its floor, flying, 8.2) | step into the room watched (8.4): out of any other room first (a visit there ends); refused by its host when full (`enter_refused`) |
| `exit` | `channel` | step out of it again: the watch goes on |
| `enter` / `exit` with `teleport: true` | `channel` | 5.1 (8.15), the user's: into that room by teleport (no `pose`: its page puts them on its pad, once it says yes), out of any other by teleport; out of it by teleport (a page that walks its user home itself). A watch begun to teleport is let go 2 s after its user leaves that room, unless they picked it |
| `teleport_ask` | | 5.1, the user's: which rooms would take this office's user by teleport: its own (its host page asked, about its owner) and each office in the lobby whose entry says `teleport`, ready (at most 48), each asked once through the lobby. `teleport_asked` back, then a `teleport_answer` for each. One round every 1.5 s (`rate_limited`) |
| `teleport` | `key` | 5.1, the user's: knock to teleport into that office's room, one this office's world does not watch (`teleport_failed` otherwise: `home`, `watching`, `no_world`, `offline`, `not_supported`, `not_found`): let in, a watch with `teleport` (`visit_started`), stepped into by the page as its scene comes |
| `teleport_answer` | `ask`, `ok` | 5.1, the host's: its answer to a `teleport_ask` on it (none within 3 s: no) |
| `place` / `unplace` | `key`, `x`, `z` (within 100 km), `turn` (0–3) / `key` | put a friend's plot in this office's world: its middle there, turned so many quarter turns (8.4); kept in the state file. At most 256; not this office's own key (4.3.0: its room is its world's middle; a state file from before that has it, or a pick of it, drops it as it is read) |
| `seen` | `key`, `at` {`dx`, `dz` (m, within 100 km), `turn` (0–3), `size` ([w, d] m: this plot's)} \| null | where this office's world has that office's plot (8.4): its middle `dx`, `dz` metres from this plot's middle, turned `turn` quarter turns left; null, not in it. Told to that office over any link with it, and again over each new one (kept in memory, not in the state file). At most 256 |
| `plot_keep` | `key`, `name`, `plot` {`center`, `size`}, `unit` | a picked plot in this office's world, to keep while its office is away (8.10): drawn from its last landscape. At most 64 |
| `plot_load` | `key` | that kept plot's landscape, please: sent as `landscape` on channel 0 (`not_found` if none is kept) |
| `plot_forget` | `key` | let that kept plot and its landscape go |
| `avatar_clear` | — | this office has no avatar (8.6) from now on (its own goes as a binary frame, 5.4) |
| `avatar_want` | `channel` | ask a visitor for its avatar (host side, 8.6): its visit-client sends it, now and whenever its page gives a new one |
| `possessions` | `list`: [{`id`, `name`, `mount`, `fly`, `speed`, `lift`}] | what this office's user owns (8.11), as its room makes them: at most 8, ids 1 to 40 letters, digits, `_` `.` `-`, each once; `speed` 0.25 to 5 (times walking pace), `lift` 0 to 5 m. Every host this office visits is told (now, and at each link). Each model goes as a binary frame (5.4, bit 6, its slot in this list as its channel) |
| `possession_want` | `channel`, `id` | ask a visitor for one of its possessions' models (host side, 8.11): its visit-client sends it, now and whenever its page gives a new one |
| `avatars` | `default` (`"mine"` \| `"theirs"`) | whose look visitors have in this room by default: this room's models, or their own avatars (`"theirs"` until this office's user chooses; the state file says it was chosen: `set`); kept in the state file |
| `avatar_use` | `key`, `use` (`"mine"` \| `"theirs"` \| null: the default) | the same for one office, apart from the default; kept in the state file |
| `phone` | `on` (bool) | this room has a phone (8.8), or no longer: every link is told |
| `call` | `channel`, `act` (`"ring"` \| `"answer"` \| `"hangup"`) | a call on that visit (8.8): ring their phone (refused `no_phone` if their room has none), answer it (3.4.3: only a call they rang and nobody has answered; else `no_call`), end it. On a voice visit (4.0, 8.7), `hangup` ends it; ringing or answering it is refused (`bad_message`: it is a call already) |
| `perform` | `channel`, `act` {`clip`, `loop`?, `secs`?} \| null, `hold` (bool) | 4.0 (#59, the room's page): what this room has the visitor on that channel do (`clip`: one of `sit`, `lie`, `drink`, `eat`, `throw`, `swat`, `wave`; `secs` at most 600) and whether it holds something of the room's, told to their visit-client (8.6). At most 10 a second a visitor (`rate_limited`); `no_such_visit` for nobody in this room |
| `huddle` | `group`, `on` (bool) | 4.0: a huddle in one of this office's worlds (8.7), started (its user into their own room's voice, which carries it) or over (every voice visit that joined it ends; its user out of that voice). Refused `not_supported` when the lobby carries no voice |
| `user` | `name` (1–24 characters) \| null | the name this office's user goes by (8.9; null: this office's): told to every office linked with it, now and as each links |
| `say` | `text` (1 to 200 characters; may be empty with pictures or links), `to`? (none or `"here"`; `{"whisper"}`; `{"key"}`; `"room"`; `"lobby"`; `{"group"}`), `media`? (pictures shared, at most 4: [{`id`, `mime`, `size`}]; among them at most one voice message, with `secs`), `links`? (at most 2: [{`url`, `title`, `description`, `site`, `image`}]) | a line this office's user says (8.9). No `to`: to everyone who can see them (the room they are in, and those who see it from outside). `{"whisper": "<office id>"}`: to that office's user alone, through the lobby (its errors, `ref: "whisper"`: `not_found`, `not_supported`). `{"whisper": "<key>", "name"?}` (0.22.0): to whoever has that office in the lobby, if it takes whispers; else kept for them (`held`), text only (`no_link` with pictures or links; `too_many` past 20 for one person or 100 in all), and passed on once they and this office are both in the lobby. `{"key"}`: into the room of an office linked with this one, either way (`no_link` if none). `"lobby"`: to everyone in the lobby (`not_supported` if the lobby does not carry chat). `{"group": "<name>"}` (3.0.0): to everyone in one of this office's worlds (6.6), text only (`not_found`: in no world of that name). Control characters become spaces; trimmed; at most 5 every 10 s, whatever their `to` (`rate_limited`) |
| `say_as` | `id` (1 to 40 letters, digits, `_` `.` `-`), `name` (what it goes by, 1 to 24 characters), `text` (1 to 200 characters) | a line one of this office's room's own figures says (8.9: a receptionist, a guide; never a person): in this office's room, to those in it within 45 m of the figure (`at`? (4.0.1): where it is, [x, y, z] in metres as `voice_where` has everyone; none: everyone in the room), and in its chat (`far` when its user is not near), as `said` with `npc`; never to a world member watching the plot from outside it. Never as this office's user: control characters become spaces; trimmed; at most 5 every 10 s from each figure, apart from the user's own (`rate_limited`); a bad `id` or empty `name` or `text`: `bad_message`. `to`? (3.3, #48): to one person alone, a whisper from the figure (no bubble, nobody else): `"owner"` (this office's user) or the id of a visit in its room (`not_found` otherwise) |
| `act` | `figure`, `action` | 3.3 (#48), the user's: one of the actions of a figure in the room its user is in (from their target's card): to that room's page (`act`), which runs it as a point at the figure would be, by that type's own rules (8.9, "Targets") |
| `speaker` | `channel`, `on` (bool) | a call on this office's phone's speaker (8.8), or back to the ear (`no_call` if none is on there) |
| `rtc_offer` | `to`: null \| a channel, `sdp` (at most 32 KiB) | 4.2, the person's (8.7): the page's WebRTC offer, for its own office (null), or for the one at the other end of the link on that channel (passed on over it). Answered with `rtc_answer` (or `rtc_failed`); an earlier connection for the same one goes |
| `rtc_close` | `to`: null \| a channel | 4.2, the person's (8.7): that connection closed (no longer wanted) |
| `mic` | `on` (bool) | 4.2, the person's (8.7): their microphone on or off. Off, their voice is not heard in person (a call, or a huddle, runs the microphone for those alone) |
| `voice_where` | `me` [x, y, z] \| null, `phone` [x, y, z] \| null, `visitors` [[`channel`, x, y, z], …], `world` [[`key`, x, y, z], …] (metres, this room's terms) | where everyone in this office's room is (8.7): its own user (null: not in it), its phone, each visitor in it; and where its world has each friend at home in their own room, a plot in it (by their key; Visit 0.20). Who hears whom in the room, and across its plot's sides, goes by it. Sent when it changes, at most 10 a second |
| `media_get` | `id`, `channel`? | a picture or a voice message (8.12) this page would show or play: sent as a binary message (5.4, bit 7) once this office has it (it asks the link the line came by, if it has not), or `media_none`. `channel` (3.4): a room's sound, from the room on that channel (8.13): asked of its host if this office does not have it |
| `asset_want` | `id` (64 hex), `channel` (of the visit or watch whose scene named it), `at` (from where, bytes; 0 first) | 4.1: a piece of an asset (8.14): from what this visit-client keeps, else (`at` 0) asked of that room; its pieces come as binary frames (5.4, `ASSET`), one per ask; `asset_none` if not to be had |
| `sound_at` | `id`, `at` [x, y, z], `volume`?, `ref`?, `far`? | 3.4, the room's page (host): one of its sounds (8.13), once, from that point (its units): to everyone in its room; at most 10 a second (`rate_limited`) |
| `preview` | `url` (http or https), `req` | a link's preview (8.12), made by this visit-client (it fetches the page once): `preview` back, with `req` |
| `history` | `before`? (ms since 1970; none: now), `limit`? (1–200, 50), `req` | the chat before then (8.12), from the database: `history` back, newest last |
| `shared` | `conversation` (`room:<key>`, `person:<key>`, `lobby`, `group:<name in lower case>`), `kind` (`media`, `voice` or `links`), `before`?, `limit`? (1–200, 60), `req` | the pictures, the voice messages or the links shared in that conversation (8.12), newest first: `shared` back |
| `attention` | `on` | whether someone is looking at this page's chat now (8.9, 0.22.0): a line meant for its user that comes meanwhile is seen as it comes; else it is in the mailbox. Off when the page connects |
| `read` | `conversation`? (`room:<key>`, `person:<key>`, `lobby`, `group:<name>`; none: every one) | this office's user has read what the mailbox (8.9) has of it: `unseen` back |
| `unhold` | `id` | a whisper kept for someone away (`held`), taken back before it went |
| `voice_join` | `key` \| null | into a room voice (8.7): a linked office's room's (its key; `no_link` if none, `banned` if it keeps this office's user out of its voice), this office's own (its own key), or none (null). One at a time |
| `voice_kick` | `key` | one out of this office's room's voice, now (8.7): they may join again |
| `ignore` / `unignore` / `ignore_clear` | `key`, `npc`? (a room's figure's id), `name`? (≤ 24) / `key`, `npc`? / — | 3.3, the user's: whom they ignore in chat (8.9): a person by their office's key (not this office's own), or one of a room's figures by that room's key and its id. At most 50 (`too_many`). Kept in the state file; `ignored` back |
| `ban` | `key`, `name`?, `voice`, `chat` (bools) | keep that office's user out of this office's room's voice, its chat, both, or neither (8.9). Kept in its database (8.12) until lifted (both false); the other office told |
| `knock` | `to` (office id), `voice`: true (to talk, 8.7), `huddle`? (a world: to join the huddle `to` started there), `message`? (≤ 280) | ask to talk with an office's user (refused `not_hosting` before `host`; `not_supported` when the lobby carries no voice). 4.0 (#57): a knock to walk in, or to link (no `voice`), is refused, `not_supported`: worlds are the way in |
| `knock_cancel` | `knock` | 4.0: a knock of this office's withdrawn (a call given up): its door hears `knock_cancelled` (`"withdrawn"`) |
| `answer` | `knock`, `accept` (bool), `message`? | answer a knock to talk on you (4.0: the only kind; `not_found` for any other id) |
| `end` | `visit` | end a visit: yours as a visitor (leave), or a visitor's to you (kick) |
| `input` | `forward`, `strafe`, `run`, `yaw`, `pitch`, `t` (8.2) | the visitor's input, whole: whenever it changes (at most every 8 ms) and every 33 ms regardless, while visiting |
| `point` | `yaw`, `pitch`, `wheel`? (8.3) | the visitor clicked at the middle of its view, looking there, or turned the wheel there (`wheel`: whole notches, positive down, at most 10 either way): at most one every 200 ms, while visiting |
| `keyframe` | `channel` | the visitor's page asks for a key delta (8.1): where everything that moves is |
| `scene_resync` | `channel` | the visitor's page asks for the host's whole scene again (an edit was lost, or did not apply) |
| `pose` | `channel`, `state` (≤ 2 KiB) | the host page's state for a visitor (where it stands), about 5 a second; kept by the visit-client and handed back in `ready` to a page that reloads |
| binary | section 5.4 | a scene delta or a scene edit (host) |

### 5.3 Visit-client → page

| type | fields | when |
|---|---|---|
| `ready` | 5.1 | after `hello` |
| `picks` | `door` (`"open"` \| `"closed"`), `picks`: [{`key`, `name`}] | 3.3: after `door`, `pick` or `unpick` (before 3.3: `guests`) |
| `signed_in` | `signed_in` (bool) | someone signed in on this workroom, or nobody any more (3, point 13): in the lobby only while someone is. `ready` has `signed_in` |
| `about` | `about` \| null | after `about`. `ready` has `about` |
| `group` | `name`, `about`, `open`, `locked`, `owner` (a key), `members` [{`key`, `name`, `since`}], `bans`? [keys] (its owner's only) | one of this office's worlds, whole (6.6): after joining the lobby, and as it changes. `ready` has `groups` [the same] |
| `browser_confirm` | `code`, `key` (the browser's), `confirm` (6 digits), `url` (the lobby's page, `#verify=` them), `qr` (that URL as a QR code: an SVG), `expires_in` (s) | 3.2: after `browser_allow`, what the browser must show to be linked (6.11): show the QR code (as an image) for its camera, and the digits |
| `group_left` / `groups` / `lobby_bans` / `browser_pending` / `browsers` | as 6.3's | the lobby's, passed on: these, `knock_sent`, `answered`, `error`, and what 5.3 says of the lobby's chat, knocks and visits; nothing else a lobby says reaches a page |
| `lobby_pass` | `req`, `url`, `expires`, `qr` | the web pass asked for (3.1, 6.11) |
| `layout` | `layout`: [{`key`, `x`, `z`, `turn`}] | after a change |
| `seen` | `key`, `name`, `at` (as the page message `seen`'s, their plot's `size`) \| null | where a friend's world has this office's plot (8.4), as they said; null: not in it, or their last link gone. `ready` has every one known: `seen`: [{`key`, `name`, `at`}] |
| `landscape` | `channel`, `key`, `json` | an office's landscape (8.10), as JSON: the room's on that visit's `channel` (kept here, or asked for and sent once), or a kept plot's (channel 0). Also to a page that comes back, for each visit it is on. `ready` has the kept plots: `kept`: [{`key`, `name`, `plot`, `unit`}] |
| `landscapes` | `channel` | this visitor reads landscapes (8.10): its whole room goes without its landscape (also `landscapes` in `visit_started`). Host side |
| `assets` | `channel` | 4.1: this visitor reads a scene's assets (8.14): the room's big pictures and meshes go to it by name (also `assets` in `visit_started`). Host side |
| `asset_want` | `id` | 4.1: give this asset of this room's again (8.14: a visitor asked for it, and this visit-client no longer keeps it). Host side |
| `asset_kept` | `id`, `size` | 4.1: an asset this room's page gave, kept, by its name (8.14). Host side |
| `asset_none` | `id` | 4.1: an asset asked for (`asset_want`) that is not to be had (8.14) |
| `plot_forgotten` | `key` | a kept plot let go: its host ended this office's visit on purpose (`kicked`: its door shut, a guest no longer), declined its watch, or this office took it out of its world (`unpick`) |
| `avatars` | `default`, `by`: [{`key`, `use`}] | whose look visitors have here (also in `ready`), after `avatars` or `avatar_use` |
| `avatar` | `channel`, `avatar` {`unit`, `json`} | a visitor's avatar, which this page asked for (host side, 8.6): unpacked and checked as a scene edit is |
| `possessions` | `channel`, `list` | what a visitor owns (host side, 8.11), as its visit-client said it, checked again; also in `visit_started` (`possessions`), for a page that reloads |
| `possession` | `channel`, `id`, `possession` {`unit`, `json`} | one of a visitor's possessions' models, which this page asked for (host side, 8.11): checked as an avatar is |
| `phone` | `channel`, `on` | the other office's room has a phone now (8.8), or no longer (also `phone` in `visit_started`) |
| `call` | `channel`, `act` | a call on that visit (8.8): they ring, answer, or hang up |
| `chat` | `channel`, `key`, `name`, `text`, and `id` (a visitor's, by its visit there), `npc` (one of the room's own figures, by its id) or `owner` (true: the room's owner); `target`? (3.3: { `kind`, `id` }, what a line said aloud is meant for); or `scope` | a line said (8.9): in this office's room by a visitor (that visit's channel, its `id`), or by one of its own figures (`npc`, no `channel`: `say_as`), or in a room its user is in or sees from outside (that visit's channel: its owner, one of its figures, or another visitor). `scope: "sim"`: said in a room's chat (`key`, `name`: its sayer): into this office's room from a linked office (`home: true`; `channel`, the link it came by), or in the chat of a room linked with this one (its owner's or said into it: `channel`, the link with that room's office). 3.4.2: `far` (said in this office's room out of its user's hearing: the room's, for its logic; shown nowhere) or `seam` (said by a friend at home, near its user across their plots' side; `owner`, `key` theirs). `scope: "whisper"`: whispered to this office's user (`id`, the sayer's office id; `key`, its key or null; or, 3.3, `npc`: one of a room's figures, `key` that room's, with `say_as`'s `to`). `scope: "lobby"`: said in the lobby (`id`, `key`, as a whisper's); this office's own too, in the lobby's order. `scope: "group"` (3.0.0): said in one of its worlds (`group`, its name; `id`, `key`, as a lobby line's; `mine` on this office's own, which comes back as the lobby's are). Never this office's own otherwise. `ready` and `online` say whether the lobby carries chat (`lobby_chat`) |
| `chat_log` | `lines` | the chat this office's user has had (8.9), for a page that connects (a reload): after `ready` and the lobby, if there is any. Each line as `chat` gave it, with `at` (ms since 1970) and `conversation`; this office's own too (`mine: true`, `scope` "say", "sim" or "whisper", `to` as the page's `chat` event has it). The last 100 (all of it is in its database, 8.12: `history`) |
| `sound` | `channel`, `id`, `at`, `volume`, `ref`, `far` | 3.4: a sound of the room on that channel (8.13), once, from that point in it (its units) |
| `media_ready` | `slot`, `id`, `mime`, `size` | a picture or a voice message the page shared, or (3.4) a room's sound (5.4, bit 7, on that slot), kept (8.12): its id. Neither: `error` (`bad_media`, `ref: "media_put"`, `slot`) |
| `unseen` | `count`, `conversations` [{`conversation`, `count`}], `lines` | the mailbox (8.9, 0.22.0): how many lines meant for this office's user came while nobody looked and are not read yet, in all and by conversation, and the newest 100 of them (each as `chat_log`'s). After `chat_log` when there are any, as it changes, and in answer to `read` |
| `held` | `held` [{`id`, `key`, `name`, `text`, `at`}] | the whispers this office's user left for someone away (8.9), still kept: after `ready` when there are any, and as they change |
| `held_sent` | `id`, `key`, `name`, `text`, `at` | one of them passed on, now that they are in the lobby (`held` follows) |
| `media_none` | `id` | a picture asked for (`media_get`) that this office does not have and will not get |
| `preview` | `req`, `url`, `title`, `description`, `site`, `image` ({`id`, `mime`, `size`} \| null); or `none: true` | a link's preview (8.12), as made here; its picture kept |
| `history` | `req`, `lines`, `more` | the chat asked for (8.12), newest last, each as `chat_log`'s; `more`: there is more before |
| `shared` | `req`, `conversation`, `kind`, `items`, `more` | what was shared there (8.12), newest first: a picture's or a voice message's {`id`, `mime`, `size`, `at`, `key`, `name`, `mine`}, a link's {`url`, `title`, `description`, `site`, `image`, `at`, `key`, `name`, `mine`} |
| `rtc_want` | `want`: [{`to`: null \| a channel, `room`, `call`, `voice` (bools)}], `ice`: [RTCIceServer] | 4.2, to the person's page (8.7): the offices to connect to (its own: null), each with the ways its user's voice is heard there (in person, a call, a room voice), and the STUN and TURN servers to connect by. When it changes, and to a page that connects |
| `rtc_answer` | `to`, `sdp` | 4.2 (8.7): the answer to its offer for that one |
| `rtc_failed` | `to`, `reason` | 4.2 (8.7): no answer to its offer (no link on that channel, the offer refused, no media port): tried again a while on |
| `voice_near` | `channel`?, `near`: [{`key`, `name`, `distance` (m), `id`?, `phone`?, `world`?}] | who is near this office's user (8.7), within `voice_range`: in its own room (no `channel`: as its visit-client has it), or in the room it is in (that visit's `channel`: as its owner's says). `id`: a visitor's, by its visit there; `phone`: a call on that room's speaker; `world`: a friend across a plot's side, near in both worlds (by this office's world's measure). When it changes; distances at most every second. `ready` has `voice_range` (45) |
| `room_voice` | `key` \| null, `name`, `members` [{`key`, `name`}], `out`? {`key`, `name`, `why`} | the room voice this office's user is in (8.7), and who is in it, when that changes; null: none (`out`: they were taken out, `why` "kicked", "banned" or "gone"). `ready` has `room_voice` (its `members` null until its room says again) |
| `voice_members` | `members` [{`key`, `name`}] | who is in this office's own room's voice (8.7), its own user first if they are. `ready` has `voice_members` |
| `user` | `key`, `name` | the name a linked office's user goes by (8.9), as they said (`user`), or their office's again; 4.3.0: this office's own user's too, by its own key, so its room's page names its owner. `ready` has `users` [{`key`, `name`}] (its own first) |
| `bans` | `bans` [{`key`, `name`, `voice`, `chat`}] | whom this office's user keeps out of their room's voice or chat (8.9), as it changes. `ready` has `bans` |
| `ignored` | `ignored` [{`key`, `npc`?, `name`}] | 3.3: whom this office's user ignores in chat (8.9), as it changes. `ready` has `ignored` |
| `banned` | `key`, `name`, `voice`, `chat` | a room that keeps this office's user out of its voice or chat (8.9), or no longer (both false). `ready` has `banned_by` |
| `enter` / `exit` | `channel`, `pose`? {`x`, `y`, `z`, `yaw`, `air`} (for `enter`) | a watcher stepped into this room, there (host side: spawn it, as a visitor), or out of it; no `pose`: a link going there, spawned where newcomers go. 4.3.2: to a user page too (engine/SPEC.md 3.4), which has them inside: heard in person, and in its panel's rows |
| `enter_refused` | `channel`, `reason` (`"full"`, or `"moved"`: a visit to a room began, and this office stepped out; 5.1: `"teleport"`, its room said no to a teleport) | stepping into a watched room was refused, or undone: outside it |
| `enter` / `exit` with `teleport: true` | `channel` | 5.1 (8.15): a watcher stepped in by teleport (no `pose`: the host page spawns it on its pad), its page having said yes; or went by teleport (its page plays it) |
| `teleport_ask` | `ask`, `from` {`id`, `name`, `key`, `sim`, `guest`, `owner`, `user`} | 5.1, to the host page: may they teleport into this room? (`guest`: in a world with this office; `owner`: its own user; `user`: the name their user goes by, if a link says it.) Answer with `teleport_answer` within 3 s |
| `teleport_asked` | `keys`, `lobby` | 5.1, to the user page: whom its `teleport_ask` waits on (this office's own key first, if its room is up), and whether this lobby carries teleports |
| `teleport_answer` | `key`, `name`, `ok`, `home`?, `reason`? | 5.1: one room's answer (`home`: this office's own); `reason` when nobody's page said it (`not_supported`, `not_ready`, `timeout`, `gone`, `rate_limited`) |
| `teleport_failed` | `key`, `reason`, `message`? | 5.1: a knock to teleport came to nothing (`declined`, `full`, `not_ready`, a lobby's refusal, or the user page's own `teleport` refused) |
| `online` / `offline` | `office`, `knocking`, `lobby_info` {`title`, `motd`, `admin`}, `lobby_voice` (4.0: the lobby carries voice knocks and huddles) / `away`? | the lobby session came up (after a drop: the same office if it resumed), with this office's own knocks still waiting ([{`id`, `to`, `link`?}]) and what the lobby's welcome said of itself (`ready` has `lobby_info` too) / went down: `away: true` when the page took it out (`leave_lobby`), `false` when it is on its way back (`join_lobby`), absent when the lobby is out of reach |
| `lobby` | `offices`: [{`id`, `name`, `status`, `sim`, `max_visitors`, `visitors`, `visiting`, `ready`, `key`, `watchers`, `world`, `links`, `whisper`, `voice`?, `about`}] | every change (6.3) |
| `knock` | `id`, `from` {`id`, `name`, `sim`, `key`}, `message`, `expires_in`, `voice`: true | someone wants to talk with you (4.0): never let in without the page, never from someone your user ignores or keeps out of their room's voice (declined, unseen). 4.0: no other knock reaches a page (one to walk in or to link, from an older lobby, is declined) |
| `perform` | `channel`, `act` {`clip`, `loop`, `secs`} \| null, `hold` | 4.0 (#59, #60): what the room this office's user is in (on that channel) has their own model do, and whether they hold something of its: for a page that draws their body to pose it the same. `visit_started` has it again (`perform`) for a page that reloads |
| `huddle` | `group`, `from` {`id`, `name`, `key`}, `on`, `since` (seconds), `mine` | 4.0: a huddle in one of this office's worlds, started (`on`; `since`: how long ago, when told again) or over; `mine`: this office's own. `ready` has those going on (`huddles`) |
| `knock_sent` / `knock_cancelled` / `answered` | as in section 6 (`voice`? on a knock to talk) | your knock's fate; a knock on you withdrawn |
| `visit_started` | `id`, `channel`, `role` (`"host"` \| `"visitor"`), `peer` {`id`, `name`, `sim`, `key`}, `guest`?, `watch`?, `inside`?, `shared_adds`?, `state`? | a visit began (its link is up). `peer.key` is the other office's key, as the link proved it; `guest: true`, a guest let in through the open door, without asking the page; `watch: true` (and `inside`), a watch (8.4: since 4.0, every visit to a room is one); `teleport: true` (5.1), a watch begun by a knock to teleport; `phone: true`, the other office's room has a phone (8.8); in `ready`, a host's visit carries the last `state` its page sent. 4.0: `voice: true` (and `huddle`, a world), a voice visit (8.7): nothing of a room, only its `id`, `channel`, `role` and `peer`; the person's page only |
| `visit_ended` | `id`, `channel`, `reason` (6.5), `voice`? | a visit ended |
| `host_paused` / `host_resumed` | `channel` | the host's page went away / came back (visitor side) |
| `input` | `channel`, `seq`, `forward`, `strafe`, `run`, `yaw`, `pitch`, `t` | a visitor's latest input (host side) |
| `point` | `channel`, `yaw`, `pitch`, `wheel`? | a visitor pointed (8.3), clamped, at most 5 a second each (host side); `wheel` only for a turn of the wheel |
| `act` | `channel` (a visitor's) or `owner` (true), `figure`, `action` | 3.3 (#48): a figure's action from someone's target card (host side), with the points' limit; the room runs it only if the figure lists it, by its type's own rules |
| `addressed` | `figure`, `from` {`id` (their visit here) \| `owner` (true), `key`, `name`}, `name`, `text`, `whisper` | 3.3 (#48), host side (the engine's room page too, whether or not its user's page is open): a line meant for one of this room's figures: said aloud with it as its `target` (everyone who can see the sayer had it as a line too), or whispered to it alone (`whisper`: nobody else has it). The room answers with `say_as`, aloud or `to` them alone; whom its figures answer is its own |
| `link_stats` | `channel`, `path` (`"direct"` \| `"relay"`), `rtt_ms` | the visit's link, about once a second, both sides: a relayed path is slower and rate-limited |
| `keyframe` | `channel` | send a key delta on this visit soon (host side) |
| `scene_resync` | `channel` | send this visitor the whole scene again (host side): it asked, or the link dropped edits it had not sent |
| `shared_adds` | `channel` | this visitor's page reads an `add` that names what it already has by uuid only (8.3; Visit 0.7 on), as `visit_started`'s `shared_adds: true` says when it is known by then (host side) |
| `scene` | `channel`, `edit` | an edit of the host's scene, unpacked and checked (8.1) (visitor side) |
| `scene_lost` | `channel` | an edit of the host's scene was dropped (malformed, too big, too many): ask for the whole scene (visitor side) |
| `error` | `code`, `message`, `ref`? | a refusal (section 11) |
| binary | section 5.4 | a scene delta (visitor) |

### 5.4 Binary frames

Both directions, one scene delta (or, from the host's page, one scene edit) per WebSocket binary message:

| bytes | field |
|---|---|
| 0–1 | `channel`, u16 big-endian |
| 2 | flags: bit 0 = a key delta (everything that has moved, 8.1); bit 1 = a scene edit (host page to visit-client only, alone); bit 2 = this office's avatar (8.6: page to visit-client only, alone, its channel 0); bits 3 and 4 = none (voice before 4.2: it goes over WebRTC now, 8.7); bit 5 = this room's landscape (8.10: page to visit-client only, alone, its channel 0; gzipped JSON, at most 16 MiB); bit 6 = one of this office's possessions' models (8.11: page to visit-client only, alone, its channel its slot in the `possessions` list; gzipped JSON, at most 4 MiB); bit 7 = a picture shared in chat, or (3.4) a room's sound (8.12, 8.13: from the page, one to keep, alone, its channel a slot the page picks, answered by `media_ready`; to it, one it asked for, its channel 0: who it is (a length byte, its id), its type (a length byte, its MIME type), then its bytes; never dropped, unlike a frame) |
| 3–10 | timestamp, u64 big-endian, microseconds: the host page's clock when it took the delta (unused for an edit). Voice to the page: byte 3 says how it came (8.7: 0 in person, 1 a call, 2 a room voice), the rest 0 |
| 11… | the delta (8.1), or the edit, gzipped JSON |

Bits 0 and 1 together (`ASSET`, 4.1, 8.14), alone, its channel 0 and timestamp unused: from the room's page, one of its
assets, its bytes (at most 16 MiB: a PNG, JPEG or WebP picture, or a gzipped mesh; `bad_asset` otherwise); to a page,
a piece of one it asked for: its name (64 bytes, hex), its whole length and where this piece starts (u32 big-endian
each), then the piece (at most 256 KiB). Never dropped, unlike a frame.

A delta larger than 1 MiB, an edit larger than 16 MiB, or an avatar larger than 4 MiB, is dropped. A visit-client sends deltas on the
link as they come (7.4) and edits on the visit's scene stream, and passes the visitor's page each delta as
it is and each edit unpacked, as `scene` (5.3).

## 6. The lobby: `basedverse-lobby/4`

The lobby is a program of its own, `lobby` (../lobby/, ../LOBBY.md), which anyone may run (../lobby/README.md); a
workroom joins the one its `VISIT_LOBBY` names, and only while someone is signed in on it (section 3, point 13). One
JSON object per WebSocket text frame, `wss://` (TLS by the proxy in front of it), on any path; the lobby pings
every 20 s and drops a connection that leaves a ping unanswered as long; a frame is at most 64 KiB. Clients talking
it are visit-clients, and (3.1) browsers their offices linked (6.11); its public page only reads (6.9).

Version 3 (Visit 0.5 to 2.x) is this section without the key's proof, worlds, `about`, `door`, linked browsers and `admin`;
its `hello` says what the office takes (`world`, `links`, `chat`, `whisper`: version 4 takes them all). A lobby run
with `--v3` speaks it too, as a lobby of its own whose offices see nobody of version 4: for carrying old workrooms
through an upgrade. Versions 1 and 2 are gone.

### 6.1 A session

1. Connect; send `hello` within 10 s, with the office's `key` (its iroh endpoint's public key, 64 lower-case hex).
2. The lobby answers `challenge` (`nonce`). The visit-client signs, with the key's secret half (Ed25519), the
   UTF-8 text

   ```
   basedverse-lobby/4 hello
   <the lobby, as dialled>
   <nonce>
   <key>
   ```

   where the lobby as dialled is its URL's host and port, in lower case, without a port of 80 or 443
   (`wss://basedverse.app/` is `basedverse.app`; `ws://127.0.0.1:8765` is `127.0.0.1:8765`), and sends `prove`
   (`signature`, 128 hex). The lobby checks it against the names it is reached by (`--hosts`): a signature made for
   another lobby is refused (`bad_proof`, and closed 4401), so no lobby can pass another's challenge on to a
   visit-client and come in as it. A lobby told no names takes version 4 only for this machine's own
   (`127.0.0.1`, `localhost`, `[::1]`, any port), as a test does: a Host header is the client's to say.
3. A banned key (6.7) is refused (`error` `banned`, closed 4403). Otherwise the lobby answers `welcome` (with the
   relays to use, and what it says of itself), then a `lobby` snapshot, then `group` for each of the office's
   worlds (6.6).
4. The visit-client starts its iroh endpoint with those relays and, once its page hosts its scene (4.2),
   sends `link` with its endpoint id (which must be the key it proved: `bad_message` otherwise) and its address.
   Until it has, its office shows `ready: false` and cannot be visited or knock. It withdraws it (`link` with
   `endpoint` and `addr` null) when no page hosts.
5. From then on: everything else of 6.2; and `link` again whenever its address changes (iroh reports this; a laptop
   changing network, say).

**Resuming.** `welcome` carries a `resume` secret. When the connection drops, the lobby keeps the session for
30 s: the office stays listed (as it was), knocks on it wait, its visits stay agreed. A visit-client that
reconnects in that time sends `hello` with the `resume` secret, proves its key again, and gets `welcome` with
`resumed: true`, the same office id, a new `resume` secret, the session's visits (`visits`, ids) and its own knocks
still waiting (`knocking`); then the `lobby` snapshot, the knocks on it still waiting, and `visit` again for each of
its visits (one agreed while it was away is news; a visit-client ignores the others), and its groups. It ends any
visit it has that `visits` does not list, and sends `link` again. A secret resumes only a session of the key that
proved itself. After 30 s the session ends for good: the office leaves the lobby, its knocks are answered and
withdrawn `gone`, its visits end `gone`.

**A key back.** A `hello` whose key has a session already (a visit-client restarted, quicker than its old
connection was seen to die), without its secret, ends that session first, for good (its connection closed 4000, its
visits `gone`), and starts a new one: a key is one office at a time.

**Names** are first come, first served, in each lobby, whatever their case; a key coming back finds its name if
nobody has taken it meanwhile. Who someone is, is their key.

### 6.2 Visit-client → lobby

| type | fields | effect |
|---|---|---|
| `hello` | `proto`: `"basedverse-lobby/4"`, `name`, `key`, `sim`?, `status`?, `max_visitors`? (default 3), `resume`?, `about`? (6.6), `voice`? (4.0, true: it takes knocks to talk and huddles), `teleport`? (5.1, true: it takes asks and knocks to teleport) | join, or resume a dropped session (6.1) |
| `hello` (a browser, 3.1) | `proto`, `browser` (its own key: Ed25519, 64 hex), `pass`? {`key`, `expires`, `nonce`, `signature`} (a web pass), `switch`? (true: the page's person agreed that the pass moves this browser from the office it is linked to), `verify`? (3.2: a room's confirmation, 6 digits: a tab its QR code opened), `forget`? (3.2.3, true: unlink this browser, its office in the lobby or not; closed 1000, `unlinked`) | a browser, to speak for the office it is linked to (6.11); `challenge` back, as for an office |
| `prove` | `signature` | the challenge signed (6.1) |
| `link` | `endpoint` (its key), `addr` (an object, ≤ 2 KiB as JSON, passed through untouched: how to reach it); or both null | publish or update how to reach this office; both null: withdraw it (not ready, until published again) |
| `status` | `status`: `"available"` \| `"busy"` | busy: no knocks to visit or link (watches go on) |
| `max_visitors` | `n` (0–8) | change it |
| `knock` | `to`, `message`?, `watch`? (true: to watch, 6.4), `link`? (true: a link, 6.4), `voice`? (4.0, true: to talk, 6.4), `huddle`? (with `voice`: a world whose huddle `to` started), `teleport`? (5.1, with `watch`: to teleport in, 6.4) | ask to visit, to watch, to link, to talk, or to teleport |
| `knock_cancel` | `knock` | 4.0: withdraw a knock of yours still waiting (`not_found` if none): its door hears `knock_cancelled`, `"withdrawn"` |
| `teleport_ask` | `to` (an office id) | 5.1 (8.15): may this office teleport into that one's room? To a door whose hello said `teleport`, ready, not this office: passed on as `teleport_ask` (its room's page answers); else `teleport_answered` with `ok: false` and why (`not_found`, `not_supported`, `not_ready`, `self`), and past 120 a minute `rate_limited`. Answered no (`gone`) if the door leaves first; nothing is kept but the ask, 5 s |
| `teleport_answer` | `ask`, `ok` | 5.1: the door's answer to a `teleport_ask` on it (`not_found`: none waits, or not on this door) |
| `answer` | `knock`, `accept`, `message`? | answer a knock on your door |
| `end` | `visit`, `reason`? (`"left"`, `"kicked"`, `"link_failed"`) | end a visit you are in, either side |
| `rename` | `name` | a new name, if it is free (`name_taken`, `name_invalid` otherwise; the office keeps its own) |
| `chat` | `text` | a line for everyone in the lobby (8.9), the sayer too. Control characters become spaces; trimmed; at most 200 characters; at most 5 every 10 s, with whispers and group lines (`rate_limited`); empty: `bad_message`. Nothing is kept |
| `whisper` | `to` (an office id), `text` | a line for one office's user (8.9), to it alone (`not_found`: no such office, or this one). Checked and counted as `chat` is. Nothing is kept |
| `about` | `about` {`text`, `tags`} \| null | this room's line in the lobby's list of rooms (6.6): text plain, at most 200 characters; at most 5 tags, each 1–20 lower-case letters, digits and `-` (`bad_message` otherwise); null: out of the list |
| `door` | `public` | 3.0's door open to anyone: taken, and nothing done (public worlds since 3.1) |
| `group_create` | `group` (a name), `about`?, `open: true` or `password` (4–64 characters) | a world of this office's own (6.6), public or with a password (neither: `bad_message`): it owns it, and is its only member |
| `group_join` | `group`, `password`? | into a world: at once if it is public; with its password if it has one (`password_required`, `wrong_password`); never if its owner keeps this key out (`banned`), or it is closed (`closed`) |
| `group_leave` | `group` | out of a world |
| `group_remove` | `group`, `key`, `ban`? (bool) | its owner takes a member out (never itself); `ban`: and keeps the key out (it need not be a member) |
| `group_unban` | `group`, `key` | its owner lets a key back in |
| `group_set` | `group`, `about`?, `password`? or `open: true` | its owner changes it: its line; a new password (its members stay); or public (its password gone) |
| `group_owner` | `group`, `key` | its owner hands it to another member |
| `group_delete` | `group` | its owner ends it |
| `group_say` | `group`, `text` | a line for everyone in one of this office's worlds, online, the sayer too (`not_found`: in no world of that name). Checked and counted as `chat` is. Nothing is kept |
| `group_huddle` | `group`, `on` | 4.0: a huddle in one of this office's worlds, started or over (6.4). One a world at a time (`huddle_on`, by its starter's name); only its starter ends it (`not_yours`); `not_supported` from an office whose hello did not say `voice` |
| `groups` | — | the lobby's worlds, please (`groups` back) |
| `browser_code` | `code` (8 letters and digits, any case, a dash or not) | 3.1, the office's own: the browser waiting with this code (`browser_pending` back; `not_found`) |
| `browser_allow` | `code` | 3.2: a confirmation for that browser to show (`browser_confirm` back; the browser hears `browser_confirming`); once it does, it is linked to this office, and in (`browsers` back) |
| `browsers` | — | this office's linked browsers, please (`browsers` back) |
| `browser_revoke` | `key` (a browser's) | unlink it: it is told (`unlinked`) and closed (4403) |
| `browser_forget` | — | a linked browser's own: unlink this browser (closed 1000, `unlinked`) |
| `browser_verify` | `confirm` (6 digits; spaces or a dash anywhere) | 3.2, a waiting browser's own: the confirmation its room shows, typed (`bad_confirm`: not one that waits for it) |
| `admin` | `action` (`"ban"`, `"unban"`, `"bans"`), `key`?, `minutes`? (none: for good), `text`? (why) | the lobby's operators (6.7); anyone else: `not_admin`. `lobby_bans` back |
| `bye` | — | leave the lobby now: the session ends at once, not held for resuming (a visit-client stopping) |

### 6.3 Lobby → visit-client

| type | fields | when |
|---|---|---|
| `challenge` | `nonce` (32 hex) | after `hello`: sign it (6.1) |
| `web_welcome` | `proto`, `id`, `name`, `key` (the office's), `limits`, `admin`?, `title`?, `motd`? | to a linked browser, its key proved (6.11) |
| `browser_code` | `code` (8), `pictures` (3 emoji), `expires_in` (s) | to a browser not linked: the code its office types in its room (6.11) |
| `browser_confirming` | `office` (its name), `expires_in` (s) | 3.2, to a waiting browser: that office would link it, once it shows its room's confirmation |
| `browser_confirmed` | `office`, `agent`, `pictures` | 3.2.2, to a device that scanned a room's QR code for another browser: that browser is linked now; this one is not (closed 1000) |
| `welcome` | `proto`, `id`, `name`, `key`, `relays`: [url], `resume` (32 hex), `resumed` (bool), `visits`: [id], `knocking`: [{`id`, `to`}], `limits` (6.10), `chat`: true, `admin`? (true: this key is one of the lobby's operators), `title`?, `motd`?, `voice`? (4.0, true: this lobby carries knocks to talk and huddles; an older one would pass a knock to talk on as a knock to visit, so a visit-client sends none to it), `ice`? (4.2, #71: its STUN and TURN server, for voice over WebRTC, 8.7: {`host`? (none: the lobby's own, as the visit-client reached it), `port` (UDP), `username`?, `credential`?: this office's own TURN login, good while it is in the lobby and ten minutes after; none: STUN only}) | after a good `hello` and `prove` |
| `lobby` | `offices`: [{`id`, `name`, `status`, `sim`, `max_visitors`, `visitors` (count), `visiting` (version 4: always `null`, nobody's whereabouts being everyone's; version 3: the host's office id, or `null`), `ready`, `key`, `watchers` (count), `world`, `links`, `whisper` (true), `voice`? (4.0: its hello said `voice`), `teleport`? (5.1: its hello said `teleport`), `about` ({`text`, `tags`} or null)}], you included | on any change (3.4.3: at most every 250 ms, what changed meanwhile together; a `status`, `max_visitors` or `about` that changes nothing, to the office that sent it alone) |
| `knock` | `id`, `from` {`id`, `name`, `sim`, `key`}, `message`, `expires_in`, `watch`?, `link`?, `voice`?, `huddle`?, `teleport`? | someone knocked (to watch, with `watch`; to link, with `link`; to talk, with `voice`, and to join your huddle in a world, with `huddle`) |
| `knock_sent` | `id`, `to`, `watch`?, `link`?, `voice`?, `teleport`? | your knock reached the door (a knock to link with a door that does not take links is an ordinary knock: no `link`) |
| `knock_cancelled` | `id`, `reason`: `"timeout"` \| `"gone"` \| `"withdrawn"` (4.0) | a knock on you was withdrawn |
| `answered` | `knock`, `accept`, `reason`? (`"declined"`, `"timeout"`, `"gone"`, `"full"`, `"not_ready"`), `message`, `watch`?, `link`?, `voice`?, `teleport`? | your knock's answer |
| `visit` | `id`, `role`, `peer` {`id`, `name`, `sim`}, `ticket` (32 hex), `peer_link` {`endpoint`, `addr`}, `watch`?, `link`?, `voice`?, `huddle`?, `teleport`? (5.1) | a visit (or a watch, or a link: a watch with `link`; or, 4.0, a voice visit, with `voice`, and the world whose huddle it joins) was agreed: the visitor dials, the host waits for it (section 7) |
| `visit_ended` | `id`, `reason` (6.5) | a visit ended |
| `lobby_chat` | `from` {`id`, `name`, `key`}, `text` | a line said in the lobby (8.9) |
| `whisper` | `from` {`id`, `name`, `key`}, `text` | a line whispered to this office's user (8.9) |
| `group` | `name`, `about`, `open` (public), `locked` (a password), `owner`, `ops` (always `[]`: 3.0's), `members` [{`key`, `name`, `since`}], `bans`? [keys] | one of this office's worlds, whole (6.6): after its welcome, and whenever it changes (its bans to its owner alone) |
| `group_left` | `group`, `reason` (`"left"`, `"removed"`, `"banned"`, `"deleted"`) | no longer in a world |
| `group_chat` | `group`, `from` {`id`, `name`, `key`}, `text` | a line said in one of this office's worlds |
| `teleport_ask` | `id`, `from` {`id`, `name`, `sim`, `key`} | 5.1 (8.15): someone asks whether they may teleport into this office's room: `teleport_answer` within 5 s |
| `teleport_answered` | `to` (the door's office id), `key`?, `ok`, `reason`? | 5.1: what `to`'s room said to this office's `teleport_ask` (with the door's key); `reason` when the lobby said no for it |
| `huddle` | `group`, `from` {`id`, `name`, `key`}, `on`, `since` (seconds) | 4.0: a huddle in one of this office's worlds started (`on`) or over (its starter ended it, left the world, or left the lobby), to its members online whose hello said `voice`; one going on is told again after the worlds, on `welcome` (`since`: how long ago it started) |
| `groups` | `groups` [{`name`, `about`, `open`, `locked`, `members`, `online`, `owner` (3.2: its owner's name)}] | the lobby's worlds, after `groups` (neither `open` nor `locked`: closed, 6.6) |
| `browser_pending` | `code`, `key` (the browser's), `agent` ("Firefox on Mac"), `pictures` | the browser waiting with the code typed (3.1, 6.11) |
| `browser_confirm` | `code`, `key`, `confirm` (6 digits), `expires_in` | 3.2, after `browser_allow`: what that browser must show to be linked |
| `browsers` | `browsers` [{`key`, `agent`, `pictures`, `created`, `used`, `online`}] | this office's linked browsers: on asking, and as one is linked, unlinked, comes or goes |
| `lobby_bans` | `bans` [{`key`, `until` (seconds since 1970, or null: for good), `reason`}] | to an operator, after `admin` |
| `error` | `code`, `message`, `ref`? | a refusal (section 11) |

`relays` is the list of iroh relay URLs to use: the lobby's own (6.8) when it runs one; empty means the
visit-client's built-in defaults.

### 6.4 Knocks and visits

- **A knock is refused** (`error` to the knocker) when: the door is not online (`not_found`), is the
  knocker (`self_knock`), is `busy` (`not_available`), has `max_visitors` visitors already (`full`), has
  not published its link, or the knocker has not (`not_ready`); the knocker is already visiting that door
  (`already_visiting`); the knocker's earlier knock there is still waiting (`knock_pending`); or the
  knocker has knocked 6 times in the last minute (`rate_limited`).
- **A knock waits** 60 s, then times out on both sides, as in version 1.
- **Letting in.** On `answer` with `accept: true`, if the door is still under `max_visitors`: a knocker
  visiting another room leaves it first (`visit_ended`, reason `"moved"`); the lobby makes a visit id and
  a fresh ticket and sends `visit` to both, each with the **other's** link (`peer_link`); everyone gets a
  fresh `lobby`. If the door filled up meanwhile, the knocker hears `answered` with `reason: "full"`; if
  either side withdrew its link meanwhile, `reason: "not_ready"` (and the door an `error`, `not_ready`).
- A visit counts toward the host's `max_visitors` from the moment it is agreed until it ends.
- **A guest at an open door.** The host's visit-client answers a knock itself, `accept: true`, when its door
  is open, its page hosts its scene, and the knock's `from.key` is a guest's (3.3: a co-member of one of its worlds,
  6.6), to watch or to visit; its page is not asked.
  When the lobby then sends `visit`, the visitor's `peer_link.endpoint` must be that key, or the host ends the
  visit (`kicked`) before any link is made (section 3, point 9). To the lobby this is an answer like any other.
- **Watches** (8.4): a knock with `watch: true`. Its `knock_sent`, `answered`, `visit` and errors say so
  (errors with `ref: "watch"`). Refused as a knock is, except: `busy` does not stop it; `full` is the door's
  `max_watchers` (8) watches; `too_many`, the knocker watches `max_watching` (8) plots already;
  `already_watching`; `not_supported`, the door's hello did not say `world`; and its rate is
  `watch_knocks_per_minute` (30: visit-clients make them by themselves). A watch agreed does not move the
  knocker out of the room it is in, and does not count toward `max_visitors` or `visiting`. The door's
  visit-client answers it itself: yes for a guest through its open door, no for anyone else.
- **Knocks to walk in, and links** (8.5): from a version-4 office, refused (`not_supported`, 4.0); a `--v3` lobby's
  3.x offices keep them, as below. A knock with `link: true`, to a door whose hello said `links`. Its `knock`, `knock_sent`,
  `answered` and `visit` say `link`; its errors are a knock's (`ref: "knock"`). Refused as a knock is (`busy`
  stops it, and 6 a minute), except: `full` is the door's `max_watchers`, `too_many` the knocker's
  `max_watching`, and `already_watching` when the knocker watches the door's plot already. The door answers it as
  any knock: its page, or its visit-client for a guest through its open door. Let in, it is a watch (`watch` and
  `link` on its `visit`), counted as one. To a door that does not take links (an older Visit), it is an ordinary
  knock, and nothing says `link`.
- **Knocks to talk** (4.0, 8.7): a knock with `voice`, between offices whose hellos said `voice` (else
  `not_supported`: an office before 4.0 would take it for a knock to visit). It rings however `busy` or full the door
  is; refused `already_talking` when the two have a voice visit already (either way), `knock_pending` while one
  waits either way, `full` past 50 voice visits an office, `not_ready` as a knock is; counted with knocks (6 a
  minute). Its knocker may withdraw it (`knock_cancel`). Let in, a **voice visit**: `visit` with `voice`, which moves
  nobody and counts toward neither visitors nor watchers. With `huddle` (a world), to join the huddle its door
  started there: refused `not_found` unless that huddle is on and the knocker is in that world.
- **Huddles** (4.0): `group_huddle` starts one in a world (one a world at a time), or ends it. The lobby tells the
  world's members online whose hellos said `voice` (`huddle`), and again on `welcome` while it goes on; it ends when
  its starter ends it, leaves the world, or leaves the lobby. Nothing of it is kept.
- **Knocks to teleport** (5.1, 8.15): a knock to watch with `teleport`, to a door whose hello said `teleport` (else
  `not_supported`), refused as a watch is (its errors with `ref: "teleport"`), but one watch past the knocker's
  `max_watching` (its world may be full: the one it teleports into is let go once it leaves it). The door's
  visit-client asks its page (`teleport_ask`): yes, it lets the knocker in (its key, as a guest's: point 9); no, or a
  shut door, declined. Its `knock`, `knock_sent`, `answered` and `visit` say `teleport`. Asks to teleport
  (`teleport_ask`) are the lobby's to pass on, never to answer for a door that takes them.

### 6.5 Why visits end

`"left"` (the visitor ended it), `"kicked"` (the host did), `"moved"` (the visitor went into another
room), `"gone"` (either office left the lobby: its visit-client stopped, its user signed out, or its connection
stayed down past the 30 s resume window), `"link_failed"` (the link could not be made, or was lost for longer than
the grace period, section 7.5).

### 6.6 Users and worlds

The lobby has two things: **users** (the offices in it, each a signed-in workroom) and **worlds** (the protocol's
groups).

- **A world** is a name (an office's name's rules, one per lobby whatever its case), a line about it, its owner (the
  key that made it), its members (keys, each with the name it last had, and since when), and the keys it keeps out.
  It is **public** (`open`: anyone joins, at once) or takes a **password** (`locked`: anyone who has it joins; the
  lobby keeps only its Argon2id hash, and an office tries at most 5 passwords a minute). Its owner (and the lobby's
  operators) changes its line, gives it a password or a new one (its members stay), makes it public again, takes
  members out (`removed`) and keeps keys out (`banned`, until let back in), and hands it on; leaving, its owner hands
  it to its oldest member; the last member leaving ends it. A key is in at most 20 worlds; a world has at most
  `--world-size` members (50). A world made before 3.1 that took requests is **closed** (neither public nor locked)
  until its owner makes it public or gives it a password.
- **What a world is for** (3.3). Its members are each other's guests: through each one's open door, the others walk
  in without asking and may put that room in their worlds (8.4) (section 3, point 14). A world makes a community
  possible; it puts nobody's room on anyone's map by itself: each member picks whose rooms sit round theirs (at most
  8, from any of their worlds), and two members of one world may see different maps. (Before 4.0, a link (8.5) reached
  anyone outside one's worlds, and a knock walks anyone into a room whose user lets them in.
- **Its chat.** Each world has one (`group_say`, `group_chat`): to its members in the lobby, the sayer too, in one
  order. Nothing of it is kept by the lobby.
- **The list of who is here.** Every office in the lobby is in its `lobby` snapshot; one may add a line and a few
  tags (`about`), shown there and, for those that have one, on the lobby's public page (6.9).
- **Kept.** The lobby keeps its worlds (its `--data-dir`: `groups.json`) and its linked browsers (`browsers.json`,
  6.11), across restarts, besides its bans. Nothing anyone says is kept.

### 6.7 Operators and bans

A lobby's operators are keys it is started with (`--admin`). Their `welcome` says `admin: true`; they run every
world (as its owner); with `admin` they ban a key from the lobby (for `minutes`, or for good, with a reason) or no
longer, and list the bans. A banned key's session ends at once (`error` `banned`, closed 4403), and its `hello` is
refused until the ban ends. Bans are kept with the worlds (`bans.json`). An operator cannot be banned.

### 6.8 The relay

A lobby may run its own iroh relay (`--relay-port`, behind its TLS proxy: the paths `/relay`, `/ping`,
`/generate_204`), and names it in every `welcome`'s `relays`. Only offices in the lobby (version 4: their keys
proved) may use it: the relay's own handshake proves an endpoint's key, and the lobby lets in those it has a session
for. Given the certificate of its name (`--relay-cert`, `--relay-key`, read again every hour), it answers QUIC
address discovery on UDP 7842 too: how a visit-client learns its public address, so that two can reach each other
directly and need the relay only when that fails. At most 4 MiB a second from each.

### 6.9 The public page

`GET /` without a WebSocket is the lobby's page (`--no-page`: none), drawn in basedverse's pixel-art world: its title
and message of the day, how many offices are in it, its worlds (name, line, public or 🔒, size, how many online),
and the users with a line (name, sim, line, tags, visitors). Never a key, nor where anyone is; read from
`GET /api/lobby`. In a browser its workroom linked (6.11), it is that workroom's: its chat, who is here, its worlds.
Laid out as a game's server browser (3.2): the worlds the screen's focus, a list to filter (a name, public or
password, not full) and sort (most here, most members, name), in tabs (Worlds, Your worlds, Users); the row picked
shown below it with what can be done (Join, a password, Chat, Leave, its owner's tools; Knock, Link, Whisper); a row
double-clicked joins; chat in a column beside it. Each world in the list says its owner's name (`groups`' `owner`).
`?side=worlds` (or `mine`, `users`) opens on that list. `GET /health` answers `ok <n> online`.

**One connection's share** (3.0.0). A connection may send 20 messages a second, in bursts of 60 (a visit-client
sends a few a minute); more, and it is closed (`rate_limited`; its session held, as for any drop). One that cannot
take what it is sent (1024 messages waiting, or one not taken in 15 s) is closed too. An office makes at most 3
worlds an hour, tries or sets at most 5 passwords a minute, and types at most 5 pairing codes a minute.

### 6.11 Linked browsers: the lobby's page, as a workroom (3.1)

The public page (6.9) is a workroom's too, in a browser that workroom linked. The browser has no sign-in of its own:
the workroom has that, and its key; so the workroom vouches for the browser, once, and the browser keeps a key of its
own to come back by.

- **The browser's key.** The page makes an Ed25519 key (Web Crypto; its secret half not extractable, kept in the
  browser's IndexedDB) and says `hello` with `browser` (its public half); the lobby sends `challenge`, and the page
  `prove`s with the UTF-8 text `basedverse-lobby/4 browser\n<lobby>\n<nonce>\n<key>` signed (the lobby as the
  browser dialled it, 6.1's way; `bad_proof` otherwise, closed 4401). Then: linked to an office in the lobby,
  `web_welcome`, the lobby, and the office's worlds; linked to one not in it, `not_in_lobby` (closed 4410: the page
  tries again now and then); not linked, `browser_code`. A browser unlinks itself by `browser_forget` once in, or
  (3.2.3) by `hello` with `forget`, its key proved, whether its office is in the lobby or not (a browser whose room
  is off is never stuck with it): its link gone, its tabs out (`unlinked`, 4403), a confirmation waiting for it off,
  closed 1000 `unlinked`; the page forgets its key, and has a new one and a code.
- **Linked by a web pass** (the room's 🌐 Lobby, `v.openLobby()`; or 📱 Phone, the same as a QR code, `v.lobbyQr()`).
  The office's visit-client makes the pass at its signed-in user's asking (`lobby_pass`, the person's page): it
  signs, with the office's key, `basedverse-lobby/4 pass\n<lobby>\n<key>\n<expires>\n<nonce>` (the lobby as its
  `VISIT_LOBBY` names it; `expires` 10 minutes on, seconds since 1970; a nonce of 32 hex), and puts it in the
  lobby page's address's fragment (`https://<lobby>/#pass=<key>.<expires>.<nonce>.<signature>`). The page reads it,
  takes it out of the address, and says it in its `hello` (`pass`); the lobby checks its signature against its own
  names (closed 4401 otherwise), that it is still good and not used already (once each), and that the office is in
  the lobby now (`not_in_lobby`); then links the browser's key to the office. A pass for a QR code lasts 2 minutes.
  A browser linked to another office already is not moved: `linked_elsewhere` (whose it is, whose it would be;
  closed 4409, the pass not spent), and the page asks its person; agreed, it says `hello` again with `switch`, and
  the browser's tabs that were the other office's are put out (`unlinked`, 4403).
- **Linked by its code.** A browser not linked gets a code (8 letters and digits, no 0, 1, I or O; shown `ABCD-EFGH`)
  and three pictures (from its key), for 10 minutes (then `code_expired`, closed 4408: a new one on asking); at most 4
  browsers wait with codes from one address (an IPv6 one: its /64), 5000 in all (`busy`, 1013). Typed in
  the room (the panel's Link a browser: `browser_code`), the lobby says which browser waits with it
  (`browser_pending`: what it is, its pictures, the same as on its screen). Allowed (`browser_allow`), it is not
  linked yet (3.2): the room gets a **confirmation** (`browser_confirm`: 6 digits, good 5 minutes, that browser's
  alone) and shows it as a QR code of the lobby's page with it (`https://<lobby>/#verify=<digits>`) and as digits; the
  browser hears who would link it (`browser_confirming`). It is confirmed one of two ways: a phone (any device: the
  person's own, at the room) scans the QR code, which opens the lobby's page there, and that page says `hello` with
  `verify` (3.2.2: the scanner is told what it confirmed, `browser_confirmed` {`office`, `agent`, `pictures`}, closed
  1000, and is not linked, unless it is that browser itself); or the digits are typed on the waiting page
  (`browser_verify`). Then it is linked, and its waiting tabs are in by themselves. Scans that match nothing: 10 every
  10 minutes from one address (`rate_limited`). A confirmation is the browser's key's, not one connection's (3.2.1): a waiting
  tab that comes back after a drop hears `browser_confirming` again, a QR code's tab links with none waiting, and the
  page takes `#verify=` when only its address's end changes (a phone opening it in the same tab). Another browser's
  tab with them is refused (`no_confirm`, 4401); a wrong one
  is counted (`bad_confirm`), and at 5 every confirmation of that browser is off (its rooms ask again). A browser has
  at most 3 rooms' confirmations waiting (a code is anyone's who sees it: the confirmation decides).
- **What the browser does** is the office's: `chat`, `whisper`, `group_say`, every `group_*`, `groups`, `about`,
  `admin` (an operator's), and `knock` (to visit or link: the office knocks, and its visit-client takes the visit,
  in its room, as for any knock); and `browser_forget`. Nothing else (`not_from_web`): never `answer`, `door`,
  `link`, `status`, `end`, nor link another browser. Its refusals and answers come to it alone.
- **What it hears** is what the office does: the lobby, its talk (the lobby's, its worlds', whispers to it), its
  worlds, knocks on its door (to answer in its room) and its own knocks' fate. Never a visit's ticket or link.
- **While its office is in the lobby.** A dropped office (held for resuming) keeps its browsers; one that leaves
  closes them (`office_gone`, 4410), and they come back in when it does. At most 4 of an office's browsers in the
  lobby at once (4429), and 10 linked (linking an eleventh unlinks the one unused longest, and puts it out); one unused
  for 90 days is unlinked. The office's list (`browsers`) says each, and `browser_revoke` unlinks one: out at once,
  every tab of it. Visit's panel says when one is linked.

### 6.10 Limits

`welcome.limits`: `name` 24, `message` 280, `frame` 65536, `knocks_per_minute` 6, `knock_ttl` 60, `max_visitors` 8
(the highest an office may set), `max_watchers` 8, `max_watching` 8, `watch_knocks_per_minute` 30, `link_addr`
2048 (bytes of `addr` as JSON), `connections_per_ip` 8, `resume` 30, `chat` 200 (characters in a line),
`chats_per_10s` 5, `groups` 20, `group_members` 50 (`--world-size`), `password` [4, 64], `browsers` 10, `about` 200, `tags` 5,
`tag` 20.

## 7. The link: visit-client ↔ visit-client

One QUIC connection per visit, made with iroh (hole punching, then the relay if no direct path works),
ALPN **`basedverse-visit/2`**. iroh connections are encrypted and authenticated by the endpoints' keys.

### 7.1 Dialling and the handshake

1. On `visit`, the **visitor's** visit-client dials `peer_link` with the ALPN. The **host's** waits for it.
2. The visitor opens the **control stream** (bidirectional) and sends `{"proto": "basedverse-visit/2",
   "visit": "<id>", "ticket": "<ticket>"}`.
3. The host checks that it has that visit, that the ticket matches, and that the connecting endpoint's id
   is the visitor's `peer_link.endpoint`. If not, it closes the connection (application error code 1,
   `"refused"`). If so it answers `{"ok": true}`.
4. Both tell their pages `visit_started`. If the dial or handshake fails for 20 s, either side sends the
   lobby `end` with `reason: "link_failed"`.

### 7.2 The control stream

Messages are JSON, each prefixed by its length (u32 big-endian). In order, reliable.

| from | message | meaning |
|---|---|---|
| visitor | `{"keyframe": true}` | send a key delta soon (passed to the host's page at most every 500 ms) |
| visitor | `{"scene_resync": true}` | send the whole scene again (passed to the host's page at most every 2 s; one asked for sooner waits, and goes) |
| visitor | `{"point": {"yaw", "pitch"}}` | a point (8.3): passed to the host's page with only those two, clamped, at most 5 a second |
| either | `{"phone": true}` / `{"phone": false}` | this side's room has a phone, or no longer (8.8): sent on every link, and on each new one while it has |
| either | `{"call": "ring" \| "answer" \| "hangup"}` | a call (8.8), passed to the page as `call`. 3.4.3: a call is on only when the side that was rung answers it: an `answer` to no ring of this side's (or to its own) is nothing, and not passed on |
| visitor | `{"say": "<text>", "media"?, "links"?, "target"?}` | a line the visitor says in the host's room (8.9): only while in it; checked and rate-limited as the page's is; passed to the host's page as `chat` (`far` if its user is not near) and on to the room's other visitors near the sayer (3.4.2: within 45 m, as the room has them; one not placed, everyone as before); with `target` (3.3), what it is meant for, and, a figure, `addressed` to the host's page too |
| either | `{"seam_said": {"key", "name", "text"}}` | 3.4.2 (#54): a line its user said at home, near the other's across their plots' side (8.9): the other's visit-client passes it to its page (`chat` with `seam`, `owner`) only if it too has them near across the side (both at home, near in both worlds), and does not keep them out of its chat |
| visitor | `{"tell": "<text>", "figure"}` | 3.3 (#48): a whisper to one of the host's room's figures: to the host's page alone (`addressed`, `whisper`), with the visitor's lines' limit; text only |
| visitor | `{"act": {"figure", "action"}}` | 3.3 (#48): a figure's action, from the visitor's target card: to the host's page (`act`), with the points' limit |
| host | `{"said": {"key", "name", "text", "id"? \| "npc"? \| "owner"?, "target"?}, "whisper"?}` | a line said in the host's room (8.9), by its owner, one of its own figures (`npc`, its id, as `say_as` said) or one of its visitors (by its visit's id): to everyone who can see the room (in it, or watching it); checked, passed to the page as `chat`. `whisper` (3.3): a figure's line to this visitor alone (`say_as` with `to`): a `chat` whisper |
| either | `{"sim_say": "<text>"}` | a line said into the other office's room's chat (8.9), by this office's user, wherever they are; checked and rate-limited as a page's is; passed to that room's page as `chat` (`scope: "sim"`, `home: true`) and on to everyone else in its chat (`sim_said`) |
| either | `{"sim_said": {"key", "name", "text"}}` | a line said in this office's room's chat (8.9), by its owner or into it from a linked office: to every office linked with it, either way, once each (on its visit, to one in the room), but its sayer; checked, passed to the page as `chat` (`scope: "sim"`, on this link's channel). Before 0.19.1: host only, to each visitor in the room |
| host | `{"near": [{"key", "name", "distance", "id"?, "phone"?}]}` | who is near the visitor in the host's room (8.7), as the host's visit-client has it: checked, passed to the page as `voice_near`; kept (who the visitor hears in person) |
| either | `{"media_want": "<id>"}` | a picture a line over this link named (8.12): the other side sends it (stream 5), or says `{"media_none": "<id>"}`. A visit-client answers for any picture it has (its id, a hash of it, is what is needed to ask); one passing a line on has its pictures, or is getting them. 3.4.3: at most 30 asked a minute a link (as many as a visit-client takes from it); at most 16 MB of pictures on their way to one link, and 64 MB to all, one picture not twice at once; each given 60 s to go. Past those, an ask is dropped (no `media_none`: it may ask again) |
| either | `{"whisper": {"text", "media"?, "links"?}}` | a whisper with pictures or links (8.12), over a link rather than through the lobby: passed to the page as `chat` (`scope: "whisper"`) |
| either | `{"user": {"name"}}` | the name this side's user goes by (8.9; null: none), sent when it changes and on each link that comes up: checked (1–24 characters), passed to the page as `user`; the other side names this side's user by it (their lines, their voice, a room voice, near lists) |
| either | `{"rtc_offer": sdp}` | 4.2 (8.7): this side's page's WebRTC offer for the other side's office: answered with `{"rtc_answer": sdp}`, or `{"rtc_failed": why}`. A voice visit carries these too |
| either | `{"rtc_close": true}` | 4.2 (8.7): this side's page's connection there closed (no longer wanted, or the page gone) |
| either | `{"world_near": true}` / `false` | this side's world has the other side's user within reach of its own (8.7: 45 m and the 15 m margin), both at home: sent when it changes, and on each link that comes up. Both sides saying so, they hear each other across a plot's side |
| either | `{"voice_join": true}` / `false` | into the other side's room's voice, or out of it (8.7); refused with `voice_out` |
| either | `{"voice_members": [{"key", "name"}]}` | who is in this side's room's voice, to each in it, when that changes: checked, passed to the page as `room_voice` |
| either | `{"voice_out": "kicked"}` / `"banned"` | out of this side's room's voice (8.7), and why: passed to the page as `room_voice` null, with `out` |
| either | `{"banned": {"voice", "chat"}}` | this side keeps the other side's user out of its room's voice, its chat, or neither now (8.9): sent when it changes, and on each link that comes up; passed to the page as `banned`. Its visit-client refuses their lines into that room's chat, and joining its voice, itself (`banned`) |
| host | `{"avatar_want": true}` | send this office's avatar (8.6): now, on a stream of its own (7.4), and again whenever its page gives a new one. Its page has given none: `{"avatar_none": true}` back (passed to the host's page as `avatar_none`) |
| host | `{"body": {"look", "eyes", "problem"}}` | how the host's room draws the visitor (8.6): `look` "theirs" (its own model) or "mine" (the room's), `eyes` over its feet (the room's units), and why not its own, if the room would show it (`problem`, at most 200 characters). Sent as it changes; checked, passed to the page as `body` |
| visitor | `{"possessions": [{"id", "name", "mount", "fly", "speed", "lift"}]}` | what the visitor owns (8.11): sent each time the link is up and whenever its page says; checked, passed to the host's page as `possessions` |
| host | `{"possession_want": "<id>"}` | send this possession's model (8.11): now, on a stream of its own (7.4), and again whenever its page gives a new one |
| visitor | `{"landscapes": true}` | its visit-client reads landscapes (8.10): sent each time the link is up, passed to the host's page as `landscapes` |
| host | `{"landscape": "<hash>"}` | this room's landscape is this one (8.10: blake3 of its gzipped JSON, hex): sent when the link is up and whenever it changes. The visitor's visit-client gives its page the one it keeps of that office by that hash, or asks for it |
| visitor | `{"landscape_want": "<hash>"}` | send that landscape: on a stream of its own (7.4), once a link for a hash |
| visitor | `{"assets": true}` | 4.1: its visit-client reads a scene's assets (8.14): sent each time the link is up, passed to the host's page as `assets` (and in `visit_started`) |
| visitor | `{"asset_want": "<name>"}` | 4.1: send that asset of this room's own (8.14): on a stream of its own (stream 6), from what is kept, else its page is asked; at most 2000 a minute |
| host | `{"asset_none": "<name>"}` | 4.1: not an asset of this room's (or its page did not give it in 30 s): the visitor's page hears `asset_none` |
| visitor | `{"shared_adds": true}` | its page reads an `add` that names what it already has by uuid only (8.3; Visit 0.7 on): sent each time the link is up, passed to the host's page once as `shared_adds` |
| host | `{"paused": true}` / `{"paused": false}` | the host's page went away / came back |
| host | `{"perform": {"act": {"clip", "loop", "secs"} \| null, "hold"}}` | 4.0 (8.6): what the room has this visitor do and hold, to the visitor's page as `perform` (only while it is in the room) |
| watcher | `{"enter": {"x", "y", "z", "yaw", "air"} \| null}` / `{"exit": true}` | stepping into the room watched, there (clamped, passed to the host's page as `enter`; null: a link going there, spawned where newcomers go), or out (8.4, 8.5); in, it counts toward `max_visitors` |
| host | `{"enter_refused": "full"}` | the room is full: the watcher stays outside |
| watcher | `{"enter": null, "teleport": true}` / `{"exit": true, "teleport": true}` | 5.1 (8.15): stepping in by teleport: the host's visit-client asks its page (`teleport_ask`, the link's key) and then passes it on as `enter` with `teleport` and no pose, or answers `{"enter_refused": "teleport"}`; out by teleport (`exit` with `teleport`, its page plays it). A host before 5.1 takes the first for a link's "go there" (where newcomers go), the second for an exit |
| either | `{"seen": {"dx", "dz", "turn", "size"} \| null}` | where the sender's world has the receiver's plot (8.4), or not; sent when it changes and each time a link comes up. Passed to the page as `seen` with only those fields |
| either | `{"bye": "<reason>"}` | ending the visit (also sent to the lobby as `end`) |

### 7.3 Inputs

Visitor to host as **QUIC datagrams**, each a JSON object `{"seq", "forward", "strafe", "run", "yaw",
"pitch", "t"}` (section 8.2): the whole state, never a change, so the newest is all that matters. Datagrams
may be lost or reordered: the host keeps the one with the highest `seq` and drops older ones, and takes at
most 120 a second. The visitor sends whenever its keys or look change (at most every 8 ms) and every 33 ms
regardless, so a lost "key up" is corrected within one interval; the host treats a visitor whose last input
is older than 300 ms as standing still.

### 7.4 The scene's deltas and edits

Host to visitor on **unidirectional streams**, each starting with one byte that says what it carries:

- **0, deltas**: one stream per key delta (as Media over QUIC carries a group of pictures): it starts with a
  key delta and carries the deltas after it until the next key delta, which starts a new stream. Each delta
  is: length (u32 big-endian, of what follows), flags (u8, bit 0 key), timestamp (u64 big-endian,
  microseconds), the delta. **Newest wins; nothing queues.** The host's visit-client resets an older stream
  when a newer key delta starts one (its unsent deltas are stale), lets at most 64 KiB be sent and not yet
  acknowledged, and drops every delta of a stream that is more than 100 ms older than the newest waiting,
  asking its page for a key delta. The visitor's visit-client stops reading an older stream when a newer
  one starts: a stalled stream is abandoned rather than waited for. Deltas go to the page as they arrive.
- **1, edits**: one stream for the visit, reliable and in order (each edit builds on the last): each edit
  its length (u32 big-endian) then the gzipped JSON. Edits are **never dropped one by one**: the host's
  visit-client lets at most 32 MiB of them wait; past that, or if the stream fails, it drops what waits and
  tells its page `scene_resync`, which sends the whole scene again.

And visitor to host, once its host asked (`avatar_want`):

- **2, its avatar** (8.6): its length (u32 big-endian, at most 4 MiB) then the gzipped JSON, the stream then
  finished. The host's visit-client takes at most one every 2 s, unpacks it to at most 16 MiB of one JSON object
  nested at most 256 deep, and passes it to its page as `avatar`. A host on an older Visit never asks.

And visitor to host, once its host asked (`possession_want`):

- **4, a possession's model** (8.11): its id (one byte, its length, then the id), its length (u32 big-endian, at
  most 4 MiB) then the gzipped JSON, the stream then finished. The host's visit-client takes one every 500 ms (a
  sooner one waits its turn, at most 8 of them: its page asks for each once), checks the id, unpacks it as an
  avatar, and passes it to its page as `possession`.

And host to visitor, once the visitor asked (`landscape_want`):

- **5, a picture** (8.12), either way, as the other side asked for it (`media_want`): its id (a length byte, then
  it), its type (likewise), its length (u32 big-endian, at most 8 MiB), then its bytes. The receiving visit-client
  takes it only if it asked for it, its bytes hash to its id (blake3) and are a PNG, JPEG, WebP or GIF; at most 30 a
  minute from a link.
- **3, its landscape** (8.10): its length (u32 big-endian, at most 16 MiB) then the gzipped JSON, the stream then
  finished. The visitor's visit-client unpacks it to check it (as an edit), keeps it as it came if its blake3 hash
  is the one the host named (else drops it), and passes it to its page as `landscape`.
- **6, an asset** (4.1, 8.14), as the visitor asked for it (`asset_want`): its name (a length byte, then it), its
  length (u32 big-endian, at most 16 MiB), then its bytes. The visitor's visit-client takes it only if it asked for it,
  its bytes hash to its name (SHA-256) and are of a kind kept (a PNG, JPEG or WebP picture, or gzipped JSON); at most
  2000 a minute from a link. A host sends at most 48 MiB of them at once to one visitor.

Datagrams (unreliable): the visitor's input (7.3), JSON, so starting with `{`. (Voice went as datagrams before 4.2,
0xA0 to 0xA3; it goes over WebRTC now, 8.7: a link carries only its offers and answers.)

### 7.5 Blips

A link that drops (Wi-Fi switching, a laptop waking) is redialled by the visitor every 2 s, with the same
ticket, for a **grace period of 15 s**; the visit carries on if it comes back (the visitor then asks for a
key delta), and ends with `"link_failed"` if not. During the gap the visitor goes on drawing the scene as it
was. A link closed on purpose (application codes 0 bye, 1 refused, 3 visit over: section 11) is not
redialled: the lobby ends its visit, and a visitor refused at the handshake ends it at once (`link_failed`).

## 8. Scenes and input

### 8.1 Scenes

**Positions are feet** (Visit 0.19; [MODELS.md](MODELS.md)). Where someone is, is the point on the ground under
their feet, in the room's units: what a host's `spawn` and `move` return, and what a world's `frame` takes. Their
eyes are above that (their own model's `eyes`, else the room's `eye`, 1.6 m unless it says), and only their camera
is there: a host sends each visitor where its eyes are (its deltas' pose, its scene's `pose`), and starts its
clicks' rays there.

A host sends its **scene**, and the visitor's page draws the room itself: walking and looking are as quick as
in its own room, the host renders and encodes nothing for anyone, and what crosses the link is the scene as
data.

- **The whole scene** is the edit `{"type": "scene", …}`: `three` (the revision), `json` (three.js's own
  `toJSON()` of the scene, or of the part shared), `ids` ([uuid, number] for every object: deltas and edits
  name objects by number; 0 is the root), `targets` (lights' targets, which three.js's JSON leaves out before
  r160: [light, target], or [light, null, [x, y, z]] for a target outside the scene), `renderer` (the host
  renderer's settings, by its release's names: `shadowMap` {enabled, type}, `toneMapping`,
  `toneMappingExposure`, `sortObjects`, `outputEncoding` or `outputColorSpace`, `physicallyCorrectLights` or
  `useLegacyLights`, `clear` [colour, alpha], `colorManagement`), `fov`, `near`, `far` (the visitor's camera),
  `walk` ({speed}, how the host's `move` walks, or null), `yaw0` and `pose` (where the visitor starts), `plot`
  ({center: [x, z], size: [w, d]}, in the scene's units, or null) and `unit` (metres in one of them). It goes
  when the visitor starts, and when asked (`scene_resync`); it replaces whatever the visitor had. A visitor's
  page that has none 3 s after its visit starts (or after it last asked) asks for it.
- **Edits** (`{"type": "edit", …}`): at most one every 33 ms, only when something changed, each field
  optional, applied in this order: `res` ({geometries, materials, textures, images, shapes}, in `toJSON` form:
  new ones, or ones changed in place, whose content the visitor's copy takes), `remove` ([number]), `add`
  ([{parent, json (the new object's `toJSON()`, with what it is made of; when every visitor the edit goes to
  reads it (`shared_adds`, Visit 0.7 on), except the geometries and materials they already have: sent before,
  in the whole scene or an edit, and used by the scene ever since, which the JSON names only by uuid; a visitor
  that does not have one asks for the whole scene), ids, targets}]), `parent` ([[number, new parent's
  number]]), `assign` ([[number, material uuid (or uuids) or null, geometry uuid or null]]),
  `props` ([{uuid, …}]: materials' colours and values as they change: `color`, `emissive`, `specular`,
  `opacity`, `transparent`, `visible`, …), `scene` ({background (a colour, {texture: uuid}, or null), fog
  ({type, color, near, far, density} or null)}).
- **Deltas**, every 33 ms when anything moved, a key delta every second and when asked (`keyframe`), all
  big-endian: u8 version (1; 2 to a watcher that has not stepped in: the pose fields are not its, and are 0); f64 `t`, the visitor's latest input time the host has, and f32 `age`, the ms
  since that input reached the host; f32 x, y, z, yaw, pitch, where the host has the visitor; u32 n; then n
  objects: u32 number, u8 flags (bit 0 visible, bit 1 scaled), f32 position (3) and quaternion (4), and f32
  scale (3) when scaled (else 1). A key delta lists everything that has moved since the whole scene; the
  others, what moved since the last.
- **Moving things** are shown about 70 ms behind the host's clock (estimated as the least of arrival minus
  timestamp over the last 2 s), between the two updates around that time: smooth, and always one to move
  toward.
- **Where the visitor stands.** Its page walks it with `Visit.walk` at the host's `walk.speed`, as its keys
  say, at once (without the host's walls). Each delta's pose is compared with where the page had predicted it
  would be `age` ms after `t`; the difference is put right at once in where it stands, and eased out of what
  it sees over about 100 ms (more than 4 units off: there at once). A host that gives no `walk` is followed,
  eased the same way: walking then waits for the round trip; looking never does.
- **Its own body**, which the host draws for everyone else, the host marks `userData.visitor = <visit id>`;
  the visitor's page does not draw it in its own eyes.
- **userData stays the host's.** Of each object's `userData` (which three.js's `toJSON` would send whole), a
  visitor gets only what Visit reads: `visitor` (above) and `visitPoint` (8.3), each a string, number or
  boolean. Everything else a sim keeps there (who a figure is, its handles, references to other objects)
  never leaves the host's page.
- **Data, never code.** The visitor's page draws a scene with the host's very three.js revision (its first
  release, `0.<r>.0`, r125 to r186 in 0.7.0), fetched from `https://cdn.jsdelivr.net/npm/three@0.<r>.0/build/`
  (or a page's own `threeBase`), checked against the SHA-384 hash visit.js carries for it (Subresource
  Integrity) and run as a module of its own: nothing else ever runs. Before three.js reads what a host sent,
  visit.js makes it safe: materials only of the kinds three.js draws itself (a `ShaderMaterial` or
  `RawShaderMaterial` is a program: it becomes plain grey), images only as PNG, JPEG or WebP data URLs at most
  4096 px a side (read from their headers, before anything decodes them) or typed-array data of that size,
  shapes' segment counts at most 256 (detail 5), and across the scene about 4 million vertices and 100,000
  objects at most. A host can only host a revision its visit.js knows, so every visitor of the same version
  draws it.
- **Checked on the way.** The visitor's visit-client unpacks each edit with a limit (64 MiB: a "zip bomb"
  stops there), checks it is one JSON object nested at most 256 deep, and passes it to the page as `scene`; one
  that fails, or past 64 MiB a second, is dropped and the page told (`scene_lost`): it asks for the whole scene
  again.

### 8.2 Input

| field | type | meaning |
|---|---|---|
| `forward` | −1, 0 or 1 | W/↑ is 1, S/↓ is −1 |
| `strafe` | −1, 0 or 1 | D/→ is 1 (right), A/← is −1 |
| `up` | −1, 0 or 1 | Space or Page Up is 1 (up), X or Page Down is −1 (down), as in WoW: what it does is the room's (its `move`) |
| `run` | boolean | Shift held |
| `riding` | string or null | the mount the visitor rides (8.11): one of its possessions' ids; the host decides |
| `yaw` | radians | where the visitor is looking, left/right, relative to how it was facing when it arrived (positive: turned left) |
| `pitch` | radians, −1.4 to 1.4 | up (positive) or down |
| `t` | ms | the visitor page's clock when it sent this input (`performance.now()`); the host's deltas carry it back |

**Look belongs to the visitor; position belongs to the host.** The visitor's page turns its view as fast as
the mouse moves and sends where it is looking; the host applies it as is. Where the visitor stands is the
host's to decide (`move`), against its own walls and furniture; the visitor's page walks at once by the
host's `walk` and takes the host's answer (8.1). The host clamps every field to its range.

**Physics are the room's.** Visit has none of its own: it carries what the room needs to decide, and the room's
`spawn` and `move` say where a visitor is, its height included. A visitor's `up` (−1, 0, 1) reaches `move`, which
flies it, or not. One stepping in from a world in the air says how high above its own floor it was (the `enter`
pose's `air`, passed to `spawn` as `at.air`), and the room puts it where it will: that high, to fall or float down,
or on the floor at once. `move` may say how high above its floor it has the visitor (`air` in the pose it returns),
for a mount's `fly` clip (8.11). The visitor's own world has it as high as the room does: its feet there. Walking off the plot, it is home at the height it came in at, or
lower if the room had it lower.

### 8.3 Points

A visitor clicking at the middle of its view **points**: while it looks around (the pointer locked to the
view), the view shows a crosshair there, and a click sends `point` with where the visitor looks (its `yaw` and
`pitch`, as in its input), at most one every 200 ms. Nothing else: not what it saw there, nor what it thinks it
clicked. The host's visit-client clamps both, drops points beyond 5 a second, and passes them to its page; the
library makes each a ray in the room's own terms, from where the **host** has the visitor (its pose: x, y, z,
its eyes) toward where it looked, and the page decides what that does, if anything. A visitor can point only
from where it stands, in a direction it can look: never through the host's eyes, never at what the host has
not put there.

**The wheel** (Visit 2.1.1) points too. Turned while the crosshair is on something whose type takes it (below), it
sends `point` with `wheel`: whole notches, positive turned down (as a page scrolls down), at most 10 either
way; the page adds up the notches turned while a point waits its turn (one every 200
ms), so none is lost. Anywhere else the wheel is the visitor's page's, as before. The visit-client clamps
`wheel` with the rest (a whole number, at most 10 either way; none, or 0, is a click) and counts a wheel point
among the 5 a second. A user page (engine/SPEC.md 7) sends one with `world.point(target, { wheel })`.

**Interactions** (Visit 0.9) are what a point may do, by type, and only what the host takes. A host lists them
(`v.interactions`): each type its own entry, `cosmetic` or not, with a `label`, who may (`"everyone"`, its
`"guests"` (3.3: those in a world with it), or, Visit 2.1.1, `"owner"`: its own user alone, at home), how far (`reach`, in its units), whether the
wheel runs it too (`wheel: true`, Visit 2.1.1) and what it does (`run`). It marks what may be clicked with the
type, `userData.visitPoint = "<type>"` (on an object, or a group it is part of). On a point the library casts the
ray itself, from the visitor's eyes: the first thing it meets that is not a visitor's body (nor hidden), then the
type on it or on what it is part of; if the host takes that type, this visitor may, it is within reach, and (for
the wheel) the type takes the wheel, its `run` gets { visit id, visitor, type, object, point (where the ray met
it), distance, wheel (the notches; 0 for a click) }. A type the host does not name does nothing, and a type
without `wheel: true` nothing on the wheel. The whole scene (and an edit, when they change) says which types are
taken, with their labels and whether each takes the wheel, an owner's own (`"owner"`) never among them: the
visitor's crosshair shows hot over something marked with one, its label beside it, and over nothing else.
(`userData.visitPoint = true`, an older host's mark, shows hot without a label; the host's page hears the point,
`point`, and decides by its own lights.)

**Cosmetic** means the interaction changes only how the room looks or sounds to the people in it: something
moves, lights up, plays, spins. Anything else (an agent's work, a tool, a file, an account, a message to anyone,
saved state beyond the room's looks) is not cosmetic, and **the library refuses to register it unless the host
says its user agreed to that one** (`agreed: true`). What a point may do is a guest's reach into the room: a host
takes points only through this list, whose default is no.

### 8.4 Worlds

An office's world is its own room with its friends' plots round it, drawn by its own page (`v.world`, section
9). Nobody shares a map: each office has its own layout, and walking between plots may look different to each.

- **Who is in it** (3.3). With the world on, a visit-client watches the plots its user picked (`pick`; at most 8,
  in the order picked): of those online whose lobby entry says `world`, those whose own visit-clients let it (it is
  their guest, in a world with them, their door open). Nobody's plot is in it by itself: being in a world with
  someone makes their room one to pick. (Before 3.3, a guest's plot and every world member's were watched by
  themselves.) A watch is a visit with
  nothing from the watcher: the host sends it the scene and its deltas (version 2) and does not spawn it.
- **Plots.** A host says its plot (`plot`, `unit`: 8.1); one that does not is taken to be what its scene covers
  (at most 200 units a side). An office's own plot sits at home, in its own room's coordinates.
- **Layout**, for each plot watched: where the office placed it (`place`: its middle at `x`, `z`, turned `turn`
  quarter turns left); else it goes by itself in a ring round the office's own, in the first of eight places
  (from one its key picks) where it overlaps nothing, `gap` (4 units) apart. A plot is scaled by its `unit`
  against the office's own. Nobody agrees to it: it is the office's alone (Visit 0.7 to 0.9 also had edges, a
  side agreed by both; gone in 0.10, and an older office's edge messages go unanswered).
- **Drawing.** The office's room is drawn clipped to its plot; then the ground between plots; then each plot,
  its scene read with the office's own three.js release (not its own), clipped to its rectangle, into the same
  depth buffer, lit by its own lights only (each plot a scene of its own), with its fog. A plot lit before r155's
  way (lights times pi) drawn by a page lit after it has its lights' strength times pi and its bulbs not dimming
  with distance, and the other way over pi: approximately its look. The watcher's own body, which the host draws
  for others, the watcher's page does not draw: it draws itself. A friend's plot wholly out of the camera's view
  (its rectangle, as high and low as its scene reaches) is left out of the frame, its shadows too, since they only
  land inside it; and a friend's plot's lights work out their shadows every 250 ms, not every frame, each plot on
  its own beat. The office's own room and the plot its user is in are always drawn, with every-frame shadows
  (`v.world({ cull, shadowMs })`; `world.drawn` says how many plots a frame drew and left out).
- **Walking.** At home the page walks its user by its own walls. When where it has them is inside a plot's
  rectangle it steps in (`enter`, where, turned into that room's terms; the host's `spawn` hears it as `at`). In,
  the host has them: their input goes to it, its `yaw` the look in the host's terms (not relative to an
  arrival: the host's `yaw0` for a watch is 0); the page walks them at once by the host's `walk` and takes its
  answers (8.1). When the host has them off its plot (by more than 0.3 of the office's units), they step out
  (`exit`) and walk on at home from there. Refused, or no answer within 3 s: back where they were before.
- **Seen.** The page tells each friend in its world where their plot sits (`seen`: their middle from its own,
  in metres, their turn, and its own plot's size), when that changes, and that they are no longer in it when
  they leave it; nothing else of its world, nor who else is in it. Its visit-client keeps the latest for each,
  tells it over any link with them, and again over each new one. A friend's page has where it is in each world
  that has told it (`v.seen`), until their last link goes. A plot whose door shuts on the world leaves it.

### 8.5 Links (gone in 4.0)

Visit 0.8 to 3.x let an office knock on anyone in the lobby, and link: let in, the knocker saw the room in its
stage's corner, and went into it, put it in its world, or not. 4.0 (#57) has neither: worlds are the way to meet.
Two people in a world are each other's guests: each picks the other's room for their world (`pick`), walks onto its
plot (`enter`, 8.4) and back, through the other's open door; a closed door lets nobody in. Talking needs no room in
common (8.7, `v.talk`). A version-4 office's knock to walk in, or to link, is refused (`not_supported`), and one an
older lobby passes on is declined without asking the page.

### 8.6 Avatars

A visitor is shown in a room as its own body (its own model: the default since 0.16), or as the room's own model.
How to make one, and every point it is measured by: [MODELS.md](MODELS.md).

- **Your own** (`v.avatar(object, { unit })`): a three.js object of its own, its origin at its feet, facing −z,
  in metres (or `unit` metres each), an `eyes` point if you like (else 92% of its height), clips named `idle`,
  `walk` and `ride` in its `animations` if it has them. Checked as it is given (`Visit.checkModel`). The
  page gives it to its visit-client as a binary frame (5.4: its JSON, `{ unit, json }`, `json` its `toJSON()`
  with only what Visit reads of `userData`, gzipped, at most 4 MiB), which keeps it in memory, for the hosts
  that ask.
- **Whose look** is the host's to choose, kept in its state file: its visitors' own avatars (`"theirs"`, "their
  model": the default) or its models (`"mine"`, "our model"), for everyone (`avatars`) and for any office apart
  (`avatar_use`, by key; the panel: that person's row). A state file from before 0.16 said `"mine"` whether chosen
  or not: read as `"theirs"`, until the user chooses (`set`).
- **Asking.** A host's page asks a visitor for its avatar (`avatar_want`) when it would show it; the visitor's
  visit-client sends it (7.4: stream 2), now and whenever its page gives a new one, or says it has none
  (`avatar_none`).
- **Visit draws it** (0.19). The host's page reads it with its own three.js, as data (`cleanScene`, at most
  300 000 vertices and 2 000 objects), scales it to its units, marks it as the visitor's body
  (`userData.visitor`), puts it in the room's scene at the visitor's feet, facing its way, and plays its `walk`
  while it moves, its `idle` while it stands, its `ride` on a mount. In the scene, it goes to every visitor; the
  visitor's own eyes do not see it. The room draws its own model for a visitor only where `v.visitors[i].look` is
  "mine".
- **Its eyes** are its `eyes` point (else 92% of its height) while the room shows it, else the room's `eye`: the
  host tells the visitor (`body`), and places its camera there.
- **Said, not swallowed.** A model the room would show and cannot (none given, or it does not read) is shown as
  the room's model, and both sides say why: the host's `v.visitors[i].problem` and panel, the visitor's `body`
  event and panel.
- **Acts** (4.0, #59, #60). A room acts out its interactions on a visitor's own model: `v.perform(visitId, clip,
  { secs, loop })`, `clip` one of `sit`, `lie`, `drink`, `eat`, `throw`, `swat`, `wave` (MODELS.md: any model works in
  any room, one without the clip playing its fallback: `sit`, `ride`, else `idle`; the rest, `idle`), or null. `sit`
  and `lie` last until the visitor moves, or the room says otherwise; the rest play once, unless `loop`; `secs`: at
  most so long. Visit plays it on their model in the room (so everyone there sees it), `v.visitors[i].act` says it (a
  room that draws its own figure for them, `look` "mine", poses that the same), and the visitor's own page is told
  (`perform`: the link's control `{"perform": {"act", "hold"}}`, 7.2, to the page): its world's `frame` says `act`, and
  `sit`, for a page that draws its own body there. Nothing else crosses the wire: what the room plays is in its scene.
- **Held things** (4.0, #59). `v.hold(visitId, object)`: a thing of the room's scene in a visitor's hand, their
  model's `hand` point (MODELS.md; without one, in front of their chest), moved there as they go, until another, or
  null (out of the scene). It is part of the room's scene, so everyone sees it, the visitor too (it is not their
  body). `v.visitors[i].holding`, and the visitor's `frame` (`holding`), say so. Stepping out of the room ends both.

### 8.7 Voice

Voice goes by where people are: nothing to join. Those in one room hear each other the nearer the louder, from
where each stands, and nobody farther than 45 m away (`voice_range`). Two friends each at home in their own room
hear each other across a plot's side (Visit 0.20) when both worlds have them near: each world is its own, so each
side's own world must put the other within reach. A room's voice (0.20) is the one thing joined: as a game's voice
chat, everyone in it hears everyone else, wherever they are.

- **Across a plot's side.** A page at home tells its visit-client where its world has each friend whose room is a
  plot in it, at home there (`voice_where`'s `world`: their room's `visit:me`, placed as the plot is). Its
  visit-client tells that friend whether it has them within reach (`world_near`: 45 m and the 15 m margin). When
  both say so, each sends its user's voice to the other, and each hears it from where its own world has the other,
  by its own world's distance (`voice_near` says so, `world`). Neither user in another's room; else as above.
- **One way a person.** Two people may reach each other more than one way (standing near each other and on a
  call, in a room's voice and near): each hears the other one way only, the first of: in person (in the same room,
  or across a plot's side, within 45 m), on a call, in a room voice. The listener's visit-client chooses, by what it
  passes on (4.3.2): a call or a room voice waits while that person's voice goes to the same page in person, and
  goes again once it stops (their microphone off: a huddle or a call runs it, and in person they are heard only
  while it is on); a voice comes to the page saying which (5.4), and the page plays the first it hears from where it
  is. So no voice is heard twice, behind itself, and none goes silent for being near. Someone on a call with you, or
  in your room voice, near you in person, is heard from where they stand as loud as that way, not fading with
  distance (4.3.2).
- **A room's voice.** Every room has one; whoever is in its chat (8.9: in it, or linked with it) may join it
  (`v.joinVoice`, the chat window's 🎧), one room voice at a time. Its owner's visit-client carries it: a member's
  page sends its voice there, and it passes each on to everyone else in it, as its owner's own. In it,
  everyone hears everyone else, not by distance, while their microphones are on. Its owner may take one out
  (`v.voiceKick`), or keep them out (8.9). The page hears who is in it (`room_voice`, `voice_members`).
- **Talking** (4.0, `v.talk`; the chat window's 🎧 with its box on a whisper). Two users, voice alone, wherever each
  is, with no link between their rooms needed: a knock to talk through the lobby (6.4) rings on the other's page,
  which answers or not (never its visit-client by itself, never for someone its user ignores or keeps out of their
  room's voice). Let in, a voice visit: a link with no scene, no corner, no plot, for the person's page only, a call
  from its first moment (as 8.8's, to the ear, never on a phone's speaker; heard one way a person, as below). Each
  side's visit-client takes one only if it asked for it (its page's knock) or let it in (its page's answer, with the
  key it answered), and ends any other before a sound goes: a lobby cannot make one up. Either side ends it (`end`,
  or `call` `hangup`). Over it go only who is talking (`user`, `banned`) and a room's voice.
- **A huddle** (4.0, `v.huddle`; the chat window's 🎧 with its box on a world). Its starter's room's voice carries it:
  started, they are in it, and the world's members online hear it start (it rings for them). Joining (`v.joinHuddle`)
  is a knock to talk on its starter with `huddle`, let in by their visit-client itself (a member of that world, not
  ignored, not kept out of its voice), whose voice visit puts the joiner in that room's voice. Leaving that voice
  ends the joiner's voice visit; its starter leaving it (or ending the huddle, or leaving the world) ends the huddle,
  and with it every voice visit that joined. A huddle runs its members' microphones, as a call does.

- **The microphone** is each user's to turn on (`v.setMic`, the panel's 🎙), remembered in their browser (on again
  at the next load, once permission is given); off, they still hear. A call, talking, or a huddle runs it while it
  lasts, on or off otherwise. It is asked for with echo cancellation, noise
  suppression and gain control. Nothing is encoded or sent while nobody is near, nobody shares a room voice, and no
  call is on.
- **Where people are.** Each room's page tells its own visit-client where everyone in it stands (`voice_where`): its
  own user while they are at home (where its world, or `v.listener`, has them), its phone, and each visitor (where
  the room has them: it moves them, so it is the room's word, not the visitor's). It also keeps an empty object
  named `visit:me` in its scene where its own user stands at home (hidden while they are away), and marks each
  visitor's body (`userData.visitor`), so that a visitor's page knows where each voice comes from.
- **Who hears whom is decided by the room's visit-client**, the same for every room, not by its page: each office
  decides for those connected to it. The room's visit-client passes a voice on to whoever is within 45 m of the
  speaker, plus a 15 m margin (so it fades in rather than starts): its own page, if its user is; and each visitor,
  the owner's as it is, another visitor's passed on (as the visitor's, by their visit's id there, and their name). It
  tells each in the room who is near them (`voice_near`, `near`), so a page with nobody near sends nothing.
- **Over WebRTC** (4.2, #71). Each page connects to every office it speaks to or hears from, as its visit-client says
  (`rtc_want`): its own office, always; the room its user is in; a call's other side (both ways: on speaker, those
  near that phone are heard from there); a friend across a plot's side (one link with them, one with a call on
  first); the room voice it joined (a huddle's by the voice visit that joined it, 4.3.2: never with the page's voice
  in person in its starter's room, passed on there to whoever is near). One RTCPeerConnection each (`rtc_offer`, answered by that office: its own over the
  page's WebSocket, another's over the link with it, `{"rtc_offer"}`, whose key the link has proved), all of an
  office's on one UDP port (`--media-port`). One no longer named goes silent, and is closed 5 s on unless it is named
  again meanwhile (a call that rings again: no gap to connect again); one that fails is made again 3 s on. Nothing of
  voice goes over a link. A page sends its microphone on a
  connection only by the ways its user is heard there (`room`, `call`, `voice`): in person while it is on (`mic`), on
  a call always, in a room voice while it is on or for a huddle; each office passes it on to the pages connected to
  it that hear it, as above, and as it would any other.
  - **A connection** offers the microphone (one sendonly audio m-line) and 12 slots (recvonly audio m-lines), and a
    data channel, `visit`. A slot carries one speaker at a time; the office says who on the data channel, as the slot
    is given: `{mid, ch?, relay, name, route, key}`: the speaker's office's key (`key`), their name, how they are heard
    (`route`: 0 in person, 1 a call, 2 a room voice), passed on as whom (`relay`: null, the office's own or its link's
    page's; a visitor's id in that room; `"phone"`, a call on that room's speaker; a room voice's speaker's key), and,
    from the page's own office, the channel it came by (`ch`). A slot is its speaker's while its packets come (600 ms).
    Packets are forwarded as they come (nothing held back or reordered there), each slot's sequence numbers and
    timestamps its own, carried on across a change of speaker, every loss on the way still a gap: the browser's own
    jitter buffer and loss concealment play it, and decode Opus's in-band FEC.
  - **One way a person**, as above: each office passes one speaker on once to each connection (in person first: a
    call or room voice of theirs not while their voice has gone to it in person in the last 200 ms, 4.3.2); a page
    plays one speaker by one slot only, the first of in person, a call, a room voice that it hears now (4.3.2: in
    person only from where it has them, within 45 m; else the next).
  - **Its servers**: the lobby's STUN and TURN (6, `ice` in its welcome). An office asks its STUN server where it is
    seen from outside, so a page reaches it from anywhere (its main interface's address, and that); a page connects
    by both, and by TURN where no direct way goes (a phone's mobile network, a strict NAT). Its TURN login is its
    office's, good while that office is in the lobby (and ten minutes after).
  - **A call on speaker on both sides**: those near each phone hear the other side's caller, not those near the other
    side's phone (before 4.2, they did).
- **Hearing.** A page plays each voice from where the speaker is (Web Audio's HRTF panner, linear to nothing at 45
  m): at home, its visitors where it has them; in another's room, its owner from `visit:me` and the voices passed on
  from their bodies. Visit marks each one heard speaking with a 🔊 over them (Visit 0.13), in the views it draws: a
  world (`speaking: false`, for a page drawing its own from `v.voices`) and another's room.
- **The voice**: Opus over WebRTC, 48 kHz mono, at most 24 kbps (`maxBitrate`), with its in-band FEC; the browser's
  own jitter buffer and loss concealment play it (before 4.2: WebCodecs, sent over the link as datagrams, played
  60 ms behind).
- **Older offices** (Visit 0.9) decided in their pages, by 15 m: a 0.9 room's visitor on 0.10 sends to it and hears
  what it sends, and a 0.9 visitor in a 0.10 room is heard and hears as the 0.10 room decides.

### 8.8 Phones

A phone joins two rooms' users wherever they stand: voice to voice, not by distance. A room may have one; Visit
carries calls, and the room shows the phone (where it stands, its ringing, picking it up).

- **A phone** (`v.phone(true, { at })`): the room says so on every link (control `phone`), and keeps an empty object
  named `visit:phone` in its scene where it stands. A user calls only those with a visit between them, either way (their plot in the other's world, or in their room: 4.0; anyone else, talk, 8.7)
  whose rooms have phones (`v.phones`). Visit puts a phone there in the room's scene (Visit 0.13: `visit:phone-model`,
  a stand, its handset at `at`, a lamp), so everyone sees it: shaking with RING RING over it while it rings, its
  handset off its cradle while its user has it to their ear, its lamp lit on speaker. `model: false`: the room draws
  its own. The panel calls (📞 call, a button for each of `v.phones`), picks up, and hangs up.
- **A call**: `ring` (`v.call`), `answer` (`v.pickUp`), `hangup` (`v.hangUp`), as control messages on the visit
  between them; unanswered, it stops ringing after 30 s. While on, each page sends its voice to the other's office
  (8.7: over WebRTC), which passes it to its user (the microphone runs for the call, on or off otherwise).
- **The handset** (as a call starts): only the user, to their ear, sounding as a telephone does.
- **Speaker** (`v.speaker(key, true)`, each side its own; the page tells its visit-client, `speaker`): the call's
  voice comes from the phone, heard by everyone within 45 m of it in that room by distance (the room's visit-client
  passes it on to its visitors near the phone, as the phone's, `relay` `phone`), and everyone that near the phone is
  heard on the call (the room's visit-client passes its visitors' voices to the other side's page, connected to it
  for the call); the user, only while near it. The other side hears them all, each apart.

### 8.9 Chat

As in a game's chat, a line goes one of five ways (its scope). Nothing to join but a world (6.6).

**Who says it.** A line, a voice, a room voice's member, and who is near are named by the name their user goes by
(Visit 0.20.1: `v.setName`, given to every office linked with theirs, `user`), else by their office's. A room is
named by its office's ("Gooshy's room"). So are the people in a room as its host has them (4.3.0: `v.visitors`'
`visitor.name`, its owner too; their office's beside it, `visitor.office`): a label over a visitor says who they are,
and the room they come from is theirs to say after it. A lobby line or a whisper through the lobby is named by the user's own name
when their office is linked with this one, else by their office's (the lobby knows offices only).

- **Say** (the default; 3.4.2, #54: by distance, as voice is, 8.7): whoever would hear its speaker's voice in person:
  everyone in the room they are in within 45 m of them (`VOICE_RANGE`), and, for a user at home, a friend near them
  across their plots' side (near in both worlds, as voice has it: `seam_said`). Nobody else: not someone farther in
  the same room, not a watcher whose world shows the plot (unless near across the side), not a corner. It shows over
  the speaker's head, and in the chat, of those who have it. A page says its user's line to its own visit-client
  (`say`, `v.say(text)`); it goes to the room the user is in: another's (to its owner's visit-client) or their own.
  The room's visit-client gives it, by where the room has everyone (`voice_where`), to each visitor near its sayer
  (`said`), but the one who said it; a room that has not placed its sayer, or someone, gives it to them as before
  (everyone in it and watching it). A visitor's line goes no farther than the room (never across its plot's side).
  The room's own page has every line said in it (`chat`; the room's logic may want them): one its user is not near
  (more than 45 m off, or not in it) marked `far`, which a page shows nowhere (no line in its chat, no bubble, not
  kept: visit.js gives it as "said_far"). A visitor's line names them by their visit's id there; the owner's says so
  (`owner`). With nobody near, a line is said all the same, to nobody (as in a game). Wider: `/room`, `/w`, `/1`, `/g`.
  A room's own figures say lines too (a receptionist, a guide: none of them a person), from its page
  (`say_as`, `v.sayAs(id, name, text)`): in that room alone, under the room's key and the figure's own name, with its
  id (`npc`). It shows over the object named `visit:npc:<id>` in the room's scene, and goes as a person's line does
  (4.0.1): visit.js says where that object is (`at`, in metres, as `voice_where` has everyone), and the room's
  visit-client gives the line to each visitor in the room within 45 m of it (one it has not placed, or a figure with
  no object: all the same), and to nobody outside the room: not a world member whose world shows the plot, even near
  its side. The room's own page has every line (`far` when its user is not near). Never as the room's user: each figure has its own limit, and its line is never theirs. On the engine
  design it is the room page's to say (../engine/SPEC.md 3.3), and the owner has it once, as everyone in the room does.
- **Whisper** (`v.say(text, { whisper })`, an office's id or key): one person, the user of an office in the lobby
  (`v.whisperTargets`: those whose Visit takes whispers), wherever they are, in their chat alone. The lobby passes it
  on (`whisper`), as a game's server does, and keeps nothing; linked or not.
- **A room's chat** (Visit 0.19.1), as a game's party: every room has one, and everyone in the room, and every
  office linked with it, either way (in a corner, in a world, visiting, visited), has every line said in it, wherever
  they are. Its owner says a line in it with `v.say(text, "room")`; one linked with it, with `v.say(text, { key })`
  (`v.chatTargets`: the rooms linked with yours). It shows in chat, not over a head. A line into another's room goes
  over the link (`sim_say`) to that room's visit-client, which gives it to its page (`chat`, `scope: "sim"`,
  `home`). That visit-client gives every line in its room's chat (its owner's, and those said into it) to every
  office linked with it, once each, but its sayer (`sim_said`: on its visit, to one in the room); each gives it to
  its page (`chat`, `scope: "sim"`, on that link's channel: the room is the office at its other end). Before 0.19.1,
  only those in the room had a line said into it, and its owner could say none: an older Visit has lines in a room
  only while in it.
- **Lobby** (`v.say(text, "lobby")`): everyone in the lobby whose Visit takes it, in their chat. The lobby relays
  it (`chat`, `lobby_chat`) to each office whose `hello` said `chat`, the sayer's too, in one order, and keeps
  nothing. A lobby that carries it says so in `welcome` (`chat: true`; the page: `v.lobbyChat`).
- **A world's** (3.0.0, `v.say(text, { group })`): everyone in one of the user's worlds (6.6), in the lobby,
  in their chat; the lobby relays it (`group_say`, `group_chat`), the sayer's too, in one order, and keeps nothing.
  Text only. Its conversation is `group:<its name in lower case>`.
- **Checked everywhere.** Every visit-client (and the lobby) checks a line again: plain text (control characters
  become spaces), at most 200 characters, at most 5 lines every 10 s from each, whatever their scope.
- **Showing.** A line said shows over its speaker a few seconds (4 s, and longer the longer it is: at most 12 s),
  until they say another. Visit draws it in the views it draws (`Visit.bubble`, a card over the head): another's
  room, from in it or outside it (over its owner's `visit:me`, over visitors' marked bodies; never one's own, from
  one's own eyes) and a world (`v.world`: at home over its user and its visitors; on each plot, over its owner and
  its visitors; and over its user). A room without a world draws them itself, from `v.chats` and the `chat` event.
- **The chat window** (the page's own since 5.0, UI.md; as the reference UI's `VisitUI.chat` has it): bottom left, as in a game. Enter to type a line, Enter to say
  it, Esc to stop; `/` opens it with the `/` typed, for a command. Where a line goes is before the box (Say, Tell
  <name>, To <name>'s room, Lobby), picked there or with `/s`, `/w <name>` (`/t`, `/tell`), `/r` (back to whoever
  last whispered to you), `/room <name>`, `/1` (`/l`), `/g <group>` (3.0.0; `/2`, `/3`… the user's groups in order) and a space, and it stays for the next line. Every line of
  every scope shows in it, coloured by scope (say white, a whisper pink, a room's blue, the lobby's gold, a group's
  green), a room's, the lobby's and a group's as channels (`[Gooshy's room] [Ann]: hi`, `[Your room] [Ann]: hi`,
  `[Lobby] [Ann]: hi`, `[Hollow] [Ann]: hi`). Joining the lobby, it says the lobby's name and message of the day. As in
  WoW (0.20.3), whoever said it is in their own colour (one of seven, by their key: the same on every page, as WoW
  colours a name by its class), and a line too long for one row goes on indented under its first. `/room`
  alone is your own room's chat ("To your room", among the choices while anyone is linked with you); `/r`, with
  nobody having whispered to you, is whom you last whispered. The names the box could take are over it, to pick
  (up and down, Tab or Enter, or a click): after `/w` or `/room`, whom it goes to; after `@` (0.20.1), whom the line
  mentions (those linked with you, your visitors, those in the lobby, by the names they go by). A name typed is
  taken once a space follows it, or on Enter (all of it, or the one name it starts). Tab, when no name is to
  finish (0.22.0), moves the box on to the next place a line can go now, in the choices' order and round again
  (Shift+Tab: back), and keeps what is typed: Say, one Tell (whoever you last whispered with, either way, while they
  take whispers), your room and each linked room, the lobby (while it carries chat), each group; not while a command is in the
  box, which says where itself. 🎙 by the box (0.22.0) records a voice message (🎙 again, or Enter: done; Esc: none;
  a minute at most), which waits over the box as a picture does and goes with the line; a line's voice message
  shows as ▶ and how long, and plays on a click. Each line has the time it came before it, dim, as WoW's chat
  timestamps do (0.22.0; the reader's clock and its locale's way of writing a time), and a day's first line has its
  day over it when the log holds more than today. It fades 20 s after the last line, and comes back with the next
  one, or Enter.
- **The mailbox** (0.22.0). A line meant for this office's user (whispered to them, said in their room's chat, or
  mentioning them; never their own) that comes while nobody is looking at their chat is unseen until they have
  read it: the visit-client marks it in its database (8.12), so a reload or a restart keeps it, and tells the page
  (`unseen`). Looking is the page's to say (`attention`); the chat window says so while its page is shown and
  focused and a key, a click or the pointer was used in the last 2 minutes. The window shows 📬 and how many, even
  while it has faded; a click lists them (each with when, its day too if not today; a click on one: the box talks
  there) and they are read
  (`read`), as are those of a conversation the box answers into.
- **Left for someone away** (0.22.0). A whisper to someone not in the lobby (by their key: someone the user has
  whispered with, or a guest (in a world with them); `/w <name>` finds them too, "Tell <name> (away)") is kept by the sayer's own
  visit-client, in its database, and passed on through the lobby, as any whisper, once they and it are both in it
  (`held`, `held_sent`). The lobby still keeps nothing. Text only: pictures and voice messages go over a link, with
  someone linked. The chat window shows ⏳ and how many with the 📬; its list has them, each to take back (✕).
- **Mentions** (0.20.1). A line with `@` and the name its reader goes by (or their office's), as a word of its own,
  mentions them: their page marks it (`mention` in the `chat` event's line), the chat window shows it apart and
  chimes as for a whisper. Whoever reads a line decides, from its text: nothing more goes with it.
- **Keeping people out** (0.20). A room's owner keeps anyone out of its voice, its chat, or both (`v.ban`; the
  panel's person row: ban voice, ban chat), kept in its database (8.12) until lifted. Out of its voice: their voice is
  not heard in the room (in person, across its plot's side, or in its voice), they hear none of it, and they are
  out of its voice. Out of its chat: their lines are not said in it (in the room or into it), and they get none of
  its lines. Their own visit-client is told (`banned`), so it refuses those lines, and joining that voice, itself.
  Calls are apart: they ring, the owner answers or not.
- **Ignoring** (3.3, as WoW's ignore list). A user ignores whom they will, for themselves alone: a person by their
  office's key, or one of a room's figures by that room's key and its id (`npc`), so one room's guide is not
  another's. Their lines never reach the user's page from then on, whatever their scope (said, a room's chat, the
  lobby, a world, a whisper): their visit-client drops them before its page or its database has them, so no bubble,
  no mailbox, no mention; and the lines kept from before are left out of `chat_log` and `history` while ignored.
  Nothing tells them. At most 50, kept in the state file (`ignore`, `unignore`, `ignore_clear`; `ignored`). The chat
  window gives a name a click (Whisper, Ignore or Unignore) and takes `/ignore name`, `/unignore name` and `/ignore`
  (whom); the panel lists them, each to let go, and Clear. Voice is not ignored: a room's bans are for that.
- **Targets** (3.3, #48: the plumbing; each world decides the rest). A user may have a **target** in the room they are
  in: one of the people there (by their office's key) or one of its **figures** (sayAs's). It is their page's alone:
  nobody else is told (`v.target`, `v.setTarget`, "target"); it goes when they leave that room, or it does. Who picks
  it: the page's own code, any way it likes; or Visit, the way the room says (its host's `targeting`, sent with its
  scene: `"click"` (default), a click on someone; `"near"`, whoever is looked at within 4 m, with 2.5° to spare, kept
  until another is or it is 8 m away; `"none"`), for anyone in it, its owner too, unless their page picks its own
  (`v.targeting(false)`). A room describes its figures with data that goes with its scene (`v.figures`: `id`, `name`,
  `title`, `about`, `actions`, the interaction types a card offers, and `talk`, whom it answers: `"everyone"` (the
  default), `"owner"` (its room's user alone) or `"none"`, a hint the card and the `@` picker follow; at most 32, 8 KB,
  never code). What shows is each
  world's (5.0: the page's own, UI.md): the reference UI's card has the name, title, line, actions (`v.actionsOf`),
  Say, Whisper and 🎧 Talk; `card: false`, none. **Lines meant for a figure**: said aloud with it as the line's `target` (`v.say(text, { target })`;
  the chat window's `@` offers the room's figures, and a line that mentions one is meant for it), everyone who can see
  the sayer has the line, and the room's page has `addressed` too; or whispered to it (`to: { figure }`), the room's
  page alone has it (`addressed`, `whisper`), the sayer keeps it as their own ("To [it]"). The room's page (on the
  engine too, whether or not its user's page is open) answers with `say_as`: aloud, or to one person alone (`to`: a
  visit's id, or "owner"), a whisper from the figure that only they have, kept as theirs, with no bubble. Whom its
  figures answer is the room's own: Visit only carries the lines (an answer that costs the room something can be its
  owner's alone). A figure's action from a card (`act`) runs as a point at the figure would, by its type's own rules,
  and only if the figure lists it. Whispers to a figure count with the user's own lines (5 every 10 s).
- **Kept** (0.20). A visit-client keeps every line its user has had, theirs too, in its database (8.12), and gives
  the last 100 to a page that connects (`chat_log`): a reload, or a restart, keeps the chat, with the lines that came
  while it reloaded. Older lines: the chat window's log, scrolled to its top (`history`). They come as `chat` events marked `old` (no sound, no bubble), before the
  window's first note; the window keeps where lines go across a reload too (this tab).
- **Text is text.** A page shows a line as text (a canvas, `textContent`), never as markup.

### 8.10 Landscapes

The part of a room that never changes (its ground, walls, trees, furniture) is its **landscape**: sent once, kept
by those who visit, and drawn while its office is away. A room marks it: `userData.visitStatic = true` on an
object makes it and everything under it landscape (the marker itself stays on the host's page).

- **Apart.** The host's page leaves the landscape out of its scene's rounds (no deltas, no edits: nothing of it is
  looked at 30 times a second) and out of the whole scene it sends (which says, `landscape.parents`, under which
  object, by number, each landscape object goes; an edit says so again when they change). It makes the landscape
  one JSON of its own: three.js's JSON of a group of the static objects in the scene's order, every uuid named
  anew in order of first appearance (`L1`, `L2`, …), so that the same landscape is the same JSON at every load, and
  gives it to its visit-client (5.4, bit 5) a second after what is static last changed. Something static that
  changes in itself, the room says so (`v.landscapeChanged()`).
- **Sent once.** The visit-client names it by its hash (blake3 of the gzipped JSON) to every visitor (`landscape`),
  and sends it (stream 3) to one that asks (`landscape_want`): one that does not have it. A visitor's visit-client
  keeps each office's latest on disk (`--cache-dir`), by that office's key and the hash, and gives it to its page
  (`landscape`), which puts each object under its parent in the room. A visitor on an older Visit gets the room
  whole, its landscape in it, and again whenever what is static changes.
- **Kept while away.** A picked plot in a world (8.4) is kept with where it was (`plot_keep`): while its office is
  away (offline, its link lost) the world draws its last landscape there, named "offline", lit softly if it has
  no lights of its own, and its user walks into it as at home, against its walls (they are walked into, not
  through) since no host is there to walk them. It goes live again when its office comes back. It is let go when
  its office says so: it ends the watch itself (`kicked`: its door shut, a guest no longer), declines one, or this
  office takes it out of its world (`unpick`).

### 8.11 Possessions: mounts

What an office's user owns and brings along to the rooms they go to. For now, **mounts**: something to ride, on
the ground or in the air. Nothing to join: a room says nothing, and its visitors ride.

**What they are is each room's to say.** Visit has none of its own: it carries what a room makes (its models,
names, paces), puts it under its rider in the room they are in, and leaves the deciding to rooms: the rider's
(what there is) and the host's (what it lets in).

- **A room's own** (`v.possessions([{ id, name, object, unit, mount, fly, speed, lift }])`): a three.js object
  each (its origin at its feet, facing −z, in `unit` metres; clips named `idle`, `walk`, `run` and `fly` in its
  `animations`, if it has them); `mount`: it can be ridden; `fly`: it flies; `speed`: times walking pace (1);
  `lift`: how far up its rider is, in metres (0). Its visit-client keeps the list and the models (in memory, at
  most 8, each as an avatar may be); every host this office visits is told the list (`possessions`), and asks for a
  model when it would show it (`possession_want`; 7.4, stream 4).
- **Riding** (`v.ride(id)`, null to get off; `v.riding`; M, the panel's): what the user rides goes with their
  input (`riding`) wherever they are; at home, their own room's to show (below).
- **The room decides.** A host's page takes it (`riding`) if it is one of the visitor's possessions that is a
  mount, and the room does not keep mounts out (`mounts: false`). Then:
  - the mount is in the room's scene, under the visitor (`mount:<name>`, its `userData.visitRider` the visit's
    id: the visitor does not see it from its own eyes, nor click it; in its world, it sees it under itself),
    under the rider's feet (their pose is their eyes, `eye` metres above their feet: 1.6 unless the room says),
    `lift` below them, facing the visitor's way, animated as it goes (in the air, walking or running, still);
  - the room's `move` is told (`who.riding`: { id, name, fly, speed, lift }) and does the rest, as its physics
    have it: lifting the rider onto it (`lift`), at its pace (`speed` times walking), flying it (`fly`, `up`), or
    none of that. Visit does none of it.
  - Its entry in `v.visitors` says it (`riding`: { id, name, fly }); a visitor's own avatar (8.6) plays its
    `ride` (or `sit`) clip, if it has one.
- **At home** your world (8.4) draws your mount under your user's feet (`me.y`; facing `me.facing`, else where
  they look), so your visitors see it too. Your page lifts your user onto it (`v.rideLift`, metres), walks them at
  its pace (`v.rideSpeed`), and flies them if it flies (`v.rideFly`; `air`, 8.2).

### 8.12 What a visit-client keeps: its database; pictures and links in chat

A visit-client has a database (Visit 0.20): from 2.0, the Postgres inside `visit`, one per workroom, in its data
directory (4.4), in a schema of its own (`visit`) as a role of its own. It keeps there what its user has had, never
anyone else's:

- **The chat**: every line its user has had (8.9), their own too, as their page had it, by conversation: a room's
  (`room:<its owner's key>`: said in it, or in its chat), a person's (`person:<their key>`: whispers), the lobby's.
  The page gets the last 100 when it connects (`chat_log`), and older lines as it asks (`history`).
- **Pictures shared** (`media`), each once, by its id: the blake3 hash of its bytes (64 hex characters). A PNG, JPEG,
  WebP or GIF of at most 8 MiB, by what its bytes are (not what it is called). **Voice messages** (0.22.0) as pictures
  are: a recording, WebM, Ogg or MP4 (as browsers record), of at most 2 MiB (a minute), by what its bytes are; a line
  names it with how long it is (`secs`, at most 60), one a line at most.
- **What its user has not read** (0.22.0: the mailbox, 8.9), marked on the lines, and **whispers left for someone
  away**, until they go (30 days at most).
- **Links shared**, with the preview their sender made (`links`).
- **Whom its room keeps out** (8.9; a state file of the release before kept them: they move once).

What was shared in a conversation, the page asks for (`shared`): its pictures, its voice messages, or its links, newest
first. `visit` makes its schema, and brings it up to date after an upgrade, at every start (4.4); a 1.x workroom's
is moved in once, by `visit import` (4.5).

**Pictures** (and voice messages, which go as they do). A page shares one (`v.share(blob)`: pasted, dropped, recorded) as a binary message (5.4, bit 7); its
visit-client checks it, keeps it, and says its id (`media_ready`). A line names its pictures (`say`'s `media`:
`{ id, mime, size }`, at most 4) and goes as any line does, its pictures named with it: in a room, into a room's chat,
or, for a whisper with pictures, over a link with that person (`whisper`, not the lobby). The lobby carries text
only: a line to it with pictures or links is refused (`not_supported`), and so is a whisper with them to someone
not linked (`no_link`). A visit-client that gets a line naming a picture it does not have asks the link the line
came by for it (`media_want`), once; that side has it (it said it, or passed it on and is getting it) and sends it
(stream 5). Meanwhile a page asking for it (`media_get`) waits. One not had in a minute is said none (`media_none`).

**Links.** As a link is typed, the page asks its own visit-client for a preview (`preview`): it fetches the page
(http or https; 6 s at most, 4 redirects, its first 512 KiB) and reads what it says of itself (its Open Graph
title, description, site and picture, else its `<title>` and description), and fetches its picture (at most 2 MiB:
kept as a picture). The line carries the preview (`links`: `{ url, title, description, site, image }`, at most 2), so
those it goes to show it and never contact the site (as messaging apps do).

### 8.13 Room sounds (3.4)

A room's own sounds, heard by everyone in it, its visitors as well as its owner, each from where it is, by distance
and side, as voices are (8.7). Files, never code: what runs on a visitor's page is visit.js and the room's data.

- **The sounds.** The room's page names its sounds (`v.sounds({ kind: file })`): each a WAV or MP3 of at most 4 MB,
  or an Ogg, WebM or MP4 of at most 2 MB (by their bytes; `bad_media` otherwise); at most 32. Its visit-client keeps
  each as it keeps a picture (8.12: by its hash), and the room's scene says them (`sounds`: kind -> { `id`, `mime` }),
  as it changes. A page that hears one fetches its clip by its id from the room it is in (`media_get` with that room's
  `channel`): its visit-client asks that room's host over their link if it does not have it, and keeps it.
- **Loops**: anything in the room's scene marked `userData.visitSound = { kind, volume, ref, far }` (set before it
  goes into the scene: it travels with it) sounds from where it is, over and over, while it is there and shown: at its
  `volume` (0 to 1) within `ref` metres, fainter farther, nothing past `far`. `bed: true`: everywhere in the room the
  same, from nowhere (wind, birds). A page plays the nearest 8 within reach (beds first), and starts each where the
  wall clock has it (its offset the time modulo the clip's length), so everyone in the room hears about the same part.
- **One-shots**: the room's page plays one once from a point in it (`v.soundAt(objectOrPoint, kind, { volume, ref,
  far })`: `sound_at`): its visit-client sends it to everyone in its room (`sound`, at most 10 a second), its own user
  too, and a page plays it if it is within `far` (at most 6 at once).
- **Who hears.** The room your user is in: a visit, a plot in your world you stepped onto, your own room at home. A host
  page (the engine's) hears nothing. Nothing plays before the browser lets it (a click or a key, its autoplay rule). Room
  sounds are on or off apart from voices (`v.setRoomSounds`, the panel's 🔈), kept in that browser. A page that plays
  its own room's sounds itself (its own synth, a mixer, an echo) says so (`v.ownSounds(false)`): Visit then plays them
  for its visitors only, and every other room's for it as ever. Nothing goes quieter under voices (yet).

### 8.14 Assets (4.1)

A scene's big pictures and meshes travel by name, once, the way its landscape does (8.10), but for things that move
too: a room's drawn signs and screens, a detailed figure's textures and its mesh. What a page is sent when it joins
is then the scene's structure (its objects, where they are, their materials, small things inline), and what is
heavy comes after, a piece at a time, at that page's own pace, kept. So a room with three dozen drawn panels and a
textured, rigged receptionist joins within a relay's 1 MiB (../engine/SPEC.md 6.3) on the first visit and costs next
to nothing on the next.

- **Assets.** A picture of a texture (a canvas, an image, an image bitmap; at most 4096 px a side) or a plain mesh
  (a `BufferGeometry` with its vertices in it), as the room's scene has it now, of at least 16 KiB once made: a
  canvas as WebP (as a redrawn one goes), an image from a data URL as it is, any other picture drawn as three.js would
  write it (PNG; JPEG past 2048 px a side); a mesh as three.js's JSON of it without its uuid, gzipped. At most 16 MiB.
  Its **name** is the SHA-256 of its bytes (64 hex, lower case): the same picture, the same mesh, the same name, at
  every load. A drawn picture too small to be one goes inline as made (its WebP, not the PNG three.js would write).
- **The room's page** makes each in the background (encoded, hashed: nothing on its frame's time) and gives it to
  its visit-client (5.4, bits 0 and 1 together: `ASSET`, alone; answered `asset_kept` with its `id`), which keeps it
  (`--asset-dir`, by name; the least lately used let go past 1 GiB) as this office's own. Until one is made, it goes
  inline as before. Before a visitor's first whole scene, the page makes the whole room's (at most 4 s).
- **Named, not carried.** To a visitor whose visit-client reads assets (it says so, `assets`, when the link is up;
  the room's page hears it as `assets` and in `visit_started`), the whole scene, an added object and resources sent
  again name each made one instead: an image `{ uuid, asset }` with no `url`; a geometry `{ uuid, type:
  "BufferGeometry", asset }` with no `data`. An edit for several visitors names them only if every one reads them; a
  visitor on an older Visit gets everything inline, as ever. A canvas redrawn goes inline in the edit, as before.
- **Fetched, a piece at a time.** A page draws the scene at once, a blank picture and an empty mesh in each named
  one's place, and asks its visit-client for what it lacks (`asset_want`: its `id`, the `channel` of the visit or
  watch whose scene named it, and `at`, from where), at most 2 at once: one piece of at most 256 KiB at a time, the
  next asked for when one comes (5.4: `ASSET`, never dropped). So nothing between them (a workroom's relay, a slow
  link) ever holds more than a few pieces. The visit-client gives it from what it keeps, else asks the room's
  visit-client over their link (`asset_want`), which gives it (stream 6) if it is that room's own (from what it keeps,
  else its page is asked, `asset_want`, and gives it again), else `asset_none`; nothing else it keeps (another room's,
  seen visiting) is given. Each comes checked against its name (its SHA-256) and is kept: a page that reloads, or
  another scene naming it, has it without the room. A page keeps up to 256 MiB of them itself, so a page whose
  connection drops (a relay hanging up on a page that fell behind) fetches nothing again. One not to be had (`asset_none`, or 45 s without a piece)
  stays blank.
- **Put in place.** A picture is checked from its header (kind, at most 4096 px a side) and decoded before it is a
  texture's; a mesh is unpacked (at most 64 MiB), checked as an inline one (cleanScene, the scene's budget of
  vertices) and copied into the geometry the scene has under that uuid.

### 8.15 Teleports (5.1)

Walking onto a friend's plot is one way into a room (8.4); a teleport is the other: from wherever your user is, to any
room that will take them, onto its pad, in a beam of light that everyone in both rooms sees. Visit carries it and
draws it; who may come is each room's own to say, by its own rule, and where they arrive is its pad, made and styled
by the room as it likes.

- **A room takes teleports** when its `host` says `teleport: { pad, allow, color, effect }` (section 9). `pad`: where
  arrivals stand, in its units: `{ x, y, z, yaw }`, an Object3D of its scene (its place in the room, facing where it
  faces), or a function of who comes giving either. What the pad looks like is the room's: Visit draws nothing of it.
  Without `teleport`, a room answers every ask no.
- **Who may come** is `allow(who)`'s to say: `true` (or a Promise of it, within 2.5 s) and nothing else is yes. `who`:
  `{ id, key, name, office, sim, guest, owner }`: their office's key, as the lobby says it for an ask and as a link
  proved it for a knock and stepping in; the name their user goes by; their office's; `guest`, in a world with this
  office (3.3); `owner`, its own user. Without `allow`: its guests and its owner (Visit's rule for walking in). It is
  asked afresh each time, and a shut door is no without asking it (section 3, point 16).
- **The list.** The person's page asks (`teleport_ask`: `v.findTeleports()`): its visit-client asks its own room's
  page about its owner, and through the lobby every office in it whose hello said `teleport`, ready (at most 48, one
  round every 1.5 s), each door's page answering as above; a door silent for 3 s is no. The page lists those that
  said yes (`v.teleportTargets`), its own room first. A page shows its list, and leaves out the room its user is in.
- **Going** (`v.teleport(key)`). A room the person's world shows (picked, or home) is stepped into by teleport
  (`enter` with `teleport`, no pose); any other is knocked on to teleport (6.4), watched once its page says yes,
  placed in the world as an unplaced plot is, and stepped into as soon as its scene is in. Either way its host's
  visit-client asks its page once more, by the key the link proved, then has it spawn them on its pad (`spawn` hears
  `{ at: <the pad>, teleport: true }`); no: `enter_refused`, `teleport`, and they stay where they were. Stepping in,
  their visit-client takes them out of the room they were in by teleport (`exit` with `teleport`), whose page plays
  it. A room come to by teleport and not picked is let go 2 s after they leave it (its host plays them going first);
  picked meanwhile, it stays in their world.
- **The beam** is Visit's, played by each room's own visit.js in its own scene (so it travels as the scene does, to
  everyone who sees the room, its owner too): a column of light at their feet, rings running along it, sparks round
  it, a glow on the floor, 1.6 s, in the room's `color` (else cyan); going, their body (Visit's model of them, where
  the room shows their own look) drawn up into it; coming, down out of it. A room that draws its own (`effect: false`)
  hears `teleported` (`{ way: "in" | "out", id, visitor, at }`) and plays what it likes; so does any room, for its
  own model of a visitor. The person's own page draws a warp over its world's view while they go (`teleport` events:
  `leaving`, `arrived`, `failed` with why), and a flash as they arrive. A page that walks its user at home itself (no
  engine) goes home onto its own pad at once, its own room playing the beam.
- **Older Visits.** An office before 5.1 says nothing of `teleport`, so it is neither asked nor knocked on; a host
  before 5.1 that is stepped into by teleport puts its visitor where newcomers go, and plays nothing. A lobby before
  5.1 carries no teleports (its `welcome` does not say `teleport`): a page's list then has its own room alone (`v.teleport`
  still takes its user into a room its world shows, over the link it has).

## 9. The library: `visit.js`

The normative surface; INTEGRATING.md shows it in use. Loaded with
`<script src="http://127.0.0.1:<port>/visit.js"></script>`, which defines `window.Visit`.

```js
// token: the token, or a function giving it (sync or async), asked again on each reconnect: a visit-client
// restarted with a new token is then found again by itself. A wrong token is final for a string.
// threeBase: where to fetch three.js from instead of the CDN ("{r}" the revision; the same files, checked
// against the same hashes).
const v = await Visit.connect({ port, token, sim: { name, version }, threeBase });

// state, always current; "change" fires whenever any of it changes (never for the per-frame traffic)
v.state        // "connecting" | "online" | "offline" (the lobby out of reach) | "away" (out of it on purpose)
v.stats        // while visiting, about once a second: { latency (input to screen, drawn here, ms), rtt (the link's,
               //   ms), path ("direct" | "relay"), fps (frames drawn a second) }
v.viewFocused  // in a room you visit (or went to by a link), and the view takes the keys and mouse (not in a corner):
               //   the page's own keys wait
v.office       // { id, name } while online
v.key          // this office's key: its lasting identity (the same every run, with a state file)
v.door         // "open" (your guests link and come in without asking) | "closed" (everyone asks)
v.guests       // [{ key, name }]: your guests (3.3): everyone in a world with you, who walks in through the open door
v.picks        // [{ key, name }]: whose rooms your world shows round yours (3.3; at most v.maxPicks, 8)
v.status       // "available" | "busy"
v.maxVisitors  // number
v.offices      // [{ id, name, status, sim, maxVisitors, visitors, visiting, ready, key, links, whisper, about }], you
               //   included: about, their line in the lobby's list of who is here ({ text, tags } or null)
v.signedIn     // someone is signed in on this workroom: only then is it in the lobby (3, point 13); "signed_in" as it changes
v.lobbyInfo    // what the lobby says of itself: { title, motd, admin (you are one of its operators) }
v.groups       // your worlds (6.6): [{ name, about, open, locked, owner, members: [{ key, name, since }], bans }] (bans: if yours)
v.groupList    // the lobby's worlds, once findGroups() has them: [{ name, about, open, locked, members, online }]
v.about        // your line in the lobby's list of who is here: { text, tags } or null
v.browsers     // browsers linked to you (6.11, 3.1): [{ key, agent, pictures, created, used, online }]; "browsers" as it changes
v.layout       // [{ key, x, z, turn }]: where you have put friends' plots (8.4)
v.seen         // [{ key, name, at: { dx, dz, turn, size } }]: where friends' worlds have your room (8.4): metres from
               //   their plot's middle (north -z), quarter turns left, and their plot's size
v.watchers     // [{ id, visitor, inside }]: who sees your room from outside it (worlds that picked it)
v.visiting     // the room in your stage: { id, host: { id, name, sim }, paused, link, inside } or null (a link's: in
               //   the corner until you go there, inside: true)
v.visitors     // your visitors as a host: [{ id, visitor: { id, name, office, sim, key }, guest, pose, link, look, eyes, problem,
               //   riding }]: visitor.name, the name the person goes by (their user's, 8.9; your own user's for the
               //   owner), else their office's till theirs is known, and anew as they change it; visitor.office, their
               //   office's name (4.3.0: before, `name` was always the office's); the same object your hooks are given;
               //   pose, their feet (8.1); look, "theirs" (Visit draws their own model) or "mine" (draw yours);
               //   eyes over their feet (your units); problem, why not their own, if you would show it; riding, the mount
               //   they ride here (8.11), { id, name, fly }, or null
v.avatars      // whose look your visitors have in your room (8.6): { default: "mine" | "theirs", by: [{ key, use }] }

v.on(event, fn); v.off(event, fn)   // "change" | "knock" | "answered" | "visit_started" | "visit_ended" | "stats" | "error" | "point"
                   //   (knock, answered and visit_started say link: true for a link's)
v.close()          // disconnect for good
v.setStatus(status); v.setMaxVisitors(n); v.rename(name)
// (4.0, #57: no v.knock, v.link, v.answer, v.stepIn, v.stepOut, v.toWorld, v.unlink, v.leave, v.knocks, v.knocking,
// v.links: into a room through your world, 8.4; talk with anyone, v.talk)
v.watching(officeId)        // whether a plot of theirs is in your world (picked, or a link put there)
v.avatar(object, { unit })  // your own body, for rooms that show their visitors' own looks (8.6, MODELS.md); null: none
                            //   "model" event: { what, problems, notes }, as it is checked; "body": how the room you are in
                            //   draws you, { channel, key, name, look, eyes, problem }
v.possessions([{ id, name, object, unit, mount, fly, speed, lift }])   // what your user owns (8.11), as your room makes them
v.mounts                    // what they can ride: [{ id, name, fly, speed, lift }] (none until your room gives some)
v.ride(id); v.riding; v.mount   // get on a mount (null: off); what they ride; the one a page's ride (M, in the reference UI) gets them on
v.setMount(id)              // 5.0: that one chosen (kept in this browser); while riding, they change to it
v.rideSpeed; v.rideLift; v.rideFly   // their mount's, for your own room at home: times walking pace, metres up, flies
v.setMic(on); v.mic         // your microphone (8.7): on, those near your user hear them, and those in a room voice with them (remembered)
v.voices; v.voiceRange      // who your user can hear, near them (as the room's visit-client says; across a plot's side, too): [{ key, name,
                            //   distance (m), speaking }]; metres (45)
v.share(blob)               // a picture to share (8.12): kept by visit-client; resolves to { id, mime, size } (a bigger one,
                            //   or another kind, made smaller as WebP first); or a voice message (an audio/* recording,
                            //   WebM, Ogg or MP4, at most a minute and 2 MB: give the line its `secs`)
v.media(id)                 // a picture or a voice message by its id: a promise of a URL this page can show or play (null: not to be had)
v.preview(url)              // a link's preview, made by visit-client: a promise of { url, title, description, site, image } or null
v.history({ before, limit })   // the chat before then (8.12), from the database: a promise of { lines, more }, newest last
v.shared(conversation, kind, { before })   // the pictures ("media"), voice messages ("voice") or links ("links") shared in a conversation (a line's
                            //   `conversation`), newest first: a promise of { items, more }
v.joinVoice(key)            // into a room voice (8.7): a linked office's room's (its key), yours ("room"), or none (null); false: not
                            //   possible. "room_voice" event: the one you are in ({ key, name, members }), or none ({ key: null, out })
v.roomVoice; v.voiceMembers // the room voice you are in: { key, name, members: [{ key, name, speaking }], joining } or null; who is in
                            //   your own room's voice: [{ key, name, speaking }]; the "voice" event: one of them starts or stops speaking
v.voiceKick(key)            // one out of your room's voice now (they may come back)
v.ban(key, { voice, chat }) // keep someone out of your room's voice, its chat, both, or neither (8.9; kept until lifted)
v.bans; v.banOf(key)        // whom you keep out: [{ key, name, voice, chat }]; one of them, or null
v.bannedBy                  // rooms that keep you out: [{ key, name, voice, chat }]; the "banned" event as it changes
v.listener({ x, y, z, yaw })   // where your user is in your room, for a room that draws no world (a world says it)
v.landscapeChanged()        // something marked userData.visitStatic (8.10) changed in itself: its landscape goes again
v.perf(on); v.perfOn; v.perfReport()   // telemetry (a page's ⏱; remembered in this browser; ?visit-perf=1): measured
                            //   each second, this page's frames and stalls (long tasks) and Visit's own work by part (ms);
                            //   5.0: Visit draws none of it: "perf" gives each second's row (UI.md has its fields),
                            //   perfReport(): the last two minutes, a row a second, each with this Visit's version
                            //   (`visit`, as `Visit.version`); with a world, `draws`: { colour, shadow }, its drawing's
                            //   draw calls a frame (4.3.1; three.js's renderer.info leaves the shadow passes out).
                            //   Nothing leaves the page
v.setName(name); v.userName   // the name your user goes by (8.9), shown to others with what they say; null: your office's
v.userOf(key)               // the name the user of a linked office goes by, or null (their office's)
v.say(text, to, { media, links })   // with pictures (v.share's) or links (v.preview's) (8.12); a line may be them alone
v.say(text, to)             // a line (8.9): true if said. No to: everyone who can see your user (over their head);
                            //   { whisper: id or key }: that person alone; "room": your room's chat (everyone in it,
                            //   and linked with it); { key } (or a key): that linked office's room's chat; "lobby":
                            //   everyone in the lobby
v.whisperTargets            // whom say(text, { whisper }) can reach: those in the lobby who take whispers: [{ id, key, name }]
v.awayTargets               // whom say(text, { whisper: key, name }) leaves a whisper for (8.9, 0.22.0): not in the lobby now,
                            //   those you whispered with and your guests (your worlds' members): [{ key, name }]; text only
v.held; v.unhold(id)        // those whispers, still kept by your visit-client: [{ id, key, name, text, at }] ("held" event);
                            //   one taken back. "held_sent": one passed on, now they are in the lobby
v.unseen; v.read(conversation)   // the mailbox (8.9): { count, conversations: [{ conversation, count }], lines } ("unseen"
                            //   event); its lines of a conversation read (none: all of them)
v.attention(on)             // someone is looking at this page's chat (its chat window says so): what comes is seen
v.chatTargets; v.lobbyChat  // whom say(text, { key }) can reach: [{ key, name }]; the lobby carries chat
v.chats                     // the lines over heads still showing where you are and in your room: [{ key, name, text, mine,
                            //   owner, id, channel }]
v.chatLog                   // the last 100 lines of every scope: [{ at, scope ("say", "whisper", "sim", "lobby"), key, name,
                            //   text, mine, owner, id, channel, to (yours: whom you whispered; a "sim" line's: the room
                            //   whose chat it was), mention (it mentions your user: @ and their name), old (from before
                            //   this page: a reload keeps the chat) }]; and the
                            //   "chat" event, each as it comes
v.phone(on, { at, model })  // a phone in your room (8.8), at that spot (your units): Visit's own there (model: false, yours)
v.phones; v.calls           // those linked to you whose rooms have phones: [{ key, name }]; your calls: [{ key, name,
                            //   state ("calling" | "ringing" | "on"), speaker, speaking }]
v.call(key); v.pickUp(key); v.hangUp(key); v.speaker(key, on)   // and the "call" event: { key, name, state, speaker, why }
v.talk(key); v.canTalk(key) // 4.0: talk with them, voice alone (8.7): a call in v.calls with voice: true, "calling" here and
                            //   "ringing" there, then "answering" and "on" (pickUp answers, hangUp declines or ends it)
v.perform(visitId, clip, { secs, loop }); v.hold(visitId, object)   // 4.0 (8.6): what your room has a visitor's own model
                            //   do (sit, lie, drink, eat, throw, swat, wave; null: nothing), and a thing of your scene in
                            //   their hand (null: none); v.visitors[i].act, .holding say so. Their world's frame says act, sit
v.huddle(group, on); v.joinHuddle(group); v.huddles; v.inHuddle   // 4.0: a world's huddle, started or ended; joined;
                            //   those going on: [{ group, from, mine }]; the one you are in: { group, key, mine }; "huddle" event
v.useAvatars(use); v.useAvatar(key, use); v.avatarUse(key)   // "theirs" | "mine": whose look visitors have in
                   //   your room, by default and for one office (null: back to the default)
v.kick(visitId)    // end someone's visit to you
v.leaveLobby()     // away: out of the lobby, nobody sees you or can reach you, your visits end (kept across restarts
v.joinLobby()      //   with VISIT_STATE_FILE); and back
v.setDoor(open)    // open or close your door (kept, with VISIT_STATE_FILE); shut, nobody watches your plot
v.setAbout(text, tags)   // your line in the lobby's list of who is here (and its public page); null: none (3.0.0)
// Worlds (6.6; the protocol's groups): members are each other's guests (3.3: in without asking through open doors, and
// each may pick the others' rooms for their own world); each has a
// chat (say(text, { group })). Public, or with a password. "group" as one of yours changes, "group_left" ({ group,
// reason: left, removed, banned, deleted }), "groups" (the list). (3.0's setPublic, answerGroup, setGroupOp: gone.)
v.createGroup(name, { about, password }); v.joinGroup(name, password); v.leaveGroup(name); v.findGroups()
v.removeFromGroup(name, key, { ban }); v.unbanFromGroup(name, key)   // its owner's
v.setGroup(name, { about, password, open: true }); v.handGroup(name, key); v.deleteGroup(name)   // its owner's
v.groupsWith(key); v.isGroupOwner(name)   // the worlds of yours that key is in; whether you run that one
v.lobbyBan(key, { minutes, reason }); v.lobbyUnban(key); v.lobbyBansList()   // the lobby's operators' (6.7): v.lobbyBans
// The lobby's page in a browser, as you (6.11, 3.1): the browser linked to your office for good (until unlinked).
v.lobbyPass()      // a Promise of the lobby's page's URL with a web pass; v.openLobby() opens it in a tab (call it from a
                   //   click), as the panel's 🌐 Lobby does; v.lobbyQr(): { url, qr (an SVG), expires }, for a phone (📱)
v.linkBrowser(code)     // a Promise of the browser waiting with the code it shows: { code, key, agent, pictures }
v.allowBrowser(code)    // 3.2: a Promise of the confirmation it must show ({ code, key, confirm, url, qr, until }; v.browserConfirm):
                        //   show the QR code (as an image) and the digits; linked once it does ("browsers" says so)
v.listBrowsers(); v.unlinkBrowser(key)   // v.browsers, asked; one unlinked
v.target           // 3.3 (#48): your target in the room you are in, yours alone (8.9, "Targets"): { kind: "person" | "figure",
                   //   id, name, room, title?, about?, actions? } or null; "target" as it changes
v.setTarget(t); v.targeting(on)   // set it ({ kind, id, name } or null); false: Visit picks none itself (your page does)
v.figuresHere      // the figures of the room you are in, as it describes them: [{ id, name, title, about, actions }]
v.say(text, { target })            // aloud, meant for a figure or person there (the room's page: "addressed" for a figure)
v.say(text, { whisper: { kind: "figure", id } })   // to that figure alone: its room's page alone has it ("addressed")
v.act(figure, action)              // a figure's action (a card's button): run by its room, by the type's own rules
v.figures([{ id, name, title, about, actions, talk }])   // host: your room's figures, for anyone in it (32, 8 KB); talk: "everyone" | "owner" | "none"
v.on("addressed", e)               // host: { figure, from: { id | owner, key, name }, name, text, whisper }: a line for a figure
v.sayAs(id, name, text, { to })    // host: a figure's line; with to (a visit's id, or "owner"), to them alone (a whisper)
v.host({ ..., targeting })         // "click" (default) | "near" | "none": how Visit picks a target for anyone in your room
v.ignore(key, { npc, name }); v.unignore(key, { npc }); v.clearIgnored()   // 3.3: whom your user ignores in chat (8.9): a
                   //   person by their key, a room's figure by that room's key and its id; at most v.maxIgnored (50)
v.ignored; v.isIgnored(key, npc)   // [{ key, npc, name }]; "ignored" as it changes
v.sounds({ kind: file })           // 3.4, host (8.13): your room's sounds (a Blob, ArrayBuffer or URL each): a Promise of { kind: id }
object.userData.visitSound = { kind, volume, ref, far, bed }   // a loop from that thing (bed: everywhere in your room)
v.soundAt(objectOrPoint, kind, { volume, ref, far })   // host: one of your room's sounds, once, from there, for everyone in it
v.roomSounds; v.setRoomSounds(on)  // the room's sounds on or off on this page (a page's 🔈), kept in this browser
v.ownSounds(false)                 // your own room's sounds: your page plays them itself (Visit, for your visitors only)
v.pick(key, name); v.unpick(key); v.isPicked(key)   // 3.3: a room in your world, or out of it: a key from v.offices,
                   //   or (proved by the link) a visit's peer.key; at most v.maxPicks (8: "too_many" past it)
v.isGuest(key)     // whether that key is in a world with you; "visit_started" says guest: true for one let in through the
                   //   open door. (addGuest and removeGuest: gone in 3.3)

// hosting: register once after connect (and again after each page load). Until then this office is not in the
// lobby to be visited, and cannot knock: every room takes part with its scene.
v.host({
  spawn(visitor, { at }) { return pose; },    // where a new visitor appears (at: where a world's watcher stepped in, if one did)
  move(pose, input, dt, { id, visitor, riding }) { return pose; },   // where it goes, its height too (the room's physics, 8.2):
                                              //   the library has already set pose.yaw and pose.pitch; id is the visit's,
                                              //   visitor { id, name, sim }; input.up: up or down; riding (8.11): { id, name,
                                              //   fly, speed, lift } or null; the pose returned may say `air` (how high above
                                              //   the floor), for a mount's fly clip
  scene: { root, three, renderer, near, far },      // the room's three.js scene (or the part to share), the THREE it was made with
                                                    //   (r125 to r186), its renderer, and visitors' camera range (8.1)
  fov: 70,                                          // visitors' camera's vertical field of view, in degrees
  walk: { speed, mounts },                          // optional: move() walks with Visit.walk at this speed: visitors walk at once;
                                                    //   mounts: true, riders at their mount's pace times it
  mounts: false,                                    // optional: keep visitors' mounts out (8.11); without it, they ride
  eye: 1.6,                                         // optional: how high a visitor's eyes are over its feet when you draw it as
                                                    //   your own model, in metres (8.1): where its camera goes
  plot: { center: [x, z], size: [w, d] }, unit: 1,  // this room's land in a world (8.4), in its units (without it: what its scene
                                                    //   covers); unit: metres in one
  preview: { x, y, z, yaw, pitch },                 // optional (unused since 4.0: no links): where a link saw this room from (8.5); without
                                                    //   it, from above its plot, going round
  beforeSample(now) {},                             // optional (4.0, #42): your room's own step, once before each sample of its
                                                    //   scene (30 a second while anyone sees it): where to move what visitors see
  teleport: { pad, allow(who) {}, color, effect },  // optional (5.1, 8.15): this room takes teleports. pad: where they arrive
                                                    //   ({ x, y, z, yaw }, an Object3D of yours, or a function of who); allow:
                                                    //   true (or a Promise of it) if who ({ id, key, name, office, sim, guest,
                                                    //   owner }) may come (without it: your guests and you); color: Visit's
                                                    //   beam's here (0x66ddff); effect: false, none of Visit's
});
v.on("teleported", ({ way, id, visitor, at }) => …)   // 5.1, hosting: someone came ("in", on your pad) or went ("out") by
                                                      //   teleport, there: for your own effect, or your own model of them
// In the engine (engine/SPEC.md) your renderer's render() does nothing (4.0, #42): Visit reads your scene itself.

// a world (8.4): after host(). Every frame, where your page has its user (in its room's terms) and what they press;
// what comes back is where they are now: at home as given, or in a plot as its host has them (in: { key, name }), its
// y their feet as that host has them (a stair, a lift, flying), never higher than they came when they walk off.
// Then world.render(camera), in place of renderer.render(scene, camera).
const world = v.world({ scene, renderer, ground, gap, clipOwn, bubbles, speaking });   // your whole scene and its renderer (host()'s by default);
                   //   bubbles (true): it draws the lines said (8.9) in the room your user is in over their speakers' heads;
                   //   speaking (true): a 🔊 over each one your user hears speaking there (8.7)
const me = world.frame(dt, { x, y, z, yaw, pitch, air, facing }, { forward, strafe, up, run });   // air: how far above its floor your user
                   //   is (flying), so a plot it steps into has it that high (8.2); up: as a visitor's input; facing: the
                   //   way your user faces (radians, as yaw), for their mount at home (8.11; else yaw)
world.render(camera);   // 4.0 (#42): what never moves (a kept plot, a landscape on its plot's root) drawn merged by material
world.plots        // [{ key, name, at: { x, z, turn }, rect: [x0, z0, x1, z1], size, inside, offline, home }] (offline: a picked
                   //   plot kept while its office is away, 8.10; size: unturned, in your room's units, 5.0; home: your own room's)
world.layout()     // 5.0: world.plots laid out anew, now (a placement kept a moment ago is in it), for a map
world.inside; world.point(target, { wheel }); world.leave(); world.release()   // wheel: notches, a wheel point (8.3)
v.place(key, { x, z, turn }); v.unplace(key)   // yours alone: each friend in it is told where it is, nothing more
v.on("seen", ({ key, name, at }) => …)          // a friend's world has your room somewhere new (at), or not (null)

// hosting: what visitors may click (8.3), by type: things marked userData.visitPoint = "<type>". A type not
// named does nothing. One not cosmetic throws unless agreed: true (your user agreed to it, by name).
v.interactions({ spin: { cosmetic: true, label: "spin it", who: "everyone", reach: 4,   // who: "everyone" | "guests" | "owner"
                         wheel: false,   // true: the wheel runs it too (wheel: its notches; 0 for a click)
                         run({ id, visitor, type, object, point, distance, wheel }) { /* ... */ } } });
// every point, raw: { id (its visit), visitor: { id, name, sim }, from: [x, y, z] (its eyes, where this page has
// it), dir: [x, y, z] (a unit vector, where it looked), yaw, pitch (in the room's terms), wheel (notches, positive
// turned down; 0: a click) }; and each interaction run: "interaction" { id, visitor, type, cosmetic, object, wheel }
v.on("point", ({ id, visitor, from, dir }) => { /* ... */ });

// visiting: where to show the room you are in; the library draws its scene there and captures input; while the
// mouse looks about (the pointer locked), a crosshair at the middle, and a click there points (8.3)
const view = v.view(element);   // { release(), setActive(on), active, canvas }: inactive, it takes no keys or mouse

// helpers
Visit.walk(pose, input, dt, { speed, blocked })   // first-person walking with wall sliding, for move()
Visit.checkModel(object, { three, unit, kind })  // a model as other rooms will read it (MODELS.md): { ok, problems, notes, height,
                                                 //   eyes, seat, clips }; kind "avatar" (default) or "mount"
Visit.bubble(THREE, text, unit)                  // a line's bubble (8.9), a sprite whose bottom sits where you put it (unit: metres
                                                 //   a unit), for a room that draws its own
Visit.limits                                     // 5.0: what Visit and visit-client keep to, for a page's UI to say: { chatLen,
                                                 //   chats, chatWindowMs, chatLog, maxPicks, maxIgnored, voiceSecs, voiceMax,
                                                 //   voiceKinds, voiceRange }

// 5.0 (#82): a page's own UI (UI.md). Visit draws none in the page: no panel, chat window, target card, map, stage
// nor ⏱ corner (Visit.panel, Visit.chat, Visit.map and Visit.stage throw, saying where they went). The reference UI,
// examples/ui/visit-ui.js, is one a room copies into its own tree. What it, or any, is built on, beside the above:
v.plot; v.plotMetres        // your room's plot ({ center, size }, your room's units); its size in metres
v.currentWorld              // the World your page made (v.world, or v.homeView's), or null
v.watches                   // the plots your world shows (never your own room): [{ id, host: { id, key, name }, inside,
                            //   paused, body: { look, eyes, problem } (how that room draws your user) or null }]
v.nameOf(key)               // an office's name, as this page knows it ("" if none)
v.hasPhone                  // your room has a phone (v.phone): a page offers v.phones to call
v.roomHere                  // the room your user is in: { kind: "home" | "visit" | "plot", key (its owner's), conversation }
v.actionsOf(target)         // a figure's actions the room it is in takes: [{ type, label }] (v.act runs one)
v.mayTalk(figure)           // whether your user may talk to it (its `talk`)
v.homeView(element)         // your own room, first person, in element, for a page that draws no world (engine/SPEC.md 7):
                            //   WASD, Shift, Space/X and the mouse, a click pointing; { element, shown, release() }: it
                            //   draws only while shown (not while visiting, nor once your page makes a world)

// 5.1 (8.15): teleports, as the person. findTeleports(): a Promise of the rooms that would take your user now (your own
// first; at most 7 s; asked again within 1.6 s: what that round found), "teleports" as each yes comes; teleport(key):
// there by teleport (a Promise of { ok, reason }).
v.findTeleports(); v.teleportTargets   // [{ key, name, user, home }]: those that said yes to the last ask
v.teleportAsking; v.teleportLobby      // an ask under way; this lobby carries teleports (else your own room alone)
v.teleport(key); v.teleporting         // { key, name, home, since, entered } while one goes, else null
v.on("teleport", ({ state, key, name, reason }) => …)   // "leaving" | "arrived" | "failed" (reason: not_allowed,
                            //   declined, timeout, gone, full, here, no_world, no_pad, refused, not_supported, offline)
```

**Poses** are the host's own: the library only stores them and passes them back. `Visit.walk` uses
`{ x, y, z, yaw, pitch }` with y up, yaw 0 looking toward −z and positive turning left, pitch positive
looking up (three.js's camera, Euler order `"YXZ"`). It moves at `speed` units a second (twice that while
`run`), and where `blocked(x, z)` shuts the way it tries the x move alone, then the z move alone, so a
visitor slides along walls.

**Calling the hooks.** `spawn` once per visitor. `move` 60 times a second per visitor, and the moment its
input arrives, with the latest input and `dt` in seconds. A hook that throws is logged and skipped for that
tick; three throws in a row end that visit (`"kicked"`).

**Scenes.** Every 33 ms, while it has visitors, the library reads the scene (objects' matrices and
visibility, materials' state, geometries' and textures' versions) and sends what changed (8.1). A room marks
each visitor's body with `userData.visitor = <visit id>`, and what a visitor may point at with
`userData.visitPoint = true`; of `userData`, only those go (8.1). Anything else under `root` is shared, so
what should not be belongs outside it.

**Timing.** The library ticks from a Worker's timer: a page's own timers slow to once a second or less
while its tab is in the background, and a host must go on moving its visitors and sending its scene. A page whose
Content-Security-Policy forbids `blob:` workers falls back to its own timers, and its visitors then see it
slowly while its tab is hidden.

## 10. Limits and defaults

| what | value |
|---|---|
| page port | 7401 (flag) |
| page token | ≥ 32 hex characters, new each run |
| page hello timeout | 5 s |
| max visitors | 3 by default, 0–8 |
| three.js | r125 to r186 (the releases visit.js carries hashes for) |
| scene updates | edits at most every 33 ms; deltas every 33 ms when anything moved; a key delta every second and on request |
| scene edits | gzipped ≤ 16 MiB, unpacked ≤ 64 MiB, nested ≤ 256; ≤ 64 MiB a second to a visitor; ≤ 32 MiB waiting on a link |
| scene contents | materials three.js draws itself; images ≤ 4096 px a side; segments ≤ 256; ≈ 4 million vertices, 100,000 objects |
| largest delta | 1 MiB |
| deltas behind | a delta more than 100 ms older than the newest waiting is dropped, to the next key delta |
| moving things shown | 70 ms behind the host's clock |
| input rate | on every change, at most every 8 ms, and every 33 ms regardless; at most 120 a second accepted |
| points | one every 200 ms from a visitor's page; at most 5 a second accepted |
| link send window | 64 KiB sent and not yet acknowledged |
| control messages on a link | at most 20 a second from either side; more are dropped |
| voice (4.2, 8.7) | a WebRTC connection a page (its own office's) and one a link; at most 256 at once; an offer at most 32 KiB; 12 voices at once on one; from each, at most 100 packets a second, each at most 1 000 bytes (more are dropped) |
| key delta requests | passed to the host's page at most every 500 ms |
| scene resync requests | passed to the host's page at most every 2 s (one sooner waits) |
| lobby silence | a minute: then the connection is made again |
| input staleness | 300 ms |
| link dial timeout | 20 s |
| link grace period | 15 s |
| lobby session kept after a drop (resume) | 30 s |
| knock wait | 60 s |
| talking (4.0) | 50 voice visits an office at once, either way (a huddle's starter carries one for each who joined); a voice visit agreed has 20 s to come up on the page that answered |
| watches | 8 watchers of a plot, 8 plots watched; 30 knocks to watch a minute; one declined tried again after 20 s |
| worlds | 20 a key, 50 members each (the lobby's `--world-size`), a password 4 to 64 characters, 5 passwords tried a minute (6.10) |
| linked browsers | 10 an office, 4 in the lobby at once; a pass or a code good 10 minutes, once; a room's confirmation 5 minutes, 5 wrong tries (6.11) |
| world | a plot not said: what its scene covers, at most 200 units a side; 4 units between plots in a ring; out of a plot 0.3 units past it; stepping in answered within 3 s |
| teleports (5.1, 8.15) | a room's page answers an ask in 3 s (its `allow` in 2.5 s), else no; the lobby keeps an ask 5 s, 120 asks an office a minute; a page's round of asks one every 1.5 s, at most 48 rooms, over in 7 s; stepping in by teleport answered within 8 s, and a teleport not there in 20 s given up; a room come to by teleport let go 2 s after its user leaves it (20 s if never stepped into); the beam 1.6 s |
| layout, seen | 256 each |
| chat | a line at most 200 characters; 5 lines every 10 s, from a page and from each visitor, whatever the scope (the lobby counts its own, lobby lines and whispers, too); shown over a head 4 to 12 s, by its length; the chat window keeps 100 lines, and fades 20 s after the last |
| pictures, voice messages | a picture at most 8 MiB, 4 a line; a voice message at most 2 MiB and 60 s, one a line |
| mailbox | the newest 100 unread lines shown (all counted); looking: the page shown and focused, used in the last 2 minutes |
| whispers left for someone away | 20 for one person, 100 in all, kept 30 days at most; passed on one every 2.5 s at most, counted with the user's own lines |
| landscapes | at most 16 MiB gzipped each; kept on disk at most 256 MiB in all (one per office, the oldest go first); 64 plots kept while their offices are away; sent a second after what is static last changed; a visitor's whole room waits at most 400 ms to hear whether it reads them |

## 11. Errors

**Page connection refusals:** HTTP 403 before the WebSocket opens (an origin not in `--origins`); close
codes 4400 bad `hello`, 4401 bad or missing token, 4409 replaced by a newer page; 1001 after the page's `bye`.

**`error` codes the page can get** (from the visit-client, or passed through from the lobby): those of
section 6.4 (`not_found`, `self_knock`, `not_available`, `full`, `not_ready`, `already_visiting`,
`knock_pending`, `rate_limited`), `message_too_long`, `name_invalid`, `name_taken` (on `rename`; the
office keeps its name), `no_phone` (ringing a room with no phone), `no_call`
(`speaker` with no call on; 3.4.3, `call` answering no ring), `already_talking` (4.0: a knock to talk with someone you talk with already), `huddle_on` (4.0: a huddle going on in that world already), `not_supported`, `too_many`, `already_watching` (knocks to watch, which a page never sees), `not_yours` (answering a knock that is not yours), `not_hosting` (knocking before
the page hosts its scene, 4.2), `no_such_visit` (`end`, `keyframe` or `scene_resync` for a visit you are not
in), `offline` (an action while the lobby is down), `away` (an action
while the office is out of the lobby on purpose: `join_lobby` first), `signed_out` (an action while nobody is signed
in on the workroom), `bad_asset` (4.1: an asset frame that is not a PNG, JPEG or WebP picture nor a gzipped mesh, or past
16 MiB, 8.14), `bad_message`; from worlds (6.6): `already_member`, `password_required`, `wrong_password`, `bad_password`,
`banned`, `closed`, `full`, `not_owner`, `rate_limited`, `busy`; from linked browsers (6.11): `not_found`, `rate_limited`,
`bad_confirm`; from the lobby's
operators (6.7): `not_admin`, and `banned`. The lobby's own, before a session: `bad_proof` (closed 4401), `banned`
(closed 4403), `proto_unsupported`, `hello_first`, `hello_timeout`, `too_many_connections`.

**Link application error codes:** 0 normal close (`bye`), 1 refused (bad ticket or endpoint), 2 protocol
mismatch, 3 visit over.

## 12. Versions

Three names, each changed only by something an older peer could not handle (additions keep the name;
[../CHANGELOG.md](../CHANGELOG.md) lists every release's additions, and what an integration does about them):
`basedverse-visit-local/2` (page ↔ visit-client), `basedverse-lobby/4` (visit-client ↔ lobby),
`basedverse-visit/2` (the link's ALPN). Visit 0.5 changed all three (rooms by their scenes, not video): a 0.5
office and an older one are in separate lobbies, and never meet. Visit 3.0 changed the lobby's (keys proved, the
sign-in, world groups): a lobby run with `--v3` keeps Visit 0.5 to 2.x offices in a lobby of their own. The library and the visit-client ship together, and the
visit-client serves its own library, so a page always runs the library its visit-client speaks. Between
machines, the lobby refuses an unknown `hello.proto` (`proto_unsupported`), and a link whose ALPN does not
match never opens: the visit ends `"link_failed"`.

## 13. Open questions

- **Avatars, further.** A host may now show a visitor as its own avatar (8.6); whether a host's own user is seen
  in its room by others, and avatars that do more than walk and stand (sit, wave), are for a later version.
- **Further worlds.** A world holds an office's own friends' plots; a friend's friends (a world beyond the
  next plot) are not drawn.
- **More than one corner.** One link is shown in the corner at a time; others wait. A page could show several,
  each with its own renderer, if people link with many at once.
- **Other engines.** Every room is three.js. glTF could carry other engines' scenes the same way, if one
  ever needs to take part.
- **Audio.** None in version 1.
- **Group lists signed by their owners.** A group's members are the lobby's word (section 3, point 14); a member
  list signed by its owner's key, which visit-clients check, would leave a lobby nothing to forge.
- **More than one lobby at once.** A workroom is in one lobby (`VISIT_LOBBY`); its key is the same in every one.
- **The relay's door.** A key is let onto the lobby's relay as it connects there; one banned, or gone, keeps a
  relay connection it already has until that ends (the relay offers no way to end one), within its rate.
- **Touch and gamepads.** Input is keyboard and mouse only for now.
