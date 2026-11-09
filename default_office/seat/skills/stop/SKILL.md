---
name: stop
description: End a working session cleanly — make the tickets say what the session actually did, update the Up next plan, release claims and leave the agent roster. Use when the user invokes "/stop", or says "stop", "wrap up", "let's end here", "that's it for today" or "hand off".
---

# /stop — leave the board true, and the next session one `/go` away

A session is not over until every step below is done. An untracked loose end is invisible to the next
agent. **The project's own CLAUDE.md wins** over anything here (closeout files, PR flow).

## 1. See what you touched

```
python3 ~/.claude/tools/forge.py agents     # your entry's claims are the items you worked on
python3 ~/.claude/tools/forge.py prs        # your open PRs (the board names their owner)
```

## 2. Make each item say what happened

| what happened | what to do |
|---|---|
| merged | `forge.py close <ref> -m "<PR, sha, what landed>"` |
| PR open, waiting on review | leave the issue open; comment where it stands |
| partly done | comment what is done and what remains (`-F file`); keep it open |
| a new problem found, not fixed | `forge.py search` first, then `forge.py create` with a rank label |
| a title or body that is now false | `forge.py edit` it in place |

Then let go: `forge.py agent release <ref> ...` for everything you no longer hold.

## 3. Update Up next

```
python3 ~/.claude/tools/forge.py resume              # the current plan
python3 ~/.claude/tools/forge.py resume -F plan.md   # replace it
python3 ~/.claude/tools/forge.py resume --clear      # nothing in flight: the board's ranking decides
```

The plan is numbered steps, each naming one `repo#N` first, each line at most 110 characters; the
context belongs on the issue, not in the plan. `resume -F` refuses prose. Keep only what is in flight
or must come before the board's own ranking. The user reorders it on the kanban; rewrite it from what
`resume` prints, never from memory.

## 4. Leave the roster — the LAST forge call

```
python3 ~/.claude/tools/forge.py agent exit
```

Stop the background `inbox --wait` waiter if it is still running (it also ends by itself once you have
left). Any forge.py call after `exit` does not re-enter you; the next session enters with `/go`.

Finish by telling the user what landed, what is open and where, and that the next session resumes with
a bare `/go`.
