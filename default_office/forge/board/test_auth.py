"""Cases for the board's sign-in (auth.py and server.py's gate), against a stand-in for auth-client (basedverse's
auth/SPEC.md 3): every route but the sign-in page's asks it first; signed out, a page goes to /login and anything
else gets 401; a write signed in with the cookie comes from the board's own page; an agent's key gets in, but not
to the owner's tokens; auth-client not answering refuses (503), never lets in.

Run: python3 board/test_auth.py
"""
import base64
import hashlib
import http.client
import socket
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
WORKFLOW = os.path.join(tempfile.mkdtemp(), "workflow.json")   # a fixed project, as test_views.py has
with open(WORKFLOW, "w") as _f:
    json.dump({"columns": [{"key": "ready", "name": "Ready", "default": True, "pick": True},
                           {"key": "done", "name": "Closed", "closed": True}]}, _f)
os.environ.update(REPO_OWNER="testorg", REPO_NAMES="", REPO_HOME="docs", REPO_RANK="severity: high",
                  REPO_WORKFLOW=WORKFLOW, REPO_TOKEN="test", BOARD_DB=os.path.join(tempfile.mkdtemp(), "board.db"))
os.environ["REPO_ENV"] = os.devnull   # hermetic: never the project .env
for _k in ("LLM_KEY", "LLM_URL", "LLM_MODEL"):   # and no model: a board set up from scratch
    os.environ.pop(_k, None)
import server  # noqa: E402

FAILS = 0
TOKEN = "t" * 64
LIVE = {"S1"}   # the stand-in's live sessions
SEEN = []       # what it was asked


def check(what, got, want):
    global FAILS
    ok = got == want
    FAILS += not ok
    print("[ok] %s" % what if ok else "[FAIL] %s\n       got  %r\n       want %r" % (what, got, want))


