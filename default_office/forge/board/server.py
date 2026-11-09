"""The board — a LIVE kanban of the forge (GitHub: the owner and repos the project .env names).

THE WORKFLOW (workflow.py, read from workflow.json beside the .env, reloaded when it changes) says
the columns: which rule puts a row in each, which ones agents pick from, which a card may be dragged
to. Each row's column is judged once per snapshot by pick.columns from the same facts as the
pickability /go reads. Two writes come from the board: MOVING a card (/api/move) rewrites its
column labels on GitHub, and the Up next list.

Up next IS the RESUME plan: the body of the one open `RESUME…` issue in the home repo (REPO_HOME),
the ordered list `/go` starts from (`forge.py resume`). Reordering, adding or removing there rewrites
that body, so the board and a session share one list and neither can drift. Rows that left the board
lately are served as `gone` (closed or relabelled) and the kanban draws them in the closed column.
Every key served here is classed in views.py by the view that shows it (served == shown);
test_views.py fails on a key served and not classed.

RANK: within a column, and in the ready set and the Agent Room's wall, rows are ordered by REPO_RANK,
an ordered list of label names (highest first); a row carrying none is `unranked`, last.

The AGENTS strip is the roster: one comment per working agent on the one open `AGENTS…` issue in the
home repo, written by `forge.py agent` (seat/tools/forge.py). roster.py reads it, and every display
of it (forge.py agents, the hook, the kanban, the Agent Room) reads what is served here, each agent
with its `fresh` class. Its one write here is the owner's ×, which deletes a dead session's entry and
with it the session's claims (no expiry: the owner clears crashes).

Live: every POLL seconds one issue listing (issues and PRs) + the plan and roster; dependencies are
re-read only for issues whose updated_at moved, and for all of them every FULL seconds (an edge added
on issue X may not bump X). Pages long-poll /api/board?v=N and return the moment it changes.
forge.py's issue writes POST /api/refresh with the issues they touched, so a `board` read right after
`forge.py block` sees the new edge instead of waiting out FULL.

GitHub has a QUOTA (5000 requests/hour per ACCOUNT: every token of one user shares it), so every GET
is conditional: a 304 costs nothing against it, and an unchanged board is nearly free to poll. When
the quota runs low the poller waits for its reset and serves the last snapshot with `error` saying
so, leaving the rest to the agents, who draw on the same account unless the board's token is another
user's (a bot account).

PICKABILITY (pick.py): /api/board?me=<ids> serves each row's `why_not` and `claimed`, computed for
that caller. `forge.py board` and the SessionStart hook only print them; this server is the one place
the rule lives.

MESSAGES (mail.py): between agents and the owner, in one SQLite file on the `boarddata` volume.
forge.py's tell/inbox verbs are clients of the /api/mail routes below; every write is one
transaction, and `inbox --wait` long-polls here like the page does.

Stdlib only, so the container is python:alpine and nothing else.
"""
import base64
import hashlib
from html import escape
import json
import os
import re
import select
import selectors
import socket
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

import auth
import gone
import mail
import pick
import roster
import tarot
import workflow


def load_env(path):
    """The project .env (the root above forge/ and seat/), under whatever the process environment
    already sets: in the container, compose's env_file has put it there; run by hand, it is read
    here. KEY=value lines, `#` comments, one layer of quotes off; a blank value is unset."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                v = v.strip()
                v = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v
                if sep and v and k.strip() and not k.lstrip().startswith("#"):
                    os.environ.setdefault(k.strip(), v)
    except OSError:
        pass


ENV_FILE = os.environ.get("REPO_ENV") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
ENV_GIVEN = {k for k, v in os.environ.items() if v.strip()}   # set by the process, not the file: they win (boards_named)
load_env(ENV_FILE)
if not (os.environ.get("REPO_TOKEN") or "").strip():
    raise SystemExit("board: no REPO_TOKEN — set it in the project .env (the root above forge/), "
                     "e.g. REPO_TOKEN=$(gh auth token)")

FORGE = os.environ.get("REPO_URL", "https://api.github.com").rstrip("/")
PUBLIC = os.environ.get("REPO_PUBLIC_URL", "https://github.com").rstrip("/")
TOKEN = os.environ["REPO_TOKEN"]
OWNER = (os.environ.get("REPO_OWNER") or "").strip()
# The repos the board spans ([] = every repo of OWNER, one org listing), and the one holding the
# RESUME and AGENTS issues (a single repo in scope is it).
REPOS = [r for r in re.split(r"[\s,]+", os.environ.get("REPO_NAMES") or "") if r]
HOME_REPO = (os.environ.get("REPO_HOME") or "").strip() or (REPOS[0] if len(REPOS) == 1 else "")
if not OWNER or not HOME_REPO:
    raise SystemExit("board: set REPO_OWNER, and REPO_HOME unless REPO_NAMES names exactly one repo, "
                     "in the project .env")
# THE OTHER BOARDS: REPO_BOARDS, `owner/repo` each, beside the home board (REPO_OWNER's REPO_NAMES). bin/init-agent adds
# one when it sets up a directory whose GitHub repo is not on the home board. Each is a board of its own: that repo's
# open issues and PRs in workflow.json's columns, ranked by REPO_RANK. Its bookkeeping stays in the home repo (a public
# repo gets none of it): its agents on the one roster (each entry's `board`), its Up next a RESUME issue of its own
# there, titled `RESUME [owner/repo] …`. Re-read from the .env at every poll, so a board set up from the room needs no
# restart. /api/board serves the home board as ever, another with ?board=<owner/repo>, and every one with ?board=*.
BOARD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
NO_BOARD = "none"   # a roster entry's board when its directory is no GitHub checkout: a desk and a console, no board
# The rank order (highest first) and the label that parks a row; see the docstring.
RANK = [r.strip() for r in (os.environ.get("REPO_RANK") or "").split(",") if r.strip()]
UNRANKED = 99  # an unranked row's tier: after every rank
# The workflow file (workflow.py says its shape). In the container the project root is mounted at
# /project, and compose points REPO_WORKFLOW there.
WORKFLOW_PATH = os.environ.get("REPO_WORKFLOW") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "workflow.json")
try:
    WF = {"wf": workflow.load(WORKFLOW_PATH), "mtime": None, "error": None}
except (ValueError, OSError) as e:  # a bad file at start: exit, and say where
    raise SystemExit("board: %s: %s" % (WORKFLOW_PATH, e))


def current_workflow():
    """The workflow, reloaded when its file changed. A file edited into something invalid keeps the
    last good workflow running and says why (served as columns_error), never a blank board."""
    try:
        m = os.path.getmtime(WORKFLOW_PATH)
    except OSError:
        m = None
    if m != WF["mtime"]:
        try:
            WF.update(wf=workflow.load(WORKFLOW_PATH), mtime=m, error=None)
        except (ValueError, OSError) as e:  # workflow.Invalid and bad JSON are ValueErrors
            WF.update(mtime=m, error="%s: %s (still running the previous workflow)" % (
                os.path.basename(WORKFLOW_PATH), e))
    return WF["wf"]
POLL = float(os.environ.get("BOARD_POLL_S", "5"))
FULL = float(os.environ.get("BOARD_FULL_S", "60"))
HOLD = 25  # long-poll hold, under any proxy's idle timeout
PER_PAGE = 100  # GitHub's largest page
PAGES = 10      # a list read stops here and says `truncated`: 1000 open issues and PRs
RATE_FLOOR = int(os.environ.get("BOARD_RATE_FLOOR", "300"))  # below this many left, wait for the reset
HDR = {"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json",
       "X-GitHub-Api-Version": "2022-11-28"}
HERE = os.path.dirname(os.path.abspath(__file__))
# Where bin/start writes this office's visit-client port and page token (visit.json), new each run when
# VISIT is on (basedverse's visit/INTEGRATING.md step 3). In the container the project root is mounted at /project.
VISIT_DIR = os.environ.get("VISIT_DIR") or os.path.join(os.path.dirname(HERE), "..", ".visit")
# Visit through the board (basedverse's engine/INTEGRATING.md, steps 3 to 5): visit-client on this compose network, by
# name; the person's page never reaches it: /visit/ws relays the signed-in owner to it with the user token bin/start
# writes (../.visit/user-token), and /visit/visit.js serves its library. The room's server page runs in the engine
# (the `engine` service), which fetches /room?role=host with its own key; bin/start says it did (../.visit/engine.json).
VISIT_HOST = os.environ.get("VISIT_HOST", "visit")
VISIT_PORT = int(os.environ.get("VISIT_PORT", "7401"))
VISIT_USER_TOKEN_FILE = os.environ.get("VISIT_USER_TOKEN_FILE") or os.path.join(VISIT_DIR, "user-token")
ENGINE_FILE = os.path.join(VISIT_DIR, "engine.json")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
RELAY_BUFFER = 64 << 20   # the most waiting for a browser that cannot keep up, before it is hung up on (no close frame).
# Above the most visit-client sends a page at once as it connects (a scene and a landscape, each up to 16 MiB): at
# 1 MiB, a room whose landscape outgrew it (1.6 MB, 2026-11-03) closed every page still loading, which reconnected
# to the same again, every 2 s, and never drew the room.
_relays, _relays_lock = {}, threading.Lock()   # the session's connection, to replace a second one's first (4409)


def visit_mode():
    """What this office runs: the engine (the room's page in it, the person's page a user of visit-client), and a
    visit-client at all (bin/start starts one when the .env names a lobby)."""
    return {"engine": os.path.exists(ENGINE_FILE), "lobby": os.path.exists(os.path.join(VISIT_DIR, "token"))}


def ws_frame(data, opcode=0x1, mask=True):
    """One WebSocket frame (RFC 6455): a client's, masked, as a relay sends its preface; a server's, bare, as it
    says a close. Payloads under 64 KiB."""
    head = bytes([0x80 | opcode])
    n = len(data)
    if n < 126:
        head += bytes([(0x80 if mask else 0) | n])
    else:
        head += bytes([(0x80 if mask else 0) | 126]) + n.to_bytes(2, "big")
    if not mask:
        return head + data
    key = os.urandom(4)
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(data))


def ws_close(code, reason=b""):
    return ws_frame(code.to_bytes(2, "big") + reason, opcode=0x8, mask=False)
# Where bin/shells (on the host, every few seconds) writes which sessions have a command running (now.json).
SHELLS_DIR = os.environ.get("SHELLS_DIR") or os.path.join(os.path.dirname(HERE), "..", ".shells")
SHELLS_STALE = 20  # an older now.json is a bin/shells that stopped: it says nothing
# The account's usage limits: bin/usage (on the host, every couple of minutes) writes what /usage shows there
# (plan.json), and each agent's status line (seat/hooks/usage_line.py) notes the session's and the week's beside
# it, a file a session.
USAGE_DIR = os.environ.get("USAGE_DIR") or os.path.join(os.path.dirname(HERE), "..", ".usage")
USAGE_PLAN = "plan.json"
USAGE_STALE = 600  # an older plan.json is a bin/usage that stopped (or a Claude Code whose get_usage broke)
USAGE_WINDOWS = {"five_hour": ("session", "session"), "seven_day": ("weekly_all", "weekly")}   # a note's windows, as /usage's rows

RESUME_REPO = HOME_REPO
PLAN_TITLE = "RESUME [%s] — the next session's critical path"   # another board's plan (forge.py's RESUME_TITLE, tagged)
PLAN_MAX_LINE = 110  # forge.py plan_problems refuses longer lines; keep the two in step
HAND = "✋"
_STEP = re.compile(r"^\d+\)\s+(.*)$")
_REF = roster._REF  # one `repo#N` grammar for plan steps and PR refs

SNAP = {"rows": [], "plan": None, "agents": [], "prs": [], "at": 0, "ver": 0, "error": None,
        "truncated": False}
CHANGED = threading.Condition()
KICK = threading.Event()
DEPS = {}  # id -> (updated_at, [every blocker, open or closed]); rows keep the open ones
GONE = []  # rows that left the board lately, each read once (gone.py); served as `gone`
# What a gone row keeps of the row it was: enough to draw it, place it and filter it on the kanban.
GONE_KEEPS = ("id", "title", "url", "rank", "tier", "col", "labels", "epic")
SNAPPING = threading.Lock()  # one snapshot at a time: a refresh must not race the poller's publish


class Board:
    """One board: the open issues and PRs of `repos` under `owner` ([] = every repo of the owner), and what the poller
    keeps for it between snapshots (its edges, its rows gone lately). The home board's are DEPS and GONE above, and its
    snapshot is SNAP's own rows, plan and PRs; another's is SNAP["boards"][key]."""
    def __init__(self, key, owner, repos, name, deps=None, gone=None):
        self.key, self.owner, self.repos, self.name = key, owner, repos, name
        self.deps = {} if deps is None else deps
        self.gone = [] if gone is None else gone

    def entry(self, error=None):
        """As /api/board lists it (`boards`): what a page needs to show it and to tell which board a ref is on."""
        repos = self.repos or []
        url = "%s/%s/%s" % (PUBLIC, self.owner, repos[0]) if len(repos) == 1 else "%s/%s" % (PUBLIC, self.owner)
        return dict({"key": self.key, "name": self.name, "repos": list(repos), "url": url}, **({"error": error} if error else {}))


HOME = Board("%s/%s" % (OWNER, HOME_REPO), OWNER, REPOS, REPOS[0] if len(REPOS) == 1 else HOME_REPO if REPOS else OWNER,
             DEPS, GONE)
OTHERS = {}   # key -> Board: the other boards, as REPO_BOARDS last named them (rebound whole, never changed in place)


def boards_named():
    """([(key, owner, repo)] the other boards REPO_BOARDS names now, [why one named is left out]). The process
    environment's REPO_BOARDS wins, as every setting's does; else the .env, read again. One already on the home board is
    left out quietly; one whose repo NAME another board has is refused, since `repo#N` would mean either."""
    if "REPO_BOARDS" in ENV_GIVEN:
        raw = os.environ.get("REPO_BOARDS") or ""
    else:
        raw = ""
        try:
            with open(ENV_FILE, encoding="utf-8") as f:
                for line in f:
                    k, sep, v = line.strip().partition("=")
                    if sep and k.strip() == "REPO_BOARDS":
                        raw = v.strip().strip("'\"")
        except OSError:
            pass
    taken, out, bad = {r.lower() for r in REPOS}, [], []
    for k in re.split(r"[\s,]+", raw):
        if not k:
            continue
        if not BOARD_NAME.fullmatch(k):
            bad.append("REPO_BOARDS: %r is not owner/repo" % k)
            continue
        owner, repo = k.split("/")
        if owner.lower() == OWNER.lower() and (not REPOS or repo.lower() in taken):
            continue   # the home board has it already
        if repo.lower() in taken:
            bad.append("REPO_BOARDS: %s left out: another board has a repo named %s (repo#N would mean either)" % (k, repo))
            continue
        taken.add(repo.lower())
        out.append((k, owner, repo))
    return out, bad


def boards_now():
    """The other boards REPO_BOARDS names now, each the same Board as before while it is named (its edges and gone go
    on), and why any is left out."""
    global OTHERS
    named, bad = boards_named()
    OTHERS = {k: OTHERS.get(k) or Board(k, owner, [repo], repo) for k, owner, repo in named}
    return list(OTHERS.values()), bad


def board_of(repo):
    """The board a repo's issues are on: another board's, else the home board (the owner's, every repo of it when
    REPO_NAMES is blank)."""
    r = (repo or "").lower()
    return next((b for b in OTHERS.values() if r in (x.lower() for x in b.repos)), HOME)


def owner_of(repo):
    return board_of(repo).owner


def _mail_changed():
    """A message: wake the pages' long-poll so the Messages panel and the Agent Room redraw at once."""
    with CHANGED:
        SNAP["ver"] += 1
        CHANGED.notify_all()


