# The lobby: where workrooms meet

`lobby` is one program: **users and worlds**. Users are the workrooms in it (who is online, a line about each);
worlds are groups of them whose members put each other's rooms round their own, public or with a password. Knocks
between rooms, visits and links, the lobby's chat and whispers, each world's chat, its own **relay** for links that
cannot go direct, and its **page**: read only for anyone with a browser, and, in a browser a workroom linked (its
Visit panel's 🌐 Lobby, 📱 Phone, or Link a browser and the code the page shows), that workroom's own lobby,
Battle.net style: its chat, who is here, its worlds, knocking. Workrooms join it with Visit
(`VISIT_LOBBY`), and only while someone is signed in on them. What rooms show each other never passes through it.
[../LOBBY.md](../LOBBY.md) says why it is as it is; [../visit/SPEC.md](../visit/SPEC.md) section 6 is its protocol,
`basedverse-lobby/4`.

basedverse runs one at **`wss://basedverse.app/`**, the lobby `bin/setup` offers a new workroom. Anyone can run
their own: it is one file and a few settings.

| | |
|---|---|
| [src/](src/) | the lobby: `core.rs` (all its state, one task), `conn.rs` (a connection, its key proved), `groups.rs` (what it keeps: worlds, linked browsers, bans), `relay.rs` (its iroh relay), `web.rs` (HTTP: the WebSocket, `/health`, the page) |
| [proto/](proto/) | `lobby-proto`: what it and `visit` agree on (messages, limits, what a hello signs), built into both |
| [web/index.html](web/index.html) | its public page |
| [tests/](tests/) | `test_lobby.py` (the binary, from outside: `tests/run.sh`), `relay.rs` (its relay, with iroh endpoints) |
| [deploy/](deploy/) | a server's setup: the lobby and Caddy under systemd (`setup.sh`, which writes Caddy's config too) |

## Try it here

```sh
lobby/cargo run -p lobby -- --listen 127.0.0.1 --port 8765        # ws://127.0.0.1:8765, its page at http://127.0.0.1:8765/
```

Then point two workrooms at `ws://127.0.0.1:8765` (`VISIT_LOBBY`), each with its owner signed in. `lobby --help`
lists every setting; each is a flag and a `LOBBY_*` variable.

Tests: `lobby/tests/run.sh` (the lobby from outside, every message and refusal) and `lobby/cargo test -p lobby`
(its relay, with real iroh endpoints); `visit/client/tests/run-e2e.sh` runs it with real visit-clients.

## Host your own

What it needs: a Linux server with systemd (x86_64 or ARM64; an EC2 `t4g.small` or `t3.small`, about $12 a month,
is plenty), a DNS name pointing at it (an `A` record; not proxied, if your DNS host offers that: its relay speaks
UDP), and these open to the internet:

| port | protocol | for |
|---|---|---|
| 22 | TCP | ssh (your address only) |
| 80, 443 | TCP | Caddy: the certificate's challenge, then `wss://` and the page |
| 7842 | UDP | the relay's address discovery: how two workrooms find a direct path to each other |

From your machine, in this repository (it builds the lobby with Docker here; the server needs nothing installed):

```sh
LOBBY_DOMAIN=lobby.example.org bin/deploy-lobby ubuntu@203.0.113.5     # -i ~/.ssh/key.pem if ssh needs it
```

It builds one static `lobby` for the server's architecture (or, with `--version v3.0.0`, takes a release's,
its checksum checked), copies it there with `deploy/`, and runs `deploy/setup.sh`, which:

1. stops a lobby of before, if there is one (Visit 2.x's: Python, Caddy and coturn in Docker);
2. installs the lobby (`/usr/local/bin/basedverse-lobby`) and Caddy (a pinned release, its checksum checked), and a
   user they run as;
3. writes their settings (`/etc/basedverse-lobby/env`) and systemd units, starts Caddy, waits for its Let's
   Encrypt certificate, starts the lobby with its relay (which reads the same certificate), and prints the address:

```
LOBBY_URL=wss://lobby.example.org/
```

Each workroom gives that as its lobby (`VISIT_LOBBY` in its `.env`). Its page is `https://lobby.example.org/`.

**Its settings**, each given as a variable to `bin/deploy-lobby` (or kept in this repository's `.env`), and kept
on the server once given:

| variable | |
|---|---|
| `LOBBY_DOMAIN` | its DNS name (default `basedverse.app`) |
| `LOBBY_TITLE`, `LOBBY_MOTD` | its name and message of the day: on its page, and said to each workroom as it joins |
| `LOBBY_ADMINS` | its operators: keys, comma-separated (a workroom's key: `ready.key`, `v.key`). They ban keys and run every world |
| `LOBBY_WORLD_SIZE` | the members a world takes, at most (50): each member's plot is on every other member's map |
| `LOBBY_V3` | `1`: let Visit 0.5 to 2.x workrooms in too, a lobby of their own, while they upgrade |
| `LOBBY_IP` | the server's IPv4, served as well (`wss://<ip>/`, a 6-day certificate Caddy renews), for workrooms that still name the lobby by it |

**Running it**, from your machine (the server is remembered: `LOBBY_DEPLOY` in this repository's `.env`):

| command | what it does |
|---|---|
| `bin/deploy-lobby` | update it: a new lobby, the same settings; its worlds, linked browsers and bans kept |
| `bin/deploy-lobby --status` | its services, and whether `wss://…/health` answers from here |
| `bin/deploy-lobby --logs` | the lobby's and Caddy's logs, followed (Ctrl-C stops) |
| `bin/deploy-lobby --down` | stop it |

On the server: `systemctl status basedverse-lobby basedverse-caddy`, `journalctl -u basedverse-lobby`. What it
keeps (its worlds, linked browsers and bans) is in `/var/lib/basedverse-lobby/data`; Caddy's certificate in
`/var/lib/basedverse-lobby/caddy`. A restart keeps both; workrooms reconnect by themselves.

**Without bin/deploy-lobby** (another init system, a proxy of your own): run `lobby` behind any TLS proxy that
passes WebSockets, with `--hosts <your name>` (what hellos sign), `--trust-proxy` (the proxy's `X-Forwarded-For`),
`--data-dir`, and, for its relay, `--relay-port` (route `/relay`, `/ping` and `/generate_204` to it) and
`--relay-cert`/`--relay-key` (your name's certificate, for UDP 7842).

**What it costs.** The server, and its traffic. Links go directly between workrooms whenever their networks allow;
only when they cannot does the relay carry them (at most 4 MiB a second each way, for a workroom in the lobby).

## Trust

- **Taking part takes Visit, signed in.** A workroom joins only while someone is signed in on it, and proves its
  key at every hello: a key cannot be taken, and a name is just a name (first come, first served).
- **Nothing of your room is visible until you let someone in.** A knock asks your page; a guest (★) walks in through
  your open door. Whoever is let in gets your room's scene, all of it.
- **A world's members are the lobby's word.** They see your plot through your open door, as guests do; a public
  world is anyone's to join. Join worlds in lobbies you trust; your guests stay proved by the link itself.
- **A world's password** is kept as an Argon2id hash, and an office tries at most 5 a minute.
- **A linked browser** speaks for its workroom only after the workroom linked it (a pass it signed, or the code the
  browser shows, typed in its room), only while the workroom is in the lobby, and never lets anyone in: answering
  knocks and the door stay in the room. The browser's key never leaves it; the workroom unlinks it from its panel, and the browser can unlink itself, its room in the lobby or not.
- **The lobby keeps no words.** Its chat, whispers and world lines pass through and are gone. It keeps its worlds
  (who is in which, by key), its linked browsers (their keys, their workrooms') and its bans.
- **Its page shows** its worlds, and who is here with a line (by name: never a key, nor where anyone is).
