#!/usr/bin/env python3
"""The lobby (../../visit/SPEC.md 6, ../../LOBBY.md), tested from outside: the real `lobby` binary, on free local
ports, talked to over WebSockets. Version 3 as the Python lobby spoke it (its own tests, carried over), and
version 4: keys proved at hello, a key back taking its session over, bans, the directory, worlds (public or with a
password; kept across a restart), linked browsers (3.1), the public page. No network beyond localhost.

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
    check("proved: welcome, version 4, its key, the worlds' limits, not an operator",
          (w["proto"], w["key"], w["limits"]["groups"], w["limits"]["group_members"], w["limits"]["password"], w.get("admin", False)), (P4, a.key, 20, 50, [4, 64], False))
    old = await join(L, "V3 Old")
    lob = await a.last_lobby()
    o = office(lob, "V4 A")
    check("version 4 sees only version 4; it takes everything, its key the one it proved",
          ([x["name"] for x in lob["offices"]], o["world"], o["links"], o["whisper"], o["key"], "public" in o), (["V4 A"], True, True, True, a.key, False))
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


def web_pass(sk, key, host, expires=None, nonce=None):
    import secrets as _s, time as _t
    expires = expires or int(_t.time()) + 300
    nonce = nonce or _s.token_hex(16)
    sig = sk.sign(("%s pass\n%s\n%s\n%d\n%s" % (P4, host, key, expires, nonce)).encode()).hex()
    return {"key": key, "expires": expires, "nonce": nonce, "signature": sig}


UA = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"


async def browser(lob, sk=None, pass_=None, host=None, verify=None):
    """A browser (3.1): its own key (made, unless given), proved, with a web pass if given: (client, what answered:
    web_welcome, browser_code, or the error that refused it)."""
    sk, key = (sk, sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()) if sk else keypair()
    c = Client(await connect(lob.url, user_agent_header=UA))
    c.sk, c.key = sk, key
    await c.send(type="hello", proto=P4, browser=key, **({"pass": pass_} if pass_ else {}), **({"verify": verify} if verify else {}))
    m = json.loads(await asyncio.wait_for(c.ws.recv(), 2))
    if m["type"] != "challenge":
        return c, m
    await c.send(type="prove", signature=sk.sign(("%s browser\n%s\n%s\n%s" % (P4, host or "127.0.0.1:%d" % lob.port, m["nonce"], key)).encode()).hex())
    while True:
        m = json.loads(await asyncio.wait_for(c.ws.recv(), 2))
        if m["type"] in ("web_welcome", "browser_code", "browser_confirmed", "error"):
            return c, m


async def t_browsers(L):
    """3.1: a browser with its own key, linked to an office (by a web pass its office made, or by the pairing code it
    shows, typed in the office's room), speaks for the office while the office is in the lobby: its chat, whispers,
    worlds, knocks; never its door nor answering. Linked, it comes back by its key alone; unlinked by its office or by
    itself, it is out."""
    host = "127.0.0.1:%d" % L.port
    a, b = await join(L, "Web A", proto=P4), await join(L, "Web B", proto=P4)
    p = web_pass(a.sk, a.key, host)
    w, wel = await browser(L, pass_=p)
    check("a pass: the browser linked, and in as its office", (wel["type"], wel["id"], wel["name"], wel["key"], "resume" in wel), ("web_welcome", a.id, "Web A", a.key, False))
    lob = await w.expect("lobby")
    check("and it has the lobby, both offices", sorted(o["name"] for o in lob["offices"]), ["Web A", "Web B"])
    bl = await a.expect("browsers")
    check("its office hears its linked browsers: this one, what it is, online", [(x["key"], x["agent"], x["online"], len(x["pictures"])) for x in bl["browsers"]],
          [(w.key, "Firefox on Linux", True, 3)])
    w2, e = await browser(L, pass_=p)
    check("the same pass again: refused, closed", (e.get("code"), await closed_code(w2)), ("bad_proof", 4401))
    _, e = await browser(L, pass_=web_pass(a.sk, a.key, host, expires=1))
    check("an expired one: refused", e.get("code"), "bad_proof")
    _, e = await browser(L, pass_=web_pass(a.sk, a.key, "elsewhere.example"))
    check("one for another lobby: refused", e.get("code"), "bad_proof")
    other, okey = keypair()
    _, e = await browser(L, pass_=web_pass(other, okey, host))
    check("one from a workroom not in the lobby: refused", e.get("code"), "not_in_lobby")
    x, e = await browser(L, sk=w.sk, host="elsewhere.example")
    check("a browser's key is proved for this lobby: a proof for another, refused", (e.get("code"), await closed_code(x)), ("bad_proof", 4401))
    await w.close()
    await asyncio.sleep(0.2)
    w, wel = await browser(L, sk=w.sk)
    check("linked: back by its key alone (a reload, another day)", (wel["type"], wel["id"]), ("web_welcome", a.id))
    await a.quiet(); await b.quiet(); await w.quiet()
    await w.send(type="chat", text="hello from the web")
    la, lb, lw = await a.expect("lobby_chat"), await b.expect("lobby_chat"), await w.expect("lobby_chat")
    check("the browser says a line in the lobby: as its office, to everyone (its office too)", (lb["from"]["id"], lb["text"], la["text"], lw["text"]), (a.id, "hello from the web", "hello from the web", "hello from the web"))
    await w.send(type="whisper", to=b.id, text="psst")
    wb = await b.expect("whisper")
    check("a whisper from the browser: from its office", (wb["from"]["id"], wb["text"]), (a.id, "psst"))
    await b.send(type="whisper", to=a.id, text="back")
    check("one to its office: the browser has it too", ((await a.expect("whisper"))["text"], (await w.expect("whisper"))["text"]), ("back", "back"))
    await w.send(type="knock", to=b.id, message="from the web")
    ks, kb = await a.expect("knock_sent"), await b.expect("knock")
    check("a knock from the browser: its office knocks (its visit-client sees knock_sent), the door asked", (ks["to"], kb["from"]["id"], kb["message"]), (b.id, a.id, "from the web"))
    check("and the browser sees it go", (await w.expect("knock_sent"))["to"], b.id)
    await b.send(type="knock", to=a.id)
    k = await a.expect("knock")
    check("a knock on its office: the browser is told too", (await w.expect("knock"))["id"], k["id"])
    for t, extra in (("answer", {"knock": k["id"], "accept": True}), ("door", {"public": True}), ("link", {"endpoint": None, "addr": None}), ("status", {"status": "busy"}),
                     ("browser_code", {"code": "ABCD-EFGH"}), ("browser_revoke", {"key": w.key})):
        await w.send(type=t, **extra)
        e = await w.expect("error", skip=("lobby", "knock_sent", "knock", "answered"))
        check("from the browser, never %s: not_from_web" % t, e["code"], "not_from_web")
    await w.send(type="groups")
    gl = await w.expect("groups", skip=("lobby", "knock_cancelled", "answered", "knock_sent", "knock"))
    check("the world list, asked from the browser, to the browser", isinstance(gl["groups"], list), True)
    await w.send(type="group_create", group="Webbers", open=True)
    g1, g2 = await a.expect("group", skip=("lobby", "knock_cancelled", "answered", "browsers")), await w.expect("group", skip=("lobby", "knock_cancelled", "answered", "knock_sent", "knock"))
    check("a world made from the browser: the office's, both told", (g1["name"], g1["owner"], g2["name"]), ("Webbers", a.key, "Webbers"))
    await w.send(type="chat", text="")
    check("its refusals go to the browser alone", ((await w.expect("error", skip=("lobby", "group", "knock_cancelled", "answered")))["code"], [m["type"] for m in await a.quiet() if m["type"] == "error"]), ("bad_message", []))

    # A browser not linked: a pairing code, its office types it in its room, sees it, allows it.
    p1, code = await browser(L)
    check("a browser not linked: a pairing code (8 letters and digits) and its 3 pictures", (code["type"], len(code["code"]), len(code["pictures"]), code["expires_in"]),
          ("browser_code", 8, 3, 600))
    await p1.send(type="chat", text="hi")
    check("waiting, it does nothing", (await p1.expect("error"))["code"], "not_linked")
    await b.quiet()
    await b.send(type="browser_code", code="zzzz-zzzz")
    check("a code nobody waits with: not_found", (await b.expect("error"))["code"], "not_found")
    await b.send(type="browser_code", code=code["code"][:4].lower() + "-" + code["code"][4:])
    pend = await b.expect("browser_pending")
    check("its office types its code: the browser waiting, the same pictures, what it is", (pend["key"], pend["pictures"], pend["agent"]), (p1.key, code["pictures"], "Firefox on Linux"))
    await b.send(type="browser_allow", code=code["code"])
    cf = await b.expect("browser_confirm")
    ing = await p1.expect("browser_confirming")
    check("3.2, allowed: not linked yet; its room gets a confirmation (6 digits), the browser is told who would link it",
          (cf["key"], len(cf["confirm"]), cf["expires_in"], ing["office"]), (p1.key, 6, 300, "Web B"))
    check("and nothing is linked meanwhile", (await L.state())["pending"], 1)
    await p1.send(type="browser_verify", confirm="000000" if cf["confirm"] != "000000" else "111111")
    check("the browser shows a wrong one: refused, still waiting", (await p1.expect("error"))["code"], "bad_confirm")
    await p1.send(type="chat", text="hi")
    check("waiting still: nothing else", (await p1.expect("error"))["code"], "not_linked")
    await p1.send(type="browser_verify", confirm=cf["confirm"][:3] + " " + cf["confirm"][3:])
    wel = await p1.expect("web_welcome")
    check("it shows its room's (typed on its page): linked, in as that office", (wel["id"], wel["name"]), (b.id, "Web B"))
    bl = await b.expect("browsers", skip=("lobby",))
    check("and in its office's list", [x["key"] for x in bl["browsers"]], [p1.key])
    check("the code is spent", (await L.state())["pending"], 0)
    await b.send(type="browser_revoke", key=p1.key)
    e = await p1.expect("error", skip=("lobby", "group"))
    check("unlinked by its office: told, closed 4403", (e["code"], await closed_code(p1)), ("unlinked", 4403))
    p2, e = await browser(L, sk=p1.sk)
    check("and its key no longer links it: a code again", e["type"], "browser_code")
    await p2.close()
    await w.send(type="browser_forget")
    check("a browser unlinks itself: closed", await closed_code(w), 1000)
    w3, e = await browser(L, sk=w.sk)
    check("its key forgotten too", e["type"], "browser_code")
    await w3.close()
    w4, wel4 = await browser(L, pass_=web_pass(a.sk, a.key, host))
    await w4.quiet()
    await a.send(type="bye")
    e = await w4.expect("error", skip=("lobby", "group"))
    check("its office leaves: the browser is told, and closed", (e["code"], await closed_code(w4)), ("office_gone", 4410))
    w5, e = await browser(L, sk=w4.sk)
    check("linked, its office not in the lobby: refused until it is", (e.get("code"), await closed_code(w5)), ("not_in_lobby", 4410))
    await b.send(type="bye")


async def t_browsers_guarded(L):
    """3.1, what keeps linked browsers safe: a link from another workroom never moves a linked browser by itself (the
    page asks its person: `switch`), and moving it puts its other tabs out; a workroom's fifth browser in at once is
    refused, and nothing of it is kept; one address has at most 4 browsers waiting with codes."""
    host = "127.0.0.1:%d" % L.port
    a, b = await join(L, "Guard A", proto=P4), await join(L, "Guard B", proto=P4)
    w, wel = await browser(L, pass_=web_pass(a.sk, a.key, host))
    check("a browser linked to A", wel["name"], "Guard A")
    w2, _ = await browser(L, sk=w.sk)
    pb = web_pass(b.sk, b.key, host)
    x, e = await browser(L, sk=w.sk, pass_=pb)
    check("B's link, to A's browser: refused, saying whose it is and whose it would be, closed 4409",
          (e.get("code"), "Guard A" in e.get("message", "") and "Guard B" in e.get("message", ""), await closed_code(x)), ("linked_elsewhere", True, 4409))
    x = Client(await connect(L.url, user_agent_header=UA))
    await x.send(type="hello", proto=P4, browser=w.key, switch=True, **{"pass": pb})
    ch = await x.expect("challenge")
    await x.send(type="prove", signature=w.sk.sign(("%s browser\n%s\n%s\n%s" % (P4, host, ch["nonce"], w.key)).encode()).hex())
    wel = await x.expect("web_welcome", skip=("lobby",))
    check("its person agrees (switch): the same link, not spent by the refusal, makes it B's", wel["name"], "Guard B")
    e1 = await w.expect("error", skip=("lobby", "group"))
    check("and its tabs that were A's are out at once", (e1["code"], await closed_code(w), (await w2.expect("error", skip=("lobby", "group")))["code"]), ("unlinked", 4403, "unlinked"))
    await x.close()
    tabs = [ (await browser(L, sk=w.sk))[0] for _ in range(4) ]
    y, e = await browser(L, sk=w.sk)
    check("a fifth tab of B's in at once: refused (4429)", (e.get("code"), await closed_code(y)), ("too_many", 4429))
    await asyncio.sleep(0.2)
    check("and nothing of it kept (4 browsers in the lobby)", (await L.state())["browsers"], 4)
    for t in tabs:
        await t.close()
    waiting = [await browser(L) for _ in range(4)]
    check("4 browsers waiting with codes, from one address", [m["type"] for _, m in waiting], ["browser_code"] * 4)
    z, e = await browser(L)
    check("a fifth: busy", (e.get("code"), await closed_code(z)), ("busy", 1013))
    for c, _ in waiting:
        await c.close()
    for c in (a, b):
        await c.send(type="bye")


async def t_browsers_confirmed(L):
    """3.2: a code typed in a room links the browser only once the browser shows the room's confirmation: by a tab
    its room's QR code opens (`hello` with `verify`), or typed. A confirmation is that browser's alone, from that
    room, for its time; wrong ones are counted."""
    a, b = await join(L, "Conf A", proto=P4), await join(L, "Conf B", proto=P4)
    w, code = await browser(L)
    await a.send(type="browser_allow", code=code["code"])
    cf = await a.expect("browser_confirm")
    await w.expect("browser_confirming")
    x, e = await browser(L, verify=cf["confirm"])
    check("3.2.2: a phone scans the room's QR code (another browser): told what it confirmed, not linked, closed",
          (e["type"], e.get("office"), e.get("agent"), e.get("pictures"), await closed_code(x)), ("browser_confirmed", "Conf A", "Firefox on Linux", code["pictures"], 1000))
    check("and the browser that waits for it is in, by itself", (await w.expect("web_welcome"))["name"], "Conf A")
    x2, e = await browser(L, verify=cf["confirm"])
    check("the same QR code again: spent", (e.get("code"), await closed_code(x2)), ("no_confirm", 4401))
    await w.close()
    w1, code = await browser(L)
    await a.send(type="browser_allow", code=code["code"])
    cf = await a.expect("browser_confirm", skip=("lobby", "browsers"))
    await w1.expect("browser_confirming")
    t, wel = await browser(L, sk=w1.sk, verify=cf["confirm"])
    check("a tab of that browser itself, the QR code's (#verify=): linked, in as A", (wel["type"], wel.get("name")), ("web_welcome", "Conf A"))
    check("and its waiting tab is in too", (await w1.expect("web_welcome"))["name"], "Conf A")
    await t.close()
    await w1.close()
    w2, code = await browser(L)
    await a.send(type="browser_allow", code=code["code"])
    cf = await a.expect("browser_confirm", skip=("lobby", "browsers"))
    await b.send(type="browser_allow", code=code["code"])
    cfb = await b.expect("browser_confirm")
    wrong = [c for c in ("000000", "111111", "222222", "333333", "444444", "555555") if c not in (cf["confirm"], cfb["confirm"])][:5]
    for c in wrong:
        await w2.send(type="browser_verify", confirm=c)
        e = await w2.expect("error", skip=("browser_confirming",))
    check("five wrong: every confirmation of it off", e["code"], "bad_confirm")
    await w2.send(type="browser_verify", confirm=cf["confirm"])
    check("then even the right one is refused (its rooms ask again)", (await w2.expect("error", skip=("browser_confirming",)))["code"], "bad_confirm")
    await w2.close()
    # 3.2.1: a confirmation is its browser's (its key's), not one connection's: a phone that drops its waiting tab
    # (its camera opened, say) finds it again; a QR code's tab links with no tab waiting at all.
    w3, code = await browser(L)
    await a.send(type="browser_allow", code=code["code"])
    cf = await a.expect("browser_confirm", skip=("lobby", "browsers"))
    await w3.expect("browser_confirming")
    await w3.close()
    await asyncio.sleep(0.2)
    w4, code4 = await browser(L, sk=w3.sk)
    again = await w4.expect("browser_confirming")
    check("its waiting tab back after a drop: a new code, and the confirmation still asked", (code4["type"], again["office"]), ("browser_code", "Conf A"))
    await w4.send(type="browser_verify", confirm=cf["confirm"])
    check("shown then: linked", (await w4.expect("web_welcome"))["name"], "Conf A")
    await w4.close()
    w5, code = await browser(L)
    await a.send(type="browser_allow", code=code["code"])
    cf = await a.expect("browser_confirm", skip=("lobby", "browsers"))
    await w5.close()
    await asyncio.sleep(0.2)
    t5, wel = await browser(L, sk=w5.sk, verify=cf["confirm"])
    check("the QR code's tab, no tab of it waiting any more: linked", (wel["type"], wel.get("name")), ("web_welcome", "Conf A"))
    await t5.close()
    for c in (a, b):
        await c.send(type="bye")


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
    check("hello with about (3.0's public door ignored): in the lobby's view", (o["about"], "public" in o), ({"text": "A room with a view", "tags": ["cozy", "music"]}, False))
    await b.send(type="about", about={"text": "x", "tags": ["Bad Tag"]})
    check("a tag is lower-case letters, digits and -", (await b.expect("error"))["code"], "bad_message")
    await b.send(type="about", about={"text": "x", "tags": ["a", "b", "c", "d", "e", "f"]})
    check("at most 5 tags", (await b.expect("error"))["code"], "bad_message")
    await b.send(type="about", about={"text": "Quiet office", "tags": ["work", "work"]})
    o = office(await a.last_lobby(), "Dir B")
    check("about, its tags once each", o["about"], {"text": "Quiet office", "tags": ["work"]})
    await a.send(type="door", public=True)
    check("3.0's door message: taken, nothing said", [m["type"] for m in await a.quiet() if m["type"] == "error"], [])
    page = json.loads(await asyncio.to_thread(L.get, "/api/lobby"))["lobby"]
    check("the public page: who is here with a line, no keys", sorted(r["name"] for r in page["users"]), ["Dir A", "Dir B"])
    check("and nothing of their keys", "key" in json.dumps(page["users"]), False)
    await b.send(type="about", about=None)
    await b.quiet()
    await asyncio.sleep(2.1)   # (the page reads the lobby again at most every 2 s)
    page = json.loads(await asyncio.to_thread(L.get, "/api/lobby"))["lobby"]
    check("about taken back: off the page, counted only", ([r["name"] for r in page["users"]], page["online"]), (["Dir A"], 2))
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


async def group_of(c, name, timeout=2.0):
    """The next view of world `name` this client hears (other worlds' and the lobby's skipped)."""
    while True:
        m = await c.expect("group", timeout=timeout, skip=("lobby", "group", "browsers"))
        if m["name"] == name:
            return m


async def t_v4_groups(L):
    """Worlds (the protocol's groups): public (anyone joins) or with a password; their owner's to change, to take
    members out of and keep keys out of, to hand on."""
    a, b, c = await join(L, "G A", proto=P4), await join(L, "G B", proto=P4), await join(L, "G C", proto=P4)
    await a.send(type="group_create", group="-bad", open=True)
    check("a world's name is a name", (await a.expect("error"))["code"], "name_invalid")
    await a.send(type="group_create", group="Hollow")
    check("a world is public or has a password: neither, refused (3.0's by-asking)", (await a.expect("error"))["code"], "bad_message")
    await a.send(type="group_create", group="Hollow", about="  the valley folk ", open=True)
    v = await a.expect("group")
    check("made, public: its owner the only member, its bans its owner's to see", (v["name"], v["about"], v["open"], v["locked"], v["owner"], [m["key"] for m in v["members"]], v["bans"], v["ops"]),
          ("Hollow", "the valley folk", True, False, a.key, [a.key], [], []))
    await b.send(type="group_create", group="hollow", open=True)
    check("a world's name is taken whatever its case", (await b.expect("error"))["code"], "name_taken")
    await b.send(type="group_join", group="HOLLOW")
    vb = await group_of(b, "Hollow")
    check("a public world: anyone in at once, and the world whole (no bans: not its owner)", ([m["key"] for m in vb["members"]], "bans" in vb), ([a.key, b.key], False))
    await b.send(type="group_say", group="Hollow", text=" hi all ")
    la, lb = await a.expect("group_chat", skip=("lobby", "group")), await b.expect("group_chat", skip=("lobby", "group"))
    check("a world's chat: to its members, the sayer too", (la["group"], la["text"], la["from"]["key"], lb["text"]), ("Hollow", "hi all", b.key, "hi all"))
    check("and nobody else", [m["type"] for m in await c.quiet() if m["type"] not in ("lobby",)], [])
    await c.send(type="group_say", group="Hollow", text="let me in")
    check("a non-member says nothing there", (await c.expect("error"))["code"], "not_found")

    await a.send(type="group_create", group="Den", password="abc")
    check("a password is 4 to 64 characters", (await a.expect("error", skip=("lobby", "group")))["code"], "bad_password")
    await a.send(type="group_create", group="Den", about="by invitation", password="open sesame")
    v = await group_of(a, "Den")
    check("made with a password: locked, not public", (v["locked"], v["open"], [m["key"] for m in v["members"]]), (True, False, [a.key]))
    await c.send(type="group_join", group="Den")
    check("joining it without one: password_required", (await c.expect("error"))["code"], "password_required")
    await c.send(type="group_join", group="den", password="nope")
    check("with the wrong one: wrong_password", (await c.expect("error", skip=("lobby", "group")))["code"], "wrong_password")
    await c.send(type="group_join", group="Den", password="open sesame")
    vc = await group_of(c, "Den")
    check("with its password: in", [m["key"] for m in vc["members"]], [a.key, c.key])
    await a.quiet()
    await c.send(type="groups")
    lst = await c.expect("groups", skip=("lobby", "group"))
    check("the directory: name, public, locked, size, online; no password", ([(x["name"], x["open"], x["locked"], x["members"], x["online"]) for x in lst["groups"]], "password" in json.dumps(lst)),
          ([("Den", False, True, 2, 2), ("Hollow", True, False, 2, 2)], False))

    await c.send(type="group_remove", group="Den", key=a.key)
    check("only its owner takes members out", (await c.expect("error", skip=("lobby", "group")))["code"], "not_owner")
    await a.send(type="group_remove", group="Den", key=c.key, ban=True)
    gl = await c.expect("group_left", skip=("lobby", "group"))
    va = await group_of(a, "Den")
    check("its owner takes a member out and keeps them out: told so; its bans", (gl["group"], gl["reason"], va["bans"]), ("Den", "banned", [c.key]))
    await c.send(type="group_join", group="Den", password="open sesame")
    check("kept out, the password does not let them in", (await c.expect("error", skip=("lobby", "group")))["code"], "banned")
    await a.send(type="group_unban", group="Den", key=c.key)
    check("let back in: off its bans", (await group_of(a, "Den"))["bans"], [])
    await c.send(type="group_join", group="Den", password="open sesame")
    await group_of(c, "Den")
    await a.quiet()
    await a.send(type="group_set", group="Den", password="new secret")
    va = await group_of(a, "Den")
    check("a new password: its members stay", (va["locked"], [m["key"] for m in va["members"]]), (True, [a.key, c.key]))
    await b.send(type="group_join", group="Den", password="open sesame")
    check("the old one no longer opens it", (await b.expect("error", skip=("lobby", "group")))["code"], "wrong_password")
    await a.send(type="group_set", group="Den", open=True)
    check("made public: its password gone", ((await group_of(a, "Den"))["open"]), True)
    await b.send(type="group_join", group="Den")
    check("and anyone in", [m["key"] for m in (await group_of(b, "Den"))["members"]], [a.key, c.key, b.key])
    await a.send(type="group_set", group="Den", open=False)
    check("not public, and no password: refused", (await a.expect("error", skip=("lobby", "group")))["code"], "bad_message")
    await a.send(type="group_owner", group="Den", key=b.key)
    check("handed on: its new owner", (await group_of(b, "Den"))["owner"], b.key)
    await a.send(type="group_set", group="Den", about="mine")
    check("the old owner changes it no more", (await a.expect("error", skip=("lobby", "group")))["code"], "not_owner")
    await b.send(type="group_delete", group="Den")
    lefts = [(await x.expect("group_left", skip=("lobby", "group")))["reason"] for x in (a, b, c)]
    check("its owner ends it: every member told", lefts, ["deleted"] * 3)

    await a.send(type="group_leave", group="Hollow")
    await a.expect("group_left", skip=("lobby", "group"))
    vb = await group_of(b, "Hollow")
    check("its owner leaves: its oldest member owns it", (vb["owner"], [m["key"] for m in vb["members"]]), (b.key, [b.key]))
    st = await L.state()
    check("the lobby keeps it", st["groups"], ["Hollow"])
    await b.send(type="group_leave", group="Hollow")
    await b.expect("group_left", skip=("lobby", "group"))
    st = await L.state()
    check("the last member leaving ends it", st["groups"], [])
    for x in (a, b, c):
        await x.send(type="bye")


async def t_world_size(S):
    """A world takes the members its lobby says (--world-size), at most."""
    a, b, c = await join(S, "Z A", proto=P4), await join(S, "Z B", proto=P4), await join(S, "Z C", proto=P4)
    check("the welcome says the size", a.welcome["limits"]["group_members"], 2)
    await a.send(type="group_create", group="Pair", open=True)
    await a.expect("group")
    await b.send(type="group_join", group="Pair")
    await group_of(b, "Pair")
    await c.send(type="group_join", group="Pair")
    check("a third, past --world-size 2: full", (await c.expect("error"))["code"], "full")
    for x in (a, b, c):
        await x.send(type="bye")


async def t_v4_groups_kept():
    """A lobby with a data directory keeps its worlds and its linked browsers across a restart: a member coming back
    hears its worlds, a linked browser comes back in by its key. A world made before 3.1 that took requests is closed
    until its owner makes it public or gives it a password."""
    d = os.path.join(TMP, "kept")
    K = await Lobby("--data-dir", d).start()
    try:
        a = await join(K, "Keeper", proto=P4)
        await a.send(type="group_create", group="Lasting", open=True)
        await a.expect("group")
        w, wel = await browser(K, pass_=web_pass(a.sk, a.key, "127.0.0.1:%d" % K.port))
        check("a browser linked", wel["type"], "web_welcome")
        await w.close()
        await a.send(type="bye")
    finally:
        await K.stop()
    check("groups.json and browsers.json written", (os.path.isfile(os.path.join(d, "groups.json")), os.path.isfile(os.path.join(d, "browsers.json"))), (True, True))
    with open(os.path.join(d, "groups.json")) as f:
        kept = json.load(f)
    kept.append({"name": "Old Ask", "about": "", "open": False, "owner": a.key, "ops": [], "members": [{"key": a.key, "name": "Keeper", "since": 1}], "pending": [], "created": 1})
    with open(os.path.join(d, "groups.json"), "w") as f:
        json.dump(kept, f)
    K = await Lobby("--data-dir", d).start()
    try:
        c, wel = await hello4(K, "Keeper", sk=a.sk)
        names = sorted([(await c.expect("group"))["name"] for _ in range(2)])
        check("after a restart: its worlds, told to their member as it says hello", names, ["Lasting", "Old Ask"])
        w2, wel = await browser(K, sk=w.sk)
        check("and its linked browser, back in by its key", (wel["type"], wel.get("name")), ("web_welcome", "Keeper"))
        await w2.close()
        o = await join(K, "Outsider", proto=P4)
        await o.send(type="groups")
        lst = {x["name"]: x for x in (await o.expect("groups"))["groups"]}
        check("a world that took requests (3.0): neither public nor locked", (lst["Old Ask"]["open"], lst["Old Ask"]["locked"]), (False, False))
        await o.send(type="group_join", group="Old Ask")
        check("closed: nobody joins", (await o.expect("error"))["code"], "closed")
        await c.send(type="group_set", group="Old Ask", password="at last")
        await group_of(c, "Old Ask")
        await o.send(type="group_join", group="Old Ask", password="at last")
        check("its owner gives it a password: in with it", [m["name"] for m in (await group_of(o, "Old Ask"))["members"]], ["Keeper", "Outsider"])
        for x in (c, o):
            await x.send(type="bye")
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
    S = await Lobby("--v3", "--resume", "1", "--world-size", "2").start()
    T = await Lobby("--v3", "--knock-ttl", "1").start()
    tests = [(t, L) for t in (t_hello, t_hello_rules, t_knock_refusals, t_visit, t_end_and_full, t_resume, t_resume_replays_visits, t_rename,
                               t_link_withdrawn, t_keys, t_watch, t_link, t_chat, t_whisper, t_v4_hello, t_v4_takeover_and_resume, t_browsers, t_browsers_guarded, t_browsers_confirmed, t_flood, t_v4_directory,
                               t_v4_groups)]
    tests += [(t_resume_window_and_bye, S), (t_world_size, S), (t_knock_timeout_and_health, T)]
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
