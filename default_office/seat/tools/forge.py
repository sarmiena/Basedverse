#!/usr/bin/env python3
"""forge.py — the agents' board tool: tickets on GitHub, the roster, and messages.

ONE copy of this file exists, in seat/tools/, reached from every project through
`~/.claude/tools/forge.py` (a link; seat/install.py makes it). Never copy it into a project.

The board is the OPEN ISSUES of the repos the project .env names. Closing one removes it: there
is no Done field to set and none to forget, and no second object to keep in step. Rows are ranked
by REPO_RANK (an ordered list of labels) and laid out in the columns workflow.json defines (a
kanban: Ready, In progress, In review, Blocked, ... each a rule over labels, claims, PRs and blocked-by
edges). The board server (forge/board) judges the columns and who may pick what; this tool prints it.

Three rules GitHub does not keep, so this tool does:
  - SCOPED LABELS ARE EXCLUSIVE: `scope/value` and `scope: value` labels; `label` replaces a
    same-scope sibling and `create`/`label` refuse two in one scope.
  - LABELS ARE PER REPO: `newlabel` creates one in every repo in scope, `labels` names gaps.
  - A CLOSE WITH OPEN BLOCKERS IS REFUSED HERE: GitHub records the edge but does not enforce it.

There IS an API quota: 5000 requests/hour per GitHub account, shared by every agent and the
board when they run as the same user. Reads are fine; do not loop forge.py in a tight script.

WHICH PROJECT: the `.env` in the root above seat/ and forge/ (or $REPO_ENV) — REPO_OWNER,
REPO_NAMES (the repos the board spans; blank = every repo of the owner), REPO_HOME (where the
RESUME and AGENTS issues live; defaults to the one repo), REPO_RANK, REPO_BOARD_URL, REPO_BOARD_KEY (the board's
sign-in: its API key for the agents, which bin/start makes), REPO_TOKEN. The same file configures the board container. A process env var of
the same name wins over it; with no token anywhere, GH_TOKEN / GITHUB_TOKEN, then `gh auth token`.

    forge.py board [--all]
    forge.py prs
    forge.py show    app#185          # body AND every comment — the decisions are there
    forge.py comment app#185 -m "..."          # or -F notes.md; --once skips text already there
    forge.py uncomment app#185 --id 4321       # delete YOUR comment; `show` prints ids
    forge.py edit    app#185 -t "new title" [-b body | -F body.md]
    forge.py close   app#185 -m "landed in abc1234"    # or -F closing.md; --once as comment
    forge.py dump    open.json         # every open board issue + comments + edges
    forge.py merge   app#252 [--style merge|squash|rebase]   # the forge closes the PR itself
    forge.py retarget app#279 --base main              # a stacked PR whose base landed
    forge.py reopen  app#185
    forge.py pr      [repo] --head my-branch -t "title" [--base main] [-b .. | -F ..]
    forge.py create  [repo] -t "title" [-b body | -F body.md] [--label L]... [--blocked-by app#12]...
    forge.py label   app#185 "severity: high" bug
    forge.py unlabel app#185 bug
    forge.py move    app#185 "In progress"   # to another column of the board's workflow (workflow.json)
    forge.py block   app#185 --on app#12
    forge.py unblock app#185 --on app#12
    forge.py labels                       # every label in scope, and any repo missing one
    forge.py newlabel off-board -d "..."  # in every repo in scope; colour from a scope sibling
    forge.py search  <words>              # open AND closed, before filing
    forge.py resume [-F plan.md|--clear]  # print / replace / empty the RESUME plan (Up next)
    forge.py agents [-v]                         # the roster: who is working, on what
    forge.py agent enter [-d "doing"] [--msgname "<ListAgents name>"]   # /go: join the roster
    forge.py agent claim app#185 [-d ".."]       # this item is MINE; refused if another holds it
    forge.py agent release app#185               # let it go (handed off, dropped, merged)
    forge.py agent exit                          # /stop: leave the roster, releasing every claim
    forge.py tell <handle|owner|all> -m "..."    # a message to another agent
    forge.py inbox [--wait]                      # your unread messages

With exactly one repo in scope, a bare `185` or `#185` means it.

⚠ **-b / -m take LITERAL text and do NOT expand `@file`.** Anything carrying
backticks, `${...}` or a code fence must go through **-F/--body-file** instead: the
shell command-substitutes those before python ever sees them, which has already
truncated an issue body mid-line and left it reading like a finished sentence.
`-b @notes.md` is refused when that file exists, rather than posted as eight
characters.

⚠ **`edit` replaces a title in place, and that is the right move for a title that
has gone false** — the title is what every board listing shows, so a correction
comment cannot retract it. Editing also PRESERVES THE ISSUE NUMBER that other
issues, commits and docs reference by hand; close-and-refile orphans every one.
"""
import argparse
import datetime
import hashlib
import http.client
import json
import os
import re
import socket
import threading
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

# MUST be explicit. The Windows seat's console codec is cp1252 and issue titles carry
# em-dashes, arrows and emoji, so a bare print() raises UnicodeEncodeError mid-board and
# the operator gets a traceback instead of their backlog. [[no-jq-use-python]]
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PER_PAGE = 100  # GitHub's largest page
PAGES = 20      # api_all's cap: 2000 rows, far past any list here
GITHUB_API = "https://api.github.com"

BASE = HDR = None
# `repos`: the repos the board spans ([] = every live repo of the owner). `home`: the repo holding
# the RESUME and AGENTS issues, under `home_owner`. Both come from the .env.
# WHICH BOARD: REPO_BOARD, set in the session's environment by its directory's .claude/settings.local.json (bin/init-agent
# writes it for a checkout of a repo off the home board): `owner/repo` makes `owner`, `repos` and `repo` that repo's,
# while `home` stays the home board's (another board's bookkeeping lives there: the one roster, its own RESUME issue);
# `none` is a directory with no GitHub repo (no board: nothing to pick). Unset, it is the home board, as it always was.
# `boards`: every other board's repo (lowercased) -> its owner, from REPO_BOARDS, so a ref to one finds its owner.
CFG = {"owner": None, "repo": None, "repos": [], "home": None, "home_owner": None, "board": "", "boards": {}}
NO_BOARD = "none"
BOARD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
HOME_OWNER = None  # the home board's owner (REPO_OWNER), whose home repo holds the roster and every board's plan


def env_path():
    """The project .env: $REPO_ENV, else `.env` in the root holding seat/ and forge/ (this file
    is <root>/seat/tools/forge.py, reached through the ~/.claude/tools link)."""
    return os.environ.get("REPO_ENV") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), ".env")


def read_env(path):
    """PURE-ish: KEY=value lines of a .env as a dict; `#` lines and blanks skipped, one layer of
    quotes taken off. {} when there is no such file."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k.strip() and not k.lstrip().startswith("#"):
                    v = v.strip()
                    out[k.strip()] = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v
    except OSError:
        pass
    return out


DOTENV = {}


# Where the board and the forge are, and the keys to them: default_office's own .env first, the process environment
# only where it has none. Claude Code fills the environment from a checkout's committed .claude/settings.json too, and
# a repo's settings must never send the token somewhere else.
FROM_DOTENV_FIRST = {"REPO_URL", "REPO_PUBLIC_URL", "REPO_TOKEN", "REPO_BOARD_URL", "REPO_BOARD_KEY"}


def seat_cfg(name):
    """REPO_<NAME>: the process environment, then the project .env (FROM_DOTENV_FIRST: the other way round). A blank
    value is unset. OSError when neither has it."""
    key = "REPO_" + name.upper()
    for v in ((DOTENV.get(key), os.environ.get(key)) if key in FROM_DOTENV_FIRST else (os.environ.get(key), DOTENV.get(key))):
        if (v or "").strip():
            return v.strip()
    raise OSError("%s is not set (in %s or the environment)" % (key, env_path()))


def gh_token():
    """The GitHub CLI's own token, when `gh` is installed and logged in; '' otherwise."""
    import subprocess
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def opt(name, default=""):
    try:
        return seat_cfg(name)
    except OSError:
        return default


def configure():
    """Point forge.py at its project and log it in, from the .env (env_path()). The token: REPO_TOKEN,
    GH_TOKEN / GITHUB_TOKEN, then `gh auth token`, so a machine where `gh` is logged in needs
    nothing more. REPO_URL only for GitHub Enterprise (`https://<host>/api/v3`)."""
    global BASE, HDR, RESUME_REPO, AGENTS_REPO, HOME_OWNER
    DOTENV.clear()
    DOTENV.update(read_env(env_path()))
    CFG["owner"] = opt("owner")
    if not CFG["owner"]:
        raise OSError("REPO_OWNER is not set (in %s or the environment)" % env_path())
    CFG["repos"] = re.split(r"[\s,]+", opt("names").strip()) if opt("names").strip() else []
    # One repo in scope is THE repo: a bare `#185` and `create -t ...` mean it.
    CFG["repo"] = CFG["repos"][0] if len(CFG["repos"]) == 1 else None
    CFG["home"] = opt("home", CFG["repo"] or "")
    if not CFG["home"]:
        raise OSError("REPO_HOME is not set (in %s), and REPO_NAMES names more than one repo" % env_path())
    RESUME_REPO = AGENTS_REPO = CFG["home"]
    HOME_OWNER = CFG["home_owner"] = CFG["owner"]
    CFG["boards"] = {k.split("/")[1].lower(): k.split("/")[0]
                     for k in re.split(r"[\s,]+", opt("boards")) if BOARD_NAME.fullmatch(k)}
    b = opt("board")
    if b.lower() == ("%s/%s" % (CFG["owner"], CFG["home"])).lower():
        b = ""   # the home board, named
    if b == NO_BOARD:
        CFG.update(board=NO_BOARD, repo=None)
    elif b:
        if not BOARD_NAME.fullmatch(b):
            raise OSError("REPO_BOARD=%r is not owner/repo (nor %s)" % (b, NO_BOARD))
        owner, repo = b.split("/")
        CFG.update(owner=owner, repos=[repo], repo=repo, board=b)
    else:
        CFG["board"] = ""
    BASE = opt("url", GITHUB_API).rstrip("/")
    tok = (opt("token") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or gh_token()).strip()
    if not tok:
        raise OSError("no token: REPO_TOKEN in %s, GH_TOKEN, GITHUB_TOKEN, or `gh auth login`" % env_path())
    HDR = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28"}


def api(path, method="GET", body=None, allow=()):
    """One GitHub REST call: its JSON, or exit with GitHub's answer. A status in `allow` returns
    None instead (a branch already deleted, a pin that is already there)."""
    data = json.dumps(body).encode() if body is not None else None
    req = Request(BASE + path, data=data, method=method,
                  headers=dict(HDR, **{"Content-Type": "application/json"}))
    try:
        with urlopen(req, timeout=20) as f:
            raw = f.read()
            return json.loads(raw.decode("utf-8", "replace")) if raw else None
    except HTTPError as e:
        if e.code in allow:
            return None
        why = e.read()[:300].decode("utf-8", "replace")
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            why = "the token's API quota is spent until %s — every agent on this token shares it" % time.strftime(
                "%H:%M", time.localtime(int(e.headers.get("X-RateLimit-Reset") or 0)))
        sys.exit("forge %s %s -> %d: %s" % (method, path, e.code, why))


def api_all(path, cap=PAGES):
    """Every page of a GitHub list, in order. Pages by `page=` until one comes back short, so it
    reads the same through a stub that knows only api(). Exits when it would pass `cap` pages
    rather than return a list that only looks complete."""
    out, sep = [], "&" if "?" in path else "?"
    for page in range(1, cap + 1):
        got = api("%s%sper_page=%d&page=%d" % (path, sep, PER_PAGE, page)) or []
        if isinstance(got, dict):  # a search answers {"items": [...]}
            got = got.get("items") or []
        out += got
        if len(got) < PER_PAGE:
            return out
    sys.exit("forge GET %s: more than %d pages — raise PAGES in forge.py" % (path, cap))


def scope_repos():
    """The repos the board spans: the .env's REPO_NAMES, else every live repo of the owner."""
    if CFG["repos"]:
        return list(CFG["repos"])
    return sorted(r["name"] for r in api_all("/orgs/%s/repos" % CFG["owner"]) if not r.get("archived"))


def open_listing():
    """Every open issue AND PR in scope (GitHub lists PRs as issues): one org listing when the
    board spans the org, else each repo's own listing."""
    o = CFG["owner"]
    if not CFG["repos"]:
        return api_all("/orgs/%s/issues?filter=all&state=open" % o)
    return [i for r in CFG["repos"] for i in api_all("/repos/%s/%s/issues?state=open" % (o, r))]


def search_scope():
    """The search qualifier for the board's scope: `org:<owner>`, or one `repo:` per repo."""
    if not CFG["repos"]:
        return "org:%s" % CFG["owner"]
    return " ".join("repo:%s/%s" % (CFG["owner"], r) for r in CFG["repos"])


