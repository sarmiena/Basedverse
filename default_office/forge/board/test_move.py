"""Cases for the board's card move (server.move, POST /api/move): what it writes to GitHub, where the
row lands, and what it refuses. A fake forge, no network.

Run: python3 board/test_move.py
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
os.environ["REPO_ENV"] = os.devnull  # hermetic: never the project .env
os.environ["BOARD_DB"] = os.path.join(tempfile.mkdtemp(), "board.db")
WORKFLOW = os.path.join(tempfile.mkdtemp(), "workflow.json")
with open(WORKFLOW, "w") as f:
    json.dump({"claim_label": "In Progress", "columns": [
        {"key": "next", "name": "Up next", "plan": True},
        {"key": "ready", "name": "Ready", "default": True, "pick": True},
        {"key": "doing", "name": "In progress", "label": "In Progress",
         "when": {"any": [{"label": "In Progress"}, {"claimed": True}]}},
        {"key": "qa", "name": "QA", "label": "status: qa"},
        {"key": "review", "name": "In review", "when": {"pr": True}},
        {"key": "done", "name": "Closed", "closed": True}]}, f)
os.environ.update(REPO_OWNER="acme", REPO_NAMES="app", REPO_HOME="app", REPO_RANK="", REPO_WORKFLOW=WORKFLOW)
import server  # noqa: E402

fails = 0


def case(label, fn, want):
    global fails
    try:
        got = fn()
    except Exception as e:  # a raise is a result too, by its type
        got = type(e).__name__
    ok = got == want
    fails += not ok
    print("[%s] %-62s %s" % ("ok " if ok else "FAIL", label, repr(got)[:60]))


ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
LABELS = {1: ["bug"], 2: ["bug", "In Progress"], 3: []}
WRITES = []
AGENT = ("<!-- board-agent -->\nname: amber\nsession: S-a\nlocal: \nmsgname: \nseat: linux\nhost: h\nlink: \n"
         "seen: %s\nclaims: app#3\ndoing: \nctx: \nblocked: \n" % ISO)
ROSTER = [(9, AGENT)]  # the AGENTS issue's comments: (id, body)


def issue(n):
    return {"number": n, "title": "row %d" % n, "state": "open", "comments": 0, "updated_at": ISO,
            "repository_url": "https://api.github.com/repos/acme/app", "html_url": "u",
            "labels": [{"name": x} for x in LABELS[n]], "issue_dependencies_summary": {"total_blocked_by": 0}}


def fake_api(path, method="GET", body=None):
    path = re.sub(r"[?&]per_page=\d+&page=1$", "", path)
    if path == "/repos/acme/app/issues?state=open":
        return [issue(n) for n in LABELS]
    if "labels=off-board" in path:
        return [{"number": 50, "title": "AGENTS", "state": "open", "labels": [{"name": "off-board"}],
                 "repository_url": "https://api.github.com/repos/acme/app"}]
    if path == "/repos/acme/app/issues/50/comments":
        return [{"id": cid, "body": body, "updated_at": ISO} for cid, body in ROSTER]
    if path == "/repos/acme/app/labels":
        return [{"name": x} for x in ("bug", "In Progress", "off-board")]
    m = re.fullmatch(r"/repos/acme/app/issues/(\d+)/labels/(.+)", path)
    if m and method == "DELETE":
        from urllib.parse import unquote
        n, name = int(m.group(1)), unquote(m.group(2))
        WRITES.append((n, "-" + name))
        LABELS[n] = [x for x in LABELS[n] if x != name]
        return [{"name": x} for x in LABELS[n]]
    m = re.fullmatch(r"/repos/acme/app/issues/comments/(\d+)", path)
    if m and method == "DELETE":
        WRITES.append(("roster", "-" + m.group(1)))
        return None
    m = re.fullmatch(r"/repos/acme/app/issues/(\d+)(/labels)?", path)
    if m and int(m.group(1)) in LABELS:
        n = int(m.group(1))
        if m.group(2) and method == "PUT":
            WRITES.append((n, list(body["labels"])))
            LABELS[n] = list(body["labels"])
            return [{"name": x} for x in LABELS[n]]
        return issue(n)
    if m:
        raise server.HTTPError(path, 404, "Not Found", {}, None)
    raise AssertionError("unfaked forge call %s %s" % (method, path))


server.api = fake_api
server.publish(server.snapshot(True))
col = lambda ref: next(r["col"] for r in server.SNAP["rows"] if r["id"] == ref)  # noqa: E731

case("before: plain row is Ready", lambda: col("app#1"), "ready")
case("before: the In Progress label is In progress", lambda: col("app#2"), "doing")
case("before: a claimed row is In progress", lambda: col("app#3"), "doing")
case("move to In progress sets its label, in one write",
     lambda: (server.move("app#1", "doing")["col"], WRITES[-1]), ("doing", (1, ["bug", "In Progress"])))
case("move back to Ready takes the column labels off",
     lambda: (server.move("app#2", "ready")["col"], WRITES[-1]), ("ready", (2, ["bug"])))
case("a column is found by its name", lambda: server.move("app#1", "In progress")["col"], "doing")
case("a claimed row moved to Ready stays In progress, and says so",
     lambda: server.move("app#3", "ready")["col"], "doing")
n = len(WRITES)
case("a computed column is refused", lambda: server.move("app#1", "review"), "Invalid")
case("so is the plan", lambda: server.move("app#1", "next"), "Invalid")
case("so is a column whose label the repo lacks (GitHub would coin it)", lambda: server.move("app#1", "qa"), "Invalid")
case("so is a ref that is not repo#N", lambda: server.move("1", "doing"), "BadRef")
case("refusals write nothing", lambda: len(WRITES), n)
case("an issue that is not there", lambda: server.move("app#77", "doing"), "HTTPError")
LABELS[3] = ["bug", "In Progress"]  # the claim label amber's claim put on app#3
case("the owner's x on a dead agent deletes its entry, then its claim's label",
     lambda: (server.remove_agent(9), WRITES[-2:], LABELS[3])[1:],
     ([("roster", "-9"), (3, "-In Progress")], ["bug"]))
# One session's two entries (entered under its local id S-a, then its bridge id) show as one agent: its x takes both.
ROSTER[:] = [(9, AGENT), (8, AGENT.replace("session: S-a\nlocal: ", "session: session_b\nlocal: S-a").replace("app#3", "app#1"))]
LABELS[1], LABELS[3] = ["bug", "In Progress"], ["bug", "In Progress"]
case("the owner's x on an agent its session entered twice deletes both entries, and both claims' labels",
     lambda: (server.remove_agent(9), WRITES[-4:])[1],
     [("roster", "-9"), ("roster", "-8"), (1, "-In Progress"), (3, "-In Progress")])

# This machine turned off with amber running (her SessionEnd never ran) and started again: the board's next read takes
# her entry off, then her claim's label, and serves the roster without her. wren, seen since the start, stays.
LABELS[3] = ["bug", "In Progress"]
LATER = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 120))
ROSTER[:] = [(9, AGENT), (7, AGENT.replace("amber", "wren").replace("S-a", "S-w").replace("claims: app#3", "claims: ")
                          .replace(ISO, LATER))]
server.HOST, server.booted, server.live_sessions = "h", lambda: int(time.time()) + 60, lambda: set()
server.ran_here = lambda sid: sid in ("S-a", "S-w")   # their transcripts are on this machine
names = lambda: sorted(a["name"] for a in server.SNAP["agents"])  # noqa: E731
DELETES = []


def failing(path, method="GET", body=None):
    if method == "DELETE" and path.endswith("/comments/9"):
        DELETES.append(path)
        raise server.HTTPError(path, 502, "Bad Gateway", {}, None)
    return fake_api(path, method, body)


server.api = failing
server.publish(server.snapshot(True))
case("a restart's reap whose delete fails keeps the entry, and its claim's label",
     lambda: (len(DELETES), names(), LABELS[3]), (1, ["amber", "wren"], ["bug", "In Progress"]))
server.publish(server.snapshot(True))
case("...and is not tried again at the next read", lambda: len(DELETES), 1)
server.api = fake_api
server.REAP_FAILED.clear()   # REAP_RETRY_S later
n = len(WRITES)
server.publish(server.snapshot(True))
case("a restart takes off the entry of an agent it ended, then its claim's label",
     lambda: (WRITES[n:], LABELS[3]), ([("roster", "-9"), (3, "-In Progress")], ["bug"]))
case("...and the roster is served without it", names, ["wren"])
server.HOST = "elsewhere"
n = len(WRITES)
server.publish(server.snapshot(True))
case("another machine's board takes nothing off", lambda: (WRITES[n:], names()), ([], ["amber", "wren"]))

print("\nall pass" if not fails else "\n%d FAILED" % fails)
sys.exit(1 if fails else 0)
