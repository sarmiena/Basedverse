"""Board parity: every key /api/board serves is accounted for in views.SERVED, and every
key it says the kanban draws is read by the kanban. No network: the body is built by server.py's own
snapshot() and board_view() from a fake forge, a fresh rig store and two snapshots (so `gone` fills).

A key the server starts serving fails here until it is classed; a key the kanban stops reading fails
here until it is drawn again or reclassed. That is the whole point: "served but not shown" cannot
come back silently.

Run: python3 board/test_views.py      (--paths prints every served key and exits)
"""
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("REPO_TOKEN", "test")
os.environ["REPO_ENV"] = os.devnull  # hermetic: never the project .env, whichever project it names
os.environ["BOARD_DB"] = os.path.join(tempfile.mkdtemp(), "board.db")
os.environ["SHELLS_DIR"] = tempfile.mkdtemp()  # bin/shells' now.json: the agent below has a command running
os.environ["USAGE_DIR"] = tempfile.mkdtemp()   # bin/usage's plan.json of the usage limits
# A fixed project, so the fixture below means the same whatever the .env names.
WORKFLOW = os.path.join(tempfile.mkdtemp(), "workflow.json")
with open(WORKFLOW, "w") as _f:  # every kind of column, so every served column key appears
    json.dump({"columns": [
        {"key": "next", "name": "Up next", "plan": True},
        {"key": "ready", "name": "Ready", "default": True, "pick": True, "hint": "h"},
        {"key": "later", "name": "Parked", "label": "later"},
        {"key": "doing", "name": "In progress", "label": "In Progress", "limit": 3,
         "when": {"any": [{"label": "In Progress"}, {"claimed": True}]}},
        {"key": "review", "name": "In review", "when": {"pr": True}},
        {"key": "blocked", "name": "Blocked", "when": {"blocked": True}},
        {"key": "done", "name": "Closed", "closed": True}]}, _f)
# Another board (a repo of another owner, set up from the room), and a name that is no board, so `boards_error` is served.
os.environ.update(REPO_OWNER="testorg", REPO_NAMES="", REPO_HOME="docs",
                  REPO_RANK="severity: high, severity: low", REPO_WORKFLOW=WORKFLOW, REPO_BOARDS="sam/tool, not-a-board")
import server  # noqa: E402
import views  # noqa: E402

OWNER = server.OWNER
NOW = time.time()
ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 30))


def issue(repo, n, title, labels, state="open"):
    return {"number": n, "title": title, "state": state, "comments": 1, "updated_at": ISO,
            "repository": {"full_name": "%s/%s" % (OWNER, repo), "name": repo},
            "labels": [{"name": l} for l in labels]}


ROW = ["severity: high", "bug"]
ISSUES = [issue("host", 1, "a ready row", ROW),
          issue("host", 2, "a blocked row", ROW),
          issue("host", 3, "Epic: a container", ["severity: low"]),
          issue("host", 4, "a parked row", ["later"]),
          issue("host", 5, "a row that will close", ROW)]
RESUME = issue("docs", 100, "RESUME", ["off-board"])
RESUME["body"] = "1) host#1 first\n2) host#2 ✋ second"
AGENT = ("<!-- board-agent -->\nname: amber-zebu\nsession: session_A\nlocal: local-a\nmsgname: app-1\n"
         "seat: windows\nhost: host-a\nlink: https://claude.ai/code/session_A\nseen: %s\n"
         "claims: host#1\ndoing: testing\nctx: 1234\nblocked: a call\n"
         'subs: [{"id":"a1","type":"general-purpose","what":"Trace callers"}]\nworking: %s\ndir: ~/work/host\n' % (ISO, ISO))
PRS = [{"number": 9, "title": "for host#1", "body": "", "html_url": "u", "user": {"login": "k"},
        "comments": 0, "pull_request": {}, "draft": False, "updated_at": ISO, "repository": {"name": "host"}},
       {"number": 8, "title": "for host#4", "body": "https://claude.ai/code/session_GONE",
        "html_url": "u", "user": {"login": "k"}, "comments": 0, "pull_request": {}, "draft": True,
        "updated_at": ISO, "repository": {"name": "host"}}]
STATE = {"issues": ISSUES}



class FakeRoster:
    """The board's roster table (rosterdb.Store), faked: one agent, every field written."""
    def rows(self):
        return [{"id": 7, "body": AGENT, "updated_at": ISO}]


def fake_api(path, method="GET", body=None):
    path = re.sub(r"[?&]per_page=\d+&page=1$", "", path)  # api_all's first page; the fixture is short
    if path.startswith("/orgs/%s/issues" % OWNER):  # GitHub lists issues and PRs together
        return STATE["issues"] + [RESUME] + PRS
    if "labels=off-board" in path:
        return [RESUME]
    if path == "/repos/sam/tool/issues?state=open":   # the other board: one row, nothing gone
        return [dict(issue("tool", 1, "a row on the other board", ROW), repository={"full_name": "sam/tool", "name": "tool"})]
    if path.endswith("/host/issues/2/dependencies/blocked_by"):
        return [{"repository_url": "https://api.github.com/repos/%s/host" % OWNER, "number": 1}]
    if path.endswith("/dependencies/blocked_by"):
        return []
    m = re.search(r"/repos/[^/]+/(\w+)/issues/(\d+)$", path)
    if m:  # a dropped row, read once by dropped(): it closed
        return issue(m.group(1), int(m.group(2)), "closed", ROW, state="closed")
    raise AssertionError("unfaked forge call " + path)