def repo_of(i):
    """The repo NAME an issue lives in. The org listing carries a `repository` object; a
    repo's own listing, search and the dependency lists carry only `repository_url`."""
    return ((i.get("repository") or {}).get("name")
            or (i.get("repository_url") or "").rstrip("/").rsplit("/", 1)[-1])


def is_pr(i):
    """GitHub lists pull requests as issues; they carry a `pull_request` key."""
    return "pull_request" in i


def this_seat():
    """This machine's platform, recorded on its roster entry: it badges the agent's avatar."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def ref(s):
    """'185' | 'app#185' | 'acme/app#185' -> (owner, repo, number)."""
    s = str(s)
    if "#" not in s:
        if not s.isdigit() or not CFG.get("repo"):
            sys.exit("bad ref %r — want repo#N" % s)
        return CFG["owner"], CFG["repo"], int(s)
    repo, _, num = s.partition("#")
    if not num.isdigit():
        sys.exit("bad ref %r — want repo#N" % s)
    if not repo:
        return CFG["owner"], CFG["repo"], int(num)
    owner, _, name = repo.rpartition("/")
    return (owner or owner_of(name)), name, int(num)


def owner_of(repo):
    """The owner a repo NAME is under: this session's board's, another board's (REPO_BOARDS), else the home board's."""
    r = (repo or "").lower()
    if CFG.get("board") not in ("", NO_BOARD) and r in [x.lower() for x in CFG["repos"]]:
        return CFG["owner"]
    return CFG.get("boards", {}).get(r) or CFG.get("home_owner") or CFG["owner"]


def need_board(what):
    """Exit, saying why, when this session has no board (REPO_BOARD=none) and `what` needs one."""
    if CFG.get("board") == NO_BOARD:
        sys.exit("%s: this session has no board. Its directory is no checkout of a GitHub repo (REPO_BOARD=none), so "
                 "there is nothing on a board to pick or file: work on what the owner asks for, in this console." % what)


def prose(literal, path, lit_flag, file_flag, required=True):
    """Resolve a body/message given EITHER as a literal string OR from a file.

    Long markdown does not survive a shell. Backticks and ${...} inside a -m/-b
    argument are command-substituted before python ever sees them: an issue body
    written here once lost a whole `note.threadId = `...`` line that way, and the
    result posted looking like a finished sentence. A file is the only form that
    is safe for arbitrary prose, so every verb taking prose accepts both forms and
    refuses to guess between them.
    """
    if literal is not None and path is not None:
        sys.exit("pass %s or %s, not both" % (lit_flag, file_flag))
    if path is not None:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            sys.exit("cannot read %s: %s" % (path, e))
    if literal is None:
        if required:
            sys.exit("need %s or %s" % (lit_flag, file_flag))
        return None
    # `-b @notes.md` is NOT expanded — it posts the eight literal characters, and
    # nothing warns, so the result reads as a broken tool rather than as a typo.
    # Refuse the shape that looks like it ought to work. Gate on the file actually
    # EXISTING, so a body that merely opens with an @mention is never refused.
    if literal.startswith("@") and os.path.isfile(literal[1:]):
        sys.exit("%s takes literal text and does not expand @file.\n"
                 "Pass the path with %s instead:  %s %s"
                 % (lit_flag, file_flag, file_flag, literal[1:]))
    return literal


def scoped(issue, scope, default):
    for lab in issue.get("labels") or []:
        n = lab.get("name", "")
        if n.startswith(scope + "/"):
            return n.split("/", 1)[1]
    return default


def labels_of(i):
    return sorted(l["name"] for l in i.get("labels") or [])


def open_edges(o, r, n, kind):
    """OPEN issues on one side of n's dependency edges: kind "blocked_by" (what blocks n) or
    "blocking" (what n blocks)."""
    deps = api_all("/repos/%s/%s/issues/%d/dependencies/%s" % (o, r, n, kind))
    # Only OPEN edges count. A closed one is a finished edge, not a wall — the forge holds the
    # edge forever, so without this check a chain head stays hidden after its blocker landed.
    return ["%s#%d" % (repo_of(d), d["number"])
            for d in deps if d.get("state") == "open"]


def open_blockers(o, r, n):
    return open_edges(o, r, n, "blocked_by")


def issue_id(o, r, n):
    """The issue's database id: what GitHub's dependency endpoints take, not its number."""
    return api("/repos/%s/%s/issues/%d" % (o, r, n))["id"]


def label_names(names, o, r):
    """`names`, once each exists in repo `r`. GitHub labels live per repo, and adding a name the
    repo lacks CREATES it there, so a typo would quietly coin a new label: it FAILS here instead,
    as does a second label in one scope (GitHub has no exclusive labels; this is where the rule
    lives)."""
    clash = scope_clash(names)
    if clash:
        sys.exit("two labels in scope %r: %s — a scoped label is exclusive" % clash)
    have = {l["name"] for l in api_all("/repos/%s/%s/labels" % (o, r))}
    missing = [n for n in names if n not in have]
    if missing:
        sys.exit("no such label(s) in %s/%s: %s\nexisting: %s%s"
                 % (o, r, ", ".join(missing), ", ".join(sorted(have)),
                    "\n(missing here but meant everywhere? forge.py newlabel <name> adds it to every repo)"))
    return list(names)


def scope_of(name):
    """PURE: 'status/in-progress' -> 'status', 'severity: high' -> 'severity'; an unscoped label
    ('bug', 'In Progress') has none."""
    if ": " in name:
        return name.split(": ", 1)[0]
    return name.rsplit("/", 1)[0] if "/" in name else None


def scope_clash(names):
    """PURE: (scope, 'a, b') for the first scope `names` names twice, else None."""
    by = {}
    for n in dict.fromkeys(names):
        if scope_of(n):
            by.setdefault(scope_of(n), []).append(n)
    return next(((s, ", ".join(ns)) for s, ns in by.items() if len(ns) > 1), None)


def relabelled(current, add):
    """PURE: the issue's whole label set once `add` is applied: a scoped label REPLACES any label
    the issue carries in the same scope. Order kept, current first."""
    scopes = {scope_of(n) for n in add} - {None}
    keep = [n for n in current if scope_of(n) not in scopes]
    return list(dict.fromkeys(keep + list(add)))


# ---------------------------------------------------------------- board views

_FETCH = object()


def pr_owners(d=_FETCH):
    """{repo#N: owner or None} for each open PR, as the board judged it (the claim on the issue the
    PR is for, else its session link); None when the board did not answer or its read is stale.
    `d` is a board read the caller already has (the hook's), else one is made here. Never a second
    judgement."""
    if d is _FETCH:
        d = board("/api/board", timeout=10, quiet=True)
    if not isinstance(d, dict) or not d.get("at") or d.get("prs_error") or d.get("error"):
        return None
    return {p["id"]: p.get("owner") for p in d.get("prs") or []}


def pr_owner_note(pid, owners):
    """The owner part of one PR line: who holds it, that nobody does, or that it is UNKNOWN --
    never silence where an owner could have been said."""
    if owners is False:
        return ""  # no board server: nothing to say, and nothing implied
    if owners is None:
        return "  — owner UNKNOWN (the board did not answer, or its read is stale)"
    if pid not in owners:
        return "  — owner UNKNOWN (the board has not seen this PR yet)"
    o = owners[pid] or {}
    if o.get("state") == "owned":
        return "  — held by %s (%s)" % (o["name"], o["via"])
    if o.get("state") == "orphaned":
        return "  — ORPHANED: %s left (its session is not on the roster)" % o.get("was")
    if o.get("state") == "unattributed":
        return "  — no agent holds it"
    if o.get("state") == "unknown":
        return "  — owner UNKNOWN (the board could not read the roster)"
    return "  — owner UNKNOWN (the board judged no owner for it)"


_SESSION_LINK = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")


def signed_body(body, session):
    """PURE: a PR body whose LAST session link is this session's, so the board -- which attributes a
    PR by its last link -- credits its author even after the author's claim is released.
    Unchanged outside a claude.ai session, or when its last link is already this one."""
    link = agent_link(session or "")
    links = _SESSION_LINK.findall(body or "")
    if not link or (links and links[-1] == link):
        return body or ""
    text = (body or "").rstrip()
    return text + ("\n\n" if text else "") + link + "\n"


def cmd_prs(a=None):
    """Every open PR in the org, or just this repo's when one is pinned.

    ⚠⚠ This used to `return []` whenever no repo was pinned — which is the normal case in the
    multi-repo workspace — so it printed NOTHING and exited 0. "No open PRs" and "I did not look"
    rendered identically, and a session could tick the check off having read nothing. That is the
    same silent-empty shape the board hook exists to avoid, and it mattered the moment work started
    landing through PRs: the whole point of reading this before picking is
    that finished work rests in a PR while its issue is still open.
    """
    need_board("prs")
    o, r = CFG["owner"], CFG.get("repo")
    if r:
        ps = api_all("/repos/%s/%s/pulls?state=open" % (o, r))
    else:
        # Org-wide. The org's issue listing carries pulls without their branches, so go per-repo
        # and keep the base/head that make a PR actionable.
        ps = []
        for repo in scope_repos():
            for p in api_all("/repos/%s/%s/pulls?state=open" % (o, repo)):
                p["_repo"] = repo
                ps.append(p)
    if not ps:
        print("no open PRs (checked %s)" % (("%s/%s" % (o, r)) if r else ", ".join(scope_repos())))
        return ps
    # THE PRs OUTRANK THE BOARD. The board tracks issues; work in review is invisible to
    # it, so a ticket whose implementation is finished and sitting in a PR still reads as
    # untouched. Believe the board over the PR list and you rebuild what already exists.
    print("OPEN PRs — these OUTRANK the board; work in review is invisible to it")
    owners = pr_owners()
    for p in ps:
        who = p.get("_repo") or r or "?"
        flags = []
        if p.get("draft"):
            flags.append("DRAFT")
        if p.get("comments"):
            flags.append("%d comment%s" % (p["comments"], "" if p["comments"] == 1 else "s"))
        print("  %s#%-4d %-22s <- %-30s %s%s%s"
              % (who, p["number"], p["base"]["ref"], p["head"]["ref"], p["title"][:44],
                 ("  (%s)" % ", ".join(flags)) if flags else "",
                 pr_owner_note("%s#%d" % (who, p["number"]), owners)))
    # ⚠ Keyed by (REPO, base), not base alone. The hazard is stacked branches inside ONE repo; two
    # PRs targeting `develop` in different repos are unrelated, and an org-wide listing would fire
    # this on every repo pair — a warning that cries wolf is one nobody reads when it is real.
    bases = {}
    for p in ps:
        bases.setdefault(((p.get("_repo") or r), p["base"]["ref"]), []).append(p["number"])
    for (repo, base), nums in bases.items():
        if len(nums) > 1:
            print("  ⚠ %d PRs in %s all target '%s' (%s). If they are dependent they show each"
                  % (len(nums), repo, base, ", ".join("#%d" % n for n in nums)))
            print("    other's commits and merging the last first silently empties the rest —")
            print("    base each on the previous branch and say the merge order out loud.")
    return ps


def board_read(extra=None):
    """The board server's /api/board for THIS caller: the one read every display of the board and
    the roster makes. No fallback to reading the forge here: a fallback is a second copy of the
    rules. A board that is down, or has not yet read the forge, EXITS rather than print an empty
    board or roster that looks like an empty one."""
    me = ",".join(sorted(my_ids(extra)))
    other = CFG.get("board") not in ("", NO_BOARD, None)   # another board's rows (the roster is every board's)
    d = board("/api/board?me=" + quote(me) + ("&board=" + quote(CFG["board"]) if other else ""), timeout=15)
    if not isinstance(d, dict) or not d.get("at"):
        if isinstance(d, dict) and d.get("error"):
            sys.exit("the board (%s) has never read the forge: %s" % (board_url(), d["error"]))
        sys.exit("the board (%s) has not read the forge yet -- try again in a few seconds" % board_url())
    return d


ROSTER_UNKNOWN = ("AGENTS: COULD NOT BE READ (%s) — who holds what is UNKNOWN, never 'nobody'.\n"
                  "Read the AGENTS issue in the home repo before offering any item or PR.")


def roster_lines(d, verbose=False):
    """The AGENTS strip from a board_read(): the server's roster (forge/board/roster.py),
    freshest first, `<- you` as the server judged it. [] only when nobody has entered: a board
    that has never read the roster says UNKNOWN, never an empty roster."""
    agents = d.get("agents") or []
    if d.get("agents_error") and not agents:
        return [ROSTER_UNKNOWN % ("the board's last roster read failed and it holds no earlier one: %s"
                                  % d["agents_error"])]
    out = []
    if d.get("agents_error"):
        out.append("AGENTS: the board's last roster read FAILED (%s); this is its previous roster"
                   % d["agents_error"])
    for ag in agents:
        out.append(agent_line(ag))
        if verbose:
            out.append("  %16s session %s%s" % ("", ag["session"], ("  " + ag["link"]) if ag.get("link") else ""))
            out.append("  %16s SendMessage on %s: %s" % ("", ag.get("host") or "?", ag.get("msgname")
                                                         or "(not recorded: no --msgname given)"))
    return out


def board_judged(d):
    """`d` (a board_read()) once it is known to carry the pickability the server judged for this
    caller: every row with its `why_not` and `unblocks`. The server is the one place that rule lives
    (forge/board/pick.py); this side only prints it. Exits, naming why, when it does not."""
    if d.get("pick_error"):
        sys.exit("the board (%s) could not judge pickability: %s" % (board_url(), d["pick_error"]))
    if any(not isinstance(r.get("why_not", ""), (str, type(None))) for r in d.get("rows") or []):
        sys.exit("the board (%s) serves an older pickability -- `docker compose restart board` in forge/"
                 % board_url())
    return d


def board_refresh(*refs):
    """Ask the board to re-read the issues a write just touched, so the next `board` sees it now
    instead of at the board's next full pass. The write already happened: a board that does not
    answer is WARNED about, never a failure."""
    refs = [r for r in refs if r]
    if board("/api/refresh", {"refs": refs}, timeout=30, quiet=True) is None:
        print("warning: the board (%s) did not refresh %s -- it shows the change at its next pass"
              % (board_url(), " ".join(refs) or "the roster"), file=sys.stderr)


def columns_line(d):
    """PURE: one line of the board's columns (workflow.json) and their counts, WIP limits shown and
    an over-limit column flagged: the kanban at a glance."""
    rows, out = d.get("rows") or [], []
    for c in d.get("columns") or []:
        n = (len((d.get("plan") or {}).get("steps") or []) if c.get("plan")
             else len(d.get("gone") or []) if c.get("closed") else sum(r.get("col") == c["key"] for r in rows))
        lim = c.get("limit")
        out.append("%s %d%s%s" % (c["name"], n, "/%d" % lim if lim else "", " OVER" if lim and n > lim else ""))
    return "COLUMNS — " + (" · ".join(out) or "(the board serves none: restart it)")


def cmd_move(a):
    """Move a card to another column of the workflow: the board sets that column's label and takes
    the other column labels off, exactly as a drag on the kanban does (one rule, one place). A rule
    may keep the row where it was -- a claimed row stays In progress -- and that is said."""
    o, r, n = ref(a.issue)
    got = board("/api/move", {"id": "%s#%d" % (r, n), "to": a.column})
    print("%s#%d -> %s  (labels: %s)" % (r, n, got.get("col") or "?", ", ".join(got.get("labels") or []) or "none"))
    if got.get("col") and got["col"].lower() != a.column.lower():
        print("  note: it shows in %r, not %r: a rule keeps it there (claimed by an agent? an open PR? blocked?)"
              % (got["col"], a.column))


def cmd_board(a):
    need_board("board")
    d = board_judged(board_read())
    if d.get("truncated"):
        print("⚠ TRUNCATED — the board's forge read hit its cap: raise PAGES in forge/board/server.py\n")
    if d.get("error"):
        print("⚠ the board's last forge read FAILED (%s); this is its previous snapshot\n" % d["error"])
    rows = d["rows"]
    # The roster comes FIRST: an item another agent has claimed is its live work, and the
    # board must not offer it.
    if d.get("agents"):
        print("AGENTS — freshest first; their claims are NOT yours to pick")
    lines = roster_lines(d)
    for line in lines:
        print(line)
    if lines:
        print()
    print(columns_line(d) + "\n")
    print("READY SET — highest rank first" + ("  (--all: every open row, with why it is not offered)" if a.all else ""))
    shown = [r for r in rows if a.all or r["why_not"] is None]
    cur = None
    for r in shown:  # served in print order
        if r["rank"] != cur:
            cur = r["rank"]
            print("\n[%s]" % cur)
        note = ("  <%s>" % r["why_not"]) if (a.all and r["why_not"]) else ""
        print("  %-12s%s  %s%s" % (r["id"], note, r["title"],
                                   "   [unblocks %d]" % r["unblocks"] if r.get("unblocks") else ""))
    print("\n%d shown of %d open." % (len(shown), len(rows)))
    # Rows that left the board lately: the kanban's 'Closed lately' column, the same served list.
    gone = d.get("gone") or []
    if gone:
        print("\nCLOSED LATELY — left the board in the last 30 min")
        for g in gone:
            print("  %-12s %3dm ago  %s%s" % (g["id"], max(0, (d.get("now") or g["at"]) - g["at"]) // 60,
                                            g["title"], "" if g["why"] == "closed" else "  (%s)" % g["why"]))


# ---------------------------------------------------------------- item commands

def cmd_show(a):
    o, r, n = ref(a.issue)
    i = api("/repos/%s/%s/issues/%d" % (o, r, n))
    print("%s/%s#%d  [%s]  %s" % (o, r, n, i["state"], i["title"]))
    print("labels: %s" % (", ".join(labels_of(i)) or "(none)"))
    print("blocked by (open): %s" % (" ".join(open_blockers(o, r, n)) or "(none)"))
    # The reverse edge: what waits on THIS issue. Closing it releases each of these, so a split
    # (/go step 9) must re-point them first. If this lookup fails, say why instead of exiting:
    # a split that cannot list them must stop, but the body and comments still print.
    try:
        line = " ".join(open_edges(o, r, n, "blocking")) or "(none)"
    except (SystemExit, Exception) as e:
        line = "(unavailable: %s)" % str(e)[:120]
    print("blocks (open):     %s" % line)
    print("-" * 72)
    print(i.get("body") or "(no body)")
    cs = api_all("/repos/%s/%s/issues/%d/comments" % (o, r, n))
    # A body is written once, when the least was known. The decisions, the measurements,
    # the options already rejected and the blockers already cleared land in COMMENTS.
    print("\n=== %d comment(s), oldest first — THE DECISIONS LIVE HERE ===" % len(cs))
    for c in cs:
        who = c.get("original_author") or c["user"]["login"]
        # The id is what `uncomment --id` takes: a comment you cannot name, you cannot purge.
        print("\n--- %s  %s  (id %d) ---\n%s" % (who, c["created_at"], c["id"], c.get("body") or ""))


def last_own(comments, login):
    """The newest comment `login` wrote, or None. Only YOUR OWN: rewriting someone else's
    words in place would put them on the record saying something they did not."""
    mine = [c for c in comments if (c.get("user") or {}).get("login") == login]
    return mine[-1] if mine else None


def own_comment(comments, login, cid):
    """Comment `cid` from THIS issue's thread if `login` wrote it, else a refusal string.
    Scoped to the thread so a mistyped id cannot reach into another issue, and to your own
    words for the same reason as `last_own`."""
    for c in comments:
        if c["id"] == cid:
            if (c.get("user") or {}).get("login") != login:
                return "comment %d is not yours -- only your own words can be removed" % cid
            return c
    return "comment %d is not on this issue" % cid


def cmd_uncomment(a):
    """Delete one of YOUR comments. `comment --replace-last` reaches only the newest, so a
    false claim with a newer comment after it had no way off the record, and the only move
    left was a retraction beside it (owner rule: purge false claims, do not accrete
    retractions). `show` prints each comment's id."""
    o, r, n = ref(a.issue)
    me = api("/user")["login"]
    c = own_comment(api_all("/repos/%s/%s/issues/%d/comments" % (o, r, n)), me, a.id)
    if isinstance(c, str):
        sys.exit("%s/%s#%d: %s" % (o, r, n, c))
    api("/repos/%s/%s/issues/comments/%d" % (o, r, a.id), "DELETE")
    print("deleted comment %d on %s/%s#%d  (%s, %d chars)"
          % (a.id, o, r, n, c["created_at"], len(c.get("body") or "")))


def already_said(o, r, n, body):
    """True if the issue already carries a comment with exactly this text, so a batch script that is
    RE-RUN posts nothing the second time. Any author counts: agents post with the owner's token, so
    authorship cannot be told apart, and identical text already there is the same evidence.
    Check-then-post: two copies running at once can both post."""
    cs = api_all("/repos/%s/%s/issues/%d/comments" % (o, r, n))
    return any((c.get("body") or "").strip() == body.strip() for c in cs)


def cmd_dump(a):
    """Every open board issue (off-board ones excluded, as `board` does), with its body, ALL comments
    and its open edges both ways, as one JSON file: what a board-wide read (/prune) works from. One
    failed request aborts it, and the old file is removed first, so a failed run leaves none."""
    o = CFG["owner"]
    if os.path.isdir(a.out):
        sys.exit("%s is a directory: give a file path" % a.out)
    if not os.path.isdir(os.path.dirname(os.path.abspath(a.out))):
        sys.exit("no such directory for %s" % a.out)  # before ~3 requests per issue, not after
    if os.path.exists(a.out):
        os.remove(a.out)  # a failed run must not leave an older dump looking current
    issues = open_listing()
    # The board's own rule: work ruled off the backlog (RESUME, AGENTS, …) is not a row to rate,
    # and neither is a PR (GitHub lists those as issues). Dedup by ref, because a row that changes
    # while paging can shift onto the next page.
    seen, rows_in = set(), []
    for i in issues:
        k = (repo_of(i), i["number"])
        if not is_pr(i) and "off-board" not in labels_of(i) and k not in seen:
            seen.add(k)
            rows_in.append(i)
    issues = rows_in

    def full(i):
        r, n = repo_of(i), i["number"]
        cs = api_all("/repos/%s/%s/issues/%d/comments" % (o, r, n))
        return {"ref": "%s#%d" % (r, n), "title": i["title"], "labels": labels_of(i),
                "created": i["created_at"], "updated": i["updated_at"], "body": i.get("body") or "",
                "comments": [{"at": c["created_at"], "id": c["id"],
                              "by": c.get("original_author") or (c.get("user") or {}).get("login"),
                              "body": c.get("body") or ""} for c in cs],
                "blocked_by": open_edges(o, r, n, "blocked_by"),
                "blocks": open_edges(o, r, n, "blocking")}
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        rows = list(pool.map(full, issues))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # the first failure stops the queued requests
    tmp = a.out + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1, ensure_ascii=False)
        os.replace(tmp, a.out)  # a write that dies halfway leaves no truncated dump at the real path
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    print("dumped %d open issues to %s" % (len(rows), a.out))


