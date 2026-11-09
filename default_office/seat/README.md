# seat — what every agent session runs on

The agent side of the board: one CLI, four hooks, a status line and two skills, wired into each project checkout
agents work in by `install.py`. The board server they talk to is in `../forge/`.

| path | what |
|---|---|
| `tools/forge.py` | the whole board API: GitHub issues and PRs of the configured repos, the agent roster (enter, claim, release, exit), the Up next plan, and messages between agents (`tell`, `inbox`). `python3 ~/.claude/tools/forge.py -h` lists every verb. |
| `hooks/board_ready_set.py` | SessionStart: puts the roster, the open PRs and the ranked ready set in the session's context |
| `hooks/session_title.py` | SessionStart: titles the session with its roster handle (`amber-heron`) |
| `hooks/agent_state.py` | writes `ctx` (context in use), `blocked` (waiting on you: a question dialog, a permission prompt, or a turn that ended asking you something), `dir` (the directory it runs in, `~` for home) and `subs` (the subagents it has working) onto the session's roster entry |
| `hooks/commit_issue_check.py` | after a `git commit` that cites no issue, reminds the agent to update or close it |
| `hooks/usage_line.py` | the status line: notes the account's usage limits (the current session's and the week's, as Claude Code hands them to a status line) in `../.usage/`, which the board falls back to when `bin/usage` is not running, then shows the status line you had (the project's, else yours) |
| `skills/go`, `skills/stop` | `/go` joins the roster and picks up work; `/stop` leaves the board true and the roster |
| `settings.json` | the hooks and status line template `install.py` merges into a project (`$HOOKS`, `$PYTHON` are filled in) |
| `install.py` | wires all of the above into this machine and the projects named (`bin/init-agent` runs it on AGENTS) |

## Configure

One `.env` in the root holding `seat/` and `forge/` (or the file `$REPO_ENV` names) says which project
this points at. Both `forge.py` and the board container read it; a process env var of the same name
wins over it, and a blank value counts as unset.

| key | what |
|---|---|
| `REPO_OWNER` | the GitHub org or user |
| `REPO_NAMES` | the repos the board spans, comma or space separated; blank = every repo of the owner. With exactly one, a bare `#87` means it. |
| `REPO_HOME` | the repo holding the Up next (`RESUME`) issue; defaults to the single repo. The roster is the board's own, in its database |
| `REPO_RANK` | label names, highest rank first, comma separated (`severity: high, severity: medium, severity: low`): the order inside each column; a row with none is unranked, last |
| `REPO_WORKFLOW` | optional: where the board's columns are defined (default `workflow.json` beside the `.env`; see `forge/README.md`) |
| `REPO_TOKEN` | a GitHub token with issues and pull requests read/write. Blank: `GH_TOKEN` / `GITHUB_TOKEN`, then `gh auth token`. The board container needs it set. |
| `REPO_BOARD_URL` | where `forge.py` reaches the board server (default `http://localhost:3001`) |
| `REPO_BOARDS` | the other boards, `owner/repo` each: every other GitHub repo an agent directory is a checkout of (`bin/init-agent` adds them) |
| `REPO_BOARD` | not in the `.env`: set in a directory's `.claude/settings.local.json` by `install.py` (`env`), the board a session there works from: `owner/repo` for another board's checkout, `none` for a directory with no GitHub repo; unset is the home board |
| `REPO_URL`, `REPO_PUBLIC_URL` | only for GitHub Enterprise |

## Install

The directories agents work in are `AGENTS` in the `.env` (comma-separated checkouts of the repo).

```sh
bin/init-agent                       # a prompt: lists the agents, adds one (cloned first if it does not exist)
bin/init-agent ~/code/App-4          # the same without the prompt; --all sets up every AGENTS directory again
bin/init-agent --dry-run             # either way, see the plan first
bin/start                            # the board: start or restart it (fills a blank REPO_TOKEN from `gh auth token`)
bin/down                             # stop the board (messages and the roster are kept)
```

`bin/init-agent` runs `seat/install.py` on those directories (it can also be run by hand:
`python3 seat/install.py [--dry-run] DIR ...`). It links `~/.claude/tools/forge.py` to `tools/forge.py`, links the project's `.claude/skills/go` and
`/stop` here, and merges the hooks into the project's `.claude/settings.local.json` (backed up first;
your own keys and hooks are kept; re-running replaces its own entries, never duplicates them), with its
status line, unless that file has a status line of its own (kept: the board then has no usage limits from
that project's sessions to fall back to). Whichever of those the checkout does not already ignore go in its
`.git/info/exclude` (local, never committed: a public repo is left with no trace). Nothing
real is overwritten: a skill directory that is not its link is reported and left alone.

A directory that is a checkout of another GitHub repo than `REPO_NAMES`'s works from that repo's own board:
`bin/init-agent` adds the repo to `REPO_BOARDS`, gives the directory `REPO_BOARD` (so `forge.py` there means that
repo), and makes the home repo's flow there (the labels the columns and `REPO_RANK` use, in the home repo's colours).
Its roster entries and Up next stay in `REPO_HOME`. A directory with no GitHub repo gets `REPO_BOARD=none`: a desk and
a console in the Agent Room, no board. Then:

1. Fill the `.env` (`gh auth login` is enough of a token for `forge.py`), and the columns in `workflow.json`.
2. Once per home repo: `python3 ~/.claude/tools/forge.py newlabel off-board`. The `RESUME` issue
   carries it, and an `off-board` issue is never a board row.
3. Start the board: `bin/start` (<http://localhost:3001>, the Agent Room at `/room`).
4. In a Claude Code session in one of the AGENTS directories: `/go`.

## Conventions forge.py keeps

- Refs are `repo#N` as GitHub spells the repo (`WebApp#87`), or a bare `#N` with one repo in scope.
- Scoped labels (`scope/value` or `scope: value`) are exclusive: `label` replaces a same-scope
  sibling, and `create` / `label` refuse two in one scope. GitHub itself does not enforce this.
- `close` refuses while the issue has an open blocker (`block` / `unblock` are GitHub's issue
  dependencies); GitHub itself would close it anyway.
- `-m` / `-b` take literal text: prose with backticks or `${...}` goes through `-F file`.
- Every call bumps the agent's roster `seen`, and prints `MAIL: n unread` when it has messages.
- GitHub's API quota is 5000 requests/hour per account, shared by every agent and the board running as that user: read
  freely, but never loop `forge.py` in a script.
