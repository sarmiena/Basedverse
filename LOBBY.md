# The lobby, in Rust: a place to meet

Branch: `lobby-4`. Release: Visit 3.0.0 with the lobby's protocol `basedverse-lobby/4`. Nothing of it reaches
`main` until all of it is built and tested; then one pull request.

## 1. What it is

The lobby was a Python program that introduced visit-clients to each other: who is online, knocks, visits and
links, one lobby-wide chat, whispers. It becomes **one Rust program, `lobby`**, in the workspace beside `visit`,
and **a place to meet**, the way a game's lobby was (Battle.net in its Diablo II days):

- **world groups**: a group's members may each put the others' plots in their worlds (SPEC 8.4), as if each
  were everyone's guest, for worlds only. Whoever makes a group owns it and names its ops; ops let people in
  (or the group is open: anyone joins). Someone in several groups may place anyone of any of them, together, in
  their own world. A link (SPEC 8.5) still reaches anyone outside your groups. Each group has its own chat;
- **the lobby-wide chat and whispers**, as before;
- **a room directory**: rooms describe themselves (a line and a few tags) and say whether anyone may walk in
  (a public door) or knocks, as today;
- **friends**: Visit's guests, by key, shown online and where they are;
- **a public page**: the lobby's address in a browser shows its channels and its directory, read only, and how
  to join it with Visit;
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
   endpoint must then be the proven key. A lobby has no accounts and keeps no passwords, so one key is the same
   office in every lobby it joins, self-hosted or not.
3. **Signed in to take part.** A visit-client joins a lobby only while its workroom has someone signed in: a
   live session of its sign-in (../auth/SPEC.md 4: made by `/v1/login`, not yet ended by signing out, a revoke
   or its idle and total limits). API keys (scripts, the engine) do not count. A workroom without the sign-in
   (no `AUTH_TOKEN_FILE`), or with nobody signed in, keeps to itself, as with no lobby, and its page is told why.
   The lobby cannot see into a workroom, so this is the visit-client's rule. What the lobby can check, the key,
   it checks.
4. **Names** stay first come, first served, in each lobby. A key coming back finds its name if nobody has taken
   it. Who someone is to you is their key: Visit marks friends (guests) by key, never by name.
5. **The lobby keeps nothing** of what is said. What it keeps, in its data directory, is its world groups (who
   is in which, by key) and its bans: the operator's, never anyone's words.
6. **Operators** (`--admin <key>`, the lobby's own owners) ban keys lobby-wide (for a while, or for good), set
   its message of the day, and run every group.
7. **A group's members are as the lobby says.** A host's visit-client lets a co-member watch its plot (its door
   open) on the lobby's word for who is in the group: join groups in lobbies you trust. Guests stay as they were,
   proved by the link (SPEC 3, point 9). Later: member lists signed by the group's owner, which a lobby could not
   forge.
8. **Group chat is text.** Voice stays as it is (SPEC 8.7): by who is near in a room, and across a plot's side
   where both worlds agree. Anyone on a plot is in that room, however they came (a knock, a guest, a group, a
   link): they see and hear each other there, linked or not.
9. **Visit draws the lobby.** The chat window gets each group's chat (`/g <group>`), the panel gets the groups
   and the directory. Rooms may draw their own from the API, as for everything else.
10. **The public page is read only.** Taking part takes a visit-client (rule 3). A browser without one has no key,
   so it can watch, and that is all. A room appears in the directory, on the page and in Visit, only once it has
   described itself (`about`). Offices that have not are counted, not named. Groups show with their size.
11. **One protocol crate.** `lobby/proto` (the `lobby-proto` crate) has what both ends must agree on: the
    messages a visit-client sends, as types; the limits; the name rule; what is signed. `lobby` and `visit` are
    both built with it, so they cannot drift apart.

## 3. From Battle.net (Diablo II's lobby)

What it did well, and what Visit's panel and chat window take from it:

- **Lists to browse, not names to know.** Its channel list was "so lovely and useful"; Diablo II Resurrected's
  `/channel join <name>`, with nothing to browse, was panned. The panel lists the groups (with their size, how many
  are online, open or by request) and the rooms that describe themselves; commands are a shortcut, never the way.
- **Chat beside the lists.** Its chat stayed on screen while the game list was open beside it. The chat window
  stays where it is while the panel browses.
- **A game's details: who is in it.** Picking a game showed its players, its description, its limits. A room's
  entry in the panel shows its line, its tags, how many are inside out of how many it takes, and Walk in (a public
  door) or Knock.
- **Who is online, for friends.** Its friends list said what each friend was doing, and which game only to mutual
  friends. Visit marks guests (★) and co-members (⚑) among those online. Where anyone is, the lobby tells nobody
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