def cmd_comment(a):
    o, r, n = ref(a.issue)
    body = prose(a.message, a.message_file, "-m", "-F")
    if a.once and a.replace_last:
        sys.exit("--once and --replace-last conflict: --replace-last rewrites in place, --once skips a repeat")
    if a.replace_last:
        # A comment that states something false is corrected IN PLACE, like `edit` does for a
        # title — a second comment retracting the first leaves both readings on the record
        # (owner rule: purge false claims, do not accrete retractions).
        me = api("/user")["login"]
        # ⚠ The WHOLE thread, every page: GitHub pages comments, and the first page alone would
        # rewrite an OLD comment of yours on a long thread.
        c = last_own(api_all("/repos/%s/%s/issues/%d/comments" % (o, r, n)), me)
        if c is None:
            sys.exit("%s/%s#%d has no comment by %s to replace" % (o, r, n, me))
        api("/repos/%s/%s/issues/comments/%d" % (o, r, c["id"]), "PATCH", {"body": body})
        print("replaced comment %d on %s/%s#%d  (%d chars)" % (c["id"], o, r, n, len(body)))
        return
    if a.once and already_said(o, r, n, body):
        print("skipped %s/%s#%d: that exact text is already there (--once)" % (o, r, n))
        return
    api("/repos/%s/%s/issues/%d/comments" % (o, r, n), "POST", {"body": body})
    print("commented on %s/%s#%d  (%d chars)" % (o, r, n, len(body)))


def cmd_edit(a):
    """Replace an issue's TITLE and/or BODY in place.

    A title that has gone false is not a candidate for a correction comment. It is
    what every board listing shows, so a stale one keeps asserting the wrong thing
    no matter what the comments say — and appending a retraction beside it leaves
    both readings on the board. Replace it. (Owner rule: purge false claims, do not
    accrete retractions.)

    Editing in place also PRESERVES THE NUMBER, which matters more than it looks:
    other issues, commits and docs reference `repo#N` by hand, and close-and-refile
    silently orphans every one of them.
    """
    o, r, n = ref(a.issue)
    body = prose(a.body, a.body_file, "-b", "-F", required=False)
    if a.title is None and body is None:
        sys.exit("nothing to change — pass -t/--title and/or -b/-F")
    patch = {}
    if a.title is not None:
        patch["title"] = a.title
    if body is not None:
        # A PR keeps its author's session link LAST (the board attributes a PR by its last
        # link), so an edit by anyone, citing any session, never costs the PR its author. A failed
        # read of the current body costs only that guarantee, never the edit.
        try:
            cur = api("/repos/%s/%s/issues/%d" % (o, r, n)) or {}
        except BaseException:
            cur = {}
        old = _SESSION_LINK.findall(cur.get("body") or "")
        if cur.get("pull_request") and old:
            body = signed_body(body, old[-1].rsplit("/", 1)[-1])
        patch["body"] = body
    # Report from the RESPONSE, not from what we sent — but never let a thin reply
    # raise after the edit has already landed: "it errored" and "it errored after
    # doing it" send a session down completely different paths.
    i = api("/repos/%s/%s/issues/%d" % (o, r, n), "PATCH", patch) or {}
    print("edited %s/%s#%d%s%s"
          % (o, r, n,
             ("  title -> %s" % i.get("title", a.title)) if a.title is not None else "",
             ("  body %d chars" % len(body)) if body is not None else ""))


