# The lobby, in Rust: a place to meet

Release: Visit 3.0.0 with the lobby's protocol `basedverse-lobby/4`; 3.1.0: users and worlds (public, or with a
password), and the lobby's page in a browser your workroom linked.

## 1. What it is

The lobby was a Python program that introduced visit-clients to each other: who is online, knocks, visits and
links, one lobby-wide chat, whispers. It becomes **one Rust program, `lobby`**, in the workspace beside `visit`,
and **a place to meet**, the way a game's lobby was (Battle.net in its Diablo II days):

- **users and worlds**, nothing else: the users are the workrooms in the lobby (each signed in, by its key), with
  a line about them if they wrote one; a world is a group of them whose members put each other's plots round their
  own (SPEC 8.4), as if each were everyone's guest, for worlds only. A world is public (anyone joins) or takes a
  password; whoever makes one owns it. Someone in several worlds places anyone of any of them, together, round
  their own room. (Before 4.0, a link (SPEC 8.5) reached anyone outside your worlds, and a knock walked anyone into a
  room its user let them into: 4.0, #57, has neither.) Each world has its own chat;
- **the lobby-wide chat and whispers**, as before;
- **friends**: Visit's guests, by key, shown online and where they are;
- **its page**: the lobby's address in a browser shows its worlds and who is here, read only; in a browser your
  workroom linked (3.1), it is your workroom's lobby: its chat, who is here, its worlds;
- **its own relay**: the iroh relay for links that cannot go direct runs inside `lobby`, for offices in the lobby
  only, in place of iroh's public relays (and of coturn, which only the browser sims of version 1 used);
- **anyone can host one**: one file, a few flags, and Caddy in front for its certificate. A workroom joins the
  lobby its `VISIT_LOBBY` names.

What the offices show each other still never passes through it: scenes, voice and pictures go between
visit-clients, directly or through the relay, which cannot read them.

## 2. Decisions

1. **Protocol 4 only, by default.** Version 1 (browser sims streaming video, PROTOCOL.md) and version 2 (Visit 0.2
   to 0.4) go. Neither has a visit-client, so neither can meet rule 3. Version 3 (Visit 0.5 to 2.x) stays,
   behind a flag (`--v3`), as a lobby of its own, so an operator can carry old workrooms through the upgrade.
   Version 3 offices do not see version 4 offices, and are not in channels or the directory.
2. **Identity is the key.** An office's key (its iroh endpoint's public key, SPEC 2) was already its lasting
   identity to its friends: guests, layouts and whispers left for someone are all by key. The lobby now proves
   it. `hello` names the key, the lobby answers with a challenge, and the visit-client signs it with the key's
   secret half. The lobby checks the signature before anything else. What is signed names the lobby the
   visit-client dialled, so a lobby cannot pass a challenge on to another and come in as its visitor. A link's
   endpoint must then be the proven key. A lobby has no accounts (the only passwords it holds are its worlds', as
   Argon2id hashes), so one key is the same office in every lobby it joins, self-hosted or not.
3. **Signed in to take part.** A visit-client joins a lobby only while its workroom has someone signed in: a
   live session of its sign-in (../auth/SPEC.md 4: made by `/v1/login`, not yet ended by signing out, a revoke
   or its idle and total limits). API keys (scripts, the engine) do not count. A workroom without the sign-in
   (no `AUTH_TOKEN_FILE`), or with nobody signed in, keeps to itself, as with no lobby, and its page is told why.
   The lobby cannot see into a workroom, so this is the visit-client's rule. What the lobby can check, the key,
   it checks.
4. **Names** stay first come, first served, in each lobby. A key coming back finds its name if nobody has taken
   it. Who someone is to you is their key: Visit marks friends (guests) by key, never by name.
