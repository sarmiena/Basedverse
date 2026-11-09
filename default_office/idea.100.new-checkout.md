# New agent → a new checkout

**From:** the ticket panel's **New agent** (commit fa78bc4). Today it offers only the agents' directories that
already exist (`AGENTS` in `.env`) and have no agent in them. When all three are taken, there is nowhere to start one.

## What

A last choice in the New agent picker: **new checkout…** You type a path (suggested: the next free
`~/code/WebApp-N`). bin/consoles then runs `bin/init-agent <path>`, which:

1. clones the repo there (`gh repo clone`, the repo in `REPO_NAMES`);
2. installs the seat (`seat/install.py`: `/go` and `/stop` linked into `<path>/.claude/skills`, the board's hooks
   merged into `<path>/.claude/settings.local.json`);
3. adds the path to `AGENTS` in `.env`.

After that it goes on as now: a new herdr tab in the chosen workspace, in that directory, Claude started, then
`/go <ticket>`. The console opens on the click and says what it is doing: cloning, setting up, starting.

## Why

So a ticket can always get a fresh agent from the room, without leaving it to run `bin/init-agent` by hand first.

## How (where it goes)

- **room.html** (`ticketPick`, `ticketAct`): the **new checkout…** button and a path box. The suggested path comes
  from bin/consoles (`places` can return `next`: the first `WebApp-N` not in `AGENTS` and not on disk).
- **server.py**: `spawn` takes `{new: path}` in place of `{dir}`, with a longer wait (a clone takes a while), or a
  separate `checkout` call the page makes first.
- **bin/consoles** (`spawn`): for `new`, runs `bin/init-agent <path>` (non-interactive: `bin/init-agent DIR`),
  reads `AGENTS` again, then carries on as for any listed directory.

## Care

- The page sends a path, the one thing today's spawn never takes. bin/consoles must keep it to a known parent:
  under the parent of the existing `AGENTS` directories, a plain name (`[A-Za-z0-9._-]+`), not existing yet or an
  empty directory. Never `..`, never somewhere else.
- `bin/init-agent` edits `.env`. The board and bin/consoles read `AGENTS` from it on each call, so nothing needs
  restarting.
- A clone can fail (no network, gh not signed in): say why in the console, and leave no half-made `AGENTS` entry.
- Tests: `test_consoles.py`, a fake `init-agent` (what it was given, and what is refused before it runs).

## Done when

From a ticket, **New agent → new checkout… → WebApp-4** clones, sets up, starts Claude and gives it `/go <ticket>`.
WebApp-4 is in `AGENTS` afterwards, and appears in the picker next time as taken.