# GitHub's names for how a PR lands. `merge` (a real merge commit) is the default because that is
# what this project's trunks have always carried — `git merge --no-ff` by hand, before this verb
# existed. GitHub's `rebase` is the old forge's plain rebase; it has no rebase-then-merge-commit
# and no fast-forward-only.
MERGE_DO = {
    "merge": "merge",
    "squash": "squash",
    "rebase": "rebase",
}


def merge_body(style, head_sha, title=None, message=None):
    """PURE: the merge request body for one merge. Tested; see test_forge.py.

    `sha` is not optional politeness. The caller reads the PR, decides from what it
    read (open, mergeable, which sha), and then merges — and between those two calls
    the branch can move. Pinning the sha makes GitHub refuse (409) a merge of anything
    other than the commit that was actually checked, so "I verified the gates on the
    merge result" cannot quietly become a claim about a different commit.
    """
    if style not in MERGE_DO:
        sys.exit("unknown merge style %r — one of: %s" % (style, ", ".join(MERGE_DO)))
    body = {"merge_method": MERGE_DO[style]}
    if head_sha:
        body["sha"] = head_sha
    if title:
        body["commit_title"] = title
    if message:
        body["commit_message"] = message
    return body


def cmd_merge(a):
    """Merge a PULL REQUEST through the forge, so the PR closes ITSELF.

    This verb did not exist, and the gap had a cost every time. Merging by hand works
    (`git push origin merge-N:<trunk>`), but **the old Forgejo did not detect a push-side
    merge** (GitHub only does when the pushed trunk contains the PR's exact head): the PR
    stays OPEN with its commits already on the trunk, so the next
    session reads finished work as unbuilt and may rebuild it. The workaround was a
    hand-written `close` saying "this close is MANUAL" — a convention that depends on
    whoever merged remembering to say it. Merging through the API closes the PR,
    records the merge sha as the forge's own fact, and needs no discipline.

    It refuses rather than guesses: an already-merged PR, a closed one, or one the
    forge itself reports as not mergeable stops here, because each of those means the
    caller's picture of the board is wrong and a second opinion is cheaper than a
    surprise on a trunk.
    """
    o, r, n = ref(a.pr)
    pr = api("/repos/%s/%s/pulls/%d" % (o, r, n)) or {}
    if pr.get("merged") or pr.get("merged_at"):
        sys.exit("%s#%d is ALREADY merged (%s). Nothing to do."
                 % (r, n, (pr.get("merge_commit_sha") or "")[:8] or "sha unknown"))
    if pr.get("state") != "open":
        sys.exit("%s#%d is %r, not open — reopen it first if this is really the intent"
                 % (r, n, pr.get("state")))
    # `mergeable` is the FORGE's answer, not ours: conflicts, protected-branch rules and
    # required checks all land in it. Trusting our own read of the branches instead is
    # how a merge gets attempted against a rule nobody remembered.
    if pr.get("mergeable") is False:
        sys.exit("%s#%d is not mergeable per the forge (conflict, or a branch rule).\n"
                 "Rebase the head branch and look at the PR before forcing anything."
                 % (r, n))
    head = ((pr.get("head") or {}).get("sha") or "")
    branch = (pr.get("head") or {}).get("ref", "?")
    base = (pr.get("base") or {}).get("ref", "?")
    # No title of our own unless asked: GitHub's default fits the style (a merge commit's
    # "Merge pull request #N from ...", a squash's PR title).
    body = merge_body(a.style, head, a.title,
                      prose(a.message, a.message_file, "-m", "-F", required=False))
    got = api("/repos/%s/%s/pulls/%d/merge" % (o, r, n), "PUT", body) or {}
    # Re-read rather than trust the reply alone: "merged" without the sha would leave the one
    # fact a later session needs unrecorded.
    after = api("/repos/%s/%s/pulls/%d" % (o, r, n)) or {}
    sha = (got.get("sha") or after.get("merge_commit_sha") or "")[:8] or "?"
    print("merged %s#%d  %s -> %s  as %s  (%s)" % (r, n, branch, base, sha, a.style))
    print("  the PR is CLOSED by the forge itself — no manual close needed")
    # GitHub's merge never deletes the branch; it is a ref delete of its own, and only in this
    # repo (a fork's branch is not ours). 404/422: the repo's auto-delete got there first.
    same_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name") == "%s/%s" % (o, r)
    if a.keep_branch or not same_repo:
        print("  head branch kept%s" % ("" if a.keep_branch else " (it lives in another repo)"))
    else:
        api("/repos/%s/%s/git/refs/heads/%s" % (o, r, quote(branch, safe="/")), "DELETE", allow=(404, 422))
        print("  head branch deleted")
    if not after.get("merged"):
        print("  \u26a0 the forge does not report it merged — check the PR before trusting this")


def retarget_body(base, head):
    """The PATCH that moves a PR onto another base. PURE, so the refusal is testable.

    A PR whose head IS the new base would be a PR from a branch into itself.
    """
    if not base:
        sys.exit("name the new base: --base <branch>")
    if base == head:
        sys.exit("the PR's head is %r -- it cannot target itself" % head)
    return {"base": base}


def cmd_retarget(a):
    """Move an open PR onto another base branch -- how a STACK lands.

    A stacked PR targets the branch below it. When that branch lands, the forge retargets
    the PRs on it only if it landed through ITS OWN PR; a base that landed another way
    (merged into a branch that then merged) is left as the PR's base, and deleting it
    leaves the PR pointing at nothing. Retargeting is then a normal step, and it needs a
    verb rather than a hand-written PATCH (2026-09-23: host#279, whose base had already
    reached `develop` through host#266).
    """
    o, r, n = ref(a.pr)
    pr = api("/repos/%s/%s/pulls/%d" % (o, r, n)) or {}
    if pr.get("state") != "open" or pr.get("merged"):
        sys.exit("%s#%d is not an open PR -- nothing to retarget" % (r, n))
    old = (pr.get("base") or {}).get("ref", "?")
    body = retarget_body(a.base, (pr.get("head") or {}).get("ref"))
    after = api("/repos/%s/%s/pulls/%d" % (o, r, n), "PATCH", body) or {}
    now = (after.get("base") or {}).get("ref", "?")
    print("retargeted %s#%d  %s -> %s" % (r, n, old, now))
    if now != a.base:
        print("  ⚠ the forge reports base %r -- check the PR" % now)


def cmd_close(a):
    o, r, n = ref(a.issue)
    # No close while any blocker is still open. That is the epic rule made mechanical - an
    # epic is blocked by its children, so it cannot close until every child has. The old
    # forge enforced it (412); GitHub records the edge but closes anyway, so the rule lives
    # HERE now. Check BEFORE commenting, or a refused close leaves the "here is what landed"
    # comment orphaned on an open issue.
    blk = open_blockers(o, r, n)
    if blk:
        sys.exit("%s/%s#%d still has OPEN blockers: %s\n"
                 "forge.py refuses this close. Close them first, or drop an edge\n"
                 "that should not be there:  forge.py unblock %s#%d --on <ref>"
                 % (o, r, n, " ".join(blk), r, n))
    msg = prose(a.message, a.message_file, "-m", "-F", required=False)
    if msg and a.once and already_said(o, r, n, msg):
        print("skipped the comment on %s/%s#%d: that exact text is already there (--once)" % (o, r, n))
    elif msg:
        api("/repos/%s/%s/issues/%d/comments" % (o, r, n), "POST", {"body": msg})
    api("/repos/%s/%s/issues/%d" % (o, r, n), "PATCH", {"state": "closed", "state_reason": "completed"})
    print("closed %s/%s#%d  (closing IS Done — there is no status to set)" % (o, r, n))
    board_refresh("%s#%d" % (r, n))


def cmd_reopen(a):
    o, r, n = ref(a.issue)
    api("/repos/%s/%s/issues/%d" % (o, r, n), "PATCH", {"state": "open"})
    print("reopened %s/%s#%d" % (o, r, n))
    board_refresh("%s#%d" % (r, n))


def cmd_pr(a):
    """Open a PULL REQUEST — the last step of every increment, not a convenience.

    Work reaches a trunk through a PR, and a verb for it is what keeps the step from
    being skipped.

    `--base` defaults to the repo's OWN default branch rather than a hardcoded
    guess: repos differ (`main`, `develop`, `master`), and guessing does not error —
    it opens the PR against the wrong trunk.
    """
    o = CFG["owner"]
    r = a.repo or CFG.get("repo")
    if not r:
        sys.exit("name the repo: forge.py pr <repo> --head <branch> -t \"...\"")
    base = a.base or (api("/repos/%s/%s" % (o, r)) or {}).get("default_branch")
    if not base:
        sys.exit("cannot resolve a base branch for %s/%s — pass --base" % (o, r))
    if a.head == base:
        sys.exit("head and base are both %r — nothing to merge" % base)
    p = api("/repos/%s/%s/pulls" % (o, r), "POST",
            {"head": a.head, "base": base, "title": a.title,
             "body": signed_body(prose(a.body, a.body_file, "-b", "-F", required=False), agent_session())}) or {}
    # Same rule as `edit`: a PR that was opened must never report as a failure just
    # because the reply was thinner than expected.
    print("opened %s#%s  %s -> %s\n  %s"
          % (r, p.get("number", "?"), a.head, base, p.get("html_url", "")))


def cmd_create(a):
    need_board("create")
    o = CFG["owner"]
    r = a.repo or CFG.get("repo")
    if not r:
        sys.exit("this board spans several repos — name one: forge.py create <repo> -t ...")
    names = list(a.label or [])
    body = {"title": a.title, "body": prose(a.body, a.body_file, "-b", "-F", required=False) or ""}
    if names:
        body["labels"] = label_names(names, o, r)
    # Every blocker resolved BEFORE the issue exists: a typo'd ref must fail here, not leave an
    # issue filed without the edge that was its point.
    deps = [(dep, issue_id(*ref(dep))) for dep in a.blocked_by or []]
    i = api("/repos/%s/%s/issues" % (o, r), "POST", body)
    # One call files it, labels it and boards it. There is no separate add-to-board step
    # to forget, which is what once made a filed issue invisible to every session.
    print("created %s#%d  %s" % (r, i["number"], " ".join(names)))
    for dep, did in deps:
        api("/repos/%s/%s/issues/%d/dependencies/blocked_by" % (o, r, i["number"]),
            "POST", {"issue_id": did})
        print("  blocked by %s" % dep)
    board_refresh("%s#%d" % (r, i["number"]), *["%s#%d" % ref(dep)[1:] for dep in a.blocked_by or []])


def add_labels(o, r, n, names):
    """Apply `names` to issue n with the scoped ones EXCLUSIVE: a label already on the issue in
    the same scope is replaced. One PUT of the whole set, so the issue is never seen with two
    values in a scope, or with none, in between. Returns the labels it now carries."""
    names = label_names(names, o, r)
    cur = labels_of(api("/repos/%s/%s/issues/%d" % (o, r, n)))
    return sorted(l["name"] for l in api("/repos/%s/%s/issues/%d/labels" % (o, r, n), "PUT",
                                         {"labels": relabelled(cur, names)}) or [])


def cmd_label(a):
    o, r, n = ref(a.issue)
    print("%s/%s#%d now: %s" % (o, r, n, ", ".join(add_labels(o, r, n, a.labels))))


def split_present(names, current):
    """The labels asked to be removed, split into those the issue carries and those it
    does not. Pure, so it is tested without a forge."""
    have = set(current)
    # Deduplicated: a name given twice would send a second DELETE for a label already gone,
    # which the forge answers with an error AFTER the removal had succeeded.
    names = list(dict.fromkeys(names))
    return [n for n in names if n in have], [n for n in names if n not in have]


def cmd_unlabel(a):
    """Remove labels from an issue — the inverse of `label`.

    Needed because the scoped labels are EXCLUSIVE: `label` can replace a wrong value with
    another in the same scope, but cannot leave a scope empty. Names are checked against the repo, so a typo
    fails loudly instead of removing nothing and exiting 0.
    """
    o, r, n = ref(a.issue)
    have = {l["name"] for l in api_all("/repos/%s/%s/labels" % (o, r))}
    typo = [x for x in a.labels if x not in have]
    if typo:
        sys.exit("no such label(s) in %s/%s: %s" % (o, r, ", ".join(typo)))
    present, absent = split_present(a.labels, labels_of(api("/repos/%s/%s/issues/%d" % (o, r, n))))
    for name in present:
        api("/repos/%s/%s/issues/%d/labels/%s" % (o, r, n, quote(name, safe="")), "DELETE")
    if absent:
        print("not on %s/%s#%d (nothing to remove): %s" % (o, r, n, ", ".join(absent)))
    print("%s/%s#%d now: %s"
          % (o, r, n, ", ".join(labels_of(api("/repos/%s/%s/issues/%d" % (o, r, n)))) or "(no labels)"))