class AuthClient(BaseHTTPRequestHandler):
    """auth-client, as the board sees it: robin signs in with the right password; `patient` is told to wait."""
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        SEEN.append((self.path, b))
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.answer(401, {"error": "service_token"})
        if self.path == "/v1/login":
            if b.get("user") == "robin" and b.get("password") == "the right password":
                LIVE.add("S2")
                return self.answer(200, {"session": "S2", "user": "robin", "expires": int(time.time()) + 3600})
            return self.answer(429, {"error": "wait", "retry_after": 4}) if b.get("user") == "patient" else self.answer(401, {"error": "wrong_login"})
        if self.path == "/v1/check":
            if b.get("session") in LIVE:
                return self.answer(200, {"user": "robin"})
            keys = {"K1": "agents", "KE": "engine", "KC": "consoles", "KX": "someone"}   # the agents', the engine's, bin/consoles', one of no known name
            return self.answer(200, {"key": keys[b["key"]]}) if b.get("key") in keys else self.answer(401, {"error": "no_session"})
        if self.path == "/v1/logout":
            LIVE.discard(b.get("session"))
            return self.answer(200, {})
        if self.path == "/v1/visit":
            return self.answer(200, {"port": 7401, "token": "v" * 64}) if b.get("session") in LIVE else self.answer(401, {"error": "no_session"})
        self.answer(404, {})

    def answer(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


class VisitClient(BaseHTTPRequestHandler):
    """visit-client, as the board sees it (basedverse's engine/SPEC.md 6.3): its library, and a page connection that
    takes a relay's preface first, then the page's hello, and says ready."""
    def do_GET(self):
        if self.path == "/visit.js":
            raw = b"// visit.js, the stand-in's\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        if self.path != "/ws":
            return self.send_error(404)
        origin = self.headers.get("Origin", "")
        if origin not in ("http://localhost:3001", "https://192.168.1.20:3443"):   # --origins
            return self.send_error(403, "not from this workroom's page")
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + server.WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        try:
            got = []
            for _ in range(2):   # the preface, then the hello
                op, data = ws_recv(self.connection)
                got.append(json.loads(data))
            VISITS.append({"origin": origin, "got": got})
            self.connection.sendall(server.ws_frame(json.dumps({"type": "ready", "role": got[0].get("role"), "sim": got[1].get("sim")}).encode(), mask=False))
            op, data = ws_recv(self.connection)   # one more from the page, echoed back as it is: bytes through, unread
            self.connection.sendall(server.ws_frame(data, opcode=op, mask=False))
            time.sleep(0.5)
        except (ConnectionError, OSError):
            pass   # the board closed it (a second connection replaced it, or the test is done)
        self.close_connection = True

    def log_message(self, *a):
        pass


VISITS = []   # what the stand-in visit-client saw on each page connection


def ws_recv(sock):
    """One WebSocket frame from a socket: (opcode, payload), masked or not, payloads under 64 KiB."""
    def exactly(n):
        b = b""
        while len(b) < n:
            c = sock.recv(n - len(b))
            if not c:
                raise ConnectionError("closed")
            b += c
        return b
    h = exactly(2)
    op, masked, n = h[0] & 0x0F, h[1] & 0x80, h[1] & 0x7F
    if n == 126:
        n = int.from_bytes(exactly(2), "big")
    key = exactly(4) if masked else None
    data = exactly(n)
    if key:
        data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
    return op, data


def ws_open(port, cookie=None, origin="http://localhost:3001", host="localhost:3001", jar="session", key=None):
    """A page's WebSocket to the board's /visit/ws: the socket once the upgrade is answered, else (status, body)."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    h = ["GET /visit/ws HTTP/1.1", "Host: " + host, "Upgrade: websocket", "Connection: Upgrade", "Sec-WebSocket-Version: 13",
         "Sec-WebSocket-Key: " + base64.b64encode(os.urandom(16)).decode()]
    if origin:
        h.append("Origin: " + origin)
    if cookie:
        h.append("Cookie: %s=%s" % (jar, cookie))
    if key:
        h.append("Authorization: Bearer " + key)
    sock.sendall(("\r\n".join(h) + "\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        c = sock.recv(4096)
        if not c:
            break
        head += c
    status = int(head[9:12] or b"0")
    if status != 101:
        rest = head.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in head else b""
        sock.close()
        return status, rest
    return sock


def ask(port, method, path, cookie=None, origin="http://localhost:3001", body=None, key=None, form=None, host="localhost:3001", headers=None, jar="session"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host, **(headers or {})}
    if cookie:
        h["Cookie"] = "%s=%s" % (jar, cookie)
    if origin:
        h["Origin"] = origin
    if key:
        h["Authorization"] = "Bearer " + key
    data = None
    if form is not None:
        data = "&".join("%s=%s" % (k, __import__("urllib.parse").parse.quote(v, safe="")) for k, v in form.items()).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    return r.status, dict(r.getheaders()), raw


def main():
    stand_in = ThreadingHTTPServer(("127.0.0.1", 0), AuthClient)
    threading.Thread(target=stand_in.serve_forever, daemon=True).start()
    tfile = os.path.join(tempfile.mkdtemp(), "token")
    with open(tfile, "w") as f:
        f.write(TOKEN + "\n")
    server.auth.PORT, server.auth.TOKEN_FILE = stand_in.server_address[1], tfile
    # Visit through the board: a stand-in visit-client on this machine, the user token and the engine's mark in a
    # .visit of the test's own.
    visit = ThreadingHTTPServer(("127.0.0.1", 0), VisitClient)
    threading.Thread(target=visit.serve_forever, daemon=True).start()
    vdir = tempfile.mkdtemp()
    with open(os.path.join(vdir, "user-token"), "w") as f:
        f.write("u" * 64 + "\n")
    server.VISIT_HOST, server.VISIT_PORT, server.VISIT_DIR = "127.0.0.1", visit.server_address[1], vdir
    server.VISIT_USER_TOKEN_FILE, server.ENGINE_FILE = os.path.join(vdir, "user-token"), os.path.join(vdir, "engine.json")
    board = server.Server(("127.0.0.1", 0), server.H)
    threading.Thread(target=board.serve_forever, daemon=True).start()
    port = board.server_address[1]

    st, hd, raw = ask(port, "GET", "/login")
    check("GET /login: the sign-in page, to anyone", (st, b'action="/login"' in raw, b'autocomplete="current-password"' in raw), (200, True, True))
    check("signed out, the kanban: to the sign-in page, and back after", (ask(port, "GET", "/")[0], ask(port, "GET", "/")[1].get("Location")), (303, "/login?next=%2F"))
    check("the room, with its query", ask(port, "GET", "/room?env=cave")[1].get("Location"), "/login?next=%2Froom%3Fenv%3Dcave")
    check("its scripts too: nothing is public but the sign-in page", (ask(port, "GET", "/common.js")[0], ask(port, "GET", "/party.js")[0]), (303, 303))
    check("the tarot reader's cards too", ask(port, "GET", "/tarot/m16.jpg")[0], 303)
    LIVE.add("S9")   # a session of its own (the board keeps a check a while: S1's must be asked afresh at the end)
    st, hd, raw = ask(port, "GET", "/tarot/m16.jpg", cookie="S9")
    check("a card signed in: the picture (a JPEG), kept a day", (st, hd.get("Content-Type"), raw[:2], hd.get("Cache-Control")), (200, "image/jpeg", b"\xff\xd8", "private, max-age=86400"))
    check("…every card of the deck", all(ask(port, "GET", "/tarot/%s.jpg" % k, cookie="S9")[0] == 200 for k in [d["key"] for d in server.tarot.DECK]), True)
    st, hd, raw = ask(port, "GET", "/party.js", cookie="S9")
    check("your agents as a party (party.js), signed in: the script both pages load", (st, hd.get("Content-Type"), b"window.Party = " in raw), (200, "text/javascript; charset=utf-8", True))
    st, hd, raw = ask(port, "GET", "/tarot/meanings.json", cookie="S9")
    check("…and Waite's meanings, one for every card", (st, hd.get("Content-Type", "").split(";")[0], sorted(json.loads(raw)["cards"]) == sorted(d["key"] for d in server.tarot.DECK)), (200, "application/json", True))
    check("…and nothing else by that road", [ask(port, "GET", path, cookie="S9")[0] for path in ("/tarot/m22.jpg", "/tarot/w15.jpg", "/tarot/SOURCES.md", "/tarot/meanings.py", "/tarot/../server.py", "/tarot/m16.jpg.bak")],
          [404, 404, 404, 404, 404, 404])
    st, hd, raw = ask(port, "GET", "/api/board")
    check("an API route signed out: 401, marked for the page's sign-in", (st, hd.get("X-Sign-In"), json.loads(raw)), (401, "required", {"error": "sign in"}))
    check("a write signed out: 401", ask(port, "POST", "/api/move", body={})[0], 401)

    st, hd, _ = ask(port, "POST", "/login", form={"user": "robin", "password": "nope", "next": "/room"})
    check("a wrong password: back to the page, saying so (and where it was going)", (st, hd.get("Location")), (303, "/login?e=wrong&next=%2Froom"))
    check("told to wait: the page says so", ask(port, "POST", "/login", form={"user": "patient", "password": "x"})[1].get("Location"), "/login?e=wait&next=%2F")
    check("what auth-client was asked: the name, the password, where from", {k: v for k, v in SEEN[-1][1].items() if k != "client"} | {"from": SEEN[-1][1]["client"]["address"]},
          {"user": "patient", "password": "x", "from": "127.0.0.1"})
    st, hd, _ = ask(port, "POST", "/login", form={"user": "robin", "password": "the right password", "next": "/room"})
    cookie = hd.get("Set-Cookie", "")
    check("the right one: on to where it was going, with the session cookie", (st, hd.get("Location"), cookie.split(";")[0]), (303, "/room", "session=S2"))
    check("...HttpOnly, SameSite=Strict, the whole site, for as long as the session", [p.strip() for p in cookie.split(";")[1:4]] + [3590 <= int(cookie.split("Max-Age=")[1]) <= 3600],
          ["HttpOnly", "SameSite=Strict", "Path=/", True])
    for nxt in ("//evil.example/x", "/\\evil.example", "https://evil.example/"):
        check("next %r: not followed, / instead" % nxt, ask(port, "POST", "/login", form={"user": "robin", "password": "the right password", "next": nxt})[1].get("Location"), "/")

    check("signed in: the kanban", ask(port, "GET", "/", cookie="S2")[0], 200)
    check("signed in: the room", ask(port, "GET", "/room", cookie="S2")[0], 200)
    check("a session that ended: the sign-in page, saying so", ask(port, "GET", "/room", cookie="GONE")[1].get("Location"), "/login?next=%2Froom&e=expired")
    check("a write from another site's page, signed in or not: refused", ask(port, "POST", "/api/move", cookie="S2", origin="https://evil.example", body={})[0], 403)
    check("...and with no Origin at all", ask(port, "POST", "/api/move", cookie="S2", origin=None, body={})[0], 403)
    check("from the board's own page: on to the route (which refuses the empty move itself)", ask(port, "POST", "/api/move", cookie="S2", body={})[0] not in (401, 403), True)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"env": "cave"})
    check("the room's environment, set from the page: the room's setting, the board keeps it", (st, json.loads(raw).get("room", {}).get("env")), (200, "cave"))
    check("...every page's board says it (the engine's builds by it)", json.loads(ask(port, "GET", "/api/board", cookie="S2")[2]).get("room", {}).get("env"), "cave")
    check("...kept for a restart (the file beside the messages)", json.load(open(server.ROOM_FILE)).get("env"), "cave")
    check("...a name that is no key: refused", ask(port, "POST", "/api/room", cookie="S2", body={"env": "../x"})[0], 400)
    check("...signed out: refused", ask(port, "POST", "/api/room", body={"env": "office"})[0], 401)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"dark": True})
    check("...the owner's theme too (dark), the environment kept", (st, json.loads(raw).get("room")), (200, {"env": "cave", "dark": True, "look": None, "music": None, "board": None, "mix": None, "guest": None, "bino": None, "acts": None, "teleport": None}))
    check("...dark is true or false", ask(port, "POST", "/api/room", cookie="S2", body={"dark": "yes"})[0], 400)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"board": "sam/Tool"})
    check("...the board the wall shows (its switch), kept", (st, json.loads(raw).get("room", {}).get("board"), json.load(open(server.ROOM_FILE)).get("board")), (200, "sam/Tool", "sam/Tool"))
    check("...a board that is no owner/repo: refused", ask(port, "POST", "/api/room", cookie="S2", body={"board": "../x"})[0], 400)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"board": ""})
    check("...'' is the home board again", (st, json.loads(raw).get("room", {}).get("board")), (200, None))
    check("...nothing given: refused", ask(port, "POST", "/api/room", cookie="S2", body={})[0], 400)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"look": {"skin": 3, "shirt": "#123456"}})
    check("...the owner's look too (the room's page draws them with it)", (st, json.loads(raw).get("room", {}).get("look")), (200, {"skin": 3, "shirt": "#123456"}))
    check("...a look is a JSON object", ask(port, "POST", "/api/room", cookie="S2", body={"look": "blue"})[0], 400)
    check("...and a small one", ask(port, "POST", "/api/room", cookie="S2", body={"look": {"x": "y" * 3000}})[0], 400)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"music": False})
    check("...the music muted (the pool club's DJ stops), the rest kept", (st, json.loads(raw).get("room", {}).get("music"), json.loads(raw).get("room", {}).get("env")), (200, False, "cave"))
    check("...every page's board says it", json.loads(ask(port, "GET", "/api/board", cookie="S2")[2]).get("room", {}).get("music"), False)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"guest": True})
    check("...the owner As a visitor (room.html's switch), every page's board saying it", (st, json.loads(raw).get("room", {}).get("guest"),
          json.loads(ask(port, "GET", "/api/board", cookie="S2")[2]).get("room", {}).get("guest")), (200, True, True))
    check("...guest is true or false", ask(port, "POST", "/api/room", cookie="S2", body={"guest": "yes"})[0], 400)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"guest": False})
    check("...and off again", (st, json.loads(raw).get("room", {}).get("guest")), (200, None))
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"bino": True})
    check("...the owner's binoculars up (their figure holds them), and down", (st, json.loads(raw).get("room", {}).get("bino"),
          json.loads(ask(port, "POST", "/api/room", cookie="S2", body={"bino": False})[2]).get("room", {}).get("bino")), (200, True, None))
    check("...bino is true or false", ask(port, "POST", "/api/room", cookie="S2", body={"bino": 1})[0], 400)
    rule = {"worlds": ["Yard"], "keys": [{"key": "a" * 64, "name": "Ann"}], "anyone": False}
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"teleport": rule})
    check("...who may teleport in (the popup's Who can come): kept as given", (st, json.loads(raw).get("room", {}).get("teleport")), (200, rule))
    check("...its worlds \"*\" (all of yours), or names; its people by an office's key; anyone true or false",
          [ask(port, "POST", "/api/room", cookie="S2", body={"teleport": t})[0] for t in ({"worlds": "*"}, {"worlds": "Yard"}, {"keys": [{"key": "nope"}]}, {"anyone": 1}, {"who": 1})],
          [200, 400, 400, 400, 400])
    v0 = server.SNAP["ver"]
    st, _, raw = ask(port, "POST", "/api/room", key="KE", origin=None, body={"acts": {"s-1": "doze", "s-2": "chat:indigo-vole", "s-3": "pool"}})
    check("the room's page (the engine's key): what its agents are up to, served", (st, json.loads(raw).get("room", {}).get("acts")),
          (200, {"s-1": "doze", "s-2": "chat:indigo-vole", "s-3": "pool"}))
    v1 = server.SNAP["ver"]
    ask(port, "POST", "/api/room", key="KE", origin=None, body={"acts": {"s-1": "doze", "s-2": "chat:indigo-vole", "s-3": "pool"}})
    check("...the pages woken for a change, not for the same again", (v1 > v0, server.SNAP["ver"] == v1), (True, True))
    check("...the settings untouched by it", json.loads(raw)["room"]["env"], "cave")
    check("...an act of no known kind, a mate on anything but a chat, a bad session, not an object: 400",
          [ask(port, "POST", "/api/room", key="KE", origin=None, body={"acts": b})[0] for b in
           ({"s-1": "dance"}, {"s-1": "doze:x"}, {"s 1": "doze"}, {"s-1": "chat:<b>"}, ["doze"], {"s-1": 3})], [400] * 6)
    check("...never kept in room.json", "acts" in json.load(open(server.ROOM_FILE)), False)
    st, _, raw = ask(port, "POST", "/api/room", key="KE", origin=None, body={"acts": {}})
    check("...none: cleared", json.loads(raw)["room"]["acts"], None)
    check("...music is true or false", ask(port, "POST", "/api/room", cookie="S2", body={"music": "off"})[0], 400)
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"mix": {"music": {"level": 0.49}, "ambient": {"level": 0.37, "mute": True}}})
    check("...the mixer's levels and mutes (the room's page gives them to Visit for the visitors), the music kept",
          (st, json.loads(raw).get("room", {}).get("mix"), json.loads(raw).get("room", {}).get("music")), (200, {"music": {"level": 0.49}, "ambient": {"level": 0.37, "mute": True}}, False))
    st, _, raw = ask(port, "POST", "/api/room", cookie="S2", body={"mix": {"ambient": {"level": 0.5}, "crowd": {"mute": False}}})
    check("...a channel's own change merged in (a slider sends its level alone): its mute kept, the others kept",
          (st, json.loads(raw).get("room", {}).get("mix")), (200, {"music": {"level": 0.49}, "ambient": {"level": 0.5, "mute": True}, "crowd": {"mute": False}}))
    check("...every page's board says it, and it is kept for a restart",
          (json.loads(ask(port, "GET", "/api/board", cookie="S2")[2]).get("room", {}).get("mix", {}).get("ambient"), json.load(open(server.ROOM_FILE)).get("mix", {}).get("ambient")),
          ({"level": 0.5, "mute": True}, {"level": 0.5, "mute": True}))
    check("...a level past 1, a level that is no number, a mute that is no bool, a channel that is no name, another key, nothing: refused",
          [ask(port, "POST", "/api/room", cookie="S2", body={"mix": m})[0] for m in
           ({"music": {"level": 1.5}}, {"music": {"level": "loud"}}, {"music": {"level": True}}, {"crowd": {"mute": "yes"}},
            {"../x": {"level": 0.5}}, {"music": {"volume": 0.5}}, {"music": {}}, {}, [0.5])], [400] * 9)
    check("...a bad mix changes nothing, the rest of the same call included",
          (ask(port, "POST", "/api/room", cookie="S2", body={"env": "pool", "mix": {"music": {"level": 2}}})[0], json.loads(ask(port, "GET", "/api/board", cookie="S2")[2]).get("room", {}).get("env")), (400, "cave"))
    check("...at most 8 channels", ask(port, "POST", "/api/room", cookie="S2", body={"mix": {"c%d" % i: {"level": 0.5} for i in range(9)}})[0], 400)
    # The casino's: the owner's chips, record and achievements, from the room's page (the engine's key), kept for a restart.
    check("the casino: nothing kept yet", json.loads(ask(port, "GET", "/api/casino", cookie="S2")[2]), {"owner": None, "visitors": 0})
    kept = {"chips": 1430, "rec": {"bjHands": 12, "bjNaturals": 1, "rlNums": [0] * 36 + [2]}, "ach": {"bj_natural": 1762300000000}}
    st, _, raw = ask(port, "POST", "/api/casino", key="KE", origin=None, body={"owner": kept})
    check("...the room's page keeps the owner's (its key)", (st, json.loads(raw).get("owner")), (200, kept))
    check("...in casino.json, beside the room's settings", json.load(open(server.CASINO_FILE)).get("owner"), kept)
    check("...the owner's page reads it (Casino stats), and so does the room's as it starts",
          [json.loads(ask(port, "GET", "/api/casino", **w)[2]).get("owner") for w in ({"cookie": "S2"}, {"key": "KE", "origin": None})], [kept, kept])
    check("...a restart reads it back", server.casino_owner(json.load(open(server.CASINO_FILE))["owner"]), kept)
    check("...chips below 0 or no number, a bad field, a list past the wheel's 37, an achievement's time no number, another key, no owner: refused",
          [ask(port, "POST", "/api/casino", key="KE", origin=None, body=b)[0] for b in
           ({"owner": {"chips": -5}}, {"owner": {"chips": True}}, {"owner": {"chips": "lots"}}, {"owner": {"chips": 5, "rec": {"../x": 1}}},
            {"owner": {"chips": 5, "rec": {"rlNums": [1] * 38}}}, {"owner": {"chips": 5, "rec": {"bjHands": -1}}}, {"owner": {"chips": 5, "ach": {"bj_natural": "today"}}},
            {"owner": {"chips": 5, "who": "me"}}, {"owner": kept, "more": 1}, {}, [kept])], [400] * 11)
    check("...and a refused one changes nothing", json.loads(ask(port, "GET", "/api/casino", cookie="S2")[2]).get("owner"), kept)
    check("...nor does the agents' key reach it", [ask(port, m, "/api/casino", key="K1", origin=None, body={"owner": kept} if m == "POST" else None)[0] for m in ("GET", "POST")], [403, 403])
    # ...and each visitor's, by their office's key (Visit's: the same every visit), so a visitor back another day finds theirs
    VA, VB, VC = "a" * 64, "b" * 64, "c" * 64
    ana = {"name": "Ana", "office": "Ana's den", "chips": 800, "rec": {"bjHands": 3}, "ach": {"bj_first": 1762300000000}}
    st, _, raw = ask(port, "POST", "/api/casino", key="KE", origin=None, body={"visitors": {VA: ana}})
    got = json.loads(ask(port, "GET", "/api/casino?visitor=" + VA, cookie="S2")[2]).get("visitor") or {}
    check("a visitor's, kept by their key (the owner's untouched), the board stamping their first and last time at the tables",
          (st, {k: got.get(k) for k in ana}, got.get("key"), got.get("first") == got.get("last") > 0, json.loads(ask(port, "GET", "/api/casino", cookie="S2")[2])),
          (200, ana, VA, True, {"owner": kept, "visitors": 1}))
    time.sleep(0.01)
    ask(port, "POST", "/api/casino", key="KE", origin=None, body={"visitors": {VA: dict(ana, chips=950)}, "owner": kept})
    again = json.loads(ask(port, "GET", "/api/casino?visitor=" + VA, cookie="S2")[2])["visitor"]
    check("...played again: their first time kept, the last moved on", (again["chips"], again["first"], again["last"] > again["first"]), (950, got["first"], True))
    time.sleep(0.01)   # (Bo played after Ana: a millisecond apart at least)
    ask(port, "POST", "/api/casino", key="KE", origin=None, body={"visitors": {VB: {"name": "Bo", "chips": 1000, "rec": {}, "ach": {}}}})
    lst = json.loads(ask(port, "GET", "/api/casino?visitors=1", cookie="S2")[2])["visitors"]
    check("...every visitor as a list for your picker (the latest to play first: their chips and achievements, no records)",
          ([e["name"] for e in lst], sorted(lst[1]), lst[1]["ach"]), (["Bo", "Ana"], ["ach", "chips", "first", "key", "last", "name", "office"], ana["ach"]))
    check("...the room's page gets everyone's as it starts (?all=1); none for a key never seen",
          (sorted(json.loads(ask(port, "GET", "/api/casino?all=1", key="KE", origin=None)[2])["visitors"]), json.loads(ask(port, "GET", "/api/casino?visitor=" + VC, cookie="S2")[2])),
          ([VA, VB], {"visitor": None}))
    check("...a key that is no office's, no name, too many at once, a bad record: refused",
          [ask(port, "POST", "/api/casino", key="KE", origin=None, body=b)[0] for b in
           ({"visitors": {"../x": ana}}, {"visitors": {VC: dict(ana, name="")}}, {"visitors": {("%064x" % i): ana for i in range(33)}},
            {"visitors": {VC: dict(ana, chips=-1)}}, {"visitors": {VC: dict(ana, name="x" * 41)}}, {"visitors": [ana]})], [400] * 6)
    server.CASINO_VISITORS = 2
    ask(port, "POST", "/api/casino", key="KE", origin=None, body={"visitors": {VC: {"name": "Cy", "chips": 10, "rec": {}, "ach": {}}}})
    server.CASINO_VISITORS = 300
    check("...past the most kept, the one longest gone let go", sorted(json.load(open(server.CASINO_FILE))["visitors"]), [VB, VC])
    check("...and a restart reads them back", {k: server.casino_owner(v, "a visitor")["name"] for k, v in json.load(open(server.CASINO_FILE))["visitors"].items()}, {VB: "Bo", VC: "Cy"})
    check("an agent's key: in, with no Origin (a script sends none)", ask(port, "POST", "/api/move", key="K1", origin=None, body={})[0] not in (401, 403), True)
    check("a key nobody made: 401", ask(port, "GET", "/api/board", key="K9")[0], 401)
    # Visit through the board (basedverse's engine/INTEGRATING.md, steps 4 and 5): no token in any page or answer.
    check("/api/visit is gone: no token for a page any more", ask(port, "GET", "/api/visit", cookie="S2")[0], 404)
    check("/visit/mode: what this office runs (no engine, no lobby, in this test's .visit)", json.loads(ask(port, "GET", "/visit/mode", cookie="S2")[2]), {"engine": False, "lobby": False})
    with open(server.ENGINE_FILE, "w") as f:
        f.write("{}")
    with open(os.path.join(vdir, "token"), "w") as f:
        f.write("h" * 64)
    check("...bin/start's marks there: the engine, a lobby; to a key too", json.loads(ask(port, "GET", "/visit/mode", key="KE")[2]), {"engine": True, "lobby": True})
    check("/visit/visit.js: visit-client's library, served from it", ask(port, "GET", "/visit/visit.js", cookie="S2")[2], b"// visit.js, the stand-in's\n")
    check("the room's server page (?role=host): the engine's key gets it", ask(port, "GET", "/room?role=host", key="KE")[0], 200)
    check("...a person's session does not", ask(port, "GET", "/room?role=host", cookie="S2")[0], 403)
    check("the room, a person's page: a key does not get it", ask(port, "GET", "/room", key="KE")[0], 403)
    # Each key reaches only its holder's routes (server.KEY_ROUTES); the rest is the owner's.
    check("the agents' key: its own routes (the board, a card's move, refresh, messages)",
          [ask(port, m, path, key="K1", origin=None, body={} if m == "POST" else None)[0] not in (401, 403) for m, path in
           (("GET", "/api/board"), ("POST", "/api/move"), ("POST", "/api/refresh"), ("GET", "/api/mail?for=x"), ("POST", "/api/mail/read"))], [True] * 5)
    # Its roster entry, in the board's database (rosterdb.py) since 2026-11-09: made, read, rewritten, taken off, over HTTP.
    entry = "<!-- board-agent -->\n```\nname: amber-otter\nsession: sess-http\nclaims: docs#1\nseen: 2026-11-09T21:00:00Z\n```\n"
    st, _, raw = ask(port, "POST", "/api/roster", key="K1", origin=None, body={"body": entry})
    rid = json.loads(raw).get("id") if st == 200 else None
    check("the agents' key: a roster entry made (an id back)", (st, isinstance(rid, int)), (200, True))
    check("…read back among the rows, and alone by its id",
          (any(r["id"] == rid for r in json.loads(ask(port, "GET", "/api/roster", key="K1", origin=None)[2])["rows"]),
           json.loads(ask(port, "GET", "/api/roster?id=%d" % rid, key="K1", origin=None)[2])["row"]["body"]), (True, entry))
    check("…rewritten in place", json.loads(ask(port, "POST", "/api/roster", key="K1", origin=None,
                                                 body={"body": entry.replace("docs#1", "docs#2"), "id": rid})[2])["id"], rid)
    check("…served on the board as an agent with its claim (the kanban, forge.py agents, the hook)",
          [(d["name"], d["claims"]) for d in server.agents_view() if d["id"] == rid], [("amber-otter", ["docs#2"])])
    check("…what is no entry is refused (422), a missing id is 404, a bad one 400",
          [ask(port, "POST", "/api/roster", key="K1", origin=None, body=b)[0] for b in
           ({"body": "just words"}, {"body": entry, "id": 10 ** 9}, {"body": entry, "id": "7"})], [422, 404, 400])
    check("…taken off, then gone", [ask(port, "POST", "/api/roster/delete", key="K1", origin=None, body={"id": rid})[0],
                                    ask(port, "GET", "/api/roster?id=%d" % rid, key="K1", origin=None)[0],
                                    ask(port, "POST", "/api/roster/delete", key="K1", origin=None, body={"id": rid})[0]], [200, 404, 404])
    check("…and neither the engine's key nor bin/consoles' reaches the roster",
          [ask(port, m, "/api/roster", key=k, origin=None, body={} if m == "POST" else None)[0] for k in ("KE", "KC") for m in ("GET", "POST")],
          [403] * 4)
    check("…and none of the owner's: the plan, the roster's ×, the room, an issue, the tarot reader, the room's server page, pages",
          [ask(port, m, path, key="K1", origin=None, body={} if m == "POST" else None)[0] for m, path in
           (("POST", "/api/plan"), ("POST", "/api/agent/remove"), ("POST", "/api/room"), ("GET", "/api/issue?id=docs%231"), ("GET", "/api/tarot"),
            ("POST", "/api/tarot/ask"), ("GET", "/room?role=host"), ("GET", "/"), ("GET", "/tarot/m00.jpg"))], [403] * 9)
    check("the engine's key: the room's page, its scripts, the board, the tarot reader, the room's settings, her cards",
          [ask(port, m, path, key="KE", origin=None, body={} if m == "POST" else None)[0] not in (401, 403) for m, path in
           (("GET", "/common.js"), ("GET", "/party.js"), ("GET", "/api/board"), ("GET", "/api/tarot"), ("POST", "/api/room"), ("GET", "/tarot/m00.jpg"))], [True] * 6)
    check("…and no messages, no moves, no plan, no issue, no console",
          [ask(port, m, path, key="KE", origin=None, body={} if m == "POST" else None)[0] for m, path in
           (("POST", "/api/mail"), ("GET", "/api/mail/recent"), ("POST", "/api/move"), ("POST", "/api/plan"), ("GET", "/api/issue?id=docs%231"), ("POST", "/api/console/send"))], [403] * 6)
    check("…nor the owner's saved readings (theirs alone), nor the agents' key",
          [ask(port, m, path, key=k, origin=None, body={"reading": "r1", "keep": True} if m == "POST" else None)[0] for k in ("KE", "K1") for m, path in
           (("GET", "/api/tarot/kept"), ("POST", "/api/tarot/keep"))], [403] * 4)
    # The owner saving readings (her card's Save): only the one on her table, as their page has it, in the board's SQLite file.
    T = server.TAROT
    with T.cond:
        T.cur = {"id": "rA", "at": 1000.0, "question": "work", "ask": "", "private": False, "done": True, "error": None, "text": "The Tower falls.",
                 "cards": [{"key": "m16", "name": "The Tower", "pos": "Past", "reversed": False}] * 3,
                 "talk": [{"id": "t1", "at": 1001.0, "ask": "mine?", "text": "Yes, yours.", "private": True, "done": True, "error": None,
                           "by": {"name": "Gus", "key": "v:1", "owner": False}}], "by": {"name": "", "key": "owner", "owner": True}}
    st, _, raw = ask(port, "POST", "/api/tarot/keep", cookie="S2", body={"reading": "rZ", "keep": True})
    check("saving a reading not on her table: 409, said why", (st, "on her table" in json.loads(raw).get("error", "")), (409, True))
    st, _, raw = ask(port, "POST", "/api/tarot/keep", cookie="S2", body={"reading": "rA", "keep": True})
    check("the one on her table: saved", (st, json.loads(raw)), (200, {"kept": True, "kept_n": 1}))
    v = json.loads(ask(port, "GET", "/api/tarot", cookie="S2")[2])
    check("…her view says so, to the owner's page", (v.get("kept"), v.get("kept_n")), (True, 1))
    check("…and not to the engine's", "kept" in json.loads(ask(port, "GET", "/api/tarot", key="KE", origin=None)[2]), False)
    st, _, raw = ask(port, "GET", "/api/tarot/kept", cookie="S2")
    check("the saved list, in brief", (st, [(x["id"], x["question"], x["talk"], len(x["cards"])) for x in json.loads(raw)["kept"]]), (200, [("rA", "work", 1, 3)]))
    full = json.loads(ask(port, "GET", "/api/tarot/kept?reading=rA", cookie="S2")[2])["reading"]
    check("one whole: its words, another's whisper without its words (as the owner's page had it)",
          (full["text"], full["talk"][0]["text"], full["talk"][0].get("hidden")), ("The Tower falls.", "", True))
    check("one not saved: 404", ask(port, "GET", "/api/tarot/kept?reading=rQ", cookie="S2")[0], 404)
    check("a malformed save: 400", ask(port, "POST", "/api/tarot/keep", cookie="S2", body={"reading": "rA"})[0], 400)
    st, _, raw = ask(port, "POST", "/api/tarot/keep", cookie="S2", body={"reading": "rA", "keep": False})
    check("removed", (st, json.loads(raw), json.loads(ask(port, "GET", "/api/tarot/kept", cookie="S2")[2])["kept"]), (200, {"kept": False, "kept_n": 0}, []))
    # No model (no LLM_KEY, no LLM_URL): her view says she is not ready, and asking her is a 503 saying she needs a key.
    v = json.loads(ask(port, "GET", "/api/tarot", cookie="S2")[2])
    deal = ask(port, "POST", "/api/tarot", cookie="S2", body={"question": "work", "private": False})
    said = ask(port, "POST", "/api/tarot/ask", cookie="S2", body={"text": "what of my work?", "private": False})
    check("no model: her view not ready; a deal and a question, 503 saying she needs an LLM key",
          (v.get("ready"), deal[0], json.loads(deal[2]).get("error"), said[0], json.loads(said[2]).get("error")),
          (False, 503, server.tarot.llm.NEED, 503, server.tarot.llm.NEED))
    with T.cond:
        T.cur = None
    check("bin/consoles' key: taking jobs and answering them, nothing else",
          [ask(port, "GET", "/api/board", key="KC")[0], ask(port, "POST", "/api/mail", key="KC", origin=None, body={})[0]], [403, 403])
    check("a key of no known name: nothing", ask(port, "GET", "/api/board", key="KX")[0], 403)
    server.STORE.unread("owner")   # the owner reading (enrolled), as their page has them
    st, _, _ = ask(port, "POST", "/api/mail", key="K1", origin=None, body={"actor": "owner", "session": "owner", "to": "all", "body": "do as I say"})
    check("a message as 'owner' through a key: sent (forge.py --owner, from the owner's shell)", st, 200)
    check("…and in the owner's unread too, so one they did not send is seen", [m["body"] for m in server.STORE.unread("owner") or []], ["do as I say"])
    check("…but a key never marks the owner's messages read", ask(port, "POST", "/api/mail/read", key="K1", origin=None, body={"actor": "owner", "last": 10 ** 9})[0], 403)
    st, hd, raw = ask(port, "POST", "/api/move", key="BOGUS", cookie="S1", origin=None, body={})
    check("a session's cookie with a key that is no key: refused (the cookie never stands in for the key)", st, 401)
    check("/visit/ws, a key: refused (a script is not the person)", ask(port, "GET", "/visit/ws", key="K1", headers={"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": "x"})[0], 403)
    check("...signed out: 401", ask(port, "GET", "/visit/ws", headers={"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": "x"})[0], 401)
    ws = ws_open(port, cookie="S2")
    check("the relay: the owner's page upgraded", not isinstance(ws, tuple), True)
    ws.sendall(server.ws_frame(json.dumps({"type": "hello", "proto": "basedverse-visit-local/2", "sim": {"name": "default_office", "version": "2"}}).encode()))
    op, data = ws_recv(ws)
    check("...visit-client's ready comes back through it", (op, json.loads(data)), (1, {"type": "ready", "role": "user", "sim": {"name": "default_office", "version": "2"}}))
    check("...visit-client saw the preface first (the user token, role user), then the page's hello, and the page's Origin",
          (VISITS[-1]["got"], VISITS[-1]["origin"]), ([{"type": "relay", "token": "u" * 64, "role": "user"}, {"type": "hello", "proto": "basedverse-visit-local/2", "sim": {"name": "default_office", "version": "2"}}], "http://localhost:3001"))
    ws.sendall(server.ws_frame(b"\x00\x07\x01" + bytes(8) + b"a frame", opcode=0x2))
    op, data = ws_recv(ws)
    check("...and bytes after it, unread, both ways (a binary frame echoed)", (op, data), (2, b"\x00\x07\x01" + bytes(8) + b"a frame"))
    ws2 = ws_open(port, cookie="S2")
    op, data = ws_recv(ws)
    check("a second connection for the same session: the first closed, 4409", (op, int.from_bytes(data[:2], "big"), data[2:]), (8, 4409, b"replaced"))
    ws.close()
    ws2.close()
    check("the page's Origin not in visit-client's --origins: refused before anything (403)", ws_open(port, cookie="S2", origin="https://evil.example")[0], 403)

    LIVE.add("S2")
    st, hd, _ = ask(port, "POST", "/logout", cookie="S2")
    check("sign out: to the sign-in page, the cookie gone", (st, hd.get("Location"), hd.get("Set-Cookie", "").startswith("session=;"), "Max-Age=0" in hd.get("Set-Cookie", "")), (303, "/login", True, True))
    check("...and the session over at once (not kept 5 s)", ask(port, "GET", "/", cookie="S2")[0], 303)

    # Another device, through an HTTPS proxy (the board's listener for one): the proxy's word for the address, the
    # __Host- cookie with Secure, and the page's origin https:// and the name it was asked by.
    lan = server.Server(("127.0.0.1", 0), server.H)
    lan.remote = True
    threading.Thread(target=lan.serve_forever, daemon=True).start()
    lport, there = lan.server_address[1], "192.168.1.20:3443"
    st, hd, _ = ask(lport, "POST", "/login", form={"user": "robin", "password": "the right password", "next": "/room"}, host=there,
                    origin="https://" + there, headers={"X-Forwarded-For": "6.6.6.6, 192.168.1.30"})
    check("signing in from another device: where from, as the proxy said (its entry, not the browser's own claim)", SEEN[-1][1]["client"]["address"], "192.168.1.30")
    cookie = hd.get("Set-Cookie", "")
    check("...the __Host- cookie, Secure", (st, cookie.split(";")[0], cookie.rstrip().endswith("; Secure")), (303, "__Host-session=S2", True))
    check("signed in there: the room", ask(lport, "GET", "/room", cookie="S2", jar="__Host-session", host=there)[0], 200)
    ws = ws_open(lport, cookie="S2", jar="__Host-session", host=there, origin="https://" + there)
    check("...and Visit from there too: the relay, through the proxy (its origin passed on)", not isinstance(ws, tuple), True)
    if not isinstance(ws, tuple):
        ws.sendall(server.ws_frame(json.dumps({"type": "hello", "proto": "basedverse-visit-local/2", "sim": {}}).encode()))
        ws_recv(ws)
        check("...visit-client saw that origin", VISITS[-1]["origin"], "https://" + there)
        ws.close()
    check("...not with this machine's HTTP cookie", ask(lport, "GET", "/room", cookie="S2", host=there)[0], 303)
    check("a write from its page there (https:// and the name it was asked by): on to the route",
          ask(lport, "POST", "/api/move", cookie="S2", jar="__Host-session", host=there, origin="https://" + there, body={})[0] not in (401, 403), True)
    check("...from this machine's page's origin: refused", ask(lport, "POST", "/api/move", cookie="S2", jar="__Host-session", host=there, body={})[0], 403)
    check("...over plain http: refused", ask(lport, "POST", "/api/move", cookie="S2", jar="__Host-session", host=there, origin="http://" + there, body={})[0], 403)
    st, hd, _ = ask(lport, "POST", "/logout", cookie="S2", jar="__Host-session", host=there, origin="https://" + there)
    check("signing out there: its cookie gone", (st, hd.get("Set-Cookie", "").startswith("__Host-session=;")), (303, True))
    lan.shutdown()

    with open(tfile, "w") as f:
        f.write("u" * 64)
    server.auth._token["value"] = None
    check("auth-client refusing the board's token: 503, nobody let in", ask(port, "GET", "/", cookie="S1")[0], 503)
    stand_in.shutdown()
    stand_in.server_close()
    check("auth-client not answering: 503, nobody let in", ask(port, "GET", "/api/board", cookie="S3")[0], 503)
    check("...but the sign-in page still shows", ask(port, "GET", "/login")[0], 200)
    board.shutdown()

    print("%d fail" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
