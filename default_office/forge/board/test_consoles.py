"""bin/consoles and the board's console routes: an agent's herdr pane, read and answered from the room.

bin/consoles' jobs are run against a fake herdr: what it reads and sends, and what it refuses before herdr hears a
thing. Then the whole way round, as it runs: bin/consoles asks the board for work (it connects out: it listens on
nothing), the board queues the room's calls for it, for the signed-in owner's page only, here and through an HTTPS
proxy's listener (another device), and refuses its routes to anyone but bin/consoles' key. Its socket call is run
against a fake herdr socket. No network beyond 127.0.0.1, no real herdr.

Run: python3 board/test_consoles.py
"""
import http.client
import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("REPO_TOKEN", "test")
os.environ["REPO_ENV"] = os.devnull
os.environ["BOARD_DB"] = os.path.join(tempfile.mkdtemp(), "board.db")
WORKFLOW = os.path.join(tempfile.mkdtemp(), "workflow.json")   # a fixed project, as test_views.py has
with open(WORKFLOW, "w") as _f:
    json.dump({"columns": [{"key": "ready", "name": "Ready", "default": True, "pick": True},
                           {"key": "done", "name": "Closed", "closed": True}]}, _f)
os.environ.update(REPO_OWNER="testorg", REPO_NAMES="", REPO_HOME="docs", REPO_RANK="severity: high",
                  REPO_WORKFLOW=WORKFLOW)
import server  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("consoles", os.path.join(HERE, "..", "..", "bin", "consoles"))
consoles = importlib.util.module_from_spec(importlib.util.spec_from_loader("consoles", _loader))
_loader.exec_module(consoles)

FAILS = 0
ORIGIN = "http://localhost:3001"
AGENTS = [{"display_agent": "lunar-koala", "pane_id": "w7:p1", "agent_status": "idle"},
          {"display_agent": "tawny-zebu", "pane_id": "w7:p5", "agent_status": "blocked"},
          {"pane_id": "w7:p3", "agent_status": "working"},   # a pane with no board handle: never reached
          # The hook's name lost (it reached herdr before herdr saw Claude start): found by the session's title.
          {"terminal_title_stripped": "mossy-wren", "pane_id": "w7:p6", "agent_status": "idle"},
          # Renamed by the owner: the hook's name, not the title.
          {"display_agent": "jade-yak", "terminal_title_stripped": "release notes", "pane_id": "w7:p2", "agent_status": "idle"},
          {"display_agent": "big-ibex", "pane_id": "w7:p9", "agent_status": "working"}]
# A long screen in colour: hundreds of KB once in JSON, as a console's first read is with its scrollback.
BIG = "\n".join("\x1b[3%dm%5d %s\x1b[0m" % (i % 8, i, "x" * 150) for i in range(2500))


def check(what, got, want):
    global FAILS
    ok = got == want
    FAILS += not ok
    print("[%s] %s%s" % ("ok" if ok else "FAIL", what, "" if ok else ": got %r, want %r" % (got, want)))


class FakeHerdr:
    """herdr's socket API as bin/consoles calls it; every call that would change a pane is recorded."""
    def __init__(self):
        self.sent = []

    def __call__(self, method, params):
        if method == "agent.list":
            return {"agents": AGENTS}
        if method == "agent.read" and params["target"] == "w7:p9":
            return {"read": {"text": BIG}}
        if method == "agent.read":
            return {"read": {"text": "\x1b[1m%s\x1b[0m screen" % params["target"]}}
        if method == "agent.prompt":
            if params["target"] == "w7:p5":
                raise consoles.HerdrError("agent_blocked", "agent is blocked")
        self.sent.append((method, params))
        return {}


def ask(port, method, path, body=None, host=None, origin=ORIGIN, token=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or "127.0.0.1:%d" % port}
    if cookie:
        h["Cookie"] = cookie
    if origin:
        h["Origin"] = origin
    if token is not None:
        h["X-Console-Token"] = token
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    return r.status, (json.loads(raw) if raw else None), dict(r.getheaders())