STORE = mail.Store(os.environ.get("BOARD_DB", "/data/board.db"), on_change=_mail_changed)

# The room's settings (its look: the environment), the room's and not a browser's (engine/SPEC.md 8.1): the person's
# page sets them (/api/room, with the session), every page gets them with the board (`room` in /api/board), and the
# room's page (the engine's) builds its scene by them. Kept beside the messages, so a restart keeps them.
ROOM_FILE = os.environ.get("ROOM_FILE") or os.path.join(os.path.dirname(os.environ.get("BOARD_DB", "/data/board.db")), "room.json")
ROOM = {"env": None, "dark": None, "look": None, "music": None, "board": None, "mix": None, "guest": None, "bino": None, "acts": None, "teleport": None}   # teleport: who may teleport into the room (room.html's Teleport popup, its Who can come; teleport_rule), kept; None: everyone in your worlds; acts: what each agent is up to between turns, as the room's page has it (room_acts; never kept); bino: the owner's binoculars up (their figure in the room holds them to its eyes; never kept across a restart); guest: the owner As a visitor (room.html's switch: their clicks and their body in the room a visitor's, to see what a visitor has; never kept across a restart); board: the board the wall shows (its switch: room.html); dark: the owner's theme, which the room's look follows; look: the owner's own looks (room.html's me.look), drawn by the room's page; music: false when the owner has muted it (the pool club's DJ stops, and nobody dances); mix: the owner's mixer, {channel: {level, mute}}, which the room's page gives Visit for the visitors and every page of the owner's plays by
MIX_MAX = 8   # channels a mix may have (room.html's mixer: music, ambient, crowd)
LOOK_MAX = 2048   # bytes of JSON a look may be
try:
    with open(ROOM_FILE) as _f:
        ROOM.update({k: v for k, v in json.load(_f).items() if k in ROOM and k not in ("guest", "bino", "acts")})
except (OSError, ValueError):
    pass
ROOM_KEY = re.compile(r"[a-z][a-z0-9_-]{0,31}")   # an environment's key, as room.html names them
# The tarot reader in the Agent Room (tarot.py; room.html's Madame Vesna): the room's last reading, kept beside the room's
# settings, so her cards stay on her table across a restart. Its words come from the model llm.py reaches (LLM_KEY, or LLM_URL).
TAROT_CARD = re.compile(r"/tarot/(?:(m(?:0\d|1\d|2[01])|[wcsp](?:0[1-9]|1[0-4]))\.jpg|(meanings)\.json)")   # a card of the deck (DECK's keys), or Waite's meanings
TAROT = tarot.Readings(os.environ.get("TAROT_FILE") or os.path.join(os.path.dirname(ROOM_FILE), "tarot.json"))
# The readings the owner saved (her card's Save; its Saved list): in the board's SQLite file (tarot.Kept), the owner's alone.
KEPT = tarot.Kept(os.environ.get("BOARD_DB", "/data/board.db"))
# The ticket each agent held last (roster.lasts), served as its `last` while it holds none: a click on its name card in
# the Agent Room opens it, and the kanban's strip names it. Kept beside the room's settings, so a restart keeps it.
LAST_FILE = os.environ.get("LAST_FILE") or os.path.join(os.path.dirname(ROOM_FILE), "last.json")
try:
    with open(LAST_FILE) as _f:
        LAST = {k: v for k, v in json.load(_f).items() if isinstance(k, str) and isinstance(v, str)}
except (OSError, ValueError, AttributeError):
    LAST = {}


def last_keep(agents, known):
    """Each agent's `last` (its last ticket, while it holds none; else ''), and the map kept, when it changed."""
    global LAST
    now = roster.lasts(LAST, agents, known)
    if now != LAST:
        LAST = now
        try:
            with open(LAST_FILE + ".tmp", "w") as f:
                json.dump(LAST, f)
            os.replace(LAST_FILE + ".tmp", LAST_FILE)
        except OSError:
            pass   # kept for this run only
    for d in agents:
        d["last"] = "" if d.get("claims") else LAST.get(d.get("session") or "", "")


def mix_merged(mix):
    """The room's mix with `mix` merged in: {channel: {"level": 0..1, "mute": bool}}, each channel's given keys over
    its kept ones (a slider sends its own channel's level, a mute button its mute). ValueError for anything else."""
    if not isinstance(mix, dict) or not mix:
        raise ValueError("mix is {channel: {level, mute}}")
    out = {k: dict(v) for k, v in (ROOM.get("mix") or {}).items()}
    for ch, v in mix.items():
        if not isinstance(ch, str) or not ROOM_KEY.fullmatch(ch):
            raise ValueError("a mix's channel is a name (music, ambient, crowd)")
        if not isinstance(v, dict) or not v or set(v) - {"level", "mute"}:
            raise ValueError("a mix's channel is {level, mute}")
        lv = v.get("level")
        if "level" in v and (isinstance(lv, bool) or not isinstance(lv, (int, float)) or not 0 <= lv <= 1):
            raise ValueError("a mix's level is 0 to 1")
        if "mute" in v and not isinstance(v["mute"], bool):
            raise ValueError("a mix's mute is true or false")
        out.setdefault(ch, {}).update({k: (round(float(x), 3) if k == "level" else x) for k, x in v.items()})
    if len(out) > MIX_MAX:
        raise ValueError("a mix has at most %d channels" % MIX_MAX)
    return out


# Who may teleport into the room (Visit 5.1, room.html's popup): {worlds: the worlds whose members may ("*": all of
# yours), keys: [{key, name}] (people by their office's key), anyone: anyone in the lobby}. You always may.
TELEPORT_KEY = re.compile(r"[0-9a-f]{64}")


def teleport_rule(t):
    """A teleport rule as the room keeps it, checked (ValueError otherwise)."""
    if not isinstance(t, dict) or set(t) - {"worlds", "keys", "anyone"}:
        raise ValueError("teleport is {worlds, keys, anyone}")
    worlds, keys = t.get("worlds", "*"), t.get("keys", [])
    if worlds != "*" and not (isinstance(worlds, list) and len(worlds) <= 20 and all(isinstance(w, str) and 0 < len(w) <= 24 for w in worlds)):
        raise ValueError('teleport.worlds is "*" or at most 20 world names')
    if not (isinstance(keys, list) and len(keys) <= 100 and all(isinstance(k, dict) and TELEPORT_KEY.fullmatch(str(k.get("key", "")))
                                                              and isinstance(k.get("name", ""), str) and len(k.get("name", "")) <= 24 for k in keys)):
        raise ValueError("teleport.keys is at most 100 {key: an office's key, name}")
    if not isinstance(t.get("anyone", False), bool):
        raise ValueError("teleport.anyone is true or false")
    return {"worlds": worlds if worlds == "*" else list(dict.fromkeys(worlds)), "keys": [{"key": k["key"], "name": k.get("name", "")} for k in keys],
            "anyone": bool(t.get("anyone", False))}


def room_set(env=None, dark=None, look=None, music=None, board=None, mix=None, guest=None, bino=None, teleport=None):
    """The room's settings from the person's page, or the room's (the ones given): kept, and every page's long-poll
    woken. `board` is the board the wall shows (owner/repo; '' for the home board), set by its switch; `mix`, the
    mixer's channels to change (mix_merged); `guest`, the owner As a visitor, and `bino`, their binoculars up (kept till a
    restart only)."""
    if env is not None and (not isinstance(env, str) or not ROOM_KEY.fullmatch(env)):
        raise ValueError("env is an environment's key")
    if dark is not None and not isinstance(dark, bool):
        raise ValueError("dark is true or false")
    if look is not None and (not isinstance(look, dict) or len(json.dumps(look)) > LOOK_MAX):
        raise ValueError("look is a JSON object, under %d bytes" % LOOK_MAX)
    if music is not None and not isinstance(music, bool):
        raise ValueError("music is true or false")
    if board is not None and (not isinstance(board, str) or (board and not BOARD_NAME.fullmatch(board))):
        raise ValueError("board is owner/repo")
    if guest is not None and not isinstance(guest, bool):
        raise ValueError("guest is true or false")
    if bino is not None and not isinstance(bino, bool):
        raise ValueError("bino is true or false")
    rule = teleport_rule(teleport) if teleport is not None else None
    if env is None and dark is None and look is None and music is None and board is None and mix is None and guest is None and bino is None and rule is None:
        raise ValueError("nothing to set: env, dark, look, music, board, mix, guest, bino, teleport")
    with CHANGED:
        merged = mix_merged(mix) if mix is not None else None   # (checked first: a bad mix changes nothing)
        if env is not None:
            ROOM["env"] = env
        if dark is not None:
            ROOM["dark"] = dark
        if look is not None:
            ROOM["look"] = look
        if music is not None:
            ROOM["music"] = music
        if board is not None:
            ROOM["board"] = board or None
        if merged is not None:
            ROOM["mix"] = merged
        if guest is not None:
            ROOM["guest"] = guest or None
        if bino is not None:
            ROOM["bino"] = bino or None
        if rule is not None:
            ROOM["teleport"] = rule
        try:
            with open(ROOM_FILE + ".tmp", "w") as f:
                json.dump({k: v for k, v in ROOM.items() if k != "acts"}, f)   # (acts: never kept)
            os.replace(ROOM_FILE + ".tmp", ROOM_FILE)
        except OSError as e:
            raise ValueError("could not keep it: %s" % e)
        SNAP["ver"] += 1
        CHANGED.notify_all()


# What an agent may be up to between turns in the room (room.html's IDLE_ACTS, and out by the pool), as the room's page
# says it: its pictures in your agents' list draw it (a doze's Z's, its phone, a chat and with whom, an errand).
ACT_KINDS = {"doze", "phone", "chat", "cooler", "coffee", "cocktail", "bananas", "pool"}
ACT_SESSION = re.compile(r"[A-Za-z0-9._:-]{1,80}")
ACT_WITH = re.compile(r"[\w .'-]{1,40}")
ACTS_MAX = 64


def room_acts(acts):
    """What each agent is up to, from the room's page (the engine's: it plays them out), the whole map each time
    ({session: kind, or "chat:<the one it chats with>"}, {} for none): kept till a restart only, never in room.json, and
    every page's long-poll woken only when it changed."""
    if not isinstance(acts, dict) or len(acts) > ACTS_MAX:
        raise ValueError("acts is an object of at most %d agents' acts" % ACTS_MAX)
    for k, v in acts.items():
        kind, _, mate = v.partition(":") if isinstance(v, str) else ("", "", "")
        if not ACT_SESSION.fullmatch(k) or kind not in ACT_KINDS or (mate and (kind != "chat" or not ACT_WITH.fullmatch(mate))):
            raise ValueError("an act is one of %s (chat:<name> for whom it chats with), by its agent's session" % ", ".join(sorted(ACT_KINDS)))
    with CHANGED:
        if (ROOM["acts"] or {}) == acts:
            return False
        ROOM["acts"] = dict(acts) or None
        SNAP["ver"] += 1
        CHANGED.notify_all()
    return True


