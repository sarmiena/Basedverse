"""The agent roster, read: one parse of the AGENTS issue's entries and one freshness rule.
Pure, stdlib, no network.

forge.py WRITES the entries (seat/tools/forge.py agent_body) and keeps its own parse for its
write and identity paths, which need the state at that instant. Every display of the roster --
`forge.py agents`, the strip in `forge.py board`, the SessionStart hook, the kanban, the Agent Room
-- reads what server.py serves from here. test_roster.py pins the two parsers to one format: it writes an entry
with forge.py's serializer and reads it back with entries().
"""
import datetime
import json
import re

AGENT_MARK = "<!-- board-agent -->"
# `ctx` (tokens in use at the last turn's end), `blocked` (what it waits on the owner for), `subs`
# (the subagents it has working, one line of JSON: [{id, type, what}]) and `working` (when the turn under
# way began, '' between turns) are written by each seat's own hook (seat/hooks/agent_state.py);
# `msgname` is the session's name in ListAgents on its own machine. `board` is the board it works from (forge.py's
# REPO_BOARD: `owner/repo`, or `none` for a directory with no GitHub repo); blank is the home board's.
AGENT_KEYS = ("name", "session", "local", "msgname", "seat", "host", "link", "seen", "claims", "doing",
              "ctx", "blocked", "subs", "working", "board", "dir")   # dir: the directory it runs in (the hook's, ~ for home)

# Freshness is the whole point of the strip: a session that crashed just stops acting, and goes
# from live to idle, stale, then dead. Seconds since its last forge.py call, at each step.
FRESH_AFTER = {"idle": 300, "stale": 1800, "dead": 7200}


def parse_subs(v):
    """The `subs` line as a list of {id, type, what}; [] for none, or for a line that is not one (a
    garbled line costs the room its subagent desks, never the agent)."""
    try:
        got = json.loads(v) if v else []
    except ValueError:
        return []
    return [{"id": str(x.get("id") or ""), "type": str(x.get("type") or "agent"), "what": str(x.get("what") or "")}
            for x in got if isinstance(x, dict)] if isinstance(got, list) else []


def parse_agent(body):
    """An agent comment's fields, or None. `claims` and `subs` are lists."""
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


def entries(comments):
    """[(comment, entry)] for every agent entry among `comments`, in comment order."""
    return [(c, d) for c in comments for d in [parse_agent(c.get("body"))] if d]


def same(a, b):
    """Do entries `a` and `b` belong to one session? Each holds the ids it was written with: forge.py
    keys an entry by the claude.ai bridge id once a process has it, Claude Code's local id beside it,
    but a process started before the link was up has only the local id, and entered under that."""
    return bool(({a.get("session"), a.get("local")} & {b.get("session"), b.get("local")}) - {None, ""})


def merge(win, lose):
    """One session's two entries as one: the fresher `win` (its comment, its doing), holding both
    entries' claims, keyed by the bridge id when either has it (forge.py's merged(), the same rule)."""
    d = dict(win, claims=win["claims"] + [c for c in lose["claims"] if c not in win["claims"]])
    for k in ("local", "msgname", "link"):
        d[k] = d.get(k) or lose.get(k) or ""
    if not d["session"].startswith("session_") and lose["session"].startswith("session_"):
        d.update(session=lose["session"], local=win["session"], link=lose.get("link") or "")
    return d


def agents(ents):
    """The roster from entries(): freshest first, one entry per session (two first-touches racing, or
    one entered under each of its ids, merged: merge), each with its comment `id`."""
    out = []
    for c, d in ents:
        d["id"] = c["id"]
        d["seen"] = d.get("seen") or c.get("updated_at") or ""
        for old in [o for o in out if same(o, d)]:
            out.remove(old)
            d = merge(d, old) if d["seen"] > old["seen"] else merge(old, d)
        out.append(d)
    return sorted(out, key=lambda d: d["seen"], reverse=True)


