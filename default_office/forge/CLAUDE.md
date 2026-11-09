# forge — working notes for sessions editing the board

## This checkout IS the live board

The board runs from this working tree (`python3 board/server.py`, started by `bin/start` on this machine, no
Docker: `bin/lib/board.sh`), so what is checked out here is what everyone sees at <http://localhost:3001>:

- **`index.html`, `room.html`, `common.js`, `console.js`, `motion.js`, `party.js` (your agents: the room's figures and the list both pages draw), the room's three.js (`vendor/`, never a CDN's: `vendor/VENDOR.md`) and the tarot
  cards (`tarot/`) are read from disk on every request.** An
  edit you save is live at the next page load.
- **`server.py` and the modules it imports (`pick.py`, `roster.py`, `mail.py`, `gone.py`, `tarot.py`, `llm.py`)
  load once, when it starts**: a change takes effect only on `bin/board-restart` (or `bin/start`).
- So a page change that calls a new or changed route is half-deployed until that restart: the new page
  talks to the old server (a quick view reading "could not be read: not found").
- **The room's server page (`room.html?role=host`, in visit's engine: what visitors see) loads once, when `visit`
  starts** (`bin/start`), or when the engine's Chrome fails and it loads it again. `bin/board-restart` does not reload
  it, nor does a refresh of your own page: a `room.html` change reaches visitors (and the 3D your own page draws as the
  room sends it) only at the next `bin/start`.

**The board signs you in** (`board/auth.py`; forge/README.md, "Signing in"): every route asks the sign-in API inside
`visit` (basedverse 2.0, on this machine: `http://127.0.0.1:7403`) first. Each restarts on its own: while `visit` is
down, every route refuses (503), and answers again once it is up. Your own `curl` gets 401: send the agents' key (`-H "Authorization: Bearer $REPO_BOARD_KEY"`), which reaches only the agents' routes (`server.KEY_ROUTES`). A second
server run by hand with no `visit` running refuses everything (503): the tests stand one in
(`board/test_auth.py`). Port 3002 is the board's listener for an HTTPS proxy (Caddy, `tailscale serve`): every
request there is another device's (forge/README.md, "From other devices"). bin/consoles asks the board for the room's
console calls (`/api/console/next`, its key only) and listens on nothing (`board/test_consoles.py` runs it whole).

After a server-side change: `bin/board-restart`, then check `curl http://localhost:3001/api/board`
(the first snapshot takes a few seconds). To try one without touching the live board, run a second
server on another port: `BOARD_PORT=3011 BOARD_DB=<tmp>/board.db python3 board/server.py` (it reads
`../.env`; an env var of the same name overrides it). Stop it by the PID listening on that port, and
don't leave it running: it polls GitHub on the same token.

## The board's model (`board/llm.py`)

A feature that wants words written (a summary, a voice for something in the room) asks `llm.py`, which reaches the
model the `.env` brings: Claude with `LLM_KEY` alone, or a model of your own at `LLM_URL` (README, "The board's model").
Call it from the server, never from a page (a page has no business holding the key or the address): `llm.stream(messages)`
for words as they come, `llm.chat(messages)` for the whole answer, `think=False` by default. Check `llm.configured()`
first, and with no model say `llm.NEED` (the user wants every such feature to say it needs an LLM key, like a Claude API
key), as the tarot reader does. Try a prompt first from a terminal: `python3 board/llm.py "..."`. The
tarot reader (`tarot.py`, `/api/tarot`) is the worked example: a thread streams into state the pages long-poll.

## Parity: served == shown

Every key `/api/board` serves is classed in `board/views.py` by the view that shows it (the kanban,
`forge.py` or the SessionStart hook, per caller, or plumbing). A field no view reads is not served
(`server.UNSERVED`). Run `python3 board/test_views.py` after any change to what the server serves or
what a view reads, and keep parity by classing or subtracting.

## Tests

```sh
for t in board/test_*.py; do python3 "$t"; done    # server, ranking, roster, messages, parity, the tarot reader and llm.py
for t in board/test_*.mjs; do node "$t"; done     # common.js, the Agent Room's motion, what a visitor may reach, party.js, the casino
python3 ../seat/tools/test_forge.py                # forge.py's pure helpers
python3 ../seat/test_install.py                    # setting up a directory on its board (install.py, bin/init-agent)
```

None of them touches the network or GitHub.