# The casino's (room.html's, at the pool club): each player's chips, their record (what they have played and won at each
# game) and the achievements they have earned: the owner's, and each visitor's by their office's key (Visit's: the same
# every visit), from the room's page (the engine's: the games are played there) as they change; read back by it as it
# starts, so a restart keeps them (and a visitor back another day finds theirs), and by the owner's page (Casino stats:
# theirs, or any visitor's who has played).
CASINO_FILE = os.environ.get("CASINO_FILE") or os.path.join(os.path.dirname(ROOM_FILE), "casino.json")
CASINO = {"owner": None, "visitors": {}}
CASINO_LOCK = threading.Lock()
CASINO_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
CASINO_FIELDS = 96   # fields a record may have; achievements earned
CASINO_BIG = 1e15
CASINO_VISITOR = re.compile(r"[0-9a-f]{64}")   # a visitor's office's key (Visit: its link's public key, 64 hex)
CASINO_VISITORS = 300   # kept, the latest to play; the rest let go
CASINO_SENT = 32   # visitors in one POST


def casino_owner(o, who="owner"):
    """A player's {chips, rec, ach, name?, office?}, checked (ValueError if not): chips, a number of 0 or more; rec,
    {field: a count, or a list of up to 37 counts, one for each number on the wheel}; ach, {achievement: when it was
    earned, in ms}; name and office, what they go by and the room they come from (a visitor's)."""
    if not isinstance(o, dict) or set(o) - {"chips", "rec", "ach", "name", "office", "first", "last"}:
        raise ValueError("%s is {chips, rec, ach, name?, office?}" % who)
    def num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0 <= v <= CASINO_BIG
    chips, rec, ach = o.get("chips", 0), o.get("rec") or {}, o.get("ach") or {}
    if not num(chips):
        raise ValueError("%s: chips is a number, 0 or more" % who)
    if not isinstance(rec, dict) or len(rec) > CASINO_FIELDS or not all(
            CASINO_FIELD.fullmatch(k) and (num(v) or isinstance(v, list) and len(v) <= 37 and all(num(x) for x in v)) for k, v in rec.items()):
        raise ValueError("%s: rec is {field: a count, or a list of up to 37 of them}, at most %d fields" % (who, CASINO_FIELDS))
    if not isinstance(ach, dict) or len(ach) > CASINO_FIELDS or not all(CASINO_FIELD.fullmatch(k) and num(v) for k, v in ach.items()):
        raise ValueError("%s: ach is {achievement: when it was earned, in ms}, at most %d" % (who, CASINO_FIELDS))
    out = {"chips": int(chips), "rec": rec, "ach": {k: int(v) for k, v in ach.items()}}
    for k, n in (("name", 40), ("office", 60)):
        v = o.get(k)
        if v is not None and (not isinstance(v, str) or len(v) > n):
            raise ValueError("%s: %s is a string of at most %d characters" % (who, k, n))
        if v:
            out[k] = v
    for k in ("first", "last"):   # (the board's own stamps, read back from its file)
        if num(o.get(k)):
            out[k] = int(o[k])
    return out


def casino_set(owner=None, visitors=None):
    """From the room's page: the owner's (if given) and those visitors' who changed ({key: theirs}), checked, then kept
    (OSError if they could not be). A visitor's first and last time at the tables are the board's to stamp."""
    o = casino_owner(owner) if owner is not None else None
    if visitors is not None and (not isinstance(visitors, dict) or len(visitors) > CASINO_SENT):
        raise ValueError("visitors is {their office's key: theirs}, at most %d" % CASINO_SENT)
    vs = {}
    for k, v in (visitors or {}).items():
        if not isinstance(k, str) or not CASINO_VISITOR.fullmatch(k):
            raise ValueError("a visitor is known by their office's key: 64 hex")
        vs[k] = casino_owner({x: y for x, y in v.items() if x not in ("first", "last")} if isinstance(v, dict) else v, "a visitor")
        if not vs[k].get("name"):
            raise ValueError("a visitor has a name")
    now = int(time.time() * 1000)
    with CASINO_LOCK:
        if o is not None:
            CASINO["owner"] = o
        for k, v in vs.items():
            v["first"], v["last"] = (CASINO["visitors"].get(k) or {}).get("first", now), now
            CASINO["visitors"][k] = v
        if len(CASINO["visitors"]) > CASINO_VISITORS:
            for k in sorted(CASINO["visitors"], key=lambda k: CASINO["visitors"][k].get("last", 0))[:len(CASINO["visitors"]) - CASINO_VISITORS]:
                del CASINO["visitors"][k]
        with open(CASINO_FILE + ".tmp", "w") as f:
            json.dump(CASINO, f)
        os.replace(CASINO_FILE + ".tmp", CASINO_FILE)
    return o, vs


def casino_view(q):
    """What GET /api/casino answers: the owner's (and how many visitors are kept); ?visitors=1, every visitor's as a
    list (their chips and achievements, for a picker and a leaderboard: no records); ?visitor=<key>, one visitor's
    whole; ?all=1, everything (the room's page, as it starts)."""
    with CASINO_LOCK:
        vs = CASINO["visitors"]
        if q.get("visitor"):
            return {"visitor": dict(vs[q["visitor"]], key=q["visitor"]) if q["visitor"] in vs else None}
        if q.get("visitors"):
            return {"visitors": sorted(({"key": k, "name": v.get("name"), "office": v.get("office"), "first": v.get("first"), "last": v.get("last"),
                                         "chips": v["chips"], "ach": v["ach"]} for k, v in vs.items()), key=lambda e: -(e["last"] or 0))}
        if q.get("all"):
            return {"owner": CASINO["owner"], "visitors": dict(vs)}
        return {"owner": CASINO["owner"], "visitors": len(vs)}


try:
    with open(CASINO_FILE) as _f:
        _kept = json.load(_f)
    try:
        CASINO["owner"] = casino_owner(_kept.get("owner"))
    except ValueError:
        pass
    for _k, _v in (_kept.get("visitors") or {}).items():
        try:
            if CASINO_VISITOR.fullmatch(_k):
                CASINO["visitors"][_k] = casino_owner(_v, "a visitor")
        except ValueError:
            pass
except (OSError, ValueError, AttributeError):
    pass


ETAGS = {}  # GET path -> (etag, json): a 304 answers from here and costs no quota
ETAGS_MAX = 5000
RATE = {"left": None, "reset": 0, "until": 0}  # the token's quota as GitHub last reported it
RATE_LOCK = threading.Lock()


def _rate(h, code=200):
    """Note the quota GitHub reported on any answer, and a secondary limit's Retry-After."""
    with RATE_LOCK:
        if h.get("X-RateLimit-Remaining") is not None:
            RATE["left"], RATE["reset"] = int(h["X-RateLimit-Remaining"]), int(h.get("X-RateLimit-Reset") or 0)
        if code in (403, 429) and h.get("Retry-After"):
            RATE["until"] = max(RATE["until"], time.time() + int(h["Retry-After"]))


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    hdr = dict(HDR, **{"Content-Type": "application/json"})
    cached = ETAGS.get(path) if method == "GET" else None
    if cached:
        hdr["If-None-Match"] = cached[0]
    req = Request(FORGE + path, data=data, method=method, headers=hdr)
    try:
        with urlopen(req, timeout=20) as f:
            _rate(f.headers)
            raw = f.read()
            got = json.loads(raw.decode("utf-8", "replace")) if raw else None
            if method == "GET" and f.headers.get("ETag"):
                if len(ETAGS) >= ETAGS_MAX:
                    ETAGS.clear()  # crude, and rare: the next poll re-fills what it reads
                ETAGS[path] = (f.headers["ETag"], got)
            return got
    except HTTPError as e:
        _rate(e.headers, e.code)
        if e.code == 304 and cached:
            return cached[1]
        raise


def api_all(path):
    """(every page of a GitHub list, truncated?). Pages by `page=` until one comes back short, so
    it reads the same through a stub that knows only api(); stops at PAGES and says so."""
    out, sep = [], "&" if "?" in path else "?"
    for page in range(1, PAGES + 1):
        got = api("%s%sper_page=%d&page=%d" % (path, sep, PER_PAGE, page)) or []
        out += got
        if len(got) < PER_PAGE:
            return out, False
    return out, True


def repo_of(i):
    """The repo NAME an issue lives in: the org listing carries a `repository` object; a repo's
    own listing and the dependency lists carry only `repository_url`."""
    return ((i.get("repository") or {}).get("name")
            or (i.get("repository_url") or "").rstrip("/").rsplit("/", 1)[-1])


def is_pr(i):
    """GitHub lists pull requests as issues; they carry a `pull_request` key."""
    return "pull_request" in i


def listing(b=HOME):
    """(every open issue and PR on board `b`, truncated?)."""
    if not b.repos:
        return api_all("/orgs/%s/issues?filter=all&state=open" % b.owner)
    out, cut = [], False
    for r in b.repos:
        got, t = api_all("/repos/%s/%s/issues?state=open" % (b.owner, r))
        out, cut = out + got, cut or t
    return out, cut


def iid(i):
    return "%s#%d" % (repo_of(i), i["number"])


def blockers(i, b=HOME):
    # GitHub's summary on the issue itself says when there is no edge at all: no request then.
    if (i.get("issue_dependencies_summary") or {}).get("total_blocked_by") == 0:
        return []
    deps, _ = api_all("/repos/%s/%s/issues/%d/dependencies/blocked_by" % (b.owner, repo_of(i), i["number"]))
    # EVERY blocker, closed ones too: snapshot() keeps those still open each poll. Filtering here
    # froze the state into the cache, so a reopened blocker stayed invisible until the next FULL.
    return ["%s#%d" % (repo_of(d), d["number"]) for d in deps]


def dep_stamp(i):
    """What says an issue's edges may have moved: its updated_at, and GitHub's dependency summary."""
    return i.get("updated_at"), json.dumps(i.get("issue_dependencies_summary"), sort_keys=True)


def rank_of(names):
    """(rank label, tier) of a row's labels: the highest REPO_RANK label it carries, else unranked."""
    tier = min((RANK.index(n) for n in names if n in RANK), default=UNRANKED)
    return (RANK[tier] if tier != UNRANKED else "unranked"), tier


def row(i, blk, b=HOME):
    names = sorted(l["name"] for l in i.get("labels") or [])
    repo = repo_of(i)
    rank, tier = rank_of(names)
    return {  # its kanban column is added once per snapshot, by pick.columns in snapshot()
        "id": iid(i), "repo": repo, "num": i["number"],
        "title": i.get("title") or "", "labels": names,
        "rank": rank, "tier": tier, "blocked": blk,
        "epic": (i.get("title") or "").startswith("Epic:"),
        "comments": i.get("comments", 0),
        "created": i.get("created_at") or "",  # within a rank, oldest first: pick._order, the kanban, the room
        "url": i.get("html_url") or "%s/%s/%s/issues/%d" % (PUBLIC, b.owner, repo, i["number"]),
    }


def off_board():
    """The open `off-board` issues in the home repo (RESUME, AGENTS). By LABEL, never search: the search
    index lags a fresh issue (see forge.py cmd_resume). PRs left out: GitHub lists them as issues."""
    return [i for i in api_all("/repos/%s/%s/issues?state=open&labels=off-board" % (OWNER, RESUME_REPO))[0]
            if not is_pr(i)]


def plan_board(title):
    """The board a RESUME issue's title names: `RESUME [owner/repo] …` is that board's plan, any other `RESUME…` the
    home board's (as it always was)."""
    m = re.match(r"RESUME \[([^\]\s]+)\]", title or "")
    return m.group(1) if m else HOME.key


def plan_issue(b=HOME, offb=None):
    """Board `b`'s one open RESUME issue in the home repo (from `offb`, an off_board() read already made), or None."""
    hits = [i for i in (off_board() if offb is None else offb)
            if (i.get("title") or "").startswith("RESUME") and plan_board(i.get("title")).lower() == b.key.lower()]
    if len(hits) > 1:
        raise RuntimeError("more than one open RESUME issue%s: %s" % (
            "" if b is HOME else " for " + b.key, ", ".join("#%d" % i["number"] for i in hits)))
    return hits[0] if hits else None


def agents_issue():
    """The open AGENTS issue. Two (a create race) are tolerated the way forge.py does: the LOWEST
    number is the roster, so the kanban and every agent agree on it."""
    hits = sorted((i for i in off_board() if (i.get("title") or "").startswith("AGENTS")),
                  key=lambda i: i["number"])
    return hits[0] if hits else None


def agent_comments():
    """(issue, [(comment, entry)]) for every agent entry on the AGENTS issue."""
    i = agents_issue()
    if not i:
        return None, []
    cs, _ = api_all("/repos/%s/%s/issues/%d/comments" % (OWNER, RESUME_REPO, i["number"]))
    return i, roster.entries(cs)


def agents_view():
    """(AGENTS issue number or None, the roster freshest first: roster.agents). The same read the
    owner's × makes (agent_comments), so the two cannot see different comment sets. The sessions this
    machine's restart ended are taken off it first (reap_rebooted)."""
    i, ents = agent_comments()
    return (i["number"] if i else None), roster.agents(reap_rebooted(ents))


# Self-healing after this machine was turned off with its agents running: none of them ran its SessionEnd (the
# hook that takes an entry off the roster), and forge.py's own reap waits for an agent on this machine to report.
# So the board, on this machine, takes them off itself, at its first read after a start and at every read after:
# roster.rebooted says which, with proof. The machine is the board's own (socket.gethostname(), as forge.py writes
# `host`), and Claude Code's files are in CLAUDE_DIR (forge.py's live_sessions reads the same registry).
HOST = socket.gethostname()
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,99}")
REAP_RETRY_S = 600   # an entry whose delete failed is tried again after this long, not at every poll
REAP_FAILED = {}     # comment id -> when its delete failed