def age(seen, now):
    """Whole seconds from ISO-8601 `seen` (Z or an offset) to `now` (epoch seconds); None if unreadable."""
    try:
        t = datetime.datetime.fromisoformat(str(seen).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return max(0, round(now - t.timestamp()))


def fresh(seen, now):
    """'' (live), 'idle', 'stale' or 'dead'. An unreadable stamp is dead: nothing says it is alive."""
    s = age(seen, now)
    if s is None:
        return "dead"
    for cls in ("dead", "stale", "idle"):
        if s >= FRESH_AFTER[cls]:
            return cls
    return ""


def local_ids(d):
    """The ids Claude Code itself knows entry `d`'s session by (its local id, not a claude.ai bridge id): forge.py's
    reap_targets reads them the same way."""
    s = str(d.get("session") or "")
    return {x for x in (d.get("local"), "" if s.startswith("session_") else s) if x}


def rebooted(ents, host, boot, live, here):
    """[(comment, entry)] of the sessions a restart of this machine ended: a machine turned off with its agents still
    running never ran their SessionEnd (the hook that takes an entry off), so their entries stay on the roster, dead,
    and the room shows agents with no pane. Each is proven ended, never guessed from quiet alone: written on `host`
    (this machine), by a session that ran here (`here(id)`: its transcript is on this machine, so another machine of
    the same name is never judged), last seen before this machine started (`boot`, epoch seconds: no process outlives
    a reboot), and held by no Claude Code running now (`live()`: their session ids, or None when that cannot be known;
    asked only when some entry is otherwise ended: a session resumed since the boot keeps its entry). One session's
    entries go together, and only when every one of them is ended (roster.agents shows them as one). `boot` None:
    nothing can be proven, so none."""
    if boot is None:
        return []
    old = [(c, d) for c, d in ents if d.get("host") == host and local_ids(d)
           and (age(d.get("seen") or c.get("updated_at"), boot) or 0) > 0]   # seen before the boot (unreadable: never)
    running = (live() or set()) if old else set()
    ended = [(c, d) for c, d in old if not local_ids(d) & running and any(here(i) for i in local_ids(d))]
    ids = {c["id"] for c, _ in ended}
    return [(c, d) for c, d in ended if not any(same(d, o) for oc, o in ents if oc["id"] not in ids)]


# `repo#N`: a reference to an issue. server.py's plan steps use this same grammar. A GitHub repo
# name may start uppercase (`WebApp#87`).
_REF = re.compile(r"\b([A-Za-z][\w.-]*)#(\d+)\b")


# The session a claude.ai link names, whatever else the link carries (a slash, a query).
_SESSION = re.compile(r"session_[A-Za-z0-9]+")


def pr_refs(pr_id, title, body):
    """The issues a PR is FOR, in the order they count: every `repo#N` in its title,
    then the FIRST in its body (a body names its issue first and mentions others in passing), never
    the PR's own number."""
    refs = ["%s#%s" % m for m in _REF.findall(title or "")]
    # The first body ref that is not the PR itself: a body may open by naming its own PR.
    first = next(("%s#%s" % m for m in _REF.findall(body or "") if "%s#%s" % m != pr_id), None)
    if first:
        refs.append(first)
    out = []
    for r in refs:
        if r != pr_id and r not in out:
            out.append(r)
    return out


def lasts(prev, agents, known=True):
    """{session: ref}: the ticket each agent on the roster held most recently. While it holds any, its
    first; once it holds none, the one `prev` (the last map) says it held. A session off the roster is
    forgotten, but only when the roster was read (`known`): a failed read forgets nobody."""
    out = dict(prev) if not known else {}
    for d in agents:
        s = d.get("session")
        if s:
            ref = (d.get("claims") or [None])[0] or prev.get(s)
            if ref:
                out[s] = ref
    return out


def pr_owner(refs, link, agents, roster_known=True):
    """Who a PR belongs to, as {"state": ...}:
      owned        {name, session, via}: a roster agent claims one of `refs` (the first claimed one
                   decides, so a PR naming two claimed issues belongs to the claimant of the one it
                   names first, title before body), or the PR body's session link is that agent's.
                   Any roster entry counts, a dead one too: the owner has not removed it yet.
      orphaned     {was, via}: nobody claims its refs and its session link names a session that is
                   NOT on the roster -- the agent that wrote it has left. `was` is that session.
      unattributed nothing to go on: no claimed ref and no session link.
      unknown      {}: it would be orphaned, but the roster could not be read (`roster_known` False),
                   so "its agent left" cannot be said."""
    for r in refs:
        for d in agents:
            if r in (d.get("claims") or []):
                return {"state": "owned", "name": d.get("name") or d.get("session"),
                        "session": d.get("session"), "via": "claim of " + r}
    sess = _SESSION.search(link or "")
    if sess:
        # By the entry's own ids as well as its link: a session whose `link` is blank still owns.
        for d in agents:
            mine = {d.get("session"), d.get("local"), (_SESSION.search(d.get("link") or "") or [None])[0]}
            if sess.group(0) in mine:
                return {"state": "owned", "name": d.get("name") or d.get("session"),
                        "session": d.get("session"), "via": "session link"}
        if not roster_known:
            return {"state": "unknown"}
        return {"state": "orphaned", "was": sess.group(0), "via": "session link"}
    return {"state": "unattributed"}
