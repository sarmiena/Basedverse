---
name: go
description: Start or resume a working session on the project's tickets, alongside other agents. Use when the user invokes "/go", or asks to "continue", "resume", "pick up where we left off" or "what's next". Joins the agent roster, starts the message waiter, reads open PRs, the Up next plan and the ranked board, then puts the pick to the user.
---

# /go — join the roster and pick up work

`python3 ~/.claude/tools/forge.py <cmd>` is the whole board: GitHub issues and PRs of the repos in
the project `.env` (`REPO_OWNER`, `REPO_NAMES`), plus the board server's roster, Up next plan and
messages. **The project's own CLAUDE.md wins** over anything here (branch names, PR flow, how specs
are written); this skill only adds the coordination with other agents.

**Your board is your directory's.** A checkout of another GitHub repo than the `.env`'s has a board of its own
(`REPO_BOARD` in its `.claude/settings.local.json`, written when the directory was set up): `board`, `resume`,
`prs`, `create` and a bare `#N` all mean that repo, while the roster is everyone's. A directory with no GitHub repo
has no board (`forge.py board` says so): enter the roster and start the waiter (step 0), then skip steps 1 to 3 and
ask the owner what to work on.

## 0. Enter the roster — the first write of every session

Call `ListAgents` first and take this session's own name from it, then:

```
python3 ~/.claude/tools/forge.py agent enter -d "/go: reading the board" --msgname "<name from ListAgents>"
```

It prints your roster line (`<- you`). Every other entry is another agent: **what it claims is not
yours to pick, review or merge.** Re-run `agent enter -d "..."` whenever what you are doing changes.

Then start the message waiter, in the background (`run_in_background`); its exit wakes you:

```
python3 ~/.claude/tools/forge.py inbox --wait
```

When it returns, read the messages it printed, act on them, and start it again. It is your ONE reader:
don't run a second `inbox` beside it. Reply with `forge.py tell <handle> -m "..."` (or `owner`, `all`).
A `MAIL: n unread` line on any other forge.py call means the waiter is about to return.

## Given a ticket: `/go <repo>#<N>`

The owner handed you that ticket (from the Agent Room: a ticket's **New agent**). After step 0, skip the pick:
claim it, read all of it, then talk it through with the owner before you start.

```
python3 ~/.claude/tools/forge.py agent claim <repo>#<N> -d "on <repo>#<N>: reading it"
python3 ~/.claude/tools/forge.py show <repo>#<N>    # body AND every comment, oldest first
```

Then say back what it asks and how you would go about it, raise what is unclear, and wait for the owner's word.
They may already be typing to you (their console opened as you started): read what came in first. If another
agent holds the claim, say so and stop; never `--steal` it. Then go on from step 4.

## 1. Read the state

```
python3 ~/.claude/tools/forge.py prs       # open PRs first: finished work rests here while its issue is open
python3 ~/.claude/tools/forge.py resume    # Up next: the ordered plan the user keeps on the board
python3 ~/.claude/tools/forge.py board     # the roster, the columns, then the ready set, ranked by REPO_RANK
```

- An open PR whose issue another agent claims is **that agent's** live work — say so, don't touch it.
- A PR with no live owner may be work left behind: report it before starting anything new.
- `board` already drops claimed, blocked and parked rows; `--all` shows them with the reason.

## 2. Put the pick to the user

With nothing of yours mid-flight, offer up to four candidates in an `AskUserQuestion`: the first Up
next step that nobody claims, then the highest-ranked ready rows. Say for each why it is ready.
Nothing pickable is a fine answer — say so and stop.

## 3. Claim it, then read all of it

```
python3 ~/.claude/tools/forge.py agent claim <repo>#<N> -d "on <repo>#<N>: <what>"
python3 ~/.claude/tools/forge.py show <repo>#<N>    # body AND every comment, oldest first
```

`claim` refuses an item another agent holds; never `--steal` without the user's word. Decisions and
measurements live in the comments, not the body — read them before forming a plan. Refs are
`repo#N` as GitHub spells the repo (`WebApp#87`), or a bare `#N` when one repo is in scope.

## 4. Do the work

- Work on a branch; open the PR early: `forge.py pr <repo> --head <branch> -t "..." -F body.md`
  (`--base` defaults to the repo's default branch). Name the issue (`WebApp#87`) in the title: the
  board attributes a PR to whoever claims the issue it names, else to the claude.ai session link
  `forge.py pr` appends to the body.
- Prose with backticks or `${...}` goes through `-F file`, never `-m`/`-b` (the shell eats it).
- Record what you learn on the issue: `forge.py comment <ref> -F note.md`; correct a false title or
  body in place with `forge.py edit`, not with a retraction comment.
- New work that outlives this session gets an issue: `forge.py search <words>` first (open AND
  closed), then `forge.py create <repo> -t "..." -F body.md --label "<rank label>"`.
  Order between issues is an edge: `forge.py block <ref> --on <blocker>`.
- Scoped labels (`scope/value`, `scope: value`) are exclusive: `forge.py label` replaces a sibling.
- The board's columns are the project's workflow (`workflow.json`). Claiming moves a card to In progress
  (and puts the workflow's claim label on the issue; releasing takes it off), and opening a PR that names
  it moves it to In review, on their own. Waiting on the owner's answer? `forge.py move <ref> "Needs
  input"` and say what you need in the issue. `forge.py move` sets that column's label, exactly as a
  drag on the kanban does.
- Merge through `forge.py merge <ref>` (never a push to the trunk: the PR would stay open), then
  `forge.py close <ref> -m "<what landed, PR, sha>"` — it refuses while a blocker is open.
- Let go of what you no longer hold: `forge.py agent release <ref>`.

Need another agent? `forge.py agents -v` lists every agent with its ListAgents name (for
SendMessage on its own machine) and `forge.py tell <handle> -m "..."` reaches it on any machine.

## 5. Stop

End with `/stop`: it releases your claims, updates Up next and takes you off the roster.