def cmd_block(a):
    o, r, n = ref(a.issue)
    o2, r2, n2 = ref(a.on)
    api("/repos/%s/%s/issues/%d/dependencies/blocked_by" % (o, r, n), "POST",
        {"issue_id": issue_id(o2, r2, n2)})
    print("%s/%s#%d is blocked by %s" % (o, r, n, a.on))
    board_refresh("%s#%d" % (r, n), "%s#%d" % (r2, n2))


def cmd_unblock(a):
    o, r, n = ref(a.issue)
    o2, r2, n2 = ref(a.on)
    api("/repos/%s/%s/issues/%d/dependencies/blocked_by/%d" % (o, r, n, issue_id(o2, r2, n2)), "DELETE")
    print("%s/%s#%d no longer blocked by %s" % (o, r, n, a.on))
    board_refresh("%s#%d" % (r, n), "%s#%d" % (r2, n2))


# ---------------------------------------------------------------- the resume plan

# ONE open issue in the home repo, titled RESUME…, labelled `off-board` (so it is never offered as
# work). Its BODY is the next session's critical path — rewritten at every stop, never appended
# to — and `/go` with no arguments starts from its first line. The kanban's Up next column IS it.
RESUME_REPO = None  # the .env's REPO_HOME, set by configure()
RESUME_TITLE = "RESUME — the next session's critical path"
PLAN_MAX_LINE = 110
_PLAN_STEP = re.compile(r"^\d+\)\s")
_PLAN_REF = re.compile(r"\b[A-Za-z][\w.-]*#\d+\b")


