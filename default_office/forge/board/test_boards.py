"""Cases for the other boards (REPO_BOARDS): one per GitHub repo the agents work in beside the home board's. Which ones
the .env names, what /api/board serves for each (?board=), where each one's plan is, and that a board which cannot be
read never stalls the home board. A fake forge, no network.

Run: python3 board/test_boards.py
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
os.environ.pop("REPO_BOARDS", None)   # from the .env below, read again at each poll
ENV = os.path.join(tempfile.mkdtemp(), ".env")
with open(ENV, "w") as f:
    f.write("REPO_BOARDS=sam/Tool\n")
os.environ["REPO_ENV"] = ENV
os.environ["BOARD_DB"] = os.path.join(tempfile.mkdtemp(), "board.db")
WORKFLOW = os.path.join(tempfile.mkdtemp(), "workflow.json")
with open(WORKFLOW, "w") as f:
    json.dump({"claim_label": "status: doing", "columns": [
        {"key": "next", "name": "Up next", "plan": True},
        {"key": "ready", "name": "Ready", "default": True, "pick": True},
        {"key": "doing", "name": "In progress", "label": "status: doing",
         "when": {"any": [{"label": "status: doing"}, {"claimed": True}]}},
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
    print("[%s] %-70s %s" % ("ok " if ok else "FAIL", label, repr(got)[:60]))


ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
# Each repo's open issues: number -> labels. Tool is sam's (another owner), on a board of its own.
OPEN = {("acme", "app"): {1: [], 2: []}, ("sam", "Tool"): {1: [], 5: []}}
DOWN = set()   # repos whose listing fails
WRITES = []
AGENTS = [(9, "<!-- board-agent -->\nname: amber\nsession: S-a\nlocal: \nseat: linux\nhost: h\nseen: %s\nclaims: app#2\n" % ISO),
          (10, "<!-- board-agent -->\nname: brisk\nsession: S-b\nlocal: \nseat: linux\nhost: h\nseen: %s\nclaims: Tool#5\n"
               "board: sam/Tool\n" % ISO)]
OFF = [{"number": 60, "title": "RESUME — the next session's critical path", "body": "1) app#1 first", "labels": [{"name": "off-board"}]},
       {"number": 61, "title": "RESUME [sam/Tool] — the next session's critical path", "body": "1) Tool#1 tool's first",
        "labels": [{"name": "off-board"}]}]



class FakeRoster:
    """The board's roster table (rosterdb.Store), faked from AGENTS: (id, body) rows."""
    def rows(self):
        return [{"id": cid, "body": b, "updated_at": ISO} for cid, b in AGENTS]

    def delete(self, rid):
        WRITES.append(("roster", "-%d" % rid))


def issue(owner, repo, n):
    return {"number": n, "title": "%s %d" % (repo, n), "state": "open", "comments": 0, "updated_at": ISO,
            "repository_url": "https://api.github.com/repos/%s/%s" % (owner, repo), "html_url": "u",
            "labels": [{"name": x} for x in OPEN[(owner, repo)].get(n, [])],
            "issue_dependencies_summary": {"total_blocked_by": 0}}