def body():
    """One /api/board body, every optional part populated."""
    server.api = fake_api
    server.ROSTER = FakeRoster()
    with open(os.path.join(os.environ["SHELLS_DIR"], "now.json"), "w") as f:
        json.dump({"at": NOW, "shells": {"local-a": ISO}}, f)
    with open(os.path.join(os.environ["USAGE_DIR"], "plan.json"), "w") as f:   # bin/usage's: /usage's rows
        json.dump({"at": NOW, "rows": [
            {"kind": "session", "group": "session", "name": None, "percent": 18, "resets_at": NOW + 3600, "severity": "normal"},
            {"kind": "weekly_all", "group": "weekly", "name": None, "percent": 17, "resets_at": NOW + 5 * 86400, "severity": "normal"},
            {"kind": "weekly_scoped", "group": "weekly", "name": "Fable", "percent": 0, "resets_at": NOW + 5 * 86400, "severity": "normal"}]}, f)
    s = server.STORE
    s.send("amber-zebu", "owner", "hello", sender_session="session_A")
    s.send("amber-zebu", "x", "hi", sender_session="session_A", to_sessions=[["session_B", "brisk"]])
    server.publish(server.snapshot(True))
    STATE["issues"] = ISSUES[:-1]  # host#5 closes: the second snapshot serves it as gone
    server.publish(server.snapshot(False))
    return json.loads(json.dumps(server.board_view({"session_A"})))


def paths(o, p=""):
    """Every key path in `o`: a list's items merged (`[]`), a dict keyed by data (views.KEYED) as `{}`."""
    out = set()
    if isinstance(o, dict):
        keyed = p in views.KEYED
        for k, v in o.items():
            q = p + ("{}" if keyed else ("." if p else "") + k)
            out.add(q)
            out |= paths(v, q)
    elif isinstance(o, list):
        for v in o:
            out |= paths(v, p + "[]")
    return out


def main():
    served = paths(body())
    if "--paths" in sys.argv:
        print("\n".join(sorted(served)))
        return 0
    page = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    page += open(os.path.join(HERE, "common.js"), encoding="utf-8").read()
    page += open(os.path.join(HERE, "party.js"), encoding="utf-8").read()   # your agents as a party: the kanban loads it
    fails = 0

    def bad(msg):
        nonlocal fails
        fails += 1
        print("[FAIL] " + msg)
    # Every served key classed; every class known; no class for a key no longer served.
    for k in sorted(served - set(views.SERVED)):
        bad("served, not classed in views.SERVED: %s" % k)
    for k in sorted(set(views.SERVED) - served - views.FAULTS):
        bad("classed, no longer served (or the fixture lost it): %s" % k)
    # A fault key the fixture cannot produce must at least still be WRITTEN by the server, or a
    # rename would leave the new key unclassed and the old class stale, both unseen.
    src = "".join(open(os.path.join(HERE, f), encoding="utf-8").read() for f in ("server.py", "mail.py"))
    for k in sorted(views.FAULTS):
        leaf = k.rsplit(".", 1)[-1]
        if '"%s"' % leaf not in src and "%s=" % leaf not in src:  # a key or a dict(...) keyword
            bad("fault key %s: the server no longer writes it" % k)
    cli = cli_views()
    if cli is None:
        bad("forge.py not found ($FORGE_PY, ../../seat/tools/forge.py, ~/.claude/tools/forge.py):"
            " the cli-classed keys cannot be checked")
        cli = ""
    for k, (cls, why) in sorted(views.SERVED.items()):
        if cls not in views.CLASSES:
            bad("%s: unknown class %r" % (k, cls))
        elif cls == "kanban" and why not in page:
            bad("%s: classed kanban, but the kanban never reads `%s`" % (k, why))
        elif cls == "cli" and cli and why not in cli:
            bad("%s: classed cli, but neither forge.py nor the hook reads `%s`" % (k, why))
    counts = {c: sum(cls == c for cls, _ in views.SERVED.values()) for c in sorted(views.CLASSES)}
    print("%d served keys: %s; %d fail" % (len(views.SERVED), ", ".join("%d %s" % (n, c) for c, n in counts.items()),
                                          fails))
    return 1 if fails else 0


def cli_views():
    """forge.py + the SessionStart hook, as one text, or None. forge.py is found as test_roster.py
    finds it; the hook sits beside it in the seat repo (seat/tools, seat/hooks)."""
    for p in (os.environ.get("FORGE_PY"), os.path.join(HERE, "..", "..", "seat", "tools", "forge.py"),
              os.path.expanduser("~/.claude/tools/forge.py")):
        if p and os.path.exists(p):
            hook = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(p))), "hooks", "board_ready_set.py")
            return "".join(open(f, encoding="utf-8").read() for f in (p, hook) if os.path.exists(f))
    return None


if __name__ == "__main__":
    sys.exit(main())
