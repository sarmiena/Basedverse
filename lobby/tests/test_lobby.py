#!/usr/bin/env python3
"""The lobby (../../visit/SPEC.md 6, ../../LOBBY.md), tested from outside: the real `lobby` binary, on free local
ports, talked to over WebSockets. Version 3 as the Python lobby spoke it (its own tests, carried over), and
version 4: keys proved at hello, a key back taking its session over, bans, the directory, world groups (kept
across a restart), the public page. No network beyond localhost.

    python3 test_lobby.py <path to the lobby binary>       (needs websockets and cryptography: requirements.txt)
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import urllib.request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

BIN = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LOBBY_BIN", "lobby")
P3, P4 = "basedverse-lobby/3", "basedverse-lobby/4"
fails = 0
TMP = tempfile.mkdtemp(prefix="lobby-test-")


def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%s  %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else "\n      got  %r\n      want %r" % (got, want)))


class Lobby:
    """A running lobby: `lobby --port 0 --debug` and whatever else, its port from its ready line."""

    def __init__(self, *args):
        self.args = args

    async def start(self):
        self.log = open(os.path.join(TMP, "lobby-%d.log" % id(self)), "w")
        self.p = await asyncio.create_subprocess_exec(BIN, "--listen", "127.0.0.1", "--port", "0", "--debug", *self.args,
                                                      stdout=asyncio.subprocess.PIPE, stderr=self.log)
        ready = json.loads(await asyncio.wait_for(self.p.stdout.readline(), 10))
        self.port = ready["port"]
        self.url = "ws://127.0.0.1:%d" % self.port
        self.http = "http://127.0.0.1:%d" % self.port
        return self

    async def stop(self):
        self.p.terminate()
        await self.p.wait()

    def get(self, path):
        with urllib.request.urlopen(self.http + path, timeout=3) as r:
            return r.read().decode()

    def post(self, path, body=b""):
        req = urllib.request.Request(self.http + path, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status

    async def state(self):
        return json.loads(await asyncio.to_thread(self.get, "/debug/state"))

    async def reset(self):
        await asyncio.to_thread(self.post, "/debug/reset")


class Client:
    """One test connection. expect() returns the next message of a type, skipping `lobby` snapshots (and, unless
    asked for, `group` views) unless asked for one; any other message first is a failure (the protocol's order is
    part of it)."""

    def __init__(self, ws):
        self.ws = ws

    async def send(self, **m):
        await self.ws.send(json.dumps(m))

    async def expect(self, type_, timeout=2.0, skip=("lobby",)):
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if m["type"] == type_:
                return m
            if m["type"] not in skip:
                raise AssertionError("wanted %s, got %s" % (type_, m))

    async def quiet(self, timeout=0.3):
        out = []
        try:
            while True:
                out.append(json.loads(await asyncio.wait_for(self.ws.recv(), timeout)))
        except (asyncio.TimeoutError, ConnectionClosed):
            return out

    async def last_lobby(self):
        return [m for m in await self.quiet() if m["type"] == "lobby"][-1]

    async def close(self):
        await self.ws.close()


def keypair():
    sk = Ed25519PrivateKey.generate()
    return sk, sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def proof(sk, host, nonce, key):
    return sk.sign(("%s hello\n%s\n%s\n%s" % (P4, host, nonce, key)).encode()).hex()


async def hello4(lob, name, sk=None, host=None, **extra):
    """A version-4 hello, its key proved: (client, welcome). `host`: what it signs as the lobby dialled."""
    sk, key = (sk, sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()) if sk else keypair()
    c = Client(await connect(lob.url))
    c.sk, c.key = sk, key
    await c.send(type="hello", proto=P4, name=name, key=key, sim={"name": "test", "version": "1"}, status="available", max_visitors=3, **extra)
    ch = await c.expect("challenge")
    await c.send(type="prove", signature=proof(sk, host or "127.0.0.1:%d" % lob.port, ch["nonce"], key))
    while True:   # its welcome, or the error that refused it
        m = json.loads(await asyncio.wait_for(c.ws.recv(), 2))
        if m["type"] in ("welcome", "error"):
            return c, m


async def join(lob, name, link=True, proto=P3, sk=None, **extra):
    """An office (version 3 unless `proto`), its link published unless link=False."""
    if proto == P4:
        c, c.welcome = await hello4(lob, name, sk=sk, **extra)
        assert c.welcome["type"] == "welcome", c.welcome
        endpoint = c.key
    else:
        c = Client(await connect(lob.url))
        await c.send(type="hello", proto=proto, name=name, sim={"name": "test", "version": "1"}, **extra)
        c.welcome = await c.expect("welcome")
        endpoint = "ep-" + name
    c.id, c.name = c.welcome["id"], name
    if link:
        c.link = {"endpoint": endpoint, "addr": {"relay": "https://relay.test/", "direct": ["192.0.2.1:7"]}}
        await c.send(type="link", **c.link)
    seen = [m for m in await c.quiet() if m["type"] == "lobby"]
    c.view = seen[-1] if seen else None
    return c


async def visit(visitor, host, msg="hi"):
    """visitor knocks on host, host lets it in; returns (visitor's visit, host's visit). Drains both."""
    await visitor.send(type="knock", to=host.id, message=msg)
    await visitor.expect("knock_sent")
    k = await host.expect("knock")
    await host.send(type="answer", knock=k["id"], accept=True)
    await visitor.expect("answered")
    vv, hv = await visitor.expect("visit"), await host.expect("visit")
    await visitor.quiet()
    await host.quiet()
    return vv, hv


def office(lob, name):
    return next(o for o in lob["offices"] if o["name"] == name)


async def closed_code(c, timeout=2):
    try:
        while True:
            await asyncio.wait_for(c.ws.recv(), timeout)
    except ConnectionClosed as e:
        return e.rcvd.code if e.rcvd else None
    except asyncio.TimeoutError:
        return "open"


# ---------- version 3, as it was ----------
async def t_hello(L):
    a = Client(await connect(L.url))
    await a.send(type="hello", proto=P3, name="Ada", sim={"name": "my-workroom", "version": "2", "x": "dropped"}, max_visitors=2)
    w = await a.expect("welcome")
    check("welcome: the protocol, an id, the name, relays, a resume secret, not resumed",
          (w["proto"], w["id"][:1], w["name"], w["relays"], len(w["resume"]), w["resumed"]), (P3, "o", "Ada", [], 32, False))
    check("and the limits", (w["limits"]["max_visitors"], w["limits"]["resume"]), (8, 30))
    lob = await a.expect("lobby")
    check("the lobby lists it, not ready until it publishes its link",
          [(o["name"], o["max_visitors"], o["visitors"], o["visiting"], o["ready"], o["sim"]) for o in lob["offices"]],
          [("Ada", 2, 0, None, False, {"name": "my-workroom", "version": "2"})])
    await a.send(type="link", endpoint="ep-ada", addr="not an object")
    check("a link needs an addr object", (await a.expect("error"))["code"], "bad_message")
    await a.send(type="link", endpoint="ep-ada", addr={"pad": "x" * 3000})
    check("and a small one", (await a.expect("error"))["code"], "bad_message")
    await a.send(type="link", endpoint="ep-ada", addr={"relay": "https://relay.test/"})
    check("a good link makes it ready", office(await a.last_lobby(), "Ada")["ready"], True)
    await a.send(type="max_visitors", n=9)
    check("max_visitors is 0 to 8", (await a.expect("error"))["code"], "bad_message")
    await a.send(type="sideways")
    check("an unknown type: unknown_type", (await a.expect("error"))["code"], "unknown_type")
    await a.send(type="group_join", group="x")
    check("version 4's messages are not version 3's", (await a.expect("error"))["code"], "unknown_type")
    await a.ws.send("not json")
    check("not JSON: bad_json", (await a.expect("error"))["code"], "bad_json")
    await a.close()


async def t_hello_rules(L):
    c = Client(await connect(L.url))
    await c.send(type="hello", proto=P3, name="-bad")
    check("an invalid name is refused", (await c.expect("error"))["code"], "name_invalid")
    taken = await join(L, "Grace")
    await c.send(type="hello", proto=P3, name="grace")
    check("a name is taken whatever its case", (await c.expect("error"))["code"], "name_taken")
    await c.send(type="hello", proto=P3, name="Grace 2", max_visitors=True)
    check("max_visitors must be a number", (await c.expect("error"))["code"], "bad_message")
    await c.send(type="status", status="busy")
    check("anything before a hello: hello_first", (await c.expect("error"))["code"], "hello_first")
    await c.send(type="hello", proto="basedverse-lobby/2", name="Grace 3")
    check("version 2 is gone", (await c.expect("error"))["code"], "proto_unsupported")
    await c.close()
    await taken.close()


async def t_knock_refusals(L):
    a, b = await join(L, "A2"), await join(L, "B2")
    n = await join(L, "N2", link=False)
    await a.send(type="knock", to="o-none")
    check("knocking on nobody", (await a.expect("error"))["code"], "not_found")
    await a.send(type="knock", to=a.id)
    check("on yourself", (await a.expect("error"))["code"], "self_knock")
    await a.send(type="knock", to=n.id)
    check("on an office with no link yet", (await a.expect("error"))["code"], "not_ready")
    await n.send(type="knock", to=a.id)
    check("or with none of your own", (await n.expect("error"))["code"], "not_ready")
    await b.send(type="status", status="busy")
    await a.quiet()
    await a.send(type="knock", to=b.id)
    check("on a busy office", (await a.expect("error"))["code"], "not_available")
    await b.send(type="status", status="available")
    await b.send(type="max_visitors", n=0)
    await a.quiet()
    await a.send(type="knock", to=b.id)
    check("on an office that takes no visitors (or has all it takes)", (await a.expect("error"))["code"], "full")
    await b.send(type="max_visitors", n=3)
    await a.quiet()
    await a.send(type="knock", to=b.id, message="x" * 281)
    check("a message over 280 characters", (await a.expect("error"))["code"], "message_too_long")
    await a.send(type="knock", to=b.id)
    await a.expect("knock_sent")
    await a.send(type="knock", to=b.id)
    check("a second knock while the first waits", (await a.expect("error"))["code"], "knock_pending")
    for x in (a, b, n):
        await x.close()


async def t_visit(L):
    a, b, c = await join(L, "A3"), await join(L, "B3"), await join(L, "C3")
    await a.send(type="knock", to=b.id, message="can I walk around?")
    sent = await a.expect("knock_sent")
    k = await b.expect("knock")
    check("the knock reaches the door", (k["id"], k["from"]["name"], k["message"]), (sent["id"], "A3", "can I walk around?"))
    await c.send(type="answer", knock=k["id"], accept=True)
    check("only its door answers it", (await c.expect("error"))["code"], "not_yours")
    await b.send(type="answer", knock=k["id"], accept=True, message="come in")
    ans = await a.expect("answered")
    check("the knocker hears yes", (ans["accept"], ans["message"]), (True, "come in"))
    va, vb = await a.expect("visit"), await b.expect("visit")
    check("both get the visit: one id, one ticket, each its role and the other",
          (va["id"] == vb["id"], va["ticket"] == vb["ticket"], len(va["ticket"]), va["role"], vb["role"], va["peer"]["name"], vb["peer"]["name"]),
          (True, True, 32, "visitor", "host", "B3", "A3"))
    check("each gets the other's link, to dial or to check the dialler against", (va["peer_link"], vb["peer_link"]), (b.link, a.link))
    lob = await c.last_lobby()
    check("the lobby shows who is visiting whom", (office(lob, "A3")["visiting"], office(lob, "B3")["visitors"]), (b.id, 1))
    await a.send(type="knock", to=b.id)
    check("no knocking on the room you are in", (await a.expect("error"))["code"], "already_visiting")
    await a.send(type="knock", to=c.id)
    await a.expect("knock_sent")
    k = await c.expect("knock")
    await c.send(type="answer", knock=k["id"], accept=True)
    await a.expect("answered")
    moved = await a.expect("visit_ended")
    await a.expect("visit")
    await c.expect("visit")
    ended = await b.expect("visit_ended")
    check("going into another room ends the visit to the first, `moved`, for both", (moved["reason"], ended["reason"], ended["id"]), ("moved", "moved", vb["id"]))
    await a.quiet()
    await a.send(type="end", visit="v-none")
    check("ending a visit you are not in", (await a.expect("error"))["code"], "no_such_visit")
    await b.quiet()
    await c.quiet()
    await visit(b, c)   # c hosts a and b
    await c.send(type="status", status="available")
    check("a host's visitors count", office(await c.expect("lobby"), "C3")["visitors"], 2)
    for x in (a, b, c):
        await x.close()


async def t_end_and_full(L):
    h, v1, v2 = await join(L, "H4"), await join(L, "V4a"), await join(L, "V4b")
    vv, hv = await visit(v1, h)
    await v1.send(type="end", visit=vv["id"], reason="sideways")
    check("an end reason must be one of left, kicked, link_failed", (await v1.expect("error"))["code"], "bad_message")
    await v1.send(type="end", visit=vv["id"])
    e1, e2 = await v1.expect("visit_ended"), await h.expect("visit_ended")
    check("the visitor leaving: `left`, to both", (e1["reason"], e2["reason"], e1["id"]), ("left", "left", vv["id"]))
    vv, hv = await visit(v1, h)
    await h.send(type="end", visit=hv["id"])
    check("the host ending it: `kicked`", ((await v1.expect("visit_ended"))["reason"], (await h.expect("visit_ended"))["reason"]), ("kicked", "kicked"))
    vv, hv = await visit(v1, h)
    await v1.send(type="end", visit=vv["id"], reason="link_failed")
    check("a link that could not be made: `link_failed`", (await h.expect("visit_ended"))["reason"], "link_failed")
    await v2.send(type="knock", to=h.id)
    await v2.expect("knock_sent")
    k = await h.expect("knock")
    await h.send(type="max_visitors", n=0)
    await h.send(type="answer", knock=k["id"], accept=True)
    check("letting in past max_visitors: the knocker hears `full`", (await v2.expect("answered"))["reason"], "full")
    check("and the host an error", (await h.expect("error"))["code"], "full")
    for x in (h, v1, v2):
        await x.close()


async def t_resume(L):
    a, b = await join(L, "A5"), await join(L, "B5")
    vv, hv = await visit(a, b)
    secret = a.welcome["resume"]
    await a.ws.close()   # dropped
    await asyncio.sleep(0.2)
    await b.send(type="status", status="available")
    lob = await b.expect("lobby")
    check("a dropped session stays listed, still visiting", office(lob, "A5")["visiting"], b.id)
    c = await join(L, "C5")
    await c.send(type="knock", to=a.id, message="while you were out")
    await c.expect("knock_sent")
    r = Client(await connect(L.url))
    await r.send(type="hello", proto=P3, name="ignored on resume", resume=secret)
    w = await r.expect("welcome")
    check("resuming: the same office, a new secret, resumed", (w["id"], w["name"], w["resumed"], w["resume"] != secret), (a.id, "A5", True, True))
    await r.expect("lobby")
    k = await r.expect("knock")
    check("a knock that came while it was out is waiting for it", (k["from"]["name"], k["message"]), ("C5", "while you were out"))
    await r.send(type="end", visit=vv["id"])
    check("its visit is still there to end", (await b.expect("visit_ended"))["reason"], "left")
    x = Client(await connect(L.url))
    await x.send(type="hello", proto=P3, name="Thief", resume=secret)
    w2 = await x.expect("welcome")
    check("a spent secret resumes nothing: a new session", (w2["resumed"], w2["id"] != a.id, w2["name"]), (False, True, "Thief"))
    await x.close()
    await r.quiet()
    r2 = Client(await connect(L.url))
    await r2.send(type="hello", proto=P3, resume=w["resume"])
    await r2.expect("welcome")
    check("the stale connection is closed when its session resumes elsewhere", await closed_code(r, 1), 4000)
    for y in (b, c, r2):
        await y.close()


async def t_resume_replays_visits(L):
    a, b = await join(L, "A8"), await join(L, "B8")
    await b.send(type="knock", to=a.id)
    await b.expect("knock_sent")
    k = await a.expect("knock")
    secret = b.welcome["resume"]
    await b.ws.close()   # away just as its knock is answered
    await asyncio.sleep(0.2)
    await a.send(type="answer", knock=k["id"], accept=True)
    va = await a.expect("visit")
    r = Client(await connect(L.url))
    await r.send(type="hello", proto=P3, resume=secret)
    w = await r.expect("welcome")
    check("a resumed welcome lists the session's visits", w["visits"], [va["id"]])
    check("and its own knocks still waiting (none: this one was answered)", w["knocking"], [])
    vb = await r.expect("visit")
    check("and a visit agreed while it was away is told to it", (vb["id"], vb["role"], vb["ticket"] == va["ticket"], vb["peer_link"]), (va["id"], "visitor", True, a.link))
    for x in (a, r):
        await x.close()


async def t_rename(L):
    a, b = await join(L, "A9"), await join(L, "B9")
    await a.send(type="rename", name="b9")
    check("renaming to a taken name: refused", (await a.expect("error"))["code"], "name_taken")
    await a.send(type="status", status="available")
    check("and the office keeps its own name", office(await a.expect("lobby"), "A9")["id"], a.id)
    await a.send(type="rename", name="-bad")
    check("renaming to an invalid name: refused", (await a.expect("error"))["code"], "name_invalid")
    await a.send(type="rename", name="A9 New")
    lob = await b.last_lobby()
    check("renaming to a free name: everyone sees it, the same office", (office(lob, "A9 New")["id"], [o["name"] for o in lob["offices"]]), (a.id, ["A9 New", "B9"]))
    c = await join(L, "A9")
    check("and the old name is free again", c.welcome["name"], "A9")
    for x in (a, b, c):
        await x.close()


async def t_resume_window_and_bye(S):
    """On a lobby holding dropped sessions 1 s (--resume 1)."""
    a, b = await join(S, "A6"), await join(S, "B6")
    await visit(a, b)
    await b.send(type="knock", to=a.id)   # b, visited by a, knocks on a too (a visit the other way)
    await b.expect("knock_sent")
    await a.expect("knock")
    await a.ws.close()
    ans = await b.expect("answered", timeout=3)
    check("past the resume window the session ends: knocks on it are answered `gone`", (ans["accept"], ans["reason"]), (False, "gone"))
    e = await b.expect("visit_ended", timeout=1)
    check("and its visits end `gone`", e["reason"], "gone")
    lob = await b.last_lobby()
    check("and it leaves the lobby", [o["name"] for o in lob["offices"]], ["B6"])
    c = await join(S, "C6")
    await visit(c, b)
    await c.send(type="bye")
    check("bye: gone at once, not held", (await b.expect("visit_ended", timeout=0.5))["reason"], "gone")
    await b.close()


async def t_knock_timeout_and_health(S):
    a, b = await join(S, "A7"), await join(S, "B7")
    await a.send(type="knock", to=b.id)
    await a.expect("knock_sent")
    k = await b.expect("knock")
    ans = await a.expect("answered", timeout=3)
    check("an unanswered knock times out", (ans["accept"], ans["reason"]), (False, "timeout"))
    check("and is withdrawn from the door", (await b.expect("knock_cancelled", timeout=3))["id"], k["id"])
    body = await asyncio.to_thread(S.get, "/health")
    check("GET /health counts the offices", body.strip(), "ok 2 online")
    await a.send(type="bye")
    await b.send(type="bye")


async def t_link_withdrawn(L):
    """A link withdrawn and published again (its page stopped hosting, and started again)."""
    a, b = await join(L, "Neu A"), await join(L, "Neu B")
    await a.send(type="link", endpoint=None, addr=None)
    lob = await b.last_lobby()
    check("a link withdrawn: not ready", office(lob, "Neu A")["ready"], False)
    await b.send(type="knock", to=a.id)
    check("and not knockable", (await b.expect("error"))["code"], "not_ready")
    await a.send(type="knock", to=b.id)
    check("nor can it knock", (await a.expect("error"))["code"], "not_ready")
    await a.send(type="link", **a.link)
    check("published again: ready", office(await b.last_lobby(), "Neu A")["ready"], True)
    await b.send(type="knock", to=a.id)
    await b.expect("knock_sent")
    k = await a.expect("knock")
    await a.send(type="link", endpoint=None, addr=None)
    await a.quiet()
    await a.send(type="answer", knock=k["id"], accept=True)
    check("letting in after withdrawing its link: refused, not_ready", (await a.expect("error"))["code"], "not_ready")
    check("and the knocker told", ((await b.expect("answered"))["reason"]), "not_ready")
    for c in (a, b):
        await c.send(type="bye")


async def t_keys(L):
    a, b = await join(L, "Key A"), await join(L, "Key B")
    nolink = await join(L, "Key C", link=False)
    lob = await a.last_lobby()
    check("each office's key in the lobby: its endpoint, or null before it has published one",
          [(o["name"], o["key"]) for o in lob["offices"]], [("Key A", "ep-Key A"), ("Key B", "ep-Key B"), ("Key C", None)])
    await a.send(type="link", endpoint=None, addr=None)
    lob = await b.last_lobby()
    check("a link withdrawn: not ready, but still its key", (office(lob, "Key A")["ready"], office(lob, "Key A")["key"]), (False, "ep-Key A"))
    await a.send(type="link", **a.link)
    await b.quiet()
    await a.send(type="knock", to=b.id)
    await a.expect("knock_sent")
    k = await b.expect("knock")
    check("a knock says whose key it is", k["from"]["key"], "ep-Key A")
    for c in (a, b, nolink):
        await c.send(type="bye")


async def t_watch(L):
    a, b, c = (await join(L, "W A", world=True), await join(L, "W B", world=True), await join(L, "W C", world=True))
    old = await join(L, "W Old")
    await a.send(type="knock", to=old.id, watch=True)
    e = await a.expect("error")
    lob = await c.last_lobby()
    check("an office that did not say world (an older Visit) cannot be watched: not_supported, never asked",
          (e["code"], e.get("ref"), office(lob, "W Old")["world"], office(lob, "W A")["world"]), ("not_supported", "watch", False, True))
    await old.send(type="bye")
    await a.send(type="knock", to=c.id)
    await a.expect("knock_sent")
    await c.send(type="answer", knock=(await c.expect("knock"))["id"], accept=True)
    await a.expect("answered")
    vv = await a.expect("visit")
    await a.quiet()
    await c.quiet()
    await b.send(type="status", status="busy")
    await b.quiet()
    await a.send(type="knock", to=b.id, watch=True)
    sent = await a.expect("knock_sent")
    k = await b.expect("knock")
    check("a knock to watch: says so both ways (and busy does not stop it)", (sent.get("watch"), k.get("watch"), k["from"]["key"]), (True, True, "ep-W A"))
    await b.send(type="answer", knock=k["id"], accept=True)
    ans = await a.expect("answered")
    wa, wb = await a.expect("visit"), await b.expect("visit")
    st = {o["id"]: o for o in (await L.state())["offices"]}
    check("let in: a watch, both told so, the watcher's room visit untouched",
          (ans["accept"], ans.get("watch"), wa.get("watch"), wb.get("watch"), wa["role"], wb["role"], st[a.id]["visiting"] == vv["id"]),
          (True, True, True, True, "visitor", "host", True))
    lob = await c.last_lobby()
    check("the lobby counts watchers apart from visitors", (office(lob, "W B")["watchers"], office(lob, "W B")["visitors"], office(lob, "W C")["visitors"]), (1, 0, 1))
    await a.send(type="knock", to=b.id, watch=True)
    e = await a.expect("error")
    check("watching the same plot twice: already_watching, ref watch", (e["code"], e.get("ref")), ("already_watching", "watch"))
    await c.send(type="knock", to=b.id, watch=True)
    await c.expect("knock_sent")
    k = await b.expect("knock")
    await b.send(type="answer", knock=k["id"], accept=False)
    ans = await c.expect("answered")
    check("a watch declined: the knocker told, a watch's answer", (ans["accept"], ans.get("reason"), ans.get("watch")), (False, "declined", True))
    await a.send(type="end", visit=wa["id"])
    e1, e2 = await a.expect("visit_ended"), await b.expect("visit_ended")
    check("a watch ends like a visit", (e1["id"], e2["id"], e1["reason"]), (wa["id"], wa["id"], "left"))
    st = {o["id"]: o for o in (await L.state())["offices"]}
    check("and the lobby forgets it", (st[b.id]["watched"], st[a.id]["watching"]), (0, 0))
    for c_ in (a, b, c):
        await c_.send(type="bye")


async def t_link(L):
    a, b = await join(L, "L A", world=True, links=True), await join(L, "L B", world=True, links=True)
    old = await join(L, "L Old", world=True)
    lob = await a.last_lobby()
    check("the lobby says who takes links", (office(lob, "L B")["links"], office(lob, "L Old")["links"]), (True, False))
    await b.send(type="status", status="busy")
    await b.quiet()
    await a.send(type="knock", to=b.id, link=True)
    e = await a.expect("error")
    check("busy stops a link, as a knock", (e["code"], e.get("ref")), ("not_available", "knock"))
    await b.send(type="status", status="available")
    await b.quiet()
    await a.send(type="knock", to=b.id, link=True, message="look")
    sent = await a.expect("knock_sent")
    k = await b.expect("knock")
    check("a link: says so both ways, with the knocker's key and message, not a watch",
          (sent.get("link"), sent.get("watch"), k.get("link"), k.get("watch"), k["from"]["key"], k["message"]), (True, None, True, None, "ep-L A", "look"))
    await b.send(type="answer", knock=k["id"], accept=True)
    ans = await a.expect("answered")
    va, vb = await a.expect("visit"), await b.expect("visit")
    check("let in: a watch that is a link, both told so",
          (ans["accept"], ans.get("link"), va.get("watch"), va.get("link"), vb.get("watch"), vb.get("link"), va["role"], vb["role"]),
          (True, True, True, True, True, True, "visitor", "host"))
    lob = await a.last_lobby()
    check("counted as a watcher, not a visitor", (office(lob, "L B")["watchers"], office(lob, "L B")["visitors"]), (1, 0))
    await a.send(type="knock", to=b.id, link=True)
    e = await a.expect("error")
    check("linked already: already_watching", (e["code"], e.get("ref")), ("already_watching", "knock"))
    await a.send(type="knock", to=old.id, link=True)
    sent = await a.expect("knock_sent")
    k = await old.expect("knock")
    check("to a door that does not take links: an ordinary knock", (sent.get("link"), k.get("link")), (None, None))
    await old.send(type="answer", knock=k["id"], accept=True)
    await a.expect("answered")
    vo = await a.expect("visit")
    st = {o["id"]: o for o in (await L.state())["offices"]}
    check("which is an ordinary visit, the link going on", (vo.get("watch"), vo.get("link"), st[a.id]["watching"]), (None, None, 1))
    await a.send(type="end", visit=va["id"])
    e1, e2 = await a.expect("visit_ended"), await b.expect("visit_ended")
    st = {o["id"]: o for o in (await L.state())["offices"]}
    check("a link ends like a visit", (e1["id"], e2["id"], st[b.id]["watched"]), (va["id"], va["id"], 0))
    for c_ in (a, b, old):
        await c_.send(type="bye")


async def t_chat(L):
    a, b = await join(L, "C A", chat=True), await join(L, "C B", chat=True)
    old = await join(L, "C Old")
    check("the welcome says the lobby relays chat", a.welcome.get("chat"), True)
    await a.send(type="chat", text="  hello\tall \n")
    la, lb = await a.expect("lobby_chat"), await b.expect("lobby_chat")
    check("a says a line: b has it, and a too (in order), plain, from a",
          (lb["text"], lb["from"]["name"], lb["from"]["id"], la["text"]), ("hello all", "C A", a.id, "hello all"))
    check("an office whose hello did not say chat gets nothing", [m["type"] for m in await old.quiet()], [])
    await b.send(type="chat", text="é" * 500)
    check("a long line: cut to 200 characters", len((await a.expect("lobby_chat"))["text"]), 200)
    await b.quiet()
    await a.send(type="chat", text=" \n ")
    check("an empty one: refused", (await a.expect("error"))["code"], "bad_message")
    for i in range(4):
        await a.send(type="chat", text="line %d" % i)
    await a.send(type="chat", text="one too many")
    got = [m for m in await a.quiet() if m["type"] in ("lobby_chat", "error")]
    check("five lines in 10 s, the sixth refused", ([m.get("text") for m in got if m["type"] == "lobby_chat"], [m.get("code") for m in got if m["type"] == "error"]),
          (["line 0", "line 1", "line 2", "line 3"], ["rate_limited"]))
    for c_ in (a, b, old):
        await c_.send(type="bye")


async def t_whisper(L):
    a, b = await join(L, "W A", chat=True, whisper=True), await join(L, "W B", chat=True, whisper=True)
    c, old = await join(L, "W C", chat=True, whisper=True), await join(L, "W Old", chat=True)
    lob = await a.last_lobby()
    check("the lobby says who takes whispers", (office(lob, "W B")["whisper"], office(lob, "W Old")["whisper"]), (True, False))
    await a.send(type="whisper", to=b.id, text="  psst\n ")
    w = await b.expect("whisper")
    check("A whispers to B: B has it, from A, plain", (w["text"], w["from"]["name"], w["from"]["id"], w["from"]["key"]), ("psst", "W A", a.id, "ep-W A"))
    check("and nobody else: not C, not A", ([m["type"] for m in await c.quiet() if m["type"] != "lobby"], [m["type"] for m in await a.quiet() if m["type"] != "lobby"]), ([], []))
    await a.send(type="whisper", to=old.id, text="hi")
    e = await a.expect("error")
    check("to an office that takes none: refused, by name", (e["code"], e.get("ref"), "W Old" in e["message"]), ("not_supported", "whisper", True))
    for to in ("nobody", a.id, 5):
        await a.send(type="whisper", to=to, text="hi")
    check("to nobody, to itself, to no id: not_found", [(await a.expect("error"))["code"] for _ in range(3)], ["not_found"] * 3)
    for i in range(4):
        await a.send(type="whisper", to=b.id, text="w%d" % i)
    await a.send(type="chat", text="and one in the lobby")
    got = [m for m in await a.quiet() if m["type"] in ("lobby_chat", "error")]
    check("whispers and lobby lines count together: the sixth refused", [m.get("code") for m in got], ["rate_limited"])
    for c_ in (a, b, c, old):
        await c_.send(type="bye")


# ---------- version 4 ----------
async def t_v4_hello(L):
    c = Client(await connect(L.url))
    await c.send(type="hello", proto=P4, name="Nokey", sim={}, status="available", max_visitors=3)
    check("version 4 without a key: refused", (await c.expect("error"))["code"], "bad_message")
    sk, key = keypair()
    await c.send(type="hello", proto=P4, name="K1", key=key.upper(), sim={}, status="available", max_visitors=3)
    check("a key is lower-case hex", (await c.expect("error"))["code"], "bad_message")
    await c.send(type="hello", proto=P4, name="K1", key=key, sim={}, status="available", max_visitors=3)
    ch = await c.expect("challenge")
    check("a key: a challenge (32 hex)", len(ch["nonce"]), 32)
    other, _ = keypair()
    await c.send(type="prove", signature=proof(other, "127.0.0.1:%d" % L.port, ch["nonce"], key))
    e = await c.expect("error")
    check("signed by another key: bad_proof, and closed", (e["code"], await closed_code(c)), ("bad_proof", 4401))
    c, e = await hello4(L, "K2", host="127.0.0.1:1")
    check("signed for another lobby (the one it dialled was not this): bad_proof", e.get("code"), "bad_proof")
    a = await join(L, "V4 A", proto=P4)
    w = a.welcome
    check("proved: welcome, version 4, its key, the groups' limits, not an operator",
          (w["proto"], w["key"], w["limits"]["groups"], w["limits"]["group_members"], w.get("admin", False)), (P4, a.key, 20, 64, False))
    old = await join(L, "V3 Old")
    lob = await a.last_lobby()
    o = office(lob, "V4 A")
    check("version 4 sees only version 4; it takes everything, its key the one it proved",
          ([x["name"] for x in lob["offices"]], o["world"], o["links"], o["whisper"], o["key"], o["public"]), (["V4 A"], True, True, True, a.key, False))
    await old.send(type="knock", to=a.id)
    check("a version-3 office cannot knock on a version-4 one", (await old.expect("error"))["code"], "not_found")
    await a.send(type="link", endpoint="ep-not-mine", addr={})
    check("a link's endpoint is the proved key", (await a.expect("error"))["code"], "bad_message")
    await a.send(type="hello", proto=P4, name="again")
    check("a second hello: already_hello", (await a.expect("error"))["code"], "already_hello")
    for x in (a, old):
        await x.send(type="bye")


async def t_v4_takeover_and_resume(L):
    a, b = await join(L, "T A", proto=P4), await join(L, "T B", proto=P4)
    await visit(a, b)
    secret = a.welcome["resume"]
    a2, w = await hello4(L, "T A again", sk=a.sk)
    check("the key says hello again (a restart): a new session, under its new name", (w["resumed"], w["id"] != a.id, w["name"]), (False, True, "T A again"))
    check("its old connection is closed", await closed_code(a), 4000)
    e = await b.expect("visit_ended")
    check("and its old session's visits end `gone`", e["reason"], "gone")
    await a2.quiet()
    await a2.ws.close()
    await asyncio.sleep(0.2)
    r, w = await hello4(L, "ignored", sk=a.sk, resume=w["resume"])
    check("resuming takes the key proved again, and the secret: the same office", (w["resumed"], w["name"]), (True, "T A again"))
    x, w2 = await hello4(L, "Thief")
    await x.send(type="bye")
    stranger_sk, _ = keypair()
    s, w3 = await hello4(L, "Stranger", sk=stranger_sk, resume=w["resume"])
    check("a resume secret with another key resumes nothing", (w3["resumed"], w3["id"] != w["id"]), (False, True))
    for c in (r, b, s):
        await c.send(type="bye")
    check("the old secret is spent too", secret != w["resume"], True)


async def t_flood(L):
    """A connection that sends faster than 20 messages a second (in bursts of 60) is closed; its session held."""
    a = await join(L, "Flood", proto=P4)
    try:
        for _ in range(100):
            await a.send(type="status", status="available")
    except ConnectionClosed:
        pass   # (closed under its feet: a client still sending sees no close frame)
    check("a flood of messages: closed", await closed_code(a, 3) != "open", True)
    st = {o["name"]: o for o in (await L.state())["offices"]}
    check("its session held, as for any drop", (st["Flood"]["connected"]), False)


async def t_v4_directory(L):
    a = await join(L, "Dir A", proto=P4, about={"text": " A room with a view\n", "tags": ["cozy", "music"]}, public=True)
    b = await join(L, "Dir B", proto=P4)
    o = office(b.view, "Dir A")
    check("hello with about and a public door: in the lobby's view", (o["about"], o["public"]), ({"text": "A room with a view", "tags": ["cozy", "music"]}, True))
    await b.send(type="about", about={"text": "x", "tags": ["Bad Tag"]})
    check("a tag is lower-case letters, digits and -", (await b.expect("error"))["code"], "bad_message")
    await b.send(type="about", about={"text": "x", "tags": ["a", "b", "c", "d", "e", "f"]})
    check("at most 5 tags", (await b.expect("error"))["code"], "bad_message")
    await b.send(type="about", about={"text": "Quiet office", "tags": ["work", "work"]})
    o = office(await a.last_lobby(), "Dir B")
    check("about, its tags once each", o["about"], {"text": "Quiet office", "tags": ["work"]})
    await a.send(type="door", public=False)
    check("a door made not public", office(await b.last_lobby(), "Dir A")["public"], False)
    page = json.loads(await asyncio.to_thread(L.get, "/api/lobby"))["lobby"]
    check("the public page: rooms that describe themselves, no keys", sorted(r["name"] for r in page["rooms"]), ["Dir A", "Dir B"])
    check("and nothing of their keys", "key" in json.dumps(page["rooms"]), False)
    await b.send(type="about", about=None)
    await b.quiet()
    await asyncio.sleep(2.1)   # (the page reads the lobby again at most every 2 s)
    page = json.loads(await asyncio.to_thread(L.get, "/api/lobby"))["lobby"]
    check("about taken back: off the page, counted only", ([r["name"] for r in page["rooms"]], page["online"]), (["Dir A"], 2))
    html = await asyncio.to_thread(L.get, "/")
    check("/ is the page", "<html" in html, True)
    for c in (a, b):
        await c.send(type="bye")


async def t_v4_bans(L, admin_sk):
    op = await join(L, "Operator", proto=P4, sk=admin_sk)
    b = await join(L, "Bad Actor", proto=P4)
    check("an operator's welcome says so", op.welcome.get("admin"), True)
    await b.send(type="admin", action="ban", key=op.key)
    check("anyone else: not_admin", (await b.expect("error"))["code"], "not_admin")
    await op.send(type="admin", action="ban", key=b.key, minutes=5, text="spam")
    e = await b.expect("error")
    bans = await op.expect("lobby_bans")
    check("banned: told why, closed 4403", (e["code"], "spam" in e["message"], await closed_code(b)), ("banned", True, 4403))
    check("the operator has the bans", [(x["key"], x["reason"], x["until"] is not None) for x in bans["bans"]], [(b.key, "spam", True)])
    c, e = await hello4(L, "Back Again", sk=b.sk)
    check("its key comes back: refused at its proof, banned", (e["type"], e["code"], await closed_code(c)), ("error", "banned", 4403))
    await op.send(type="admin", action="unban", key=b.key)
    check("unbanned", (await op.expect("lobby_bans"))["bans"], [])
    b2 = await join(L, "Bad Actor", proto=P4, sk=b.sk)
    check("and in again", b2.welcome["name"], "Bad Actor")
    for x in (op, b2):
        await x.send(type="bye")


async def t_v4_groups(L):
    a, b, c = await join(L, "G A", proto=P4), await join(L, "G B", proto=P4), await join(L, "G C", proto=P4)
    g = lambda m: m["type"] == "group"
    await a.send(type="group_create", group="-bad")
    check("a group's name is a name", (await a.expect("error"))["code"], "name_invalid")
    await a.send(type="group_create", group="Hollow", about="  the valley folk ")
    v = await a.expect("group")
    check("made: its owner the only member, by key", (v["name"], v["about"], v["open"], v["owner"], [m["key"] for m in v["members"]], v["pending"]),
          ("Hollow", "the valley folk", False, a.key, [a.key], []))
    await b.send(type="group_create", group="hollow")
    check("a group's name is taken whatever its case", (await b.expect("error"))["code"], "name_taken")
    await b.send(type="group_join", group="HOLLOW")
    w = await b.expect("group_answered")
    r = await a.expect("group_request")
    check("asking to join a group that is not open: it waits, its ops are asked", (w.get("accept"), r["group"], r["from"]["key"], r["from"]["name"]), (None, "Hollow", b.key, "G B"))
    v = await a.expect("group")
    check("and its owner's view has the request", [p["key"] for p in v["pending"]], [b.key])
    await c.send(type="group_answer", group="Hollow", key=b.key, accept=True)
    check("only its ops answer", (await c.expect("error"))["code"], "not_op")
    await a.send(type="group_answer", group="Hollow", key=b.key, accept=True)
    ans = await b.expect("group_answered", skip=("lobby", "group"))
    vb = [m for m in await b.quiet() if g(m)][-1]
    check("let in: told so, and the group whole, without its requests", (ans["accept"], [m["key"] for m in vb["members"]], vb.get("pending")), (True, [a.key, b.key], None))
    await b.send(type="group_say", group="Hollow", text=" hi all ")
    la, lb = await a.expect("group_chat", skip=("lobby", "group")), await b.expect("group_chat", skip=("lobby", "group"))
    check("a group's chat: to its members, the sayer too", (la["group"], la["text"], la["from"]["key"], lb["text"]), ("Hollow", "hi all", b.key, "hi all"))
    check("and nobody else", [m["type"] for m in await c.quiet() if m["type"] not in ("lobby",)], [])
    await c.send(type="group_say", group="Hollow", text="let me in")
    check("a non-member says nothing there", (await c.expect("error"))["code"], "not_found")
    await a.send(type="group_set", group="Hollow", open=True)
    await a.quiet()
    await c.send(type="group_join", group="Hollow")
    vc = await c.expect("group")
    check("an open group takes anyone who asks", [m["key"] for m in vc["members"]], [a.key, b.key, c.key])
    await c.send(type="groups")
    lst = await c.expect("groups", skip=("lobby", "group"))
    check("the directory: its name, about, size, how many online", [(x["name"], x["open"], x["members"], x["online"]) for x in lst["groups"]], [("Hollow", True, 3, 3)])
    await a.send(type="group_op", group="Hollow", key=b.key, op=True)
    await a.quiet()
    await b.send(type="group_remove", group="Hollow", key=a.key)
    check("its owner cannot be taken out", (await b.expect("error", skip=("lobby", "group")))["code"], "not_op")
    await b.send(type="group_remove", group="Hollow", key=c.key)
    gl = await c.expect("group_left", skip=("lobby", "group"))
    check("an op takes a member out: told so", (gl["group"], gl["reason"]), ("Hollow", "removed"))
    await b.quiet()
    await a.send(type="group_leave", group="Hollow")
    await a.expect("group_left", skip=("lobby", "group"))
    vb = [m for m in await b.quiet() if g(m)][-1]
    check("its owner leaves: its first op owns it", (vb["owner"], vb["ops"], [m["key"] for m in vb["members"]]), (b.key, [], [b.key]))
    st = await L.state()
    check("the lobby keeps it", st["groups"], ["Hollow"])
    await b.send(type="group_leave", group="Hollow")
    await b.expect("group_left", skip=("lobby", "group"))
    st = await L.state()
    check("the last member leaving ends it", st["groups"], [])
    for x in (a, b, c):
        await x.send(type="bye")


async def t_v4_groups_kept():
    """A lobby with a data directory keeps its groups across a restart, and a member coming back hears its groups."""
    d = os.path.join(TMP, "kept")
    K = await Lobby("--data-dir", d).start()
    try:
        a = await join(K, "Keeper", proto=P4)
        await a.send(type="group_create", group="Lasting", open=True)
        await a.expect("group")
        await a.send(type="bye")
    finally:
        await K.stop()
    check("groups.json written", os.path.isfile(os.path.join(d, "groups.json")), True)
    K = await Lobby("--data-dir", d).start()
    try:
        c, w = await hello4(K, "Keeper", sk=a.sk)
        v = await c.expect("group")
        check("after a restart: the group, told to its member as it says hello", (v["name"], v["owner"]), ("Lasting", a.key))
        await c.send(type="bye")
    finally:
        await K.stop()


async def t_no_v3():
    K = await Lobby().start()
    try:
        c = Client(await connect(K.url))
        await c.send(type="hello", proto=P3, name="Old")
        e = await c.expect("error")
        check("without --v3, version 3 is refused, saying what it speaks", (e["code"], P4 in e["message"], P3 in e["message"]), ("proto_unsupported", True, False))
        await c.close()
    finally:
        await K.stop()


async def main():
    admin_sk, admin_key = keypair()
    L = await Lobby("--v3", "--admin", admin_key, "--title", "Test lobby").start()
    S = await Lobby("--v3", "--resume", "1").start()
    T = await Lobby("--v3", "--knock-ttl", "1").start()
    tests = [(t, L) for t in (t_hello, t_hello_rules, t_knock_refusals, t_visit, t_end_and_full, t_resume, t_resume_replays_visits, t_rename,
                               t_link_withdrawn, t_keys, t_watch, t_link, t_chat, t_whisper, t_v4_hello, t_v4_takeover_and_resume, t_flood, t_v4_directory,
                               t_v4_groups)]
    tests += [(t_resume_window_and_bye, S), (t_knock_timeout_and_health, T)]
    try:
        for t, lob in tests:
            print("\n" + t.__name__[2:])
            try:
                await t(lob)
            except Exception as e:
                global fails
                fails += 1
                print("FAIL  %s raised %s: %s" % (t.__name__, type(e).__name__, e))
            await asyncio.sleep(0.2)
            await lob.reset()   # every session a test dropped is held for the resume window: end them before the next
        print("\nv4_bans")
        await t_v4_bans(L, admin_sk)
        await L.reset()
        for t in (t_v4_groups_kept, t_no_v3):
            print("\n" + t.__name__[2:])
            await t()
        st = await L.state()
        check("\nevery office left the lobby", (len(st["offices"]), st["visits"], st["knocks"]), (0, 0, 0))
    finally:
        await L.stop()
        await S.stop()
        await T.stop()
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n%d failure(s)" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