def fake_api(path, method="GET", body=None):
    path = re.sub(r"[?&]per_page=\d+&page=1$", "", path)
    m = re.fullmatch(r"/repos/(\w+)/(\w+)/issues\?state=open", path)
    if m and (m.group(1), m.group(2)) in OPEN:
        if m.group(2) in DOWN:
            raise server.HTTPError(path, 502, "Bad Gateway", {}, None)
        return [issue(m.group(1), m.group(2), n) for n in OPEN[(m.group(1), m.group(2))]]
    if path == "/repos/acme/app/issues?state=open&labels=off-board":
        return [dict(i, state="open") for i in OFF]
    if re.fullmatch(r"/repos/(\w+)/(\w+)/labels", path):
        return [{"name": x} for x in ("status: doing", "off-board")]
    if path == "/repos/acme/app/issues" and method == "POST":   # a new plan issue
        WRITES.append(("new", body["title"], body["body"], body["labels"]))
        OFF.append({"number": 62, "title": body["title"], "body": body["body"], "labels": [{"name": "off-board"}]})
        return {"number": 62, "body": body["body"]}
    m = re.fullmatch(r"/repos/acme/app/issues/(6\d)", path)
    if m and method == "PATCH":
        WRITES.append(("plan", int(m.group(1)), body["body"]))
        next(i for i in OFF if i["number"] == int(m.group(1)))["body"] = body["body"]
        return {}
    m = re.fullmatch(r"/repos/(\w+)/(\w+)/issues/(\d+)(/labels)?", path)
    if m and (m.group(1), m.group(2)) in OPEN:
        key, n = (m.group(1), m.group(2)), int(m.group(3))
        if m.group(4) and method == "PUT":
            WRITES.append(("labels", m.group(1), m.group(2), n, list(body["labels"])))
            OPEN[key][n] = list(body["labels"])
            return [{"name": x} for x in OPEN[key][n]]
        if n in OPEN[key]:
            return issue(m.group(1), m.group(2), n)
        return dict(issue(m.group(1), m.group(2), 1), number=n, state="closed")
    raise AssertionError("unfaked forge call %s %s" % (method, path))


server.api = fake_api
server.ROSTER = FakeRoster()
ids = lambda rows: sorted(r["id"] for r in rows)  # noqa: E731

# Which boards the .env names.
case("the .env's REPO_BOARDS, read again: another owner's repo is a board",
     lambda: [(k, o, r) for k, o, r in server.boards_named()[0]], [("sam/Tool", "sam", "Tool")])
with open(ENV, "w") as f:
    f.write("REPO_BOARDS=sam/Tool, acme/app, bob/APP, nope\n")
case("one already on the home board is left out quietly; a clashing repo name and a bad name are said",
     lambda: ([k for k, _, _ in server.boards_named()[0]], server.boards_named()[1]),
     (["sam/Tool"], ["REPO_BOARDS: bob/APP left out: another board has a repo named APP (repo#N would mean either)",
                     "REPO_BOARDS: 'nope' is not owner/repo"]))
with open(ENV, "w") as f:
    f.write("REPO_BOARDS=sam/Tool\n")

server.publish(server.snapshot(True))
home = server.board_view(["S-a"])
case("the home board is served as ever: its rows only, its plan", lambda: (ids(home["rows"]), [s["ref"] for s in home["plan"]["steps"]]),
     (["app#1", "app#2"], ["app#1"]))
case("...with the boards listed, home first", lambda: [(b["key"], b["name"], b["repos"], b["url"]) for b in home["boards"]],
     [("acme/app", "app", ["app"], "https://github.com/acme/app"), ("sam/Tool", "Tool", ["Tool"], "https://github.com/sam/Tool")])
case("...and which one it is", lambda: home["board"], "acme/app")
case("an agent with no board is the home board's; one with its own keeps it",
     lambda: sorted((a["name"], a["board"]) for a in home["agents"]), [("amber", "acme/app"), ("brisk", "sam/Tool")])
tool = server.board_view(["S-b"], "sam/tool")
case("?board= another (any case): its rows, its own RESUME issue as its plan",
     lambda: (tool["board"], ids(tool["rows"]), [s["ref"] for s in tool["plan"]["steps"]], tool["plan"]["num"]),
     ("sam/Tool", ["Tool#1", "Tool#5"], ["Tool#1"], 61))
case("its claimed row is In progress, by the one roster", lambda: next(r["col"] for r in tool["rows"] if r["id"] == "Tool#5"), "doing")
case("its link is under its own owner", lambda: tool["boards"][1]["url"], "https://github.com/sam/Tool")
every = server.board_view([], "*")
case("?board=* serves every board's rows, and each board's plan", lambda: (ids(every["rows"]), sorted(every["plans"])),
     (["Tool#1", "Tool#5", "app#1", "app#2"], ["acme/app", "sam/Tool"]))