def plan_problems(text):
    """Why `text` is not a REFERENTIAL plan — [] when it is.

    A plan is numbered steps, each naming at least one `repo#N`, each short. The context for a
    step lives on the issue it names; a line long enough to need a clause is a line that has
    started carrying that context, which is exactly how the `/go` paragraph grew. Refusing it
    here is the mechanical form of "referential, not prosaic".
    """
    lines = [l.rstrip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return ["the plan is empty"]
    probs = []
    for i, l in enumerate(lines, 1):
        if not _PLAN_STEP.match(l):
            probs.append("line %d is not a numbered step `N) …`: %r" % (i, l[:60]))
        elif not _PLAN_REF.search(l):
            probs.append("line %d names no repo#N — a step with no issue is untracked work: %r" % (i, l[:60]))
        if len(l) > PLAN_MAX_LINE:
            probs.append("line %d is %d chars > %d — the context belongs on the issue" % (i, len(l), PLAN_MAX_LINE))
    return probs


def plan_of(title):
    """PURE: the board a RESUME issue's title names: `RESUME [owner/repo] …` is that board's plan, any other `RESUME…`
    the home board's ('')."""
    m = re.match(r"RESUME \[([^\]\s]+)\]", title or "")
    return m.group(1) if m else ""


def pick_resume(issues, board=None):
    """The one open RESUME issue among `issues` for `board` (this session's, by default: '' is the home board's), or
    None. Two is an error, not a choice."""
    want = (CFG.get("board") or "" if board is None else board).lower()
    hits = [i for i in issues if (i.get("title") or "").startswith("RESUME") and plan_of(i.get("title")).lower() == want]
    if len(hits) > 1:
        sys.exit("more than one open RESUME issue (%s) — close all but one"
                 % ", ".join("#%d" % i["number"] for i in hits))
    return hits[0] if hits else None


def cmd_resume(a):
    """Print the resume plan, or (with -b/-F) replace it. Another board's plan is its own RESUME issue in the home repo,
    `RESUME [owner/repo] …`."""
    need_board("resume")
    # ⚠ By LABEL, never search: the search index lags a fresh issue (measured on the old forge —
    # a plan written a second earlier read back as "no RESUME plan", and the next rewrite would have
    # made a SECOND one), and a search also matches every issue whose text merely says "resume".
    cur = pick_resume(off_board(HOME_OWNER, RESUME_REPO))
    if a.clear and (a.body is not None or a.body_file is not None):
        sys.exit("pass --clear or -b/-F, not both")
    if a.body is None and a.body_file is None and not a.clear:
        if not cur:
            print("no RESUME plan — /go falls back to the board")
            return
        print("%s/%s#%d  %s\n" % (HOME_OWNER, RESUME_REPO, cur["number"], cur["title"]))
        print((cur.get("body") or "").strip()
              or "(empty — nothing in flight or imperative; /go falls back to the board)")
        return
    # An EMPTY plan is a real state, not an error: the plan holds only what is
    # in flight or must come before the board's own ranking, and most days that is nothing. It takes
    # `--clear`, never an empty -F, so a blank file cannot wipe a plan by accident.
    if a.clear and not cur:
        print("no RESUME plan — nothing to clear; /go falls back to the board")
        return
    if a.clear:
        body = ""
    else:
        body = prose(a.body, a.body_file, "-b", "-F")
        probs = plan_problems(body)
        if probs:
            sys.exit("refused — a RESUME plan is REFERENTIAL, not prose:\n  " + "\n  ".join(probs))
    if cur:
        n = cur["number"]
        api("/repos/%s/%s/issues/%d" % (HOME_OWNER, RESUME_REPO, n), "PATCH", {"body": body})
    else:
        r = api("/repos/%s/%s/issues" % (HOME_OWNER, RESUME_REPO), "POST",
                {"title": RESUME_TITLE if not CFG.get("board") else RESUME_TITLE.replace("RESUME", "RESUME [%s]" % CFG["board"], 1),
                 "body": body,
                 "labels": label_names(["off-board"], HOME_OWNER, RESUME_REPO)})
        n = r["number"]
    print("%s/%s#%d %s — resume with: /go"
          % (HOME_OWNER, RESUME_REPO, n, "cleared (the board decides)" if a.clear else "rewritten"))


# ---------------------------------------------------------------- the agent roster

# WHO is working, and on WHAT. Every agent posts as the same GitHub user and ListAgents cannot see
# another machine, so without a roster one agent offers another's live PRs as left-behind work. The
# roster lives where every agent already looks: ONE `off-board` issue in the home repo, and ONE
# comment on it per agent, which only that agent rewrites.
# `/go` enters, `claim`/`release` say what it holds, `/stop` exits (deletes the comment). Every
# forge.py call made from inside a session touches its entry (`seen`), and so does the seat's hook
# while it works, so the age the board shows is time since the agent last DID something. A session
# that exits takes its own entry off (the hook's SessionEnd); one that crashed or was closed is taken
# off by the next agent on the same machine to report, once Claude Code no longer runs it (reap). One
# that a shutdown of its machine ended the board itself takes off, after the machine starts again
# (server.reap_rebooted). Any other stale entry (another machine's) the owner removes with the × on the kanban.
AGENTS_REPO = None  # the .env's REPO_HOME, set by configure()
AGENTS_TITLE = "AGENTS — who is working, and on what"
AGENT_MARK = "<!-- board-agent -->"
# `msgname` is the name ListAgents shows for the session on ITS machine (`app-6d [82b1bf]`),
# which is what SendMessage takes there. Nothing in a session's environment carries it, so the
# agent passes what its own ListAgents says (`agent enter --msgname`); a nudge across machines then
# reads it here instead of picking a session by elimination.
# `ctx`, `blocked`, `subs` and `working` are written by the seat's own hook (hooks/agent_state.py),
# never typed: the context in use at the last turn's end (tokens), what the session waits on the owner
# for, the subagents it has working (one line of JSON: [{id, type, what}]), and when the turn under way
# began ('' between turns). `board` is the board the session works from (REPO_BOARD, its directory's: board_key()), written
# by its own `agent` writes; blank is the home board's. `dir` is the directory it runs in, written by the hook too (`~` for
# the home directory), for the room's list of agents.
AGENT_KEYS = ("name", "session", "local", "msgname", "seat", "host", "link", "seen", "claims", "doing",
              "ctx", "blocked", "subs", "working", "board", "dir")
_ADJ = ("amber", "brisk", "cobalt", "dusky", "ember", "fallow", "gilded", "hazel",
        "indigo", "jade", "keen", "lunar", "mossy", "nimble", "ochre", "pale",
        "quiet", "russet", "slate", "tawny", "umber", "vivid", "woolly", "young",
        "zesty", "bold", "coral", "dapper", "frosty", "gentle", "hardy", "ivory")
_NOUN = ("heron", "otter", "lynx", "finch", "badger", "marten", "plover", "ibex",
         "wren", "stoat", "kestrel", "newt", "osprey", "puffin", "quail", "raven",
         "shrike", "tern", "vole", "wombat", "yak", "zebu", "auk", "bison",
         "crane", "dingo", "egret", "ferret", "gecko", "hare", "jackal", "koala")


def agent_session():
    """This agent's identity, from its OWN environment — never typed. The claude.ai session id
    comes first: it is the link already in every PR body, so a PR and its roster entry name the
    same session. A local-only session falls back to Claude Code's own id, and so does a process
    started before the link was up (my_entry finds the entry under either id; the next write keys
    it by the bridge id). None outside a session (the owner running forge.py by hand), and then
    nothing is entered or touched."""
    return (os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID")
            or os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip() or None


def is_me(d, ids):
    """PURE: is entry `d` this session? `ids` is every id this process can see for itself —
    the bridge id, Claude Code's local id, and a hook's stdin `session_id`. An entry records both
    of its ids, so a process that sees only one of them (a hook) still recognises its own claims
    instead of reading them as another agent's."""
    ids = {i for i in ids if i}
    return bool(ids & {d.get("session"), d.get("local")})


def my_ids(extra=None):
    return {os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID"), os.environ.get("CLAUDE_CODE_SESSION_ID"),
            extra} - {None, ""}


def session_ids(session):
    """Every id `session` is known by here: all of this process's own when it is this session, else just it."""
    return my_ids() if session == agent_session() else {session}


def my_entry(agents, session, ids):
    """PURE: `session`'s roster entry: the one keyed by it, else the one holding another of its `ids`. A
    process sees the bridge id only once the claude.ai link is up, so one started before that (a mailbox
    waiter, an early /go) knows the session by its local id alone, and the entry may be keyed by either."""
    return (next((d for d in agents if d["session"] == session), None)
            or next((d for d in agents if is_me(d, ids)), None))


def same_agent(a, b):
    """PURE: do entries `a` and `b` belong to one session? Each holds the ids it was written with."""
    return bool(({a.get("session"), a.get("local")} & {b.get("session"), b.get("local")}) - {None, ""})


def merged(win, lose):
    """PURE: one session's two entries (entered under each of its ids, or two first-touches racing) as
    one: the fresher `win` (its comment, its doing), holding both entries' claims, keyed by the bridge
    id when either has it, as agent_session() keys an entry, its local id beside it."""
    d = dict(win, claims=win["claims"] + [c for c in lose["claims"] if c not in win["claims"]])
    for k in ("local", "msgname", "link"):
        d[k] = d.get(k) or lose.get(k) or ""
    if not d["session"].startswith("session_") and lose["session"].startswith("session_"):
        d.update(session=lose["session"], local=win["session"], link=lose.get("link") or agent_link(lose["session"]))
    return d


def mail_id():
    """The id this session's mail is addressed to and read under: Claude Code's local id, which every
    process of the session has (a mailbox waiter started before the bridge was up has no other), else
    agent_session(). A sender addresses an entry by its `local` for the same reason (mail_to)."""
    return (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip() or agent_session()


def mail_to(d):
    return d.get("local") or d["session"]


def agent_name(session):
    """PURE: a stable two-word handle for a session id. Readable in a terminal and on the board,
    and the same everywhere, because it is derived rather than stored first."""
    h = hashlib.sha256(session.encode("utf-8")).digest()
    return "%s-%s" % (_ADJ[h[0] % len(_ADJ)], _NOUN[h[1] % len(_NOUN)])


def my_name():
    """THIS session's handle: the name its roster entry already carries, else the one a new entry
    would get -- agent_name() of Claude Code's LOCAL id, which is what the SessionStart hook titles
    the session with (hooks/session_title.py). A hook never sees the bridge id (the claude.ai link
    is not up yet), so naming from it would give one session two names. A stored name is never
    re-derived: messages and the roster keep naming a live session the same way for its whole life."""
    s = agent_session()
    if not s:
        return None
    try:
        mine = next((d for d in agents_now()[1] if is_me(d, my_ids())), None)
        if mine and mine.get("name"):
            return mine["name"]
    except Exception:
        pass
    return agent_name((os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip() or s)


def agent_link(session):
    return "https://claude.ai/code/" + session if session.startswith("session_") else ""


def parse_subs(v):
    """PURE: the `subs` line as a list of {id, type, what}; [] for none or for a line that is not one."""
    try:
        got = json.loads(v) if v else []
    except ValueError:
        return []
    return [{"id": str(x.get("id") or ""), "type": str(x.get("type") or "agent"), "what": str(x.get("what") or "")}
            for x in got if isinstance(x, dict)] if isinstance(got, list) else []


def subs_line(subs):
    """PURE: the inverse of parse_subs: one line of JSON, '' for none."""
    return json.dumps(subs, ensure_ascii=False, separators=(",", ":")) if subs else ""


def parse_agent(body):
    """PURE: an agent comment's fields, or None when `body` is not one. `claims` and `subs` are lists."""
    if AGENT_MARK not in (body or ""):
        return None
    d = {}
    for line in body.splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip() in AGENT_KEYS:
            d[k.strip()] = v.strip()
    if not d.get("session"):
        return None
    d["claims"] = (d.get("claims") or "").split()
    d["subs"] = parse_subs(d.get("subs"))
    return d


def agent_body(d):
    """PURE: the inverse of parse_agent. Fenced so it reads as a record in the forge's UI too."""
    vals = dict(d, claims=" ".join(d.get("claims") or []), subs=subs_line(d.get("subs")))
    return "%s\n```\n%s\n```\n" % (AGENT_MARK, "\n".join(
        "%s: %s" % (k, re.sub(r"\s+", " ", str(vals.get(k) or "")).strip()) for k in AGENT_KEYS))


def roster(comments, dupes=None):
    """PURE: every agent entry on the AGENTS issue, freshest first, each with its comment `id`.
    A session with two entries (two first-touches racing, or one entered under each of its ids)
    shows once, merged: the fresher wins, and the loser's comment id goes into `dupes` so the next
    write can delete it — left alone, it would resurface as a dead agent holding claims the moment
    the fresher one exits."""
    out = []
    for c in comments:
        d = parse_agent(c.get("body"))
        if d is None:
            continue
        d["id"] = c["id"]
        d["seen"] = d.get("seen") or c.get("updated_at") or ""
        for old in [o for o in out if same_agent(o, d)]:
            out.remove(old)
            win, lose = (d, old) if d["seen"] > old["seen"] else (old, d)
            if dupes is not None:
                dupes.append(lose["id"])
            d = merged(win, lose)
        out.append(d)
    return sorted(out, key=lambda d: d["seen"], reverse=True)


def with_seen(body, seen):
    """PURE: `body` with only its `seen:` line replaced. A touch rewrites nothing else, so it
    cannot write back a stale claims list over a claim made a moment earlier."""
    return re.sub(r"(?m)^seen:.*$", "seen: " + seen, body, count=1)


def with_fields(body, fields):
    """PURE: `body` with only the named lines replaced, like with_seen. A field an older entry was
    written without is added just before the closing fence, so the entry still parses."""
    for k, v in fields.items():
        line = "%s: %s" % (k, re.sub(r"\s+", " ", str(v or "")).strip())
        new, n = re.subn(r"(?m)^%s:.*$" % re.escape(k), lambda _: line, body, count=1)
        body = new if n else re.sub(r"(?m)^```\s*$(?![\s\S]*^```)", lambda m: line + "\n" + m.group(0),
                                    body, count=1)
    return body


def claim_holders(agents, refname, ids):
    """PURE: the OTHER agents holding `refname`: all but the entry holding one of `ids`, the caller's."""
    return [d for d in agents if refname in d["claims"] and not is_me(d, ids)]


def ago(iso, now=None):
    """PURE-ish: '12s' / '5m' / '3h' / '2d' since an ISO-8601 UTC stamp; '?' if unparseable."""
    try:
        t = datetime.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return "?"
    s = max(0, int(((now or datetime.datetime.now(datetime.timezone.utc)) - t).total_seconds()))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return "%d%s" % (s // n, unit)
    return "%ds" % s


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def off_board(o, r):
    """Repo r's open `off-board` issues (RESUME, AGENTS, ...). A repo's own listing, not search:
    no index to lag a fresh issue. PRs left out: GitHub lists them as issues."""
    return [i for i in api_all("/repos/%s/%s/issues?state=open&labels=off-board" % (o, r)) if not is_pr(i)]


def agents_issue(create=False):
    """The open AGENTS issue, found by LABEL (never search, same reason as cmd_resume). Created
    only by an explicit `agent` verb, never by a touch. Two (a create race) are TOLERATED,
    not refused: every reader takes the LOWEST number, so all agents converge on one and the
    kanban keeps working; the stray is closed by hand at leisure."""
    hits = sorted((i for i in off_board(HOME_OWNER, AGENTS_REPO)
                   if (i.get("title") or "").startswith("AGENTS")), key=lambda i: i["number"])
    if hits or not create:
        return hits[0] if hits else None
    i = api("/repos/%s/%s/issues" % (HOME_OWNER, AGENTS_REPO), "POST",
            {"title": AGENTS_TITLE,
             "body": "The agent roster. One comment per working agent, written by `forge.py agent`;"
                     " the kanban shows it live. Not work — never offered by the board.",
             "labels": label_names(["off-board"], HOME_OWNER, AGENTS_REPO)})
    return i


def agents_now(create=False, dupes=None):
    """(issue, roster) — the issue is None when no agent has ever entered."""
    i = agents_issue(create)
    if not i:
        return None, []
    return i, roster(api_all("/repos/%s/%s/issues/%d/comments"
                             % (HOME_OWNER, AGENTS_REPO, i["number"])), dupes)


def new_entry(session, seat=None, host=None, local=None):
    return {"name": agent_name(local or session), "session": session, "local": local or "",
            "seat": seat or this_seat(), "host": host or socket.gethostname(),
            "link": agent_link(session), "claims": [], "board": CFG.get("board") or ""}


def upsert_agent(session, mutate=None, create=True, **fresh):
    """Create or rewrite `session`'s entry, stamping `seen`. Returns the entry as written, or
    None when there is no AGENTS issue and `create` is off."""
    dupes = []
    i, agents = agents_now(create, dupes)
    if not i:
        return None
    for cid in dupes:
        api("/repos/%s/%s/issues/comments/%d" % (HOME_OWNER, AGENTS_REPO, cid), "DELETE")
    mine = my_entry(agents, session, session_ids(session))
    d = dict(mine) if mine else new_entry(session, **fresh)
    if d["session"] != session and session.startswith("session_"):
        # Entered under the local id, before the claude.ai link was up (or under an earlier link): keyed
        # by the bridge id from now on, as every process that has it keys it, the local id beside it.
        d.update(session=session, local=d.get("local") or d["session"], link=agent_link(session))
    if fresh.get("local") and d.get("local") and d["local"] != fresh["local"]:
        # A new LOCAL id under the same session is a /clear: a new conversation that remembers
        # nothing of the old one, so a new agent. It takes the handle the SessionStart hook titled
        # it with (hooks/session_title.py), and the old conversation's doing/ctx/blocked go with
        # it. Claims and messages stay: they key on the session. Only this explicit write
        # renames, never touch(): a process started before the /clear (a mailbox waiter)
        # still carries the old id and would rename it back.
        d.update(local=fresh["local"], name=agent_name(fresh["local"]), doing="", ctx="", blocked="", subs=[], working="")
    elif fresh.get("local") and not d.get("local"):
        d["local"] = fresh["local"]
    if session == agent_session():
        d["board"] = CFG.get("board") or ""   # its own write says which board it works from, as its directory says
    if mutate:
        mutate(d, agents)
    d["seen"] = utcnow()
    body = agent_body(d)
    if mine:
        api("/repos/%s/%s/issues/comments/%d" % (HOME_OWNER, AGENTS_REPO, mine["id"]), "PATCH", {"body": body})
    else:
        d["id"] = api("/repos/%s/%s/issues/%d/comments" % (HOME_OWNER, AGENTS_REPO, i["number"]),
                      "POST", {"body": body})["id"]
    return d


def local_id(session):
    """Claude Code's own id, recorded beside the bridge id so a hook that sees only one finds us."""
    loc = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    return loc if loc and loc != session else None


# A touch is a roster read and a write on GitHub (about 4 requests), and every forge.py call and
# every round of a mailbox waiter wants one: done each time, that was most of the account's hourly
# quota. So a session touches at most once per TOUCH_EVERY_S (a stamp file beside the hook's state),
# and `seen` is then that old at most for a live agent: under the board's 300 s mark for idle.
TOUCH_EVERY_S = 180


def touch(force=False):
    """Bump this session's `seen`, at most once per TOUCH_EVERY_S unless `force`, so the board's age
    is time since the agent last acted, to within that. Only the `seen` line is rewritten, from a
    fresh read of the comment, so a touch racing a `claim` cannot put back the old claims. It never
    creates the AGENTS issue (two first calls would each create one), and it never creates an ENTRY:
    only an explicit `agent` verb does (/go's `agent enter`). A touch that re-created a missing entry
    undid every `agent exit` and every owner x on the very next forge.py call, the leaver's own
    included. True when it touched, False when this session is not on the roster, None when it was
    skipped or could not tell. BEST-EFFORT, BY CONSTRUCTION: it rides every other verb, so a failure
    must never cost the caller its command — api() exits on HTTP errors, hence BaseException."""
    s = agent_session()
    if not s:
        return None
    stamp = os.path.join(os.path.expanduser("~"), ".cache", "forge-board", "touched-" + re.sub(r"[^\w.-]", "_", s))
    try:
        if not force and time.time() - os.path.getmtime(stamp) < TOUCH_EVERY_S:
            return None
    except OSError:
        pass
    try:
        i, agents = agents_now()
        if not i:
            return None
        mine = my_entry(agents, s, my_ids())
        if not mine:
            return False
        path = "/repos/%s/%s/issues/comments/%d" % (HOME_OWNER, AGENTS_REPO, mine["id"])
        body = (api(path) or {}).get("body") or ""
        if parse_agent(body):
            api(path, "PATCH", {"body": with_seen(body, utcnow())})
        try:
            os.makedirs(os.path.dirname(stamp), exist_ok=True)
            with open(stamp, "w"):
                pass
        except OSError:
            pass
        return True
    except BaseException:
        return None


def agent_line(d, ids=()):
    """One roster line. `<- you` is the board's served `you` for an entry read from the board, and
    is_me() for this session's own entry just written (a write path reads the forge directly)."""
    you = d["you"] if "you" in d else is_me(d, ids)
    # The board's freshness verdict (forge/board/roster.py), marked as the kanban colours it: a
    # session that crashed just stops acting, and must not read as live work.
    # A class this side does not know is still SHOWN, never dropped into reading as live.
    f = d.get("fresh") or ""
    fresh = {"idle": " [idle]", "stale": " [STALE]", "dead": " [DEAD]"}.get(f, " [%s]" % f.upper() if f else "")
    subs = d.get("subs") or []
    return "  %-16s seat:%-8s %-12s %5s ago%s  %s%s%s%s" % (
        d.get("name") or "?", d.get("seat") or "?", (d.get("host") or "?")[:12], ago(d["seen"]), fresh,
        ("claims " + " ".join(d["claims"])) if d["claims"] else "claims nothing",
        ("  — " + d["doing"]) if d.get("doing") else "",
        "  [%d subagent%s working]" % (len(subs), "" if len(subs) == 1 else "s") if subs else "",
        "   <- you" if you else "")


def cmd_agents(a):
    # The board serves the roster; every display reads it there.
    d0 = board_read()
    agents, num = d0.get("agents") or [], d0.get("agents_issue")
    lines = roster_lines(d0, a.verbose)
    if not agents:
        print("\n".join(lines) if lines else "AGENTS: nobody has entered (forge.py agent enter; /go does it)")
        return
    print("AGENTS — %d in the project, freshest first (%s/%s#%s)"
          % (len(agents), HOME_OWNER, AGENTS_REPO, num))
    for line in lines:
        print(line)
    print("A claim is another agent's work: never offer, review or merge it as yours. One held by a\n"
          "[STALE] or [DEAD] agent is likely a crashed session: tell the owner, who clears it.")


def agent_state(a):
    """`agent state`: the seat hook's write (hooks/agent_state.py) of `ctx`, `blocked`, `subs`, `working` and `dir` onto this
    session's entry. The hook sees Claude Code's LOCAL id on its stdin, so the entry is found by
    is_me, and a session not on the roster is left off it: only `/go` enters one. Only the named
    lines are rewritten, from a fresh read, as touch() does, and nothing is written when they
    already say it; `seen` moves with them, since a turn ending or a prompt opening is an act.
    `--ended` (SessionEnd: the session exited, or /clear began another conversation) takes the entry
    off instead, as /stop would. Every call then clears the roster of sessions that have left (reap)."""
    ids = my_ids(a.hook_session)
    i, agents = agents_now()
    mine = next((d for d in agents if is_me(d, ids)), None) if i else None
    if mine and a.ended:
        leave(mine, "its session ended")
    elif mine:
        write_state(a, mine)
    if i:
        reap(agents, ids)


def write_state(a, mine):
    fields = {}
    if a.ctx is not None:
        fields["ctx"] = a.ctx
    if a.blocked is not None:
        fields["blocked"] = a.blocked
    if a.subs is not None:
        fields["subs"] = subs_line(parse_subs(a.subs))
    if a.working is not None:
        fields["working"] = a.working
    if getattr(a, "dir", None):
        fields["dir"] = a.dir
    if not fields:
        return
    path = "/repos/%s/%s/issues/comments/%d" % (HOME_OWNER, AGENTS_REPO, mine["id"])
    body = (api(path) or {}).get("body") or ""
    d = parse_agent(body)
    now = dict(d or {}, subs=subs_line((d or {}).get("subs")))
    # The heartbeat: nothing changed, but a working session's `seen` is moved once it is this old.
    due = a.beat_after is not None and seen_age((d or {}).get("seen")) >= a.beat_after
    if not d or (all((now.get(k) or "") == " ".join(str(v).split()) for k, v in fields.items()) and not due):
        return
    api(path, "PATCH", {"body": with_fields(body, dict(fields, seen=utcnow()))})


# A roster entry whose session has LEFT is taken off by any agent on the same machine, at its next
# report (agent state): nothing else would ever remove it, and a dead agent's claims would hold
# their issues forever. "Left" is proven, never guessed from quiet alone: the entry is this machine's,
# no running Claude Code process holds either of its ids (Claude Code's own registry of its processes,
# ~/.claude/sessions/<pid>.json), and it has been quiet REAP_QUIET_S (a working session reports at
# least every 4 minutes). The registry is trusted only when it lists the caller's own session, so a
# Claude Code that keeps it differently reaps nothing. Never on Windows: there os.kill(pid, 0) would
# END the process it asks about. An entry from another machine is that machine's agents' to clear,
# or the owner's (the kanban's remove).
REAP_QUIET_S = 600


def live_sessions():
    """The session ids of the Claude Code processes running on this machine, or None when that
    cannot be known (no registry, or Windows)."""
    if os.name == "nt":
        return None
    d = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
    try:
        names = os.listdir(d)
    except OSError:
        return None
    out = set()
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                s = json.load(f)
            pid, sid = int(s["pid"]), s["sessionId"]
            os.kill(pid, 0)  # signal 0: only asks whether it runs
        except PermissionError:  # it runs, as another user
            out.add(sid)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        else:
            out.add(sid)
    return out


def reap_targets(agents, live, ids, host, age=None):
    """PURE: the roster entries whose sessions have left (see REAP_QUIET_S): none when `live` is None
    or does not hold one of the caller's `ids`. An entry with only a claude.ai bridge id is never
    one: the registry knows local ids. `age` maps a `seen` to seconds (seen_age, for tests)."""
    age = age or seen_age
    if live is None or not (set(ids) & live):
        return []
    out = []
    for d in agents:
        known = {d.get("local")} | ({d.get("session")} if not str(d.get("session") or "").startswith("session_") else set())
        known -= {None, ""}
        if (d.get("host") == host and known and not is_me(d, ids) and not (known & live)
                and age(d.get("seen")) >= REAP_QUIET_S):
            out.append(d)
    return out


def leave(d, why):
    """Take entry `d` off the roster and free its claims (the claim label too), as /stop and the
    kanban's remove do."""
    api("/repos/%s/%s/issues/comments/%d" % (HOME_OWNER, AGENTS_REPO, d["id"]), "DELETE", allow=(404,))
    mark_claims(d.get("claims") or [], False)
    print("%s left the roster (%s)%s" % (d.get("name") or d.get("session"), why,
                                         "; released " + " ".join(d["claims"]) if d.get("claims") else ""))


def reap(agents, ids):
    gone = reap_targets(agents, live_sessions(), ids, socket.gethostname())
    for d in gone:
        try:
            leave(d, "its session is no longer running")
        except SystemExit as e:
            print("warning: could not take %s off the roster: %s" % (d.get("name"), str(e.code).splitlines()[0]),
                  file=sys.stderr)
    if gone:
        board_refresh()


def seen_age(seen):
    """PURE-ish: seconds since ISO-8601 `seen`; unreadable or missing is infinitely old."""
    try:
        t = datetime.datetime.fromisoformat(str(seen).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()


def workflow_path():
    """The board's workflow file: REPO_WORKFLOW, else workflow.json beside the project .env."""
    return opt("workflow") or os.path.join(os.path.dirname(env_path()), "workflow.json")


def claim_label():
    """The workflow's `claim_label`, or None: the label a claim puts on the issue, so GitHub itself
    shows the issue is taken. The board server validates the file; this only reads that one key."""
    try:
        with open(workflow_path(), encoding="utf-8") as f:
            return (json.load(f).get("claim_label") or "").strip() or None
    except (OSError, ValueError, AttributeError):
        return None


def mark_claims(refs, on):
    """Put the claim label on each issue in `refs` (on), or take it off (off). Best-effort and AFTER
    the roster write that IS the claim: a repo without the label, or a GitHub error, costs a warning,
    never the claim. On, it is a scoped label like any other, so it replaces the issue's other status
    (`forge.py label` semantics); off, it takes off only itself, so a card someone moved to review
    stays there."""
    lab = claim_label()
    for t in refs if lab else []:
        o2, r2, n2 = ref(t)
        try:
            if on:
                add_labels(o2, r2, n2, [lab])
            elif lab in labels_of(api("/repos/%s/%s/issues/%d" % (o2, r2, n2))):
                api("/repos/%s/%s/issues/%d/labels/%s" % (o2, r2, n2, quote(lab, safe="")), "DELETE", allow=(404,))
        except SystemExit as e:
            print("warning: %s: the claim label %r was not %s: %s"
                  % (t, lab, "set" if on else "taken off", str(e.code).splitlines()[0]), file=sys.stderr)


def cmd_agent(a):
    """enter / claim / release / doing / exit — this session's own roster entry."""
    if a.action == "state":
        return agent_state(a)
    s = a.session or agent_session()
    if not s:
        sys.exit("no session: forge.py agent runs inside a Claude Code session "
                 "(CLAUDE_CODE_SESSION_ID), or pass --session")
    # Registering ANOTHER agent: its platform and host are not this machine's, so they are given.
    fresh = ({"seat": a.seat, "host": a.host or "?"} if a.session
             else {"local": local_id(s)})
    ids = session_ids(s)
    if a.action == "exit":
        i, agents = agents_now()
        mine = my_entry(agents, s, ids)
        if not mine:
            print("%s was not on the roster — nothing to remove" % (my_name() if s == agent_session() else agent_name(s)))
            return
        if a.session and a.session == agent_session():
            sys.exit("--session names ANOTHER agent (the owner clearing a dead one); leave as yourself without it")
        if a.session:
            # Removing ANOTHER agent (a crashed session) is the owner's act, from their own shell;
            # the kanban's × does the same.
            acting(a) if a.owner else sys.exit("removing another agent is the owner's: add --owner, from your own shell")
        api("/repos/%s/%s/issues/comments/%d" % (HOME_OWNER, AGENTS_REPO, mine["id"]), "DELETE")
        print("%s left%s" % (mine["name"], ("; released " + " ".join(mine["claims"]))
                             if mine["claims"] else ""))
        mark_claims(mine["claims"], False)
        board_refresh()  # the roster every display reads is the board's: show the exit now
        return
    refs = []
    for t in a.refs:
        o2, r2, n2 = ref(t)
        refs.append("%s#%d" % (r2, n2))
    if a.action in ("claim", "release") and not refs:
        sys.exit("name what to %s: forge.py agent %s host#660" % (a.action, a.action))
    if a.action == "claim":
        # A claim names a real OPEN issue; a typo must fail here, not claim nothing forever.
        for t in a.refs:
            o2, r2, n2 = ref(t)
            if (api("/repos/%s/%s/issues/%d" % (o2, r2, n2)) or {}).get("state") != "open":
                sys.exit("%s is not an open issue — nothing to claim" % t)

    def mutate(d, agents):
        if a.action == "claim":
            for t in refs:
                held = claim_holders(agents, t, ids)
                if held and not a.steal:
                    h = held[0]
                    sys.exit("%s is CLAIMED by %s (seat:%s, %s, last action %s ago).\n"
                             "That is another agent's live work. --steal only on the owner's word."
                             % (t, h["name"], h["seat"], h["host"], ago(h["seen"])))
                for h in held:  # --steal: take it off the other entry, which is the owner's call
                    h2 = dict(h, claims=[c for c in h["claims"] if c != t])
                    api("/repos/%s/%s/issues/comments/%d" % (HOME_OWNER, AGENTS_REPO, h["id"]), "PATCH",
                        {"body": agent_body(h2)})
                    print("  took %s from %s" % (t, h["name"]))
                if t not in d["claims"]:
                    d["claims"].append(t)
        elif a.action == "release":
            d["claims"] = [c for c in d["claims"] if c not in refs]
        if a.doing is not None:
            d["doing"] = a.doing
        if a.msgname is not None:
            d["msgname"] = a.msgname

    d = upsert_agent(s, mutate, **fresh)
    if a.action == "enter":
        # The mailbox starts HERE: a broadcast sent before this session's first read must still
        # reach it (the board enrols a reader at its first read). Best-effort, like the notice.
        board("/api/mail?for=%s" % quote(mail_id() if s == agent_session() else s), timeout=3, quiet=True)
    if a.action == "claim" and not a.steal:
        # The refusal above checked a snapshot; two agents claiming in the same second both pass it.
        # Re-read AFTER the write: on a tie the OLDER entry (lower comment id) keeps the item and the
        # newer one backs off, so exactly one wins and neither needs to retry blind.
        lost = [(t, h) for t in refs for h in claim_holders(agents_now()[1], t, ids) if h["id"] < d["id"]]
        if lost:
            gone = {t for t, _ in lost}
            upsert_agent(s, lambda e, _: e.update(claims=[c for c in e["claims"] if c not in gone]))
            t, h = lost[0]
            sys.exit("%s was claimed by %s in the same moment — theirs (older entry); backed off."
                     % (t, h["name"]))
    if a.action in ("claim", "release"):
        mark_claims(refs, a.action == "claim")
    print(agent_line(d, my_ids()))
    board_refresh()  # the roster every display reads is the board's: show this write now


# THE BOARD SERVER (forge/board): the snapshot every display reads, and the agents' messages in its
# own SQLite file (mail.py), reached over HTTP from every machine. Messages are not GitHub comments:
# several writers doing read-modify-writes from several machines cannot be made atomic there. Here
# these verbs only ask. `actor` is this session's roster handle, or `owner` outside a session.
BOARD_HOLD_S = 30  # longer than the board's own long-poll hold (25 s)


BOARD_DEFAULT = "http://localhost:3001"


def board_url():
    """REPO_BOARD_URL (the .env or the environment), else the board on this machine."""
    try:
        return seat_cfg("board_url").rstrip("/")
    except Exception:
        return BOARD_DEFAULT


def board(path, body=None, timeout=BOARD_HOLD_S, quiet=False, net=False):
    """One board call: its JSON, or exit with the board's reason (409 refused by the rules, 422
    refused for good). `quiet` returns None on ANY failure: for the notice every call prints.
    `net` returns None only when the board did not answer (a waiter retries that); an answer that
    refuses still exits, so a typo is said, not retried forever."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if opt("board_key"):   # the board's sign-in: the agents' API key (bin/start makes it: REPO_BOARD_KEY)
        headers["Authorization"] = "Bearer " + opt("board_key")
    req = Request(board_url() + path, data=data, method="POST" if body is not None else "GET", headers=headers)
    try:
        with urlopen(req, timeout=timeout) as f:
            return json.loads(f.read().decode("utf-8", "replace") or "null")
    except (http.client.HTTPException, ValueError) as e:  # a torn or garbled answer: a network failure
        if quiet or net:
            return None
        sys.exit("the board (%s) answered badly: %s" % (board_url(), e))
    except HTTPError as e:
        if quiet or (net and e.code >= 500):
            return None
        raw = e.read().decode("utf-8", "replace")
        try:
            why = json.loads(raw).get("error")
        except (ValueError, AttributeError):
            why = None
        sys.exit("board: %s" % (why or "HTTP %d %s" % (e.code, raw[:200])))
    except OSError as e:
        if quiet or net:
            return None
        sys.exit("the board (%s) did not answer: %s — is the board container up? (forge/: docker compose up -d board)"
                 % (board_url(), e))


def acting(a):
    """(handle, owner?) for this call. The owner is an EXPLICIT act: `--owner`, refused inside an
    agent session. Never inferred from a missing session -- an ssh hop or a scrubbed environment
    would otherwise hand an agent the owner's powers by accident."""
    s = agent_session()
    if getattr(a, "owner", False):
        if s:
            sys.exit("--owner is the owner's, from their own shell; this is agent session %s" % my_name())
        return "owner", True
    if not s:
        sys.exit("no agent session here. The owner says so: add --owner")
    return my_name(), False


def cmd_tell(a):
    text = prose(a.message, a.message_file, "-m", "-F")
    to, sessions = a.to, []
    if to not in ("owner", "all"):
        # A message is addressed to SESSIONS: a handle can name two (handles collide) and recurs
        # across sessions, so it is resolved here, on the roster, to the sessions it names now.
        sessions = [[mail_to(e), e["name"]] for e in agents_now()[1] if e["name"] == to]
        if not sessions:
            sys.exit("no agent %r on the roster (forge.py agents); or send to owner / all" % to)
    sent = board("/api/mail", {"actor": acting(a)[0], "session": mail_id() or "owner", "to": to,
                               "to_sessions": sessions, "body": text})["to"]
    print("sent to %s" % ", ".join(sent))


def show_mail(mail):
    for m in mail:
        print("--- %s  %s -> %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M", time.gmtime(m["at"])), m["sender"],
                                         m.get("rcpt_name") or m["rcpt"], m["body"]))


def cmd_inbox(a):
    acting(a)
    me = mail_id() or "owner"  # the reader is the SESSION: handles collide and recur
    if a.all:
        show_mail(board("/api/mail/recent?n=50")["mail"])
        return
    next_touch = 0.0
    while True:
        r = board("/api/mail?for=%s%s" % (quote(me), "&wait=1" if a.wait else ""), net=a.wait)
        if r is None:  # only under --wait: the board restarted or the long-poll dropped
            time.sleep(5)
            continue
        got = r["mail"]
        if got or not a.wait:
            break
        # A waiter is alive: its roster age must not invite the owner's x. Once per TOUCH_EVERY_S, not
        # every long-poll (each touch is GitHub requests), and the same read says whether it is still
        # entered: a waiter that outlives its agent's `/stop` (TaskStop kills only the shell, not this
        # process) must end, not touch the roster back to life.
        if time.monotonic() >= next_touch:
            next_touch = time.monotonic() + TOUCH_EVERY_S
            if touch(force=True) is False:
                sys.exit("%s left the roster: the mailbox waiter ends" % my_name())
    if not got:
        print("no unread mail")
        return
    show_mail(got)
    board("/api/mail/read", {"actor": me, "last": got[-1]["id"]})


def mail_notice():
    """The one line every forge.py call prints when this session has unread mail. Best-effort and
    quick: a board that is down costs nothing here."""
    s = mail_id()
    if not s:
        return None
    r = board("/api/mail?for=%s" % quote(s), timeout=1.0, quiet=True)
    n = (r or {}).get("mail") or []
    if not n:
        return None
    return "MAIL: %d unread (latest from %s) -- forge.py inbox" % (len(n), n[-1]["sender"])


def label_repos():
    """The repos a label must exist in: every repo the board spans."""
    return scope_repos()


def cmd_labels(a):
    """Every label the board's repos carry, and any repo missing one: GitHub has no org labels,
    so the set is kept in step across repos by `newlabel`, and a gap shows here."""
    need_board("labels")
    o, repos = CFG["owner"], label_repos()
    where = {}
    for r in repos:
        for l in api_all("/repos/%s/%s/labels" % (o, r)):
            where.setdefault(l["name"], set()).add(r)
    for name in sorted(where):
        gap = [r for r in repos if r not in where[name]]
        print("  %-32s exclusive=%s%s" % (name, bool(scope_of(name)),
                                         ("   missing in: " + ", ".join(gap)) if gap and len(repos) > 1 else ""))


def cmd_search(a):
    """Search every issue, OPEN AND CLOSED, before filing one. `board` lists only open rows, so a
    defect closed as a dup or as not warranted an hour ago would read as unfiled and get refiled."""
    need_board("search")
    # GitHub search: its index can lag an issue filed seconds ago, so a miss right after a
    # create is not proof of absence. At most 2 pages: the first 200 matches.
    rows = []
    for page in (1, 2):
        b = (api("/search/issues?q=%s&per_page=%d&page=%d"
                 % (quote("%s %s is:issue" % (" ".join(a.words), search_scope())), PER_PAGE, page)) or {}).get("items") or []
        rows += b
        if len(b) < PER_PAGE:
            break
    for i in rows:
        print("  %-14s %-6s %s" % ("%s#%d" % (repo_of(i), i["number"]), i["state"], i["title"][:100]))
    print("%d match(es)%s" % (len(rows), " (first 200)" if len(rows) >= 200 else ""))


def cmd_newlabel(a):
    """Create a label in every repo the board spans: GitHub has no
    org labels, so "once" means once per repo. Repos that already carry it are left alone, so a
    re-run fills only the gaps `labels` names.

    A scoped name (`severity: high`, `area/api`) is EXCLUSIVE by forge.py's rule (`label` replaces a sibling) and
    takes its colour from a sibling in the same scope, so it cannot stand out from its siblings."""
    need_board("newlabel")
    o, scope = CFG["owner"], scope_of(a.name)
    have = {r: api_all("/repos/%s/%s/labels" % (o, r)) for r in label_repos()}
    todo = [r for r, ls in have.items() if not any(l["name"] == a.name for l in ls)]
    if not todo:
        sys.exit("label %s already exists in every repo" % a.name)
    sib = next((l for ls in have.values() for l in ls if scope and l["name"].startswith(scope + "/")), None)
    color = (a.color or (sib["color"] if sib else "888888")).lstrip("#")
    for r in todo:
        api("/repos/%s/%s/labels" % (o, r), "POST", {"name": a.name, "color": color,
                                                     "description": a.description or ""})
    print("created %s in %s  exclusive=%s color=%s" % (a.name, ", ".join(todo), bool(scope), color))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("board"); b.add_argument("--all", action="store_true", help="every open row, with why it is not offered")
    b.set_defaults(fn=cmd_board)
    pr = sub.add_parser("prs"); pr.set_defaults(fn=lambda a: cmd_prs())
    s = sub.add_parser("show"); s.add_argument("issue"); s.set_defaults(fn=cmd_show)
    cm = sub.add_parser("comment"); cm.add_argument("issue")
    cm.add_argument("-m", "--message"); cm.add_argument("-F", "--message-file")
    cm.add_argument("--once", action="store_true",
                    help="skip if this exact text is already on the issue (safe re-runs)")
    cm.add_argument("--replace-last", action="store_true",
                    help="rewrite YOUR newest comment in place instead of adding one")
    cm.set_defaults(fn=cmd_comment)
    uc = sub.add_parser("uncomment"); uc.add_argument("issue")
    uc.add_argument("--id", type=int, required=True, help="the comment id `show` prints")
    uc.set_defaults(fn=cmd_uncomment)
    ed = sub.add_parser("edit"); ed.add_argument("issue")
    ed.add_argument("-t", "--title"); ed.add_argument("-b", "--body")
    ed.add_argument("-F", "--body-file"); ed.set_defaults(fn=cmd_edit)
    cl = sub.add_parser("close"); cl.add_argument("issue"); cl.add_argument("-m", "--message")
    cl.add_argument("-F", "--message-file")
    cl.add_argument("--once", action="store_true",
                    help="skip the comment if this exact one is already there; still closes")
    cl.set_defaults(fn=cmd_close)
    du = sub.add_parser("dump"); du.add_argument("out"); du.set_defaults(fn=cmd_dump)
    ro = sub.add_parser("reopen"); ro.add_argument("issue"); ro.set_defaults(fn=cmd_reopen)
    mg = sub.add_parser("merge"); mg.add_argument("pr")
    mg.add_argument("--style", choices=sorted(MERGE_DO), default="merge")
    mg.add_argument("-t", "--title"); mg.add_argument("-m", "--message")
    mg.add_argument("-F", "--message-file")
    mg.add_argument("--keep-branch", action="store_true"); mg.set_defaults(fn=cmd_merge)
    rt = sub.add_parser("retarget"); rt.add_argument("pr"); rt.add_argument("--base", required=True)
    rt.set_defaults(fn=cmd_retarget)
    pl = sub.add_parser("pr"); pl.add_argument("repo", nargs="?")
    pl.add_argument("--head", required=True); pl.add_argument("--base")
    pl.add_argument("-t", "--title", required=True); pl.add_argument("-b", "--body")
    pl.add_argument("-F", "--body-file"); pl.set_defaults(fn=cmd_pr)
    cr = sub.add_parser("create"); cr.add_argument("repo", nargs="?")
    cr.add_argument("-t", "--title", required=True); cr.add_argument("-b", "--body")
    cr.add_argument("-F", "--body-file")
    cr.add_argument("--label", action="append", help="a label to file it with (repeatable)")
    cr.add_argument("--blocked-by", action="append"); cr.set_defaults(fn=cmd_create)
    lb = sub.add_parser("label"); lb.add_argument("issue"); lb.add_argument("labels", nargs="+")
    lb.set_defaults(fn=cmd_label)
    ul = sub.add_parser("unlabel"); ul.add_argument("issue"); ul.add_argument("labels", nargs="+")
    ul.set_defaults(fn=cmd_unlabel)
    mv = sub.add_parser("move", help="move a card to another column of the board's workflow (sets its label)")
    mv.add_argument("issue"); mv.add_argument("column", help="the column's key or name, e.g. doing or \"In progress\"")
    mv.set_defaults(fn=cmd_move)
    bk = sub.add_parser("block"); bk.add_argument("issue"); bk.add_argument("--on", required=True)
    bk.set_defaults(fn=cmd_block)
    ub = sub.add_parser("unblock"); ub.add_argument("issue")
    ub.add_argument("--on", required=True); ub.set_defaults(fn=cmd_unblock)
    sub.add_parser("labels").set_defaults(fn=cmd_labels)
    se = sub.add_parser("search"); se.add_argument("words", nargs="+"); se.set_defaults(fn=cmd_search)
    nl = sub.add_parser("newlabel"); nl.add_argument("name")
    nl.add_argument("-d", "--description"); nl.add_argument("--color")
    nl.set_defaults(fn=cmd_newlabel)
    rs = sub.add_parser("resume"); rs.add_argument("-b", "--body"); rs.add_argument("-F", "--body-file")
    rs.add_argument("--clear", action="store_true", help="empty the plan: nothing in flight or imperative")
    rs.set_defaults(fn=cmd_resume)

    ag = sub.add_parser("agents", help="the agent roster: who is working, on what, freshest first")
    ag.add_argument("-v", "--verbose", action="store_true", help="also print session ids and links")
    ag.set_defaults(fn=cmd_agents)
    at = sub.add_parser("agent", help="this session's roster entry: enter | claim | release | exit | state")
    at.add_argument("action", choices=("enter", "claim", "release", "exit", "state"))
    at.add_argument("refs", nargs="*", help="repo#N to claim or release")
    at.add_argument("-d", "--doing", help="one line: what this agent is doing now")
    at.add_argument("--msgname", help="the name ListAgents gives this session on its machine "
                                      "(\"This session is app-6d [82b1bf]\"): what SendMessage takes there")
    at.add_argument("--steal", action="store_true", help="take a claim another agent holds (only on the owner's word)")
    at.add_argument("--session", help="act for ANOTHER session id (registering an agent that predates the roster)")
    at.add_argument("--seat", help="with --session: that agent's platform (windows | mac | linux)")
    at.add_argument("--host", help="with --session: that agent's host")
    at.add_argument("--owner", action="store_true", help="exit --session: the owner removing a dead agent")
    at.add_argument("--ctx", help="state: context in use, in tokens (the seat hook writes it)")
    at.add_argument("--blocked", help="state: what the session waits on the owner for; empty clears it")
    at.add_argument("--subs", help="state: the subagents it has working, as JSON [{id, type, what}]; empty clears it")
    at.add_argument("--working", help="state: when the turn under way began (UTC); empty between turns")
    at.add_argument("--dir", help="state: the directory the session runs in (~ for home)")
    at.add_argument("--beat-after", type=int, help="state: move `seen` though nothing changed, once it is this many seconds old")
    at.add_argument("--ended", action="store_true", help="state: the session ended (SessionEnd): take its entry off the roster")
    at.add_argument("--hook-session", help="state: the session id from the hook's stdin")
    at.set_defaults(fn=cmd_agent)

    tl = sub.add_parser("tell", help="send a message to an agent's handle, owner, or all")
    tl.add_argument("to"); tl.add_argument("-m", "--message"); tl.add_argument("-F", "--message-file")
    tl.add_argument("--owner", action="store_true", help="the owner, from their own shell (refused in an agent session)")
    tl.set_defaults(fn=cmd_tell)
    ib = sub.add_parser("inbox", help="your unread messages; --wait blocks until one arrives")
    ib.add_argument("--wait", action="store_true"); ib.add_argument("--all", action="store_true")
    ib.add_argument("--owner", action="store_true", help="the owner, from their own shell (refused in an agent session)")
    ib.set_defaults(fn=cmd_inbox)

    a = p.parse_args()
    try:
        configure()
    except Exception as e:
        sys.exit("forge not configured: %s" % e)
    if a.cmd not in ("agent", "inbox"):  # `agent` writes the entry itself; `inbox` IS the mail
        # The mail notice runs beside the touch and the command, so a slow board costs nothing.
        got = []

        def notice():
            try:
                got.append(mail_notice())
            except BaseException:  # best-effort, like the touch: never costs the caller its command
                pass
        th = threading.Thread(target=notice, daemon=True)
        th.start()
        touch()
        a.fn(a)
        th.join(0.5)
        if got and got[0]:
            print(got[0], file=sys.stderr)
        return
    a.fn(a)


if __name__ == "__main__":
    main()