def booted():
    """When this machine last started (epoch seconds: /proc/stat's btime), or None where that cannot be read."""
    try:
        with open("/proc/stat") as f:
            return next(int(l.split()[1]) for l in f if l.startswith("btime "))
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def live_sessions():
    """The session ids of the Claude Code processes running on this machine (its registry, CLAUDE_DIR/sessions/<pid>.json,
    each one's pid alive), or None when there is no registry."""
    d = os.path.join(CLAUDE_DIR, "sessions")
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return None
    out = set()
    for n in names:
        try:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                s = json.load(f)
            os.kill(int(s["pid"]), 0)   # signal 0: only asks whether it runs
        except PermissionError:   # it runs, as another user
            out.add(s["sessionId"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        else:
            out.add(s["sessionId"])
    return out


def ran_here(sid):
    """Whether session `sid` ran on this machine: Claude Code keeps its transcript in CLAUDE_DIR/projects/<dir>/<sid>.jsonl."""
    if not SESSION_ID.fullmatch(sid or ""):
        return False
    try:
        return any(os.path.exists(os.path.join(CLAUDE_DIR, "projects", p, sid + ".jsonl"))
                   for p in os.listdir(os.path.join(CLAUDE_DIR, "projects")))
    except OSError:
        return False


def reap_rebooted(ents, now=None):
    """`ents` ([(comment, entry)], agent_comments') less the entries of sessions this machine's restart ended
    (roster.rebooted), each taken off the roster with its claims, as the owner's × would, and said in the board's log.
    One whose delete fails stays, and is tried again REAP_RETRY_S later. Never raises: the roster is still served."""
    now = time.time() if now is None else now
    try:
        boot = booted()
        gone = [(c, d) for c, d in roster.rebooted(ents, HOST, boot, live_sessions, ran_here)
                if now - REAP_FAILED.get(c["id"], 0) >= REAP_RETRY_S]
    except Exception as e:
        print("board: could not tell which agents this machine's restart ended (%s: %s)" % (type(e).__name__, e), flush=True)
        return ents
    out = {}
    for c, d in gone:
        try:
            drop([(c, d)])
        except HTTPError as e:
            if e.code == 404:   # already gone (another machine's forge.py, the owner's ×)
                out[c["id"]] = d
                continue
            REAP_FAILED[c["id"]] = now
            print("board: could not take %s off the roster (HTTP %d); trying again in %d min"
                  % (d.get("name") or d.get("session"), e.code, REAP_RETRY_S // 60), flush=True)
            continue
        except Exception as e:
            REAP_FAILED[c["id"]] = now
            print("board: could not take %s off the roster (%s: %s); trying again in %d min"
                  % (d.get("name") or d.get("session"), type(e).__name__, e, REAP_RETRY_S // 60), flush=True)
            continue
        REAP_FAILED.pop(c["id"], None)
        out[c["id"]] = d
        print("board: took %s off the roster: its session ended when this machine went down (last seen %s, up again since %s)%s"
              % (d.get("name") or d.get("session"), d.get("seen") or c.get("updated_at") or "?",
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(boot)),
                 "; released " + " ".join(d["claims"]) if d.get("claims") else ""), flush=True)
    return [(c, d) for c, d in ents if c["id"] not in out]


# A PR body ends with the session that wrote it; the page matches it to a roster entry's link.
SESSION_LINK = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")


def prs_view(pulls):
    """Every open PR in the org, freshest first, from the org listing the snapshot already read
    (GitHub lists PRs as issues): no call of its own. That listing has no head branch, so the row
    says what the PR is and who wrote it, not where it lives."""
    out = []
    for p in pulls:
        # The LAST session link: a body ENDS with the session that wrote it, and may cite others above.
        links = SESSION_LINK.findall(p.get("body") or "")
        pid = "%s#%d" % (repo_of(p), p["number"])
        out.append({"id": pid, "title": p["title"], "refs": roster.pr_refs(pid, p["title"], p.get("body")),
                    "url": p["html_url"], "author": (p.get("user") or {}).get("login") or "",
                    "link": links[-1] if links else "", "comments": p.get("comments") or 0,
                    "draft": bool(p.get("draft") or (p.get("pull_request") or {}).get("draft")),
                    "updated": p.get("updated_at") or ""})
    return sorted(out, key=lambda x: x["updated"], reverse=True)


class BadRef(Exception):
    """A quick-view id that is not `repo#N`: the caller's error (400), apart from a forge fault."""


MD = {}  # (text, repo) -> html: a POST is never conditional, so a quick view re-opened costs nothing
MD_MAX = 2000


def markdown(text, repo):
    """`text` as HTML by GitHub's own renderer, in GFM mode as its issue page does, with `repo` as
    the context a bare `#N` links into. Its sanitizing, and links absolute to github.com. A render
    that fails costs only that text, shown escaped, never the whole issue."""
    key = (text, repo)
    if key in MD:
        return MD[key]
    req = Request(FORGE + "/markdown", method="POST",
                  data=json.dumps({"text": text, "mode": "gfm", "context": "%s/%s" % (owner_of(repo), repo)}).encode(),
                  headers=dict(HDR, **{"Content-Type": "application/json"}))
    try:
        with urlopen(req, timeout=20) as f:
            _rate(f.headers)
            html = f.read().decode("utf-8", "replace")
    except Exception:
        return '<pre style="white-space:pre-wrap">%s</pre>' % escape(text)
    if len(MD) >= MD_MAX:
        MD.clear()
    MD[key] = html
    return html


def issue_view(ref):
    """One issue for the kanban's quick view, read from the forge on each open: its body,
    every comment oldest first (the decisions live there) and its edges both ways. Read live, not
    from the snapshot, which holds no bodies; a ref that is not `repo#N` is a BadRef."""
    m = _REF.fullmatch(ref or "")
    if not m:
        raise BadRef("not an issue ref: %r" % ref)
    repo, n = m.group(1), int(m.group(2))
    base = "/repos/%s/%s/issues/%d" % (owner_of(repo), repo, n)
    with ThreadPoolExecutor(max_workers=8) as pool:
        fi = pool.submit(api, base)
        cs, deps, blocks = (r[0] for r in pool.map(api_all, (base + "/comments", base + "/dependencies/blocked_by",
                                                            base + "/dependencies/blocking")))
        i = fi.result()
        html = list(pool.map(lambda t: markdown(t, repo) if t.strip() else "",
                             [i.get("body") or ""] + [c.get("body") or "" for c in cs]))

    def edge(d):
        return {"id": "%s#%d" % (repo_of(d), d["number"]), "title": d.get("title") or "",
                "state": d.get("state") or ""}
    return {"id": "%s#%d" % (repo, n), "title": i.get("title") or "", "state": i.get("state") or "",
            "url": i.get("html_url") or "%s/%s/%s/issues/%d" % (PUBLIC, owner_of(repo), repo, n),
            "labels": sorted(l["name"] for l in i.get("labels") or []),
            "user": (i.get("user") or {}).get("login") or "", "created": i.get("created_at") or "",
            "body": html[0],
            "comments": [{"user": c.get("original_author") or (c.get("user") or {}).get("login") or "",
                          "at": c.get("created_at") or "", "body": h} for c, h in zip(cs, html[1:])],
            "blocked_by": [edge(d) for d in deps or []], "blocks": [edge(d) for d in blocks or []]}


def remove_agent(cid):
    """The owner's ×: delete one agent entry, and any other its session holds (the roster shows them as
    one: roster.merge). Refuses anything that is not an agent entry on the AGENTS issue, so a stray id
    cannot delete an ordinary comment anywhere on the forge."""
    _, entries = agent_comments()
    hit = next(((c, d) for c, d in entries if c["id"] == cid), None)
    if not hit:
        raise LookupError("comment %r is not an agent on the roster (already gone?)" % cid)
    drop([(c, d) for c, d in entries if c["id"] == cid or roster.same(d, hit[1])])
    KICK.set()


def drop(gone):
    """Agent entries [(comment, entry)] deleted from the AGENTS issue, then the label each of their claims put on
    its issue: the owner's × (remove_agent) and a restart's reap (reap_rebooted)."""
    for c, _ in gone:
        api("/repos/%s/%s/issues/comments/%d" % (OWNER, RESUME_REPO, c["id"]), "DELETE")
    # Its claims go with it, and so does the label each claim put on its issue (the workflow's
    # claim_label), AFTER the entry is gone: a failed delete strips nothing. Best-effort per issue.
    lab = WF["wf"].get("claim_label")
    for ref in sorted({r for _, d in gone for r in d.get("claims") or []}) if lab else []:
        m = _REF.fullmatch(ref)
        try:
            path = "/repos/%s/%s/issues/%s" % (owner_of(m.group(1)), m.group(1), m.group(2))
            if lab in [l["name"] for l in api(path).get("labels") or []]:
                api(path + "/labels/" + quote(lab, safe=""), "DELETE")
        except Exception:
            pass


def parse_plan(body):
    """Steps as {ref, owner, text}. The FIRST repo#N on a line is the step's issue (the
    /stop rule); the rest of the line is kept verbatim, so a session's wording survives
    a reorder on the board."""
    steps = []
    for line in (body or "").splitlines():
        m = _STEP.match(line.strip())
        r = m and _REF.search(m.group(1))
        if not r:
            continue
        rest = (m.group(1)[:r.start()] + m.group(1)[r.end():]).strip()
        owner = rest.startswith(HAND)
        steps.append({"ref": "%s#%s" % (r.group(1), r.group(2)), "owner": owner,
                      "text": rest[len(HAND):].strip() if owner else rest})
    return steps


def plan_body(steps):
    lines = []
    for n, s in enumerate(steps, 1):
        line = "%d) %s%s %s" % (n, s["ref"], " " + HAND if s["owner"] else "", s["text"])
        lines.append(line.rstrip()[:PLAN_MAX_LINE].rstrip())
    return "\n".join(lines)


def digest(body):
    return hashlib.sha256((body or "").strip().encode()).hexdigest()[:16]


def plan_view(p):
    return p and {"num": p["number"], "hash": digest(p.get("body")),
                  "url": "%s/%s/%s/issues/%d" % (PUBLIC, OWNER, RESUME_REPO, p["number"]),
                  "steps": parse_plan(p.get("body"))}


def shells(now):
    """Session id -> when its oldest running command began (ISO UTC), as bin/shells last saw; {} when it
    is not running (no file, or a stale one) or the file cannot be read: no one is shown running anything."""
    try:
        with open(os.path.join(SHELLS_DIR, "now.json")) as f:
            d = json.load(f)
        if now - float(d.get("at") or 0) > SHELLS_STALE:
            return {}
        return {str(k): str(v) for k, v in (d.get("shells") or {}).items() if k}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def usage(now):
    """The account's usage limits, a row per bar /usage draws: {"limits": [{kind, group, name, used (percent),
    resets_at, passed, severity}], "at" (when they were heard, ISO UTC), "from"}, or {} when nothing has said.
    From bin/usage while it is fresh ("from": "usage"): /usage's own rows, the session's, the week's and each
    model's week (`name`: Fable, say). Else from what the agents' status lines were last handed ("status lines"):
    the session's and the week's only. Once its reset has passed, a row reads 0% used (`passed`) till told
    otherwise."""
    return usage_plan(now) or usage_notes(now)


def usage_limit(kind, group, name, used, reset, severity, now):
    passed = reset is not None and reset <= now
    used = 0.0 if passed else round(min(100.0, max(0.0, used)), 1)
    return {"kind": kind, "group": group, "name": name, "used": used, "resets_at": usage_iso(reset) if reset is not None else None,
            "passed": passed, "severity": "normal" if passed else severity or ("critical" if used >= 95 else "warning" if used >= 80 else "normal")}


def usage_iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def usage_plan(now):
    """bin/usage's plan.json, as usage() serves it, or {} when there is none or it is stale."""
    try:
        with open(os.path.join(USAGE_DIR, USAGE_PLAN)) as f:
            d = json.load(f)
        at = float(d["at"])
        limits = [usage_limit(str(r["kind"]), r.get("group"), r.get("name"), float(r["percent"]),
                              int(r["resets_at"]) if r.get("resets_at") is not None else None, r.get("severity"), now)
                  for r in d["rows"]]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}
    return {"limits": limits, "at": usage_iso(at), "from": "usage"} if limits and now - at <= USAGE_STALE else {}


def usage_notes(now):
    """The status lines' notes, as usage() serves them. They are one account's: for each window the latest reset
    is the current one, and of the sessions on it the highest use is the newest (use only grows till the reset; a
    session idle a while says what it heard last). `at` is when the newest of those was noted."""
    got = {}
    try:
        names = os.listdir(USAGE_DIR)
    except OSError:
        return {}
    for n in names:
        if not n.endswith(".json") or n == USAGE_PLAN:
            continue
        try:
            with open(os.path.join(USAGE_DIR, n)) as f:
                d = json.load(f)
            at = float(d["at"])
            for w in USAGE_WINDOWS:
                if isinstance(d.get(w), dict):
                    got.setdefault(w, []).append((float(d[w]["used_percentage"]), int(d[w]["resets_at"]), at))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    limits, heard = [], 0
    for w, (kind, group) in USAGE_WINDOWS.items():
        if w not in got:
            continue
        latest = max(r for _, r, _ in got[w])
        now_on = [x for x in got[w] if x[1] >= latest - 60]   # the same reset, give or take the clock
        limits.append(usage_limit(kind, group, None, max(u for u, _, _ in now_on), latest, None, now))
        heard = max([heard] + [a for _, _, a in now_on])
    return {"limits": limits, "at": usage_iso(heard), "from": "status lines"} if limits else {}


def board_rows(b, full):
    """(open rows, every open issue, the open PRs, truncated?) of board `b`: one listing, its edges re-read only for
    issues that moved (all of them when `full`)."""
    # GitHub lists issues and PRs together, so one listing is the rows and the PR strip both: the
    # org's (`filter=all` is every issue the token can see, not only its user's), or each repo's.
    listed, truncated = listing(b)
    every = [i for i in listed if not is_pr(i)]
    still_open = {iid(i) for i in every}
    issues = [i for i in every
              if not any(l["name"] == "off-board" for l in i.get("labels") or [])]
    stale = [i for i in issues if full or b.deps.get(iid(i), (None,))[0] != dep_stamp(i)]
    # 8, not more: GitHub's secondary limit counts concurrent requests per account.
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, blk in zip(stale, pool.map(lambda i: blockers(i, b), stale)):
            b.deps[iid(i)] = (dep_stamp(i), blk)
    for k in [k for k in b.deps if k not in still_open]:
        del b.deps[k]
    # Only OPEN blockers block (the forge keeps a finished edge forever), judged now, not when read.
    rows = [row(i, [x for x in b.deps[iid(i)][1] if x in still_open], b) for i in issues]
    return rows, every, [i for i in listed if is_pr(i)], truncated


def board_judged(rows, pulls, agents, known, last_prs):
    """(the rows in their columns, the PRs with their owners, prs_error, columns_error)."""
    # Open PRs are a strip too: a failed read keeps the last good list and says so. Read BEFORE the
    # columns: a workflow can put a row named by an open PR in its own column (`pr`).
    try:
        prs, prs_error = prs_view(pulls), None
    except Exception as e:
        prs, prs_error = last_prs, "%s: %s" % (type(e).__name__, e)
    pr_refs = {r for p in prs for r in p.get("refs") or []}
    # The kanban's columns, judged ONCE here for the owner (pick.columns) and kept on the rows, like
    # their labels: a fault in the per-caller pickability never costs the columns, and a row that
    # leaves the board still knows which column it left from. A fault in a roster record costs only
    # the claim facts, never the snapshot: the columns then come from labels and edges alone, and
    # the fault is served.
    wf = current_workflow()
    try:
        rows, columns_error = pick.columns(rows, agents, pr_refs, wf), WF["error"]
    except Exception as e:
        rows, columns_error = pick.columns(rows, [], pr_refs, wf), "%s: %s" % (type(e).__name__, e)
    # Each PR's owning agent: a fact about the roster, not about who asks, so it is judged here and a
    # claim that moves bumps `ver` (roster.pr_owner says how).
    # A roster that failed to read cannot say an agent LEFT: such a PR is `unknown`, not orphaned.
    prs = [dict(p, owner=roster.pr_owner(p.get("refs") or [], p.get("link"), agents, known)) for p in prs]
    return rows, prs, prs_error, columns_error


def snapshot(full):
    rows, every, pulls, truncated = board_rows(HOME, full)
    # The roster is a STRIP, not the board: if it cannot be read, keep the last good one and say
    # so, rather than letting it stall the rows and the plan.
    try:
        (agents_num, agents), agents_error = agents_view(), None
    except Exception as e:
        agents_num, agents = SNAP.get("agents_issue"), [dict(d) for d in SNAP["agents"]]
        agents_error = "%s: %s" % (type(e).__name__, e)
    # Freshness is judged HERE, every poll, so it moves with time: publish() bumps `ver` when an
    # agent crosses a threshold (a class CHANGE, not every poll), and every reader redraws. So is
    # `shell`, its running command (bin/shells: by the session id its command carries, the roster's
    # `local`, or `session`): a start time, the same while that command runs, so only a command
    # starting to count or ending bumps `ver`.
    now = time.time()
    running = shells(now)
    for d in agents:
        d["fresh"] = roster.fresh(d.get("seen"), now)
        d["shell"] = running.get(d.get("local") or "") or running.get(d.get("session") or "") or ""
        d["board"] = d.get("board") or HOME.key   # an entry that names none is the home board's, as every one was
    last_keep(agents, agents_error is None)
    rows, prs, prs_error, columns_error = board_judged(rows, pulls, agents, agents_error is None, SNAP["prs"])
    others, boards_error = boards_now()
    try:   # one read of the home repo's off-board issues for every other board's plan
        offb = off_board() if others else None
    except Exception:
        offb = None   # each board's own read then says why
    return {"rows": rows, "plan": plan_view(plan_issue()), "agents": agents,
            "agents_issue": agents_num, "agents_error": agents_error, "prs": prs, "prs_error": prs_error,
            "truncated": truncated, "gone": dropped(rows, every, truncated, now),
            "columns": workflow.served(WF["wf"]), "columns_error": columns_error, "usage": usage(now),
            "boards": {b.key: other_part(b, full, agents, agents_error is None, offb, now) for b in others},
            "boards_error": "; ".join(boards_error) or None}


def other_part(b, full, agents, known, offb, now):
    """Another board's part of a snapshot (SNAP["boards"][key]): its rows in their columns, its plan, PRs and rows gone,
    judged as the home board's are, against the one roster. One that cannot be read keeps its last good part and says
    why (`error`): no board stalls another."""
    last = (SNAP.get("boards") or {}).get(b.key)
    try:
        rows, every, pulls, truncated = board_rows(b, full)
        rows, prs, prs_error, columns_error = board_judged(rows, pulls, agents, known, (last or {}).get("prs") or [])
        return {"rows": rows, "plan": plan_view(plan_issue(b, offb)), "prs": prs, "prs_error": prs_error,
                "truncated": truncated, "gone": dropped(rows, every, truncated, now, b, last and last["rows"]),
                "columns_error": columns_error, "error": None}
    except Exception as e:
        return dict(last or {"rows": [], "plan": None, "prs": [], "prs_error": None, "truncated": False, "gone": [],
                             "columns_error": None}, error="%s: %s" % (type(e).__name__, e))


def dropped(rows, every, truncated, now, b=HOME, prev=None):
    """Board `b`'s gone list, updated with the rows that left it since the last snapshot (`prev`, its rows then; the
    home board's are SNAP's), each read ONCE: a row
    still in the search (relabelled off-board) is judged from what the search returned; one that is
    not is fetched. A fetch that fails for any reason but a 404 records nothing rather than a guess.
    The first snapshot after a start drops nothing: there is no previous board to compare. A row
    back on the board leaves the list (gone.keep)."""
    have = {r["id"] for r in rows}
    if b is HOME:
        prev = SNAP["rows"] if SNAP["at"] else None
    if prev is not None:
        searched = {iid(i): i for i in every}

        def read(r):  # (row, issue, or None for a 404), or None when nothing true can be said
            if r["id"] in searched:
                return r, searched[r["id"]]
            try:
                return r, api("/repos/%s/%s/issues/%d" % (b.owner, r["repo"], r["num"]))
            except HTTPError as e:  # GitHub answers 410 Gone for a deleted issue
                return (r, None) if e.code in (404, 410) else None
            except Exception:
                return None
        # Concurrently, like the edges: a burst of closes must not hold SNAPPING for one call each.
        with ThreadPoolExecutor(max_workers=8) as pool:
            for got in pool.map(read, [r for r in prev if r["id"] not in have]):
                why = got and gone.why(got[1], truncated)
                if why:
                    # The row as the board last showed it: its column, and what the kanban's filters
                    # read, so a gone row fades out where it was and only in a view it belongs to.
                    r = got[0]
                    b.gone.append(dict({k: r.get(k) for k in GONE_KEEPS}, why=why, at=now))
    b.gone[:] = gone.keep(b.gone, now, have)
    return list(b.gone)


# What of the usage limits a page shows: its bars and where they came from, not when they were heard (bin/usage's
# plan.json is new every couple of minutes): a change in them is a change of the board's, so every page's long-poll
# has it (the Agent Room's screen said "bin/usage is not running" till something else changed, from bin/start on).
usage_shown = lambda u: ((u or {}).get("limits"), (u or {}).get("from"))


def publish(s):
    """Swap in a new snapshot; bump `ver` and wake long-pollers only if it changed."""
    with CHANGED:
        same = (s["rows"] == SNAP["rows"] and s["plan"] == SNAP["plan"]
                and s["agents"] == SNAP["agents"] and s["agents_error"] == SNAP.get("agents_error")
                and s["agents_issue"] == SNAP.get("agents_issue")
                and s["prs"] == SNAP["prs"] and s["prs_error"] == SNAP.get("prs_error")
                and s.get("truncated") == SNAP["truncated"] and s.get("gone") == SNAP.get("gone")
                and s.get("columns") == SNAP.get("columns")
                and s.get("columns_error") == SNAP.get("columns_error")
                and s.get("boards") == SNAP.get("boards") and s.get("boards_error") == SNAP.get("boards_error")
                and usage_shown(s.get("usage")) == usage_shown(SNAP.get("usage")))
        SNAP.update(s, at=time.time(), error=None)
        if not same:
            SNAP["ver"] += 1
            CHANGED.notify_all()


def move(ref, to):
    """Move one card to column `to` (a key or name): set that column's label on the issue and take
    every other column label off, in ONE label write, then re-judge the board at once. Returns where
    the row is now: a rule may keep it where it was (a claimed row stays In progress), and the caller
    says so rather than pretending it moved. Refuses a column that is computed, the plan or closed,
    and a label the issue's repo does not have (GitHub would quietly create it)."""
    wf = current_workflow()
    target = workflow.drop_target(wf, to)  # Invalid: the caller's error, said
    m = _REF.fullmatch(ref or "")
    if not m:
        raise BadRef("not an issue ref: %r" % ref)
    repo, n = m.group(1), int(m.group(2))
    b = board_of(repo)
    if target["label"]:
        have, _ = api_all("/repos/%s/%s/labels" % (b.owner, repo))
        if target["label"] not in {l["name"] for l in have}:
            raise workflow.Invalid("%s/%s has no label %r: create it first (forge.py newlabel %r)"
                                   % (b.owner, repo, target["label"], target["label"]))
    path = "/repos/%s/%s/issues/%d" % (b.owner, repo, n)
    i = api(path)
    if i.get("state") != "open" or "pull_request" in i:
        raise LookupError("%s is not an open issue" % ref)
    labels = workflow.moved_labels([l["name"] for l in i.get("labels") or []], wf, target)
    got = api(path + "/labels", "PUT", {"labels": labels}) or []
    with SNAPPING:
        b.deps.pop(ref, None)
        publish(snapshot(False))
    now = next((r for r in board_rows_now(b) if r["id"] == ref), None)
    return {"id": ref, "col": now["col"] if now else None, "labels": sorted(l["name"] for l in got)}


def refresh(refs):
    """Re-read the edges of the issues a forge.py write touched, then publish at once. Only
    those: the rest of a snapshot is one search plus the strips, while a FULL is one dependency
    call per open row. A ref the forge does not know is just re-read and found absent."""
    with SNAPPING:
        for r in refs:
            for b in [HOME] + list(OTHERS.values()):
                b.deps.pop(str(r), None)
        publish(snapshot(False))
    return {"ver": SNAP["ver"]}


def board_rows_now(b):
    """Board `b`'s rows in the snapshot served now."""
    return SNAP["rows"] if b is HOME else ((SNAP.get("boards") or {}).get(b.key) or {}).get("rows") or []


# Fields the server keeps for its own judgments but never serves: no view reads them, and served ==
# shown. A row's `unblocks` is pick.py's count; a PR's refs decide its `owner`; `updated` orders the
# PRs; a message's sender_session is its sender's identity in the store.
UNSERVED = {"rows": ("unblocks",), "prs": ("refs", "updated"), "mail": ("sender_session",)}


# A board's own part of the body, as the home board's is served at the top level: the rest (the roster, messages, usage,
# the room, the columns) is every board's.
BOARD_PART = ("rows", "plan", "prs", "prs_error", "truncated", "gone")


class NoBoard(LookupError):
    """?board= names no board this server has."""


def board_view(me, which=None):
    """The /api/board body for caller `me`: the snapshot plus pickability. Computed on the
    RESPONSE only, so `ver` and the long-poll stay global (one version for every caller).
    `which` is the board: the home board (None, as ever), another by its key, or `*` for every board at once (the Agent
    Room, whose desks hold cards of any board and whose wall shows one: rows, PRs and gone of all, `plans` by board)."""
    with CHANGED:
        body = dict(SNAP, forge=PUBLIC, owner=OWNER, now=time.time(), room=dict(ROOM))
    parts = body.pop("boards", None) or {}
    others = OTHERS
    body["boards"] = [HOME.entry()] + [others[k].entry(p.get("error")) for k, p in parts.items() if k in others]
    if not body.get("boards_error"):
        body.pop("boards_error", None)
    if which == "*":
        for k in ("rows", "prs", "gone"):
            body[k] = list(body.get(k) or []) + [x for p in parts.values() for x in p.get(k) or []]
        body["truncated"] = bool(body.get("truncated")) or any(p.get("truncated") for p in parts.values())
        body["plans"] = dict({HOME.key: body.get("plan")}, **{k: p.get("plan") for k, p in parts.items()})
        body["board"] = "*"
    elif which and which.lower() != HOME.key.lower():
        key = next((k for k in parts if k.lower() == which.lower()), None)
        if key is None:
            raise NoBoard("no board %r: the boards are %s" % (which, ", ".join(e["key"] for e in body["boards"])))
        body.update({k: parts[key].get(k) for k in BOARD_PART}, columns_error=parts[key].get("columns_error") or body.get("columns_error"))
        body["board"] = key
    else:
        body["board"] = HOME.key
    # The messages, read OUTSIDE the board's lock (the store calls back into it).
    try:
        body["mail"] = STORE.recent_mail(40)
        body["owner_unread"] = [m["id"] for m in STORE.unread("owner")]
    except Exception as e:  # a store fault costs the Messages panel, never the whole board
        body.update(mail=[], owner_unread=[], mail_error="%s: %s" % (type(e).__name__, e))
    # A fault in the rule costs the pickability fields, never the board: the kanban does not read
    # them, and forge.py / the hook exit on `pick_error` rather than print an unjudged board.
    try:
        body["rows"] = pick.view(body["rows"], body["agents"], me, WF["wf"])
    except Exception as e:
        body["pick_error"] = "%s: %s" % (type(e).__name__, e)
    # The roster for this caller: `you` by the SAME widening pickability used (pick.widen), and
    # `fresh` judged again NOW, so it keeps moving while the forge is down and snapshot() is not
    # running (the snapshot's own `fresh` is what bumps `ver` on a change while it is up).
    mine = pick.widen(me, body["agents"])
    body["agents"] = [dict(d, you=pick.is_me(d, mine), fresh=roster.fresh(d.get("seen"), body["now"]))
                      for d in body["agents"]]
    for k, drop in UNSERVED.items():
        body[k] = [{f: v for f, v in d.items() if f not in drop} for d in body.get(k) or []]
    return body


def hand_refused(name, ref):
    """Why ticket `ref` may not be handed to agent `name` (a ticket goes only to an agent on its board), or None. An
    agent the roster does not have, or a ref that is no repo#N, is bin/consoles' to refuse."""
    m = _REF.fullmatch(ref or "") if isinstance(ref, str) else None
    a = next((d for d in SNAP.get("agents") or [] if d.get("name") == name), None)
    if not m or not a:
        return None
    want, has = board_of(m.group(1)).key, a.get("board") or HOME.key
    if has.lower() == want.lower():
        return None
    return "%s is on the %s board, and %s works from %s" % (ref, want, name, "no board" if has == NO_BOARD else "the %s board" % has)


def rate_wait(now):
    """(seconds to hold off GitHub, why) or (0, None). Below RATE_FLOOR the quota is left for the
    agents, whose writes matter more than a fresher board; a secondary limit says its own wait."""
    with RATE_LOCK:
        if RATE["until"] > now:
            return RATE["until"] - now, "GitHub secondary rate limit: holding off %ds" % (RATE["until"] - now)
        if RATE["left"] is not None and RATE["left"] < RATE_FLOOR and RATE["reset"] > now:
            return RATE["reset"] - now, ("GitHub quota low (%d left): this snapshot is held until %s"
                                         % (RATE["left"], time.strftime("%H:%M", time.localtime(RATE["reset"]))))
    return 0, None


def refresher():
    last_full = 0
    while True:
        full = time.time() - last_full >= FULL
        wait, why = rate_wait(time.time())
        if wait:
            with CHANGED:
                SNAP["error"] = why
                SNAP["ver"] += 1
                CHANGED.notify_all()
            KICK.wait(min(wait, 60))  # re-judged at least every minute; a forge.py refresh still wakes it
            KICK.clear()
            continue
        try:
            with SNAPPING:
                publish(snapshot(full))
            if full:
                last_full = time.time()
        except Exception as e:  # keep serving the last good board; say why it is stale
            # ⚠ Bumping `ver` on EVERY failed poll is load-bearing: it is what keeps the page
            # re-fetching during an outage, and so seeing each agent's `fresh` re-judged by
            # board_view. Bump only on a changed error and agent freshness freezes on the page.
            with CHANGED:
                SNAP["error"] = "%s: %s" % (type(e).__name__, e)
                SNAP["ver"] += 1
                CHANGED.notify_all()
        KICK.wait(POLL)
        KICK.clear()


def save_plan(base, steps, board=None):
    """Rewrite board `board`'s plan from the kanban (the home board's when None). `base` is the hash the page last read:
    if a session rewrote the plan since, refuse (409) rather than overwrite its steps. Another board's first plan is
    its RESUME issue, made here, in the home repo (forge.py makes the home board's: `resume -F`)."""
    b = HOME if not board or board.lower() == HOME.key.lower() else next(
        (x for k, x in OTHERS.items() if k.lower() == board.lower()), None)
    if b is None:
        raise LookupError("no board %r" % board)
    p = plan_issue(b)
    if not p and b is HOME:
        raise LookupError("no open RESUME issue in %s; `forge.py resume -F` creates it" % RESUME_REPO)
    if p and digest(p.get("body")) != base:
        raise PermissionError("the plan changed since this page read it; reloaded, try again")
    clean, seen = [], set()
    for s in steps:
        ref = str(s.get("ref") or "")
        if not _REF.fullmatch(ref) or ref in seen:
            raise ValueError("bad or repeated step: %r" % ref)
        seen.add(ref)
        clean.append({"ref": ref, "owner": bool(s.get("owner")),
                      "text": re.sub(r"\s+", " ", str(s.get("text") or "")).strip()})
    body = plan_body(clean)
    if p:
        api("/repos/%s/%s/issues/%d" % (OWNER, RESUME_REPO, p["number"]), "PATCH", {"body": body})
    else:
        p = api("/repos/%s/%s/issues" % (OWNER, RESUME_REPO), "POST",
                {"title": PLAN_TITLE % b.key, "body": body, "labels": ["off-board"]})
    p["body"] = body
    with CHANGED:
        view = plan_view(p)
        if b is HOME:
            SNAP["plan"] = view
        elif b.key in (SNAP.get("boards") or {}):
            SNAP["boards"] = dict(SNAP["boards"], **{b.key: dict(SNAP["boards"][b.key], plan=view)})
        SNAP["ver"] += 1
        CHANGED.notify_all()
        return view


# -- the message routes (mail.py does the work; these only unpack) ----------------------------------
# `actor` is the caller's roster handle (or `owner` from the kanban), `session` its session id. This
# is the LAN, with the trust the board's plan writes have: it guards against an agent's ACCIDENT, and
# on one shared GitHub account nothing could guard against an agent set on posing as the owner.
WAIT_HOLD = 25  # one long-poll; a client asks again until a message arrives or it gives up


def mail_get(path, q):
    if path == "/api/mail/recent":
        return {"mail": STORE.recent_mail(int(q.get("n", 50)))}
    if path == "/api/mail":
        if q.get("wait"):
            return {"mail": STORE.unread_wait(q["for"], WAIT_HOLD)}
        return {"mail": STORE.unread(q["for"])}
    raise LookupError("no route " + path)


MAIL_POST = {
    "/api/mail": lambda b: {"to": STORE.send(b["actor"], b["to"], b.get("body") or "",
                                             b.get("session") or "", b.get("to_sessions") or [])},
    "/api/mail/read": lambda b: STORE.mark_read(b["actor"], b["last"]) or {"ok": True},
}


# Each API key reaches only the routes its holder uses; anything else is the owner's (their session, on their page).
# agents: seat/tools/forge.py (the board, moving a card, refreshing, messages). engine: the room's server page (the
# page, its scripts and pictures, the board, the tarot reader's reading and the lines meant for her, the room's
# settings: an agent at the wall's switch; the owner's chips and record at the casino, kept and read back). consoles: bin/consoles taking jobs and answering them. A key of any other
# name reaches nothing.
KEY_ROUTES = {
    "agents": {("GET", "/api/board"), ("POST", "/api/refresh"), ("POST", "/api/move"), ("GET", "/api/mail"), ("GET", "/api/mail/recent"),
               ("POST", "/api/mail"), ("POST", "/api/mail/read")},
    "engine": {("GET", "/room"), ("GET", "/common.js"), ("GET", "/motion.js"), ("GET", "/console.js"), ("GET", "/party.js"), ("GET", "/visit/visit.js"), ("GET", "/visit/mode"), ("GET", "/vendor/three.min.js"),
               ("GET", "/vendor/OrbitControls.js"), ("GET", "/teleport-beam.js"), ("GET", "/api/board"), ("GET", "/api/tarot"), ("POST", "/api/tarot/ask"), ("POST", "/api/room"),
               ("GET", "/api/casino"), ("POST", "/api/casino")},
    "consoles": {("GET", "/api/console/next"), ("POST", "/api/console/answer")},
}


def key_may(label, method, path):
    if (method, path) in KEY_ROUTES.get(label, ()):
        return True
    return label == "engine" and method == "GET" and TAROT_CARD.fullmatch(path) is not None   # her cards, for the table


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json", headers=()):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        if ctype.startswith("text/html"):   # no other page may frame these (a click on the console, or remove, under another site's)
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def length(self, cap=1 << 20):
        """The request body's stated length: a number from 0 to `cap` (ValueError otherwise: a negative one would have
        rfile.read take everything until the socket closes, a huge one all the memory it is sent)."""
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 <= n <= cap:
            raise ValueError("Content-Length is 0 to %d bytes" % cap)
        return n

    def redirect(self, where, cookie=None):
        self.send(303, b"", "text/plain", [("Location", where)] + ([("Set-Cookie", cookie)] if cookie else []))

    # -- the sign-in (auth.py; basedverse's auth/INTEGRATING.md) -------------------------------------------------
    def gate(self, method):
        """The one check before every route: True when the request may go on (self.who, self.session set), else
        it has been answered. Signed out: a page goes to the sign-in page, anything else gets 401. A write signed
        in with the cookie must come from the board's own page (Origin). auth-client not answering: 503."""
        path = urlparse(self.path).path
        self.who = self.session = None
        if (method, path) in auth.PUBLIC:
            return True
        session = auth.session_of(self.headers.get("Cookie"), auth.COOKIE_REMOTE if self.remote else auth.COOKIE)
        key = auth.bearer(self.headers.get("Authorization"))
        try:
            who = auth.who(None, key) if key else auth.who(session, None) if session else None
        except auth.Unreachable as e:
            self.send(503, {"error": "the sign-in is not answering (%s): bin/start starts it" % e})
            return False
        if who:
            if method != "GET" and not key and self.headers.get("Origin") not in self.origins():
                self.send(403, {"error": "a write from another site's page"})
                return False
            self.who, self.session = who, (session if not key else None)
            if key and not key_may(who.get("key"), method, path):
                self.send(403, {"error": "the %s key may not use %s %s" % (who.get("key"), method, path)})
                return False
            return True
        if key or path.startswith("/api/") or path.startswith("/visit/") or method != "GET":
            self.send(401, {"error": "sign in"}, headers=[("X-Sign-In", "required")])
        else:
            self.redirect("/login?next=%s%s" % (quote(self.path, safe=""), "&e=expired" if session else ""))
        return False

    @property
    def remote(self):
        """Whether this request came through an HTTPS proxy (the board's listener for one: BOARD_PROXY_PORT), from
        another device: so no Visit (visit-client's port is this machine's own; basedverse's auth/INTEGRATING.md 10)."""
        return getattr(self.server, "remote", False)

    def origins(self):
        """Where the board's own page is, for a write: here, as compose.yml publishes it; through the proxy, https://
        and the name the proxy was asked by (only the proxy reaches that listener, and it serves HTTPS for it)."""
        return {"https://" + (self.headers.get("Host") or "").strip()} if self.remote else auth.ORIGINS

    def client(self):
        """The browser's address: through the proxy, the last X-Forwarded-For entry (the proxy's; any before it are
        the browser's own claims); else the connection's."""
        return (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()[:100] if self.remote else self.client_address[0]

    def sign_in(self):
        """POST /login (the sign-in page's form): auth-client checks it; signed in, the cookie, and on to `next`."""
        try:
            n = min(self.length(), 8192)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace"), max_num_fields=10).items()}
        except ValueError:
            return self.redirect("/login?e=wrong")
        nxt = auth.safe_next(form.get("next"))
        try:
            code, b = auth.login(form.get("user", ""), form.get("password", ""), self.client(), (self.headers.get("User-Agent") or "")[:300])
        except auth.Unreachable as e:
            return self.send(503, ("the sign-in is not answering (%s): bin/start starts it" % e).encode(), "text/plain; charset=utf-8")
        if code == 200:
            return self.redirect(nxt, auth.cookie(b["session"], int(b["expires"]) - time.time(), self.remote))
        # Refused: why, in the board's log, with the shape of what was typed and never what (a name typed may be a
        # password in the wrong field): its length, capitals, spaces at either end, letters past ASCII.
        shape = lambda t: "%d long%s%s%s" % (len(t), ", capitals" if t != t.lower() else "", ", spaces at an end" if t != t.strip() else "",
                                             ", non-ASCII" if any(ord(c) > 127 for c in t) else "")
        print("sign-in refused: %s %s, from %r%s; name %s, password %s" % (code, (b or {}).get("error", ""), self.client(), " (through the proxy)" if self.remote else "",
                                                                           shape(form.get("user", "")), shape(form.get("password", ""))), flush=True)
        return self.redirect("/login?e=%s&next=%s" % ("wait" if code == 429 else "wrong" if code == 401 else "fault", quote(nxt, safe="")))

    def sign_out(self):
        try:
            auth.logout(self.session)
        except auth.Unreachable:
            pass   # the cookie goes anyway
        self.redirect("/login", auth.cookie("", 0, self.remote))

    def do_GET(self):
        if not self.gate("GET"):
            return
        u = urlparse(self.path)
        if u.path == "/login":  # the sign-in page (basedverse's auth/web/login.html): public, one file
            with open(os.path.join(HERE, "login.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/room":  # the Agent Room: a third view of the same snapshot
            # ?role=host is the room's server page (engine/INTEGRATING.md step 5): the engine's, by its key, never a
            # person's; without it, the person's page: a session's, never a key's.
            host = (parse_qs(u.query).get("role") or [""])[0] == "host"
            if host and self.session:
                return self.send(403, {"error": "the room's server page is the engine's (its key), not a person's"})
            if not host and not self.session:
                return self.send(403, {"error": "the room is for the signed-in owner's page, not a key"})
            with open(os.path.join(HERE, "room.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if u.path in ("/common.js", "/console.js", "/party.js", "/visit-ui.js", "/teleport-beam.js"):  # the shared presentation every board page loads; an agent's console (the room's and the kanban's); your agents as a party (the room's figures, and the list of them the room and the kanban draw); Visit's UI on your page, this room's copy of basedverse's reference UI (Visit 5.0)
            with open(os.path.join(HERE, u.path[1:]), "rb") as f:
                return self.send(200, f.read(), "text/javascript; charset=utf-8")
        card = TAROT_CARD.fullmatch(u.path)
        if card:  # the tarot reader's cards: the Rider-Waite deck's scans and Waite's meanings (board/tarot/, its SOURCES.md), read from disk, kept a day
            try:
                with open(os.path.join(HERE, "tarot", (card.group(1) + ".jpg") if card.group(1) else "meanings.json"), "rb") as f:
                    raw = f.read()
            except OSError:
                return self.send(404, {"error": "no such card"})
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg" if card.group(1) else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.end_headers()
            self.wfile.write(raw)
            return
        if u.path in ("/vendor/three.min.js", "/vendor/OrbitControls.js"):  # the room's three.js, kept here (vendor/VENDOR.md), never a CDN's
            with open(os.path.join(HERE, "vendor", u.path[8:]), "rb") as f:
                return self.send(200, f.read(), "text/javascript; charset=utf-8")
        if u.path == "/motion.js":  # the Agent Room's snapshot diff and per-agent queues
            with open(os.path.join(HERE, "motion.js"), "rb") as f:
                return self.send(200, f.read(), "text/javascript; charset=utf-8")
        # Visit, through the board (engine/INTEGRATING.md steps 4 and 5): no token ever reaches a page. (/api/visit,
        # which handed a page visit-client's token, is gone with 0.23: the page is relayed instead.)
        if u.path == "/visit/mode":  # what this office runs: the engine, a lobby
            return self.send(200, visit_mode())
        if u.path == "/visit/visit.js":  # visit-client's library, the one its binary speaks, served from it on each request
            try:
                with urlopen("http://%s:%d/visit.js" % (VISIT_HOST, VISIT_PORT), timeout=5) as r:
                    return self.send(200, r.read(), "text/javascript; charset=utf-8")
            except (URLError, OSError) as e:
                return self.send(503, {"error": "no visit-client (%s): the .env names no lobby, or bin/start has not started it" % e})
        if u.path == "/visit/ws":  # the person's connection to visit-client, relayed: the signed-in owner's page only
            if not self.session:
                return self.send(403, {"error": "for the signed-in owner's page, not a key"})
            return self.relay()
        if u.path == "/api/console/screen":  # the room's console: an agent's herdr pane (bin/consoles does it)
            q = parse_qs(u.query)
            return self.console({"do": "screen", "agent": (q.get("agent") or [""])[0], "since": (q.get("since") or ["0"])[0]})
        if u.path == "/api/console/places":  # where a new agent may start, for a ticket's New agent (bin/consoles knows)
            return self.console({"do": "places"})
        if u.path == "/api/console/next":  # bin/consoles asking for work: held until there is some
            if not self.consoles_only():
                return
            job = CONSOLES.next()
            if job is None:
                self.send_response(204)
                self.end_headers()
                return
            # An ask held from a bin/consoles that has since stopped (bin/start replaces it: its asks stay held here up
            # to HOLD) must not take a job to nowhere: the job goes back, first in line, for the one running now.
            if peer_gone(self.connection):
                return CONSOLES.requeue(job)
            try:
                return self.send(200, job)
            except OSError:
                return CONSOLES.requeue(job)
        if u.path == "/api/board":
            v = (parse_qs(u.query).get("v") or [None])[0]
            with CHANGED:
                if v is not None and v == str(SNAP["ver"]):
                    CHANGED.wait_for(lambda: str(SNAP["ver"]) != v, timeout=HOLD)
                if v is not None and v == str(SNAP["ver"]):
                    self.send_response(204)
                    self.end_headers()
                    return
            me = (parse_qs(u.query).get("me") or [""])[0].split(",")
            try:
                return self.send(200, board_view(me, (parse_qs(u.query).get("board") or [None])[0]))
            except NoBoard as e:
                return self.send(404, {"error": str(e)})
        if u.path == "/api/casino":  # the players' chips, records and achievements at the casino (the room's page keeps them here)
            return self.send(200, casino_view({k: v[0] for k, v in parse_qs(u.query).items()}))
        if u.path == "/api/tarot":  # the Agent Room's tarot reader: the room's reading (tarot.py); ?v=N held until it changes
            v = (parse_qs(u.query).get("v") or [None])[0]
            redact = (self.who or {}).get("key") != "engine"   # another's whispers' words are the engine's page's alone (to whisper them back)
            view = TAROT.view(redact) if v is None else TAROT.wait(v, HOLD, redact)
            if view is None:
                self.send_response(204)
                self.end_headers()
                return
            if redact:   # the owner's page: whether the reading on her table is saved, and how many are
                view.update(kept=KEPT.has((view.get("reading") or {}).get("id")), kept_n=KEPT.count())
            return self.send(200, view)
        if u.path == "/api/tarot/kept":   # the readings the owner saved: in brief, newest first; ?reading=<id>: that one whole
            rid = (parse_qs(u.query).get("reading") or [None])[0]
            KEPT.refresh(TAROT.view(True)["reading"])   # (one still on her table: with its talk since)
            if rid is None:
                return self.send(200, {"kept": KEPT.list()})
            r = KEPT.get(rid)
            return self.send(200, {"reading": r}) if r else self.send(404, {"error": "no saved reading %s" % rid})
        if u.path.startswith("/api/mail"):
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            return self.store_call(lambda: mail_get(u.path, q))
        if u.path == "/api/issue":  # the quick view
            try:
                return self.send(200, issue_view((parse_qs(u.query).get("id") or [""])[0]))
            except BadRef as e:
                return self.send(400, {"error": str(e)})
            except HTTPError as e:
                return self.send(404 if e.code == 404 else 502, {"error": "forge: HTTP %d" % e.code})
            except Exception as e:  # never a dropped connection: the panel must say why
                return self.send(502, {"error": "%s: %s" % (type(e).__name__, e)})
        self.send(404, {"error": "not found"})

    def relay(self):
        """The person's WebSocket to visit-client (basedverse's engine/SPEC.md 6.3): a WebSocket to it on this network
        with the browser's Origin, the relay's preface first (the user token, role user), then bytes both ways unread
        until either side closes; at most RELAY_BUFFER waiting for the browser (then hung up, no close frame); one connection a session
        (a second replaces the first, 4409). The handler's thread is the pump's."""
        h = self.headers
        if h.get("Upgrade", "").lower() != "websocket" or not h.get("Sec-WebSocket-Key"):
            return self.send(400, {"error": "a WebSocket upgrade"})
        try:
            with open(VISIT_USER_TOKEN_FILE) as f:
                token = f.read().strip()
        except OSError as e:
            return self.send(503, {"error": "no user token (%s): bin/start writes it when it starts visit-client" % e})
        try:
            up = socket.create_connection((VISIT_HOST, VISIT_PORT), timeout=10)
        except OSError as e:
            return self.send(503, {"error": "no visit-client (%s): the .env names no lobby, or bin/start has not started it" % e})
        up.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            up.sendall(("GET /ws HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                        "Sec-WebSocket-Version: 13\r\nOrigin: %s\r\n\r\n"
                        % (VISIT_HOST, VISIT_PORT, base64.b64encode(os.urandom(16)).decode(), h.get("Origin", ""))).encode())
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = up.recv(4096)
                if not chunk:
                    break
                head += chunk
            status = head.split(b" ", 2)[1:2]   # (any HTTP/1.x)
            if status != [b"101"]:
                up.close()
                return self.send(403 if status == [b"403"] else 502, {"error": "visit-client refused the connection (%s)" % head[:40].decode("ascii", "replace")})
            up.sendall(ws_frame(json.dumps({"type": "relay", "token": token, "role": "user"}).encode()))
        except OSError as e:
            up.close()
            return self.send(502, {"error": "visit-client: %s" % e})
        accept = base64.b64encode(hashlib.sha1((h["Sec-WebSocket-Key"] + WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        down = self.connection
        down.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with _relays_lock:
            old = _relays.get(self.session)
            _relays[self.session] = down
        if old:
            try:
                old.sendall(ws_close(4409, b"replaced"))
                old.close()
            except OSError:
                pass
        self.close_connection = True
        try:
            self.pump(down, up, head[head.index(b"\r\n\r\n") + 4:])
        finally:
            with _relays_lock:
                if _relays.get(self.session) is down:
                    del _relays[self.session]

    @staticmethod
    def pump(down, up, leftover):
        """Bytes both ways, unread, until either side closes."""
        sel = selectors.DefaultSelector()
        sel.register(down, selectors.EVENT_READ)
        sel.register(up, selectors.EVENT_READ)
        for s in (down, up):
            s.setblocking(False)
        to_browser = bytearray(leftover)
        try:
            while True:
                if to_browser:
                    try:
                        sent = down.send(to_browser)
                        del to_browser[:sent]
                    except BlockingIOError:
                        pass
                for key, _ in sel.select(timeout=30):
                    try:
                        data = key.fileobj.recv(65536)
                    except BlockingIOError:
                        continue
                    if not data:
                        return
                    if key.fileobj is up:
                        to_browser += data
                        if len(to_browser) > RELAY_BUFFER:   # a link that cannot take the scene: the page starts again
                            # Hung up, with no close frame: the browser has part of a frame already (the relay never
                            # reads where one ends), and a close frame after it read as that frame's rest ("Could
                            # not decode a text frame as UTF-8"). The page reconnects as after any drop.
                            return
                    else:
                        up.setblocking(True)
                        up.sendall(data)   # the browser's frames, masked already, as they are
                        up.setblocking(False)
        except OSError:
            return
        finally:
            for s in (down, up):
                try:
                    s.close()
                except OSError:
                    pass

    def console(self, job, wait=None):
        """A call from the room's console, for bin/consoles: queued for it (it asks for work: it connects out to the
        board, and listens on nothing), and its answer returned, for the signed-in owner's page only, on this machine
        or another device (basedverse's auth/INTEGRATING.md, step 9)."""
        if not self.session:
            return self.send(403, {"error": "for the signed-in owner's page, not a key"})
        try:
            status, body = CONSOLES.call(job, wait)
        except LookupError as e:
            return self.send(503, {"error": str(e)})
        except TimeoutError as e:
            return self.send(504, {"error": str(e)})
        return self.send(status, body)

    def consoles_only(self):
        """bin/consoles' own routes: its key only (auth-client's `consoles`), never a session or another key."""
        if self.who != {"key": "consoles"}:
            self.send(403, {"error": "bin/consoles' own (its key)"})
            return False
        return True

    def store_call(self, fn):
        """A message route: its result, 409 with the reason for a refusal."""
        try:
            return self.send(200, fn())
        except mail.Refused as e:
            return self.send(409, {"error": str(e)})
        except KeyError as e:  # a missing field (KeyError IS a LookupError: it must come first)
            return self.send(400, {"error": "missing %s" % e})
        except LookupError as e:
            return self.send(404, {"error": str(e)})
        except (ValueError, TypeError, AttributeError) as e:
            return self.send(400, {"error": "%s: %s" % (type(e).__name__, e)})
        except Exception as e:  # never a dropped connection: the caller must hear why
            return self.send(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_POST(self):
        # The ONLY writes: a card's move (its column labels), the Up next list, the owner's × on a dead
        # agent, messages, the room's settings (/api/room) and its tarot reader's reading and talk (/api/tarot, the board's own). /api/refresh writes nothing to the forge: it re-reads it. Edges and issue
        # state are not writable here. Signing in and out, besides.
        if not self.gate("POST"):
            return
        route = urlparse(self.path).path
        if route == "/login":
            return self.sign_in()
        if route == "/logout":
            return self.sign_out()
        if route == "/api/console/image":   # a picture pasted into the room's console: its bytes, for bin/consoles to keep by the agent
            try:
                n = self.length(1 << 31)
            except ValueError:
                return self.send(400, {"error": "Content-Length is a number, 0 or more"})
            if not 0 < n <= Consoles.IMAGE_MAX:
                return self.send(413 if n else 400, {"error": "a picture is 1 byte to %d MB" % (Consoles.IMAGE_MAX >> 20)})   # never read cut short
            raw = self.rfile.read(n)
            agent = (parse_qs(urlparse(self.path).query).get("agent") or [""])[0]
            kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            return self.console({"do": "image", "agent": agent, "type": kind, "data": base64.b64encode(raw).decode()}, wait=Consoles.IMAGE_WAIT)
        if route in ("/api/console/send", "/api/console/keys", "/api/console/answer", "/api/console/spawn", "/api/console/hand",
                     "/api/console/plan"):
            # Each its own limit: an answer (a screen with all its scrollback: an agent with a long conversation sends
            # more than a megabyte) up to ANSWER_MAX, anything the room asks up to ASK_MAX; length()'s own 1 MB cap
            # refused the first as "Content-Length is a number", and that agent's console stayed blank.
            try:
                n = self.length(1 << 40)   # a number (its size is this route's to judge: below, before reading any)
            except ValueError:
                return self.send(400, {"error": "Content-Length is a number, 0 or more"})
            if n > (Consoles.ANSWER_MAX if route.endswith("answer") else Consoles.ASK_MAX):
                return self.send(413, {"error": "%d bytes is more than the console takes" % n})   # never read cut short
            try:
                b = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self.send(400, {"error": "the body is a JSON object"})
            if not isinstance(b, dict):
                return self.send(400, {"error": "the body is a JSON object"})
            if route.endswith("answer"):   # bin/consoles' answer to a job it took
                if not self.consoles_only():
                    return
                CONSOLES.answer(b.get("id"), b.get("status"), b.get("body"))
                return self.send(200, {})
            if route.endswith("send"):    # the room's console: a reply, or keys
                return self.console({"do": "send", "agent": b.get("agent"), "text": b.get("text")})
            if route.endswith("spawn"):   # New agent (for a ticket, or none): one started in herdr (bin/consoles checks all of it)
                return self.console({"do": "spawn", "dir": b.get("dir"), "workspace": b.get("workspace"), "ref": b.get("ref"),
                                     "new_workspace": b.get("new_workspace"), "setup": b.get("setup") is True},
                                    wait=Consoles.SPAWN_WAIT + (Consoles.SETUP_WAIT if b.get("setup") is True else 0))
            if route.endswith("plan"):    # what setting a directory up would do, for you to confirm (bin/init-agent --plan)
                return self.console({"do": "plan", "dir": b.get("dir")}, wait=Consoles.SETUP_WAIT)
            if route.endswith("hand"):    # a ticket given to an agent already here: only one on the ticket's board
                why = hand_refused(b.get("agent"), b.get("ref")) if self.session else None
                if why:
                    return self.send(409, {"error": why, "code": "wrong_board"})
                return self.console({"do": "hand", "agent": b.get("agent"), "ref": b.get("ref")})
            return self.console({"do": "keys", "agent": b.get("agent"), "keys": b.get("keys")})
        if route == "/api/room":   # the room's settings: its environment (room.html's dropdown, from any page)
            try:
                n = self.length()
                b = json.loads(self.rfile.read(n) or b"{}")
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            if not isinstance(b, dict):
                return self.send(400, {"error": "the body is a JSON object"})
            try:
                if "acts" in b:   # the room's page: what its agents are up to (on its own, or with settings)
                    room_acts(b["acts"])
                if "acts" not in b or set(b) - {"acts"}:
                    room_set(b.get("env"), b.get("dark"), b.get("look"), b.get("music"), b.get("board"), b.get("mix"), b.get("guest"), b.get("bino"), b.get("teleport"))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            return self.send(200, {"room": dict(ROOM)})
        if route == "/api/casino":   # the room's page: the players' chips, records and achievements ({owner?, visitors?: {key: theirs}}), kept
            try:
                n = self.length(1 << 20)
                b = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(b, dict) or not b or set(b) - {"owner", "visitors"}:
                    raise ValueError("the body is {owner?: {chips, rec, ach}, visitors?: {their office's key: {name, chips, rec, ach}}}")
                o, vs = casino_set(b.get("owner"), b.get("visitors"))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            except OSError as e:
                return self.send(500, {"error": "could not keep it: %s" % e})
            return self.send(200, {"owner": o, "visitors": sorted(vs)})
        if route == "/api/tarot/keep":   # her card's Save: the reading on her table saved ({reading, keep: true}), or one removed (keep: false)
            try:
                n = self.length()
                b = json.loads(self.rfile.read(n) or b"{}") if n <= 4096 else None
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            if not isinstance(b, dict) or not isinstance(b.get("reading"), str) or not isinstance(b.get("keep"), bool):
                return self.send(400, {"error": "the body is {reading: its id, keep: true or false}"})
            if b["keep"]:
                cur = TAROT.view(True)["reading"]   # the owner's view: another's whispers without their words, kept so
                if not cur or cur.get("id") != b["reading"]:
                    return self.send(409, {"error": "only the reading on her table can be saved"})
                try:
                    KEPT.keep(cur)
                except ValueError as e:
                    return self.send(409, {"error": str(e)})
            else:
                KEPT.drop(b["reading"])
            return self.send(200, {"kept": KEPT.has(b["reading"]), "kept_n": KEPT.count()})
        if route in ("/api/tarot", "/api/tarot/ask"):   # a new reading (her card's New reading), or a question about it (aloud or whispered)
            KEPT.refresh(TAROT.view(True)["reading"])   # (a saved one about to give way: with all its talk)
            try:
                n = self.length()
                b = json.loads(self.rfile.read(n) or b"{}") if n <= 4096 else None
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            if not isinstance(b, dict):
                return self.send(400, {"error": "the body is a JSON object, of 4 KB at most"})
            # Who asked: the owner from their own page (its session); from a key (the engine's page, for whoever said it in
            # the room: Visit's `addressed`), as it says, the owner if it says nothing.
            by = b.get("by") if (self.who or {}).get("key") == "engine" else {"owner": True, "name": str((b.get("by") or {}).get("name") or "") if isinstance(b.get("by"), dict) else ""}
            try:
                if route.endswith("/ask"):
                    return self.send(200, TAROT.ask(b.get("text"), b.get("private") is True, by, (self.who or {}).get("key") != "engine"))
                # `cards` 1 or 3: dealt at once; none (her card's Deal): she asks which first, one card or three
                n = b.get("cards") if b.get("cards") in (1, 3) and not isinstance(b.get("cards"), bool) else None
                return self.send(200, TAROT.start(b.get("question") or "", b.get("private") is True, "", by, (self.who or {}).get("key") != "engine", cards=n))
            except tarot.Limited as e:
                return self.send(429, {"error": str(e)})
            except (tarot.Busy, tarot.NoReading) as e:
                return self.send(409, {"error": str(e)})
            except tarot.NotReady as e:
                return self.send(503, {"error": str(e)})
            except ValueError as e:
                return self.send(400, {"error": str(e)})
        if route in MAIL_POST:
            try:
                n = self.length()
                b = json.loads(self.rfile.read(n) or b"{}")
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            if not isinstance(b, dict):
                return self.send(400, {"error": "the body is a JSON object"})
            if not self.session and b.get("actor") == "owner":
                # The owner's own page is the only one that speaks as them unseen. Through a key (forge.py --owner from
                # their shell, or anything else that holds one), a message as "owner" goes to the owner's unread too, so
                # one they did not send is seen; and nobody but their page marks their messages read.
                if route == "/api/mail/read":
                    return self.send(403, {"error": "the owner's messages are marked read on their own page"})
                b["session"] = "owner-key"
            return self.store_call(lambda: MAIL_POST[route](b))
        if route == "/api/refresh":
            try:
                n = self.length()
                b = json.loads(self.rfile.read(n) or b"{}")
                refs = b.get("refs") if isinstance(b, dict) else None
                if not isinstance(refs, list):
                    raise ValueError("need {\"refs\": [\"repo#N\", ...]}")
                return self.send(200, refresh(refs))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            except Exception as e:  # the forge did not answer: say so; the caller only warns
                return self.send(502, {"error": "%s: %s" % (type(e).__name__, e)})
        if route == "/api/move":
            try:
                n = self.length()
                b = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(b, dict):
                    raise ValueError("need {\"id\": \"repo#N\", \"to\": \"<column>\"}")
                return self.send(200, move(b.get("id"), b.get("to")))
            except (BadRef, workflow.Invalid, ValueError) as e:
                return self.send(400 if isinstance(e, BadRef) else 409, {"error": str(e)})
            except LookupError as e:
                return self.send(404, {"error": str(e)})
            except HTTPError as e:
                return self.send(502, {"error": "forge: HTTP %d %s" % (e.code, e.read()[:200].decode("utf-8", "replace"))})
            except Exception as e:  # never a dropped connection: the drop must say why
                return self.send(502, {"error": "%s: %s" % (type(e).__name__, e)})
        if self.path not in ("/api/plan", "/api/agent/remove"):
            return self.send(404, {"error": "not found"})
        try:
            n = self.length()
            b = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/agent/remove":
                try:
                    cid = int(b["id"])
                except (KeyError, TypeError, ValueError):
                    return self.send(400, {"error": "need a numeric comment id"})
                try:
                    remove_agent(cid)
                except LookupError as e:
                    return self.send(404, {"error": str(e)})
                except HTTPError as e:
                    return self.send(e.code, {"error": e.read()[:400].decode("utf-8", "replace")})
                except Exception as e:  # never a dropped connection: the × must say why
                    return self.send(500, {"error": "%s: %s" % (type(e).__name__, e)})
                return self.send(200, {"ok": True})
            self.send(200, save_plan(b.get("base"), b.get("steps") or [], b.get("board")))
        except PermissionError as e:
            KICK.set()
            self.send(409, {"error": str(e)})
        except LookupError as e:
            self.send(404, {"error": str(e)})
        except HTTPError as e:
            self.send(e.code, {"error": e.read()[:400].decode("utf-8", "replace")})
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            self.send(400, {"error": str(e)})

    def log_message(self, fmt, *args):
        if not self.path.startswith(("/api/board", "/api/mail?")):
            super().log_message(fmt, *args)


class Consoles:
    """The room's console calls, queued for bin/consoles, which asks for them (GET /api/console/next, held until
    there is one) and answers each (POST /api/console/answer); each call waits for its answer. bin/consoles is there
    while one of its asks is held, or was in the last few seconds."""
    HOLD = 25      # an empty ask answered after this long (204): it asks again
    WAIT = 10      # a call given up after this long with no answer
    SPAWN_WAIT = 90   # a new agent's (bin/consoles' SPAWN_S for Claude to start, NAMED_S for its name, and to spare)
    SETUP_WAIT = 130  # ...and a directory set up first (bin/consoles' SETUP_S for bin/init-agent, and to spare)
    GONE = 5       # with no ask held, bin/consoles is taken for gone this long after its last
    # Bytes in one answer: a screen and its scrollback (bin/consoles' HISTORY, 5000 lines with their colours, about
    # 1 MB, all of it at a console's first read), with room to spare; and in one of the room's calls (a reply of
    # bin/consoles' TEXT_MAX, 4000 characters, at most 12 bytes each in JSON).
    ANSWER_MAX = 16 << 20
    ASK_MAX = 64 << 10
    IMAGE_MAX = 10 << 20   # bytes in a picture pasted into the console (bin/consoles' IMAGE_MAX), its own route's
    IMAGE_WAIT = 30        # its call's wait: base64 on the way, written to disk there

    def __init__(self):
        self.cv = threading.Condition()
        self.jobs, self.answers, self.pending = deque(), {}, set()
        self.n, self.asking, self.last = 0, 0, 0.0

    def call(self, job, wait=None):
        with self.cv:
            if not self.asking and time.monotonic() - self.last > self.GONE:
                raise LookupError("bin/consoles is not running (bin/start starts it; it needs herdr)")
            self.n += 1
            jid = self.n
            self.pending.add(jid)
            self.jobs.append(dict(job, id=jid))
            self.cv.notify_all()
            end = time.monotonic() + (wait or self.WAIT)
            while jid not in self.answers:
                left = end - time.monotonic()
                if left <= 0:
                    self.pending.discard(jid)
                    self.jobs = deque(j for j in self.jobs if j["id"] != jid)
                    raise TimeoutError("bin/consoles did not answer")
                self.cv.wait(left)
            self.pending.discard(jid)
            return self.answers.pop(jid)

    def next(self):
        with self.cv:
            self.asking += 1
            try:
                end = time.monotonic() + self.HOLD
                while not self.jobs:
                    left = end - time.monotonic()
                    if left <= 0:
                        return None
                    self.cv.wait(left)
                return self.jobs.popleft()
            finally:
                self.asking -= 1
                self.last = time.monotonic()

    def requeue(self, job):
        """A job handed to an ask whose bin/consoles was gone by then: first in line again, while its call waits."""
        with self.cv:
            if job.get("id") in self.pending:
                self.jobs.appendleft(job)
                self.cv.notify_all()

    def answer(self, jid, status, body):
        with self.cv:
            if jid in self.pending and isinstance(status, int) and 100 <= status < 600:
                self.answers[jid] = (status, body if isinstance(body, (dict, list)) else {})
                self.cv.notify_all()


CONSOLES = Consoles()


def peer_gone(sock):
    """Whether the other end has closed `sock` (a bin/consoles that asked, then stopped): readable, with nothing to read."""
    try:
        ready, _, _ = select.select([sock], [], [], 0)
        return bool(ready) and sock.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


class Server(ThreadingHTTPServer):
    daemon_threads = True  # a held long-poll must not block shutdown
    remote = False         # the LAN's HTTPS proxy's listener (BOARD_PROXY_PORT): requests from another device


if __name__ == "__main__":
    threading.Thread(target=refresher, daemon=True).start()
    port = int(os.environ.get("BOARD_PORT", "3001"))
    print("board on :%d  forge %s  owner %s  repos %s  home %s  rank %s  columns %s  poll %gs  other boards %s"
          % (port, FORGE, OWNER, ",".join(REPOS) or "(all)", HOME_REPO, " > ".join(RANK) or "(none)",
             " | ".join(c["name"] for c in WF["wf"]["columns"]), POLL,
             ", ".join(k for k, _, _ in boards_named()[0]) or "(none)"), flush=True)
    # In its container, on every address of the container: compose publishes it on this machine's 127.0.0.1 only. A
    # second listener when BOARD_PROXY_PORT says where (compose publishes it there too): an HTTPS proxy's (Caddy, for
    # the LAN; `tailscale serve`; any), where every request is another device's.
    host, proxy = os.environ.get("BOARD_HOST", "127.0.0.1"), os.environ.get("BOARD_PROXY_PORT")
    if proxy:
        lan = Server((host, int(proxy)), H)
        lan.remote = True
        threading.Thread(target=lan.serve_forever, daemon=True).start()
        print("…and on :%s for an HTTPS proxy, from other devices" % proxy, flush=True)
    Server((host, port), H).serve_forever()