5. **The lobby keeps nothing** of what is said. What it keeps, in its data directory, is its worlds (who is in
   which, by key; a password's hash; the keys kept out), its linked browsers (a browser's key, its workroom's) and
   its bans: the operator's, never anyone's words.
6. **Operators** (`--admin <key>`, the lobby's own owners) ban keys lobby-wide (for a while, or for good), set
   its message of the day, and run every world.
7. **A world's members are as the lobby says.** A host's visit-client lets a co-member watch its plot (its door
   open) on the lobby's word for who is in the world: join worlds in lobbies you trust. Guests stay as they were,
   proved by the link (SPEC 3, point 9). Later: member lists signed by the world's owner, which a lobby could not
   forge.
8. **World chat is text.** Voice stays as it is (SPEC 8.7): by who is near in a room, and across a plot's side
   where both worlds agree. (4.0, #58: and a world's **huddle**, by the owner's word: one member starts it, it rings for
   the others online, and whoever joins is in the starter's room's voice, carried by the starter's office as any room
   voice is; the lobby only says it started and ended. And **talking**: two people, voice alone, by a knock to talk
   the other answers. The lobby carries the knock, never a sound.) Anyone on a plot is in that room, however they came (a knock, a guest, a world, a
   link): they see and hear each other there, linked or not.
9. **Visit draws the lobby.** The chat window gets each world's chat (`/g <world>`), the panel gets the worlds,
   who is here, and the browsers linked. Rooms may draw their own from the API, as for everything else.
10. **Users and worlds, nothing else (3.1).** 3.0 had world groups that took requests, with ops, and beside them
    rooms open to anyone (a public door) in a list of rooms: two ways to be open. Now a room is not open by itself;
    a world is. Public: anyone in the lobby joins with a click. A password: anyone who has it; nobody need be online
    to let them in, as requests needed. Its owner keeps keys out, gives a new password (its members stay), hands it
    on. A public world takes at most `--world-size` members (50): each member's plot is on every other's map. Your
    door stays yours: a world puts your plot on its members' maps, and its members walk in through your open door (4.0: no knocks).
    Worlds with a password are listed with a 🔒 (Diablo II hid its password games; worlds are meant to be found).
11. **A browser your workroom linked (3.1).** A browser at the lobby's page has no sign-in: the workroom has that,
    and its key. So the browser makes a key of its own (never leaving it), and the workroom vouches for it, once:
    its room's 🌐 Lobby (or 📱 Phone: a QR code) has its visit-client sign a web pass (its key, this lobby, 10
    minutes, once) that links the browser that opens it; or the page shows a code, typed in the room (Link a
    browser) with the same three pictures on both screens, and then (3.2) the browser shows the room's confirmation
    back: its QR code scanned by the browser's own camera, or its digits typed there. A code proves the room saw the
    browser; the confirmation, that the browser sees the room: a code someone talks you into typing links nothing
    without your screen. Linked, the browser comes back by its key alone, and
    speaks for the workroom while the workroom is in the lobby, until either unlinks it. Talk, worlds, the list,
    yes; letting anyone in, never: that stays in the room. Signatures, not a shared secret: the lobby
    needs proof that the room vouches for this browser, not a channel the two share. The page, unlinked, is read
    only: lobby chat is said to the workrooms in the lobby, not to the web.
12. **One protocol crate.** `lobby/proto` (the `lobby-proto` crate) has what both ends must agree on: the
    messages a visit-client sends, as types; the limits; the name rule; what is signed. `lobby` and `visit` are
    both built with it, so they cannot drift apart.

## 3. From Battle.net (Diablo II's lobby)

What it did well, and what Visit's panel and chat window take from it:

- **Lists to browse, not names to know.** Its channel list was "so lovely and useful"; Diablo II Resurrected's
  `/channel join <name>`, with nothing to browse, was panned. The panel and the page list the worlds (their size,
  how many are online, public or 🔒) and who is here; commands are a shortcut, never the way.
- **Chat beside the lists.** Its chat stayed on screen while the game list was open beside it. The chat window
  stays where it is while the panel browses.
- **A game's details: who is in it.** Picking a game showed its players, its description, its limits. A world's
  entry shows its line, its members (online or not, its owner ♛), and Join, or Join… with its password, as a
  password game asked for one. Someone's entry shows their line, their tags, how many are in their room out of how
  many it takes, and Whisper (4.0: no Knock nor Link; worlds are the way to meet).
- **Who is online, for friends.** Its friends list said what each friend was doing, and which game only to mutual
  friends. Visit marks co-members (⚑: 3.3, its guests) among those online. Where anyone is, the lobby tells nobody
  (its list has no `visiting` in version 4): those they visit know.
- **A greeting.** Its login said how many were online. Visit's chat says the lobby's message of the day, and how
  many are in it, as it joins.
- **What it got wrong:** ops and bans that ended when the op left (so bots sat in channels to hold them: groups
  here are kept, by key); flood protection that dropped lines without a word (every refusal here is said); a game
  list that went stale (the lobby's list is pushed as it changes).

## 4. Phases

| # | phase | state |
|---|---|---|
| 1 | `lobby` in Rust, protocol 3 as the Python one spoke it, checked by its own tests (run against the binary) | done: lobby/tests/test_lobby.py, the Python lobby's cases carried over |
| 2 | protocol 4: the key proved, the sign-in gate, bans and operators; visit-client 3.0 speaks it | done: the e2e signs each workroom in, out and in again |
| 3 | world groups: the lobby (kept on disk), visit-client (watching co-members), visit.js (group chat, panel) | done: members watch each other's plots (e2e), group chat, the panel's groups |
| 4 | the directory: `about`, the public door; friends online in the panel | done: Walk in at a public door (e2e) |
| 5 | the public page | done: `GET /`, `GET /api/lobby` (tests: the directory) |
| 6 | the relay inside `lobby` | done: lobby/tests/relay.rs (two in the lobby link through it, no direct path; a stranger kept off; QUIC address discovery up) |
| 7 | hosting one: the release's `lobby` files, `lobby/deploy` without Docker, the README | done: setup.sh dry-run under systemd in a container (all but Let's Encrypt) |
| 8 | the specs, the changelog, every test (ci.yml's steps, here), the pull request | done: every ci.yml step here (unit 45, lobby 153, e2e 290, browser 302 checks); an independent review's findings fixed (the Host a hello may sign, bounded queues and rates, group limits, a reconnect's groups) |
| 9 | 3.1: users and worlds (public, or a password; owner's kick, ban, hand on; `--world-size`), no public door | done: the lobby's tests (worlds, a 3.0 world kept closed until opened), e2e (a password world through visit-client) |
| 11 | 3.2: linking by code confirmed both ways: the room's QR code scanned by the browser's camera (`#verify=`), or its digits typed | done: the lobby's tests (a QR code's tab, typed, another browser refused, wrong ones counted), e2e and the browser test (the panel's QR code, the digits typed), the page in Chrome (its prompt; a #verify tab linking both) |
| 10 | 3.1: linked browsers (a key of their own; a pass, a QR code, or a code typed in the room), the page as a workroom | done: the lobby's tests, e2e (a pass and a code through visit-client), the browser test (the panel: 📱, a code, allow, unlink), the page in Chrome (linked by its code, a reload) |
