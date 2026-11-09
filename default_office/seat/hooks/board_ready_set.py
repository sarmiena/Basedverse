#!/usr/bin/env python3
"""SessionStart — put the board in front of the model before it does anything, so a session that
never runs /go still knows what is tracked, who is working on what, and where new work belongs.

The board is the FORGE's open issues (GitHub: the owner and repos the project .env names). There is
no separate project object: the rank is a label (REPO_RANK), `blocked by` is a real GitHub
dependency edge, and a finished item is CLOSED, so it is simply absent.

The rows, their blockers, who may pick each one and the agent roster come from the board server's
/api/board: the one place those rules live, read through the same `forge.py` calls `forge.py board`
makes. This hook only prints. The open PRs are read from GitHub here (search, one call).
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from urllib.request import Request, urlopen


def load_forge():
    """forge.py itself, so the hook reads the board through the same calls as `forge.py board`."""
    import importlib.util
    here = os.path.dirname(os.path.realpath(__file__))
    for p in (os.path.join(here, "..", "tools", "forge.py"),
              os.path.join(os.path.expanduser("~"), ".claude", "tools", "forge.py")):
        if os.path.isfile(p):
            break
    spec = importlib.util.spec_from_file_location("forge", p)
    forge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(forge)
    return forge


def repo_name(p):
    """The repo a search hit lives in: search carries only `repository_url`."""
    return ((p.get("repository") or {}).get("name")
            or (p.get("repository_url") or "").rstrip("/").rsplit("/", 1)[-1] or None)


def roster_block(forge, board):
    """The agent roster, printed FIRST, through forge.py's own roster_lines so the hook, `forge.py
    board`, `forge.py agents` and the kanban cannot disagree. Unreadable is said as unknown, never as
    nobody. (Which rows a claim takes off the ready set is the board server's call.)"""
    if board is None:
        return (forge.ROSTER_UNKNOWN % "the board did not serve it" if forge else
                "AGENTS: COULD NOT BE READ — who holds what is UNKNOWN, never 'nobody'.") + "\n\n"
    if not board.get("agents"):
        lines = forge.roster_lines(board)  # a never-read roster says UNKNOWN; [] is truly nobody
        return "\n".join(lines or ["AGENTS: nobody on the roster. /go enters you (`forge.py agent enter`)."]) + "\n\n"
    return ("AGENTS ON THE PROJECT — freshest first. A CLAIM IS ANOTHER AGENT'S WORK: never offer,\n"
            "review or merge it, or the PR for it, as yours. One held by a [STALE] or [DEAD] agent is\n"
            "likely a crashed session: tell the owner, who clears it. `forge.py agents -v` for links.\n"
            + "\n".join(forge.roster_lines(board)) + "\n\n")


def pr_refs(get, owner, prs):
    """Map (repo, number) -> (head, base) for each open PR. A search hit is a PR seen as an issue,
    with no branches, so they come from `/repos/{owner}/{repo}/pulls`: one call per repo that has a
    PR, concurrently. Worth it: the head is what you would check out, and the base matters too."""
    out = {}
    repos = sorted({repo_name(p) for p in prs} - {None})

    def one(repo):
        try:
            return repo, get("/repos/%s/%s/pulls?state=open&per_page=100" % (owner, repo))
        except Exception:
            return repo, []

    with ThreadPoolExecutor(max_workers=4) as ex:
        for repo, rows in ex.map(one, repos):
            for pr in rows:
                out[(repo, pr.get("number"))] = ((pr.get("head") or {}).get("ref"),
                                                 (pr.get("base") or {}).get("ref"))
    return out


def pr_block(prs, refs=None, forge=None, owners=None) -> str:
    """The OPEN PRs, printed above the board because they outrank it for 'what is already done'.
    A PR fetch that failed is reported as unknown, never as none: absence and ignorance must not
    print the same."""
    if prs is None:
        return ("OPEN PRs: COULD NOT BE READ. Do NOT treat this as 'no open PRs'; run\n"
                "`python3 ~/.claude/tools/forge.py prs` before picking anything, because finished work\n"
                "rests in a PR while its issue is still open.\n\n")
    if not prs:
        return "OPEN PRs: none.\n\n"
    refs = refs or {}
    rows = []
    for p in prs:
        repo = repo_name(p) or "?"
        head, base = refs.get((repo, p.get("number")), (None, None))
        where = ("  [%s -> %s]" % (head, base)) if head else ""
        flags = ["DRAFT"] if p.get("draft") or (p.get("pull_request") or {}).get("draft") else []
        if p.get("comments"):
            flags.append("%d comment%s" % (p["comments"], "" if p["comments"] == 1 else "s"))
        held = forge.pr_owner_note("%s#%s" % (repo, p.get("number")), owners) if forge else ""
        rows.append("  %s#%s%s  %s%s%s" % (repo, p.get("number"), where, (p.get("title") or "").strip(),
                                          ("  (%s)" % ", ".join(flags)) if flags else "", held))
    return ("OPEN PRs — READ THESE BEFORE PICKING ANYTHING (%d). A finished increment sits here while its\n"
            "issue is still OPEN, so the board below shows that row as untouched. Check whether each is\n"
            "waiting on a review, on fixes, or just on a merge. A PR whose issue an agent above CLAIMS is\n"
            "that agent's live work: report it as theirs and pick something else.\n"
            "%s\n\n" % (len(prs), "\n".join(rows)))


def main() -> int:
    try:
        forge = load_forge()
    except BaseException as e:
        json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
                   "BOARD UNAVAILABLE — could not load tools/forge.py (%s: %s)." % (type(e).__name__, e)}},
                  sys.stdout)
        return 0
    try:
        forge.configure()  # forge.py's own reading of the project .env: one rule for both
    except OSError:
        return 0  # no project configured on this machine: nothing to show, nothing to say
    if forge.CFG.get("board") == forge.NO_BOARD:
        # A directory with no GitHub repo (REPO_BOARD=none): a desk in the room and a console, no board. The roster
        # still, so it knows who else is about; never the home board's backlog, which is not its work.
        try:
            read = forge.board_read()
        except BaseException:
            read = None
        json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": roster_block(forge, read) +
                   "NO BOARD — this directory is no checkout of a GitHub repo, so it has no board and nothing to pick:\n"
                   "work on what the owner asks for, in this console. `/go` still enters you on the roster (a desk in the\n"
                   "Agent Room) and starts your message waiter; skip its board steps."}}, sys.stdout)
        return 0
    api, owner = forge.BASE, forge.CFG["owner"]
    public = "https://github.com" if api == forge.GITHUB_API else re.sub(r"/api/v3$", "", api)
    repos = forge.CFG["repos"]
    home = "%s/%s%s" % (public, owner, "/" + repos[0] if len(repos) == 1 else "")

    def get(path):
        with urlopen(Request(api + path, headers=forge.HDR), timeout=8) as f:
            return json.loads(f.read().decode("utf-8", "replace"))

    # Open PRs, fetched beside the board and printed ABOVE it. Best-effort: a PR fetch that fails must
    # never cost the session its backlog.
    try:
        prs = get("/search/issues?per_page=100&q=" + quote(forge.search_scope() + " is:pr is:open"))["items"]
    except Exception:
        prs = None
    refs = pr_refs(get, owner, prs) if prs else {}
    try:
        sid = json.load(sys.stdin).get("session_id")
    except Exception:
        sid = None
    # SessionStart's stdin carries the session id even where the env may not, so a resumed session
    # is still `you`.
    read, board, why = None, None, None
    try:
        read = forge.board_read(sid)
        board = forge.board_judged(read)
    except SystemExit as e:  # forge.py's own words: it names the board and what to check
        why = str(e.code)
    except Exception as e:
        why = "%s: %s" % (type(e).__name__, e)
    agents_txt = roster_block(forge, read)
    owners = forge.pr_owners(read)
    if board is None:
        # Say so. Emitting nothing would open the session with no board and no reason.
        json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart",
                   "additionalContext": agents_txt + pr_block(prs, refs, forge, owners) +
                   "BACKLOG UNAVAILABLE — %s\nDo NOT treat an empty backlog as no work; the board was not read." % why}},
                  sys.stdout)
        return 0
    rows = [r for r in board["rows"] if r["why_not"] is None]
    warn = ""
    if board.get("truncated"):
        warn += "⚠ TRUNCATED — the board's read hit its cap (PAGES in forge/board/server.py): INCOMPLETE.\n"
    if board.get("error"):
        warn += ("⚠ STALE BOARD — the board's last read FAILED (%s); this is its previous snapshot.\n"
                 % board["error"])
    lines, cur = [], None
    for r in rows:  # served in print order: highest rank first
        if r["rank"] != cur:
            cur = r["rank"]
            lines.append("\n[%s]" % cur)
        lines.append("  %-12s %s%s" % (r["id"], r["title"],
                                       "   [unblocks %d]" % r["unblocks"] if r.get("unblocks") else ""))
    context = (
        "BACKLOG — the open issues at %s are the board.\n" % home
        + warn
        + "READY SET below: what nobody else claims and nothing blocks, highest rank first (REPO_RANK\n"
        "labels; rows with none are [unranked]). `forge.py board --all` shows every open row and why\n"
        "the rest are not offered (claimed, a container epic, blocked, or in a column agents do not\n"
        "pick from). Move a card between the board's columns with `forge.py move <ref> <column>`.\n"
        "An issue BODY is not the issue: decisions and measurements live in its COMMENTS. Run\n"
        "`python3 ~/.claude/tools/forge.py show <repo>#<N>` before calling anything open or undecided.\n"
        "When work lands, close or update its issue in the same breath as the merge. Work that\n"
        "outlives this session and maps to no issue gets one (`forge.py search` first, then `create`).\n"
        "The GitHub API quota (5000/h per account) is shared by every agent on the account: read freely,\n"
        "but never loop forge.py in a script. Full workflow: the /go skill, and the project's CLAUDE.md.\n"
        + "\n%d ready of %d open." % (len(rows), len(board["rows"]))
        + "\n".join(lines)
    )
    json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                      "additionalContext": agents_txt + pr_block(prs, refs, forge, owners) + context}},
              sys.stdout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