case("a board there is not is refused", lambda: server.board_view([], "else/where"), "NoBoard")

# A ticket is handed only to an agent on its board (the room's Give to an agent; the server checks it too).
case("a ticket handed to an agent on its board goes", lambda: [server.hand_refused("brisk", "Tool#5"), server.hand_refused("amber", "app#1")],
     [None, None])
case("...to one on another board is refused, saying why", lambda: server.hand_refused("amber", "Tool#5"),
     "Tool#5 is on the sam/Tool board, and amber works from the acme/app board")
case("an agent the roster lacks, or no repo#N, is bin/consoles' to refuse", lambda: [server.hand_refused("nobody", "Tool#5"),
     server.hand_refused("amber", "x")], [None, None])

# A row closes on the other board: gone there, not on the home board.
del OPEN[("sam", "Tool")][1]
server.publish(server.snapshot(False))
case("a row that left another board is that board's gone",
     lambda: ([g["id"] for g in server.board_view([], "sam/Tool")["gone"]], server.board_view([])["gone"]), (["Tool#1"], []))

# A board that cannot be read keeps its last good read, says why, and the home board goes on.
DOWN.add("Tool")
OPEN[("acme", "app")][3] = []
v = server.SNAP["ver"]
server.publish(server.snapshot(False))
b = server.board_view([])
case("the home board moves on while another cannot be read", lambda: ids(b["rows"]), ["app#1", "app#2", "app#3"])
case("...the other keeps its rows, with why", lambda: (ids(server.board_view([], "sam/Tool")["rows"]), "502" in b["boards"][1]["error"]),
     (["Tool#5"], True))
DOWN.clear()
server.publish(server.snapshot(False))
case("...and once it reads again, the error goes", lambda: "error" in server.board_view([])["boards"][1], False)

# A card moved on the other board is written to its own owner's repo.
case("move on another board writes its own repo's labels, and finds the row there",
     lambda: (server.move("Tool#5", "ready")["col"], WRITES[-1]), ("doing", ("labels", "sam", "Tool", 5, [])))

# Plans: each board's own, the home board's untouched by another's.
p = server.board_view([], "sam/Tool")["plan"]
case("another board's plan rewritten from the kanban: its own issue, and the home plan unchanged",
     lambda: (server.save_plan(p["hash"], [{"ref": "Tool#5", "text": "next"}], "sam/Tool")["num"], WRITES[-1][:2],
              [s["ref"] for s in server.board_view([])["plan"]["steps"]]), (61, ("plan", 61), ["app#1"]))
OFF[:] = [i for i in OFF if i["number"] != 61]
server.publish(server.snapshot(False))
case("a board with no plan yet serves none", lambda: server.board_view([], "sam/Tool")["plan"], None)
case("...and its first plan written makes its RESUME issue in the home repo, tagged with the board",
     lambda: (server.save_plan(None, [{"ref": "Tool#5", "text": "go"}], "sam/Tool")["num"], WRITES[-1]),
     (62, ("new", "RESUME [sam/Tool] — the next session's critical path", "1) Tool#5 go", ["off-board"])))
case("the home board's plan still needs its issue first (forge.py makes it)",
     lambda: (OFF.__setitem__(slice(None), [i for i in OFF if i["number"] != 60]), server.publish(server.snapshot(False)),
              server.save_plan(None, [{"ref": "app#1"}]))[2], "LookupError")
case("a plan for a board there is not is refused", lambda: server.save_plan(None, [], "else/where"), "LookupError")

# A board taken out of REPO_BOARDS is gone at the next poll; nothing is restarted.
with open(ENV, "w") as f:
    f.write("REPO_BOARDS=\n")
server.publish(server.snapshot(False))
case("a board no longer named is no longer served", lambda: [b["key"] for b in server.board_view([])["boards"]], ["acme/app"])

print("\nall pass" if not fails else "\n%d FAILED" % fails)
sys.exit(1 if fails else 0)