def main():
    fake = FakeHerdr()
    watch = consoles.Watcher(fake)
    do = lambda job: consoles.do(job, fake, watch)

    # Reading.
    st, b = do({"do": "screen", "agent": "lunar-koala"})
    check("a screen, with its colours and the agent's status", (st, {k: b[k] for k in ("agent", "status", "screen", "total", "lines")}),
          (200, {"agent": "lunar-koala", "status": "idle", "screen": "\x1b[1mw7:p1\x1b[0m screen", "total": 0, "lines": []}))
    check("an agent no herdr pane has", do({"do": "screen", "agent": "nobody"})[0], 404)
    check("a pane herdr has no name for: found by its title, the session's handle",
          [do({"do": "screen", "agent": "mossy-wren"})[1].get("screen"), consoles.pane_of("mossy-wren", fake)[0]],
          ["\x1b[1mw7:p6\x1b[0m screen", "w7:p6"])
    check("a renamed session: found by the hook's name", consoles.pane_of("jade-yak", fake)[0], "w7:p2")
    check("a name that is no handle", do({"do": "screen", "agent": "../x"})[0], 400)
    check("a since that is no line number", do({"do": "screen", "agent": "lunar-koala", "since": "-1"})[0], 400)
    check("a job it does not know", do({"do": "rm", "agent": "lunar-koala"})[0], 400)

    # Answering.
    st, _ = do({"do": "send", "agent": "lunar-koala", "text": "--help me\x1b[2~\r\nplease"})
    check("a reply goes to its pane, escapes out, newlines kept (and a leading -- is text)",
          (st, fake.sent[-1:]), (200, [("agent.prompt", {"target": "w7:p1", "text": "--help me[2~\nplease"})]))
    n = len(fake.sent)
    st, b = do({"do": "send", "agent": "tawny-zebu", "text": "yes"})
    check("an agent at a question: the reply is refused (answer with the keys)", (st, b.get("code")), (409, "agent_blocked"))
    check("an empty reply", do({"do": "send", "agent": "lunar-koala", "text": "  \n"})[0], 400)
    check("a reply too long", do({"do": "send", "agent": "lunar-koala", "text": "x" * (consoles.TEXT_MAX + 1)})[0], 400)
    check("…none of which reached herdr", len(fake.sent), n)
    st, _ = do({"do": "keys", "agent": "tawny-zebu", "keys": ["2"]})
    check("a key answers a question", (st, fake.sent[-1]), (200, ("agent.send_keys", {"target": "w7:p5", "keys": ["2"]})))
    n = len(fake.sent)
    check("a key not in KEYS (ctrl+c): refused", do({"do": "keys", "agent": "lunar-koala", "keys": ["ctrl+c"]})[0], 400)
    check("a mix with one bad key: refused whole", do({"do": "keys", "agent": "lunar-koala", "keys": ["up", "q"]})[0], 400)
    check("not a list of keys", do({"do": "keys", "agent": "lunar-koala", "keys": "esc"})[0], 400)
    check("…none of which reached herdr", len(fake.sent), n)

    # A picture pasted into the console: checked, kept for the agent, its path back (sent with the reply: the page's).
    import base64
    consoles.PASTE_DIR = tempfile.mkdtemp()
    PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
    b64 = lambda raw: base64.b64encode(raw).decode()
    n = len(fake.sent)
    st, b = do({"do": "image", "agent": "lunar-koala", "type": "image/png", "data": b64(PNG)})
    path = (b or {}).get("path", "")
    check("a picture: kept here, by the agent's name, its path back", (st, os.path.dirname(path), os.path.basename(path).startswith("lunar-koala-"), path.endswith(".png"),
          os.path.exists(path) and open(path, "rb").read() == PNG), (200, consoles.PASTE_DIR, True, True, True))
    check("…its path a line Claude Code takes for a picture's (no space in it)", " " in path, False)
    for what, job, want in (("a type it does not take (SVG)", {"type": "image/svg+xml", "data": b64(b"<svg/>")}, 400),
                            ("bytes that are not the type's", {"type": "image/png", "data": b64(b"not a png at all")}, 400),
                            ("a WebP that is a RIFF of something else", {"type": "image/webp", "data": b64(b"RIFF\0\0\0\0WAVEfmt ")}, 400),
                            ("data that is not base64", {"type": "image/png", "data": "%%%"}, 400),
                            ("too big", {"type": "image/png", "data": b64(PNG + b"\0" * consoles.IMAGE_MAX)}, 400),
                            ("for an agent herdr has not", {"type": "image/png", "data": b64(PNG), "agent": "nobody"}, 404)):
        check("a picture refused: " + what, do(dict({"do": "image", "agent": "lunar-koala"}, **job))[0], want)
    old = os.path.join(consoles.PASTE_DIR, "old.png")
    with open(old, "wb") as f:
        f.write(PNG)
    os.utime(old, (time.time() - 2 * consoles.PASTE_KEEP_S,) * 2)
    do({"do": "image", "agent": "lunar-koala", "type": "image/jpeg", "data": b64(b"\xff\xd8\xff\xe0" + b"\0" * 16)})
    check("a JPEG too; and those older than PASTE_KEEP_S gone", (os.path.exists(old), len(os.listdir(consoles.PASTE_DIR))), (False, 2))
    check("…and none of it typed anything", len(fake.sent), n)

    # A new agent for a ticket, and a ticket handed to one here: the agents' directories (AGENTS), herdr's workspaces.
    root = tempfile.mkdtemp()
    for d in ("WebApp", "WebApp-2", "WebApp-3"):
        os.makedirs(os.path.join(root, d))
    os.environ["AGENTS"] = ",".join(os.path.join(root, d) for d in ("WebApp", "WebApp-2", "WebApp-3"))
    BUSY = os.path.realpath(os.path.join(root, "WebApp"))

    consoles.SETTLE_S = 0.05   # quick, for a test
    class Hive(FakeHerdr):
        """herdr with a workspace, an agent at work in WebApp, and new tabs: a new agent named by the seat's hook (as
        `hook` says: its handle, or nothing yet), started ready or at a question (`start`: a code it raises, `fails`
        times, then none); `idle`: a shell left at its prompt in a directory, a pane with no agent."""
        def __init__(self, hook="ruby-owl", start=None, fails=99, idle=None):
            super().__init__()
            self.hook, self.start, self.fails, self.idle, self.new = hook, start, fails, idle, []

        def __call__(self, method, params):
            if method == "pane.list":
                return {"panes": [{"pane_id": "w7:pB", "workspace_id": "w7", "agent": None, "cwd": self.idle}] if self.idle else []}
            if method == "pane.process_info":
                return {"process_info": {"shell_pid": 7, "foreground_processes": [{"pid": 7, "name": "bash"}]}}
            if method == "tab.close":
                self.sent.append((method, params))
                return {}
            if method == "workspace.list":
                return {"workspaces": [{"workspace_id": "w7", "label": "WebApp"}, {"workspace_id": "w9"}]}
            if method == "agent.list":
                return {"agents": [{"display_agent": "lunar-koala", "pane_id": "w7:p1", "agent_status": "idle", "cwd": os.path.join(BUSY, "sub")}]
                        + [{"display_agent": self.hook or n, "pane_id": p, "agent_status": "idle"} for p, n in self.new]}
            if method == "tab.create":
                self.sent.append((method, params))
                return {"tab": {"tab_id": "w7:t4"}, "root_pane": {"pane_id": "w7:p8"}}
            if method == "agent.start":
                self.sent.append((method, params))
                if self.start and self.fails > 0:
                    self.fails -= 1
                    if self.start != "agent_not_ready":
                        raise consoles.HerdrError(self.start, "the pane is not at a shell prompt")
                    self.new.append((params["pane_id"], params["name"]))
                    raise consoles.HerdrError(self.start, "agent is blocked")
                self.new.append((params["pane_id"], params["name"]))
                return {}
            return super().__call__(method, params)
    hv = Hive()
    pl = consoles.do({"do": "places"}, hv, watch)
    check("places: herdr's workspaces (one with no label, by its id), and the agents' directories, the one an agent works in (its subdirectory) named",
          (pl[0], pl[1]["workspaces"], [(x["name"], x["agent"]) for x in pl[1]["dirs"]]),
          (200, [{"id": "w7", "label": "WebApp"}, {"id": "w9", "label": "w9"}], [("WebApp", "lunar-koala"), ("WebApp-2", None), ("WebApp-3", None)]))
    free = os.path.realpath(os.path.join(root, "WebApp-2"))
    job = lambda **k: dict({"do": "spawn", "dir": free, "workspace": "w7", "ref": "WebApp#193"}, **k)
    for what, j, want in (("a ticket that is no repo#N", job(ref="WebApp#193; rm -rf ~"), 400),
                          ("a directory not in AGENTS", job(dir="/tmp"), 400),
                          ("one in AGENTS by another spelling (no real path)", job(dir=os.path.join(root, "WebApp-2", "..", "WebApp-2")), 400),
                          ("a workspace herdr has not", job(workspace="w1"), 400),
                          ("a directory an agent works in already", job(dir=BUSY), 409)):
        check("spawn refused: " + what, (consoles.do(j, hv, watch)[0], hv.sent), (want, []))
    now = lambda f: f()   # its /go, here and now (a thread, as it runs)
    st, b = 200, consoles.spawn(job(), hv, now)
    check("spawn: a new tab there (not focused), Claude started in it, named by its hook, then /go for the ticket, once it is idle",
          (st, b, [m for m, _ in hv.sent], hv.sent[0][1], hv.sent[1][1]["kind"], hv.sent[2][1]),
          (200, {"agent": "ruby-owl", "pane": "w7:p8", "ready": True, "board": "testorg/docs"}, ["tab.create", "agent.start", "agent.prompt"],
           {"workspace_id": "w7", "cwd": free, "label": "WebApp-2", "focus": False}, "claude", {"target": "w7:p8", "text": "/go WebApp#193"}))
    first = hv.sent[1][1]["name"]
    check("…its first name still finds its pane, renamed (its console, opened by it, follows it)",
          (consoles.NAME.fullmatch(first) is not None, consoles.pane_of(first, hv)[0]), (True, "w7:p8"))
    class Shy(Hive):
        """herdr refusing a prompt a moment, just after Claude started: "not an active named agent"."""
        def __init__(self):
            super().__init__()
            self.refuse = 1
        def __call__(self, method, params):
            if method == "agent.prompt" and self.refuse:
                self.refuse -= 1
                raise consoles.HerdrError("agent_not_ready", "agent w7:p8 is not an active named agent")
            return super().__call__(method, params)
    hs = Shy()
    st, b = consoles.do(job(), hs, watch)
    for _ in range(60):
        if hs.sent and hs.sent[-1][0] == "agent.prompt":
            break
        time.sleep(0.1)
    check("spawn, herdr refusing its /go a moment just after it started: answered all the same, its /go given once herdr takes it",
          (st, b.get("agent"), hs.sent[-1]), (200, "ruby-owl", ("agent.prompt", {"target": "w7:p8", "text": "/go WebApp#193"})))
    later, hq = [], Hive(hook=None, start="agent_not_ready")
    b = consoles.spawn(job(), hq, later.append)
    check("spawn, Claude at a question as it starts (a folder to trust): answered at once, by its first name, its /go held",
          (b["ready"], b["agent"] == hq.sent[1][1]["name"], [m for m, _ in hq.sent], len(later)), (False, True, ["tab.create", "agent.start"], 1))
    check("…and given once it is past it", (consoles.go_when_ready("w7:p8", {"target": "w7:p8", "text": "/go WebApp#193"}, hq, wait=1), hq.sent[-1]),
          (True, ("agent.prompt", {"target": "w7:p8", "text": "/go WebApp#193"})))
    hr = Hive(start="pane_busy", fails=2)
    st, b = 200, consoles.spawn(job(), hr, now)
    check("spawn, its new tab's shell not ready for Claude at first: started again, a moment later, till it is",
          (st, b.get("ready"), [m for m, _ in hr.sent].count("agent.start"), hr.sent[-1][1].get("text")), (200, True, 3, "/go WebApp#193"))
    hx = Hive(start="pane_busy")
    st, b = consoles.do(job(), hx, watch)
    check("spawn, Claude never starting there: refused, said, and the tab it made closed again",
          (st, b.get("code"), [m for m, _ in hx.sent].count("agent.start"), hx.sent[-1]), (502, "pane_busy", consoles.TRIES, ("tab.close", {"tab_id": "w7:t4"})))
    hi = Hive(idle=free)
    st, b = 200, consoles.spawn(job(), hi, now)
    check("spawn where a start left a shell at its prompt in that directory: Claude started in it, no new tab",
          (st, b.get("pane"), [m for m, _ in hi.sent]), (200, "w7:pB", ["agent.start", "agent.prompt"]))

    # No ticket: a bare /go. Other boards: a directory's board is its seat's (REPO_BOARD); a ticket only to one on its board.
    hn = Hive()
    b = consoles.spawn(job(ref=None), hn, now)
    check("spawn with no ticket: a bare /go, and the board it works from (the home board's)",
          (hn.sent[-1][1]["text"], b["board"]), ("/go", "testorg/docs"))
    os.makedirs(os.path.join(root, "WebApp-3", ".claude"), exist_ok=True)
    with open(os.path.join(root, "WebApp-3", ".claude", "settings.local.json"), "w") as f:
        json.dump({"env": {"REPO_BOARD": "sam/Tool"}}, f)
    os.environ["REPO_BOARDS"] = "sam/Tool"
    tool = os.path.realpath(os.path.join(root, "WebApp-3"))
    check("places: each directory's board, as its seat says", [x["board"] for x in consoles.places(Hive())["dirs"]],
          ["testorg/docs", "testorg/docs", "sam/Tool"])
    for what, j in (("a home-board ticket to a directory on another board", job(dir=tool)),
                    ("another board's ticket to a home-board directory", job(ref="Tool#4"))):
        hw = Hive()
        check("spawn refused: " + what, (consoles.do(j, hw, watch)[:1], consoles.do(j, hw, watch)[1].get("code"), hw.sent), ((409,), "wrong_board", []))
    hw = Hive()
    check("…and its own board's ticket goes", (consoles.spawn(job(dir=tool, ref="tool#4"), hw, now)["board"], hw.sent[-1][1]["text"]), ("sam/Tool", "/go tool#4"))

    # A directory not set up yet: set up first (bin/init-agent), only when asked, only one settable() takes.
    home = tempfile.mkdtemp()
    for d in ("code/lib", "code/.secret/x", "code/site"):
        os.makedirs(os.path.join(home, d))
    os.makedirs(os.path.join(home, "code", "lib", ".git"))
    saved_home, os.environ["HOME"] = os.environ.get("HOME"), home
    lib = os.path.realpath(os.path.join(home, "code", "lib"))
    ran = []

    def init(cmd, **kw):   # bin/init-agent, faked: the seat on sam/Lib's board, the directory added to AGENTS
        ran.append(cmd[1:])
        d = cmd[-1]
        os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
        with open(os.path.join(d, ".claude", "settings.local.json"), "w") as f:
            json.dump({"env": {"REPO_BOARD": "sam/Lib"}}, f)
        os.environ["AGENTS"] += "," + d
        return type("R", (), {"returncode": 0, "stdout": "  ~/code/lib is a checkout of sam/Lib: a board of its own\n", "stderr": ""})()
    try:
        for what, d, code in (("a directory not in AGENTS, not asked to set it up", lib, None),
                              ("set up: one outside your home", "/tmp", "bad_dir"),
                              ("set up: one in a hidden directory", os.path.join(home, "code", ".secret", "x"), "bad_dir"),
                              ("set up: a relative path", "code/lib", "bad_dir"),
                              ("set up: the board's own checkout", consoles.ROOT, "bad_dir"),
                              ("set up: one that is not there", os.path.join(home, "nope"), "bad_dir")):
            hs2 = Hive()
            try:
                consoles.spawn(job(dir=d, ref=None, setup=code is not None), hs2, now, run=init)
                got = "spawned"
            except consoles.HerdrError as e:
                got = e.code
            check("spawn refused: " + what, (got, hs2.sent, ran), ("bad_dir", [], []))
        hs2 = Hive()
        b = consoles.spawn(job(dir="~/code/lib", ref=None, setup=True), hs2, now, run=init)
        check("spawn, set up first: bin/init-agent on its real path, then started there on the board it set, a bare /go",
              (ran, b["board"], hs2.sent[0][1]["cwd"], hs2.sent[-1][1]["text"]), ([[consoles.INIT, lib]], "sam/Lib", lib, "/go"))
        hs2, site = Hive(), os.path.realpath(os.path.join(home, "code", "site"))
        fail = lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "  cannot make it\n", "stderr": ""})()
        try:
            consoles.spawn(job(dir=site, ref=None, setup=True), hs2, now, run=fail)
            got = "spawned"
        except consoles.HerdrError as e:
            got = (e.code, "cannot make it" in str(e))
        check("spawn, bin/init-agent failing: refused, said, nothing started", (got, hs2.sent), (("setup_failed", True), []))
        check("places: no directory guessed, only the agents' own", sorted(consoles.places(Hive())), ["dirs", "workspaces"])

        # Set up… first asks what it would do (bin/init-agent --plan), changing nothing; only a directory settable() takes.
        planned = []
        def plan_run(cmd, **kw):
            planned.append(cmd[1:])
            return type("R", (), {"returncode": 0, "stdout": json.dumps({"dir": cmd[-1], "board": "sam/Site", "steps": ["add it"]}), "stderr": ""})()
        check("plan: bin/init-agent --plan on its real path, its answer as it is",
              (consoles.plan({"dir": "~/code/site"}, run=plan_run, call=Hive()), planned), ({"dir": site, "board": "sam/Site", "steps": ["add it"], "busy": None}, [[consoles.INIT, "--plan", site]]))
        class Taken(Hive):   # a session of yours (not on the board) in that directory: herdr titles its pane, no handle
            def __call__(self, method, params):
                if method == "agent.list":
                    return {"agents": [{"pane_id": "w7:p4", "agent": "claude", "display_agent": None, "terminal_title_stripped": "Lobby work", "cwd": site}]}
                return super().__call__(method, params)
        check("plan: who works there already, said before you confirm",
              consoles.plan({"dir": site}, run=plan_run, call=Taken())["busy"], '"Lobby work" (a claude session in herdr pane w7:p4)')
        try:
            consoles.spawn(job(dir=site, ref=None, setup=True), Taken(), now, run=init)
            got = "spawned"
        except consoles.HerdrError as e:
            got = (e.code, str(e))
        check("…and spawn refuses it, plainly, before setting anything up",
              (got, ran[-1][-1] == site), (("dir_busy", '"Lobby work" (a claude session in herdr pane w7:p4) already works in %s: one agent to a checkout' % site), False))
        try:
            consoles.plan({"dir": "/etc"}, run=plan_run, call=Hive())
            got = "planned"
        except consoles.HerdrError as e:
            got = e.code
        check("plan: one settable() refuses is refused before anything runs", (got, len(planned)), ("bad_dir", 2))
        refused = []
        for bad in ("~/x\nREPO_BOARDS=evil/repo", "~/x,~", "~/x\r"):
            try:
                consoles.settable(bad)
                refused.append(False)
            except consoles.HerdrError as e:
                refused.append(e.code == "bad_dir")
        check("settable: a path with a line break or a comma is refused (it would be a line, or an entry, of its own in .env's AGENTS)", refused, [True, True, True])
        try:
            consoles.plan({"dir": "~/code/site"}, run=lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"})(), call=Hive())
            got = "planned"
        except consoles.HerdrError as e:
            got = (e.code, "boom" in str(e))
        check("plan: bin/init-agent failing is said", got, ("setup_failed", True))

        # A new herdr workspace: made in that directory, its first pane where Claude starts.
        class Fresh(Hive):
            def __call__(self, method, params):
                if method in ("workspace.create", "workspace.close"):
                    self.sent.append((method, params))
                    if self.start == "pane_busy" and method == "workspace.close":
                        return {}
                    return {"workspace": {"workspace_id": "w12"}, "tab": {"tab_id": "w12:t1"}, "root_pane": {"pane_id": "w12:p1"}}
                return super().__call__(method, params)
        hf = Fresh()
        b = consoles.spawn(job(dir=lib, ref=None, workspace=None, new_workspace="  Basedverse "), hf, now)
        check("spawn in a new workspace: made, named, in that directory, Claude started in its first pane, no tab of its own",
              ([m for m, _ in hf.sent], hf.sent[0][1], b["pane"]), (["workspace.create", "agent.start", "agent.prompt"],
                                                                  {"label": "Basedverse", "cwd": lib, "focus": False}, "w12:p1"))
        for what, name in (("no name", "  "), ("a name too long", "x" * 41), ("a control character", "a\x1bb")):
            hb = Fresh()
            try:
                consoles.spawn(job(dir=lib, ref=None, new_workspace=name), hb, now)
                got = "spawned"
            except consoles.HerdrError as e:
                got = e.code
            check("spawn refused: a new workspace with " + what, (got, hb.sent), ("bad_workspace", []))
        hx2 = Fresh(start="pane_busy")
        try:
            consoles.spawn(job(dir=lib, ref=None, new_workspace="w"), hx2, now)
            got = "spawned"
        except consoles.HerdrError as e:
            got = e.code
        check("spawn in a new workspace, Claude never starting: the workspace it made closed again",
              (got, hx2.sent[-1]), ("pane_busy", ("workspace.close", {"workspace_id": "w12"})))
    finally:
        os.environ["HOME"] = saved_home
        os.environ.pop("REPO_BOARDS", None)
    n = len(fake.sent)
    st, _ = do({"do": "hand", "agent": "lunar-koala", "ref": "WebApp#193"})
    check("hand: the ticket told an agent here (HAND)", (st, fake.sent[-1]), (200, ("agent.prompt", {"target": "w7:p1", "text": consoles.HAND.format(ref="WebApp#193")})))
    check("hand refused: a ticket that is no repo#N", do({"do": "hand", "agent": "lunar-koala", "ref": "say hi"})[0], 400)
    check("hand refused: an agent at a question", do({"do": "hand", "agent": "tawny-zebu", "ref": "WebApp#193"})[0], 409)
    check("hand refused: an agent herdr has not", do({"do": "hand", "agent": "nobody", "ref": "WebApp#193"})[0], 404)
    check("…none of which reached herdr", len(fake.sent), n + 1)

    # The scrollback: what scrolls off the top of a screen is kept, colours and all.
    S = lambda *ls: "\n".join(ls)
    body = ["line %d" % i for i in range(30)]
    foot = ["", "─" * 20, "❯", "─" * 20, "  auto mode on"]
    screen_at = lambda i, extra=(): S(*(["\x1b[1m" + body[j] + "\x1b[0m" for j in range(i, i + 10)] + list(extra) + foot))
    plain = lambda t: [consoles.ESC.sub("", l).rstrip() for l in t.split("\n")]
    check("scrolled: the same top", consoles.scrolled(plain(screen_at(0)), plain(screen_at(0, ["more"]))), 0)
    check("scrolled: three lines up", consoles.scrolled(plain(screen_at(0)), plain(screen_at(3))), 3)
    check("scrolled: a redraw", consoles.scrolled(plain(screen_at(0)), plain(S(*["other %d" % i for i in range(15)]))), None)
    check("scrolled: a mostly blank top says nothing", consoles.scrolled(plain(S("", "", "x", "", "")), plain(S("", "y", "", "", ""))), 0)
    p = consoles.Pane(100.0)
    for i in (0, 0, 2, 5):
        p.see(screen_at(i))
    check("the lines that left the top, in order, colours kept", (p.total, list(p.hist)), (5, ["\x1b[1mline %d\x1b[0m" % i for i in range(5)]))
    p.see(S(*["other %d" % i for i in range(15)]))
    p.see(S(*["else %d" % i for i in range(15)]))
    check("redraws it cannot follow: one GAP, not one each", list(p.hist)[5:], [consoles.GAP])
    check("since: from a line on", p.since(3), (3, list(p.hist)[3:]))
    check("since: past the end (lines came back onto the screen): from the end", p.since(99), (p.total, []))
    d = consoles.Pane(0.0)
    d.see(screen_at(0, ["tool 1", "tool 2", "tool 3", "tool 4"]))
    d.see(screen_at(4, ["tool 1", "tool 2", "tool 3", "tool 4"]))   # the text moved up 4
    d.see(screen_at(2, ["tool 1", "tool 2"]))                       # the tool's lines shrank: down 2, two lines back
    check("a shift down hands back the lines that came onto the screen again", ([consoles.ESC.sub("", x) for x in d.hist], d.since(4)), (["line 0", "line 1"], (2, [])))
    d.see(screen_at(6))
    check("…and they go on again in the same place", [consoles.ESC.sub("", x) for x in d.hist], ["line %d" % i for i in range(6)])
    q = consoles.Pane(0.0)
    for i in range(consoles.HISTORY + 40):
        q.see(S(*(["l%d" % j for j in range(i, i + 10)] + foot)))
    check("about HISTORY lines kept; an older since gives the first kept", (consoles.HISTORY <= len(q.hist) <= consoles.HISTORY + 500, q.since(0)[0]), (True, q.base))

    # Kept across a restart: saved, and taken up by the next run, its last screen told against the one there now.
    class Screens:
        """herdr with two agents on the screen the test sets: a board handle's, and a title that is no handle."""
        def __init__(self):
            self.text = screen_at(0)

        def __call__(self, method, params):
            if method == "agent.list":
                return {"agents": [{"display_agent": "lunar-koala", "pane_id": "w7:p1", "agent_status": "idle"},
                                   {"terminal_title_stripped": "release notes", "pane_id": "w7:p2", "agent_status": "idle"}]}
            return {"read": {"text": self.text}}
    keep, h = tempfile.mkdtemp(), Screens()
    w1 = consoles.Watcher(h, keep)
    for i in (0, 3, 5):
        h.text = screen_at(i)
        w1.step()
    w1.save()
    check("saved: a board handle's scrollback, no other name's (none is a file's name)", sorted(os.listdir(keep)), ["lunar-koala.json"])
    h.text = screen_at(8)   # it went on while bin/consoles was down
    w2 = consoles.Watcher(h, keep)
    b = w2.screen("lunar-koala")
    check("the next run takes it up: since the same time, the same lines at the same numbers, and those that went by meanwhile",
          (b["watched"], b["from"], b["total"], b["lines"]), (w1.panes["lunar-koala"].watched, 0, 8, ["\x1b[1mline %d\x1b[0m" % i for i in range(8)]))
    w2.save()
    w3 = consoles.Watcher(h, keep)
    check("…and when nothing went by, nothing more", (w3.screen("lunar-koala")["total"], w3.panes["lunar-koala"].dirty), (8, False))
    h.text = S(*["other %d" % i for i in range(15)])
    w4 = consoles.Watcher(h, keep)
    check("…a redraw meanwhile it cannot follow: GAP", w4.screen("lunar-koala")["lines"][8:], [consoles.GAP])
    with open(os.path.join(keep, "lunar-koala.json"), "w") as f:
        json.dump({"watched": "noon", "base": 0, "hist": []}, f)
    check("an unreadable save: a scrollback of its own (said on stderr)", consoles.Watcher(h, keep).screen("lunar-koala")["total"], 0)
    for name, age in (("lunar-koala", 2), ("gone-gecko", 2), ("new-newt", 0)):
        with open(os.path.join(keep, name + ".json"), "w") as f:
            f.write("{}")
        os.utime(os.path.join(keep, name + ".json"), (time.time() - age * consoles.KEEP_S,) * 2)
    consoles.Watcher(h, keep).step()
    check("pruned: an old save of an agent herdr no longer has; one it has, and a new one, kept", sorted(os.listdir(keep)),
          ["lunar-koala.json", "new-newt.json"])

    # The socket call itself, against a fake herdr socket: a JSON line each way, an error as HerdrError.
    path = os.path.join(tempfile.mkdtemp(), "herdr.sock")
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen(2)
    got = []

    def answer():
        for reply in ({"id": "consoles", "result": {"agents": AGENTS}}, {"id": "consoles", "error": {"code": "invalid_key", "message": "unsupported key q"}}):
            conn, _ = srv.accept()
            with conn:
                buf = b""
                while not buf.endswith(b"\n"):
                    buf += conn.recv(4096)
                got.append(json.loads(buf))
                conn.sendall((json.dumps(reply) + "\n").encode())
    threading.Thread(target=answer, daemon=True).start()
    check("herdr(): the result", consoles.herdr("agent.list", {}, path)["agents"][0]["pane_id"], "w7:p1")
    check("herdr(): the request as herdr's API has it", got[0], {"id": "consoles", "method": "agent.list", "params": {}})
    try:
        consoles.herdr("agent.send_keys", {"target": "w7:p1", "keys": ["q"]}, path)
        check("herdr(): an error raises", "returned", "raised")
    except consoles.HerdrError as e:
        check("herdr(): an error raises, with herdr's code", e.code, "invalid_key")
    try:
        consoles.herdr("agent.list", {}, os.path.join(tempfile.mkdtemp(), "none.sock"))
        check("herdr(): no herdr", "returned", "raised")
    except consoles.HerdrError as e:
        check("herdr(): no herdr running", e.code, "no_herdr")

    # The whole way round: bin/consoles asks the board for work (with its key), the board queues the room's calls for
    # it (auth-client stood in for: test_auth.py has the sign-in itself), here and through an HTTPS proxy's listener.
    server.auth.who = lambda session=None, key=None: ({"user": "owner"} if session == "s1" else
                                                      {"key": "consoles"} if key == "kc" else {"key": "agents"} if key == "k1" else None)
    server.Consoles.HOLD, server.Consoles.GONE = 1, 0.5   # quick, for a test
    board = server.Server(("127.0.0.1", 0), server.H)
    threading.Thread(target=board.serve_forever, daemon=True).start()
    lan = server.Server(("127.0.0.1", 0), server.H)
    lan.remote = True
    threading.Thread(target=lan.serve_forever, daemon=True).start()
    bport, lport, me, there = board.server_address[1], lan.server_address[1], "session=s1", "__Host-session=s1"
    screen = "/api/console/screen?agent=lunar-koala"
    check("bin/consoles not asking: the console says so (503)", ask(bport, "GET", screen, cookie=me)[0], 503)
    stop = threading.Event()
    worker = threading.Thread(target=consoles.work, args=("http://127.0.0.1:%d" % bport, "kc", fake, watch, stop), daemon=True)
    worker.start()
    for _ in range(50):
        if server.CONSOLES.asking:
            break
        time.sleep(0.05)
    st, b, _ = ask(bport, "GET", screen, host="localhost:3001", cookie=me)
    check("a screen, the whole way round (bin/consoles asked for it)", (st, b.get("agent"), b.get("screen")), (200, "lunar-koala", "\x1b[1mw7:p1\x1b[0m screen"))
    st, b, _ = ask(bport, "GET", "/api/console/screen?agent=big-ibex", cookie=me)
    check("a screen of hundreds of KB (a long one, in colour): the whole way round, whole",
          (st, len(json.dumps(b)) > 400000, b and b.get("screen") == BIG), (200, True, True))
    st, _, _ = ask(bport, "POST", "/api/console/send", {"agent": "lunar-koala", "text": "through the board"}, cookie=me)
    check("a reply, to its pane", (st, fake.sent[-1]), (200, ("agent.prompt", {"target": "w7:p1", "text": "through the board"})))
    st, _, _ = ask(bport, "POST", "/api/console/send", {"agent": "lunar-koala", "text": "😀" * consoles.TEXT_MAX}, cookie=me)
    check("the longest reply, in the widest characters (12 bytes each in JSON): to its pane, whole",
          (st, fake.sent[-1][1]["text"] == "😀" * consoles.TEXT_MAX), (200, True))
    n = len(fake.sent)
    check("a call larger than any reply: refused as too large (413), never read cut short",
          (ask(bport, "POST", "/api/console/send", {"agent": "lunar-koala", "text": "x" * 70000}, cookie=me)[0], len(fake.sent)), (413, n))
    check("bin/consoles' own refusals come through (a key not in KEYS)", ask(bport, "POST", "/api/console/keys", {"agent": "lunar-koala", "keys": ["q"]}, cookie=me)[0], 400)
    st, _, _ = ask(bport, "POST", "/api/console/hand", {"agent": "lunar-koala", "ref": "WebApp#7"}, cookie=me)
    check("a ticket handed, the whole way round", (st, fake.sent[-1]), (200, ("agent.prompt", {"target": "w7:p1", "text": consoles.HAND.format(ref="WebApp#7")})))
    check("places, the whole way round: bin/consoles' (this fake has no workspaces)", ask(bport, "GET", "/api/console/places", cookie=me)[0], 200)
    check("a spawn, the whole way round: bin/consoles checks it (no such directory)", ask(bport, "POST", "/api/console/spawn", {"dir": "/tmp", "workspace": "w7", "ref": "WebApp#7"}, cookie=me)[0], 400)
    n = len(fake.sent)
    check("a spawn from another site's page: refused", ask(bport, "POST", "/api/console/spawn", {"dir": free, "workspace": "w7", "ref": "WebApp#7"}, cookie=me, origin="https://evil.example")[0], 403)
    check("a spawn signed out: refused", ask(bport, "POST", "/api/console/spawn", {"dir": free, "workspace": "w7", "ref": "WebApp#7"})[0], 401)
    check("…neither reached bin/consoles", len(fake.sent), n)
    check("signed out: 401", ask(bport, "GET", screen)[0], 401)
    n = len(fake.sent)
    check("a key from another site's page: refused", ask(bport, "POST", "/api/console/keys", {"agent": "lunar-koala", "keys": ["esc"]}, cookie=me, origin="https://evil.example")[0], 403)
    check("…before it reached bin/consoles", len(fake.sent), n)
    check("no token, no port for any page: the old route that handed them out is gone", ask(bport, "GET", "/api/console", cookie=me)[0], 404)

    def picture(port, raw, kind="image/png", agent="lunar-koala", cookie=me, origin=ORIGIN, key=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % port, "Content-Type": kind}
        if cookie:
            h["Cookie"] = cookie
        if origin:
            h["Origin"] = origin
        if key:
            h["Authorization"] = "Bearer " + key
        c.request("POST", "/api/console/image?agent=" + agent, body=raw, headers=h)
        r = c.getresponse()
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)
    st, b = picture(bport, PNG)
    check("a picture pasted, the whole way round: kept by bin/consoles, its path back", (st, os.path.exists((b or {}).get("path", "")) and open(b["path"], "rb").read() == PNG), (200, True))
    st, b = picture(bport, b"<svg/>", "image/svg+xml")
    check("…bin/consoles' own refusal comes through (an SVG)", st, 400)
    server.Consoles.IMAGE_MAX, was = 100, server.Consoles.IMAGE_MAX
    check("…one larger than the board takes: 413, never read", picture(bport, PNG + b"\0" * 200)[0], 413)
    server.Consoles.IMAGE_MAX = was
    check("…signed out: refused", picture(bport, PNG, cookie=None)[0], 401)
    check("…from another site's page: refused", picture(bport, PNG, origin="https://evil.example")[0], 403)
    check("…an agent's key: refused (the owner's page only)", picture(bport, PNG, cookie=None, origin=None, key="k1")[0], 403)

    def keyed(port, method, path, key, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request(method, path, body=None if body is None else json.dumps(body).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        return c.getresponse().status
    check("an agent's key: not the owner's console", keyed(bport, "GET", screen, "k1"), 403)
    check("bin/consoles' key: not a page's console either", keyed(bport, "GET", screen, "kc"), 403)
    check("an agent's key: no spawning agents", keyed(bport, "POST", "/api/console/spawn", "k1", {"dir": free, "workspace": "w7", "ref": "WebApp#7"}), 403)
    check("its work, to an agent's key: refused", keyed(bport, "GET", "/api/console/next", "k1"), 403)
    check("…to the owner's session: refused", ask(bport, "GET", "/api/console/next", cookie=me)[0], 403)
    check("an answer from an agent's key: refused", keyed(bport, "POST", "/api/console/answer", "k1", {"id": 1, "status": 200, "body": {}}), 403)
    big = {"id": 1, "status": 200, "body": {"screen": "x" * (3 << 19)}}   # 1.5 MB: a screen with a long conversation's scrollback
    check("bin/consoles' answer of more than a megabyte: taken (its own limit, ANSWER_MAX)", keyed(bport, "POST", "/api/console/answer", "kc", big), 200)
    def declared(port, path, key, n):   # only its headers, saying n bytes: the board answers before reading any
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.putrequest("POST", path); c.putheader("Authorization", "Bearer " + key); c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(n)); c.endheaders()
        return c.getresponse().status
    check("…one past ANSWER_MAX: 413, never read", declared(bport, "/api/console/answer", "kc", server.Consoles.ANSWER_MAX + 1), 413)
    check("…and what the room asks stays under ASK_MAX: 413", ask(bport, "POST", "/api/console/send", {"agent": "tawny-zebu", "text": "x" * (65 << 10)}, cookie=me)[0], 413)

    # From another device: the proxy's listener, its HTTPS cookie, its origin (https:// and the name it was asked by).
    check("from another device: the console, through the proxy", ask(lport, "GET", screen, host="192.168.1.20:3443", origin=None, cookie=there)[0], 200)
    check("…its session in the __Host- cookie only (the HTTP one is this machine's)", ask(lport, "GET", screen, host="192.168.1.20:3443", cookie=me)[0], 401)
    st, _, _ = ask(lport, "POST", "/api/console/keys", {"agent": "tawny-zebu", "keys": ["1"]}, host="192.168.1.20:3443", cookie=there, origin="https://192.168.1.20:3443")
    check("…keys from its page there", (st, fake.sent[-1]), (200, ("agent.send_keys", {"target": "w7:p5", "keys": ["1"]})))
    check("…but not from another origin", ask(lport, "POST", "/api/console/keys", {"agent": "tawny-zebu", "keys": ["1"]}, host="192.168.1.20:3443", cookie=there)[0], 403)
    check("…and Visit's mode there too (its page connects through the board now: engine/INTEGRATING.md)", ask(lport, "GET", "/visit/mode", host="192.168.1.20:3443", cookie=there)[0], 200)

    stop.set()
    worker.join(5)
    time.sleep(0.6)
    check("bin/consoles gone: the console says so again", ask(bport, "GET", screen, cookie=me)[0], 503)

    # bin/start replaces bin/consoles while an ask of the one it stopped is still held here: a call must not go to it.
    a2, b2 = socket.socketpair()
    check("peer_gone: an open connection is not", server.peer_gone(a2), False)
    b2.close()
    check("…one whose other end has closed is", server.peer_gone(a2), True)
    a2.close()
    stale = socket.create_connection(("127.0.0.1", bport))
    stale.sendall(b"GET /api/console/next HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer kc\r\n\r\n")
    time.sleep(0.3)
    stale.close()   # the stopped one's ask, held here, its process gone
    got = {}
    asking = threading.Thread(target=lambda: got.update(r=ask(bport, "GET", "/api/console/places", cookie=me)), daemon=True)
    asking.start()
    time.sleep(0.5)   # the call is handed to the dead ask meanwhile, and must come back
    stop2 = threading.Event()
    worker2 = threading.Thread(target=consoles.work, args=("http://127.0.0.1:%d" % bport, "kc", fake, watch, stop2), daemon=True)
    worker2.start()   # the new bin/consoles
    asking.join(12)
    check("a call handed to a stopped bin/consoles' ask goes back, to the one running now", (got.get("r") or [None])[0], 200)
    stop2.set()
    worker2.join(5)
    lan.shutdown()
    board.shutdown()

    print("%d fail" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
