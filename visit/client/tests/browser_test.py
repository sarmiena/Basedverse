#!/usr/bin/env python3
"""visit.js in real browsers: the real lobby (the `lobby` binary beside visit), two visit-clients (each signed in on its
own workroom: the lobby is joined only then), the test room (room.html, three.js)
open in two headless Chromes driven over the DevTools protocol, A's made with three.js r128 and B's with r160.
B knocks on A, draws A's scene, walks around it with real key presses, sees what A changes, and both pages
reload mid-visit. Then worlds: each draws the other's plot round its own, walks onto it and off, sees where the
other has put it (and nothing else of the other's world), puts the other flush against its own and walks across,
and loses it when the door shuts. Last, links: A sees B's room in a corner, goes
there, comes back, puts it in its world, and unlinks. Run by run-browser.sh, which starts the Chromes.

    python3 browser_test.py path/to/visit-client CDP_PORT_A CDP_PORT_B OUT_DIR
"""
import asyncio
import base64
import functools
import http.server
import json
import math
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "..", "..", "examples")   # visit/examples
import websockets  # noqa: E402

BIN, CDP_A, CDP_B, OUT = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
LOBBY_BIN = os.path.join(os.path.dirname(BIN), "lobby")
PASSWORD = "correct horse battery staple"


def free_ports(n):
    """Ports nothing on this machine listens on now, n apart: the test runs on the host's network (a CI runner's too),
    where a fixed one may be taken."""
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


LOBBY_PORT, WEB_PORT, PORT_A, PORT_B, PORT_C, AUTH_A, AUTH_B, AUTH_C = free_ports(8)


def udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def lan_ip():
    """This machine's main address (TURN's relays are given at it: a browser takes none at 127.0.0.1)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("192.0.2.1", 9))
    ip = s.getsockname()[0]
    s.close()
    return ip


# 4.2 (#71): the lobby's STUN and TURN, and A's page made to connect through TURN alone (as a phone on a mobile
# network, or behind a strict NAT, does): every voice of A's goes through the lobby's relays, B's directly.
TURN_PORT = udp_port()
RELAY_ONLY = """(() => { const P = window.RTCPeerConnection; window.RTCPeerConnection = function (c) { return new P(Object.assign({}, c, { iceTransportPolicy: "relay" })); };
  window.RTCPeerConnection.prototype = P.prototype; })();"""
AUTH = {"A": AUTH_A, "B": AUTH_B, "C": AUTH_C}
TMP = tempfile.mkdtemp()
fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("%s  %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else "\n      got  %r\n      want %r" % (got, want)), flush=True)


class Tab:
    """One Chrome's page, over the DevTools protocol."""

    def __init__(self, port, name):
        self.port, self.name, self.n, self.pending, self.errors = port, name, 0, {}, []

    async def start(self, ws_url=None):
        tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % self.port))
        self.ws = await websockets.connect(ws_url or next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"], max_size=50_000_000)
        self.reader = asyncio.create_task(self._read())
        await self.call("Runtime.enable")
        await self.call("Page.enable")
        await self.call("Emulation.setDeviceMetricsOverride", width=1280, height=720, deviceScaleFactor=1, mobile=False)

    @classmethod
    async def spawn(cls, port, name, url):
        """Another page of that Chrome, in a window of its own (a window apart draws: a tab behind another would not)."""
        ver = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port))
        async with websockets.connect(ver["webSocketDebuggerUrl"], max_size=50_000_000) as bws:
            await bws.send(json.dumps({"id": 1, "method": "Target.createTarget", "params": {"url": "about:blank", "newWindow": True}}))
            while True:
                m = json.loads(await bws.recv())
                if m.get("id") == 1:
                    tid = m["result"]["targetId"]
                    break
        t = cls(port, name)
        t.target_id = tid
        hit = None
        for _ in range(50):
            hit = next((x for x in json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port)) if x.get("id") == tid), None)
            if hit:
                break
            await asyncio.sleep(0.1)
        await t.start(hit["webSocketDebuggerUrl"])
        await t.goto(url)
        return t

    async def close_window(self):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s" % (self.port, self.target_id)).read()
        except Exception:
            pass

    async def _read(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self.pending:
                self.pending.pop(m["id"]).set_result(m.get("result", {"error": m.get("error")}))
            elif m.get("method") == "Runtime.exceptionThrown":
                d = m["params"]["exceptionDetails"]
                self.errors.append(d.get("exception", {}).get("description") or "%s (line %s)" % (d.get("text", "?"), d.get("lineNumber")))
            elif m.get("method") == "Runtime.consoleAPICalled" and m["params"]["type"] == "error":
                self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in m["params"]["args"]))

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        return await asyncio.wait_for(fut, 30)

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    async def until(self, expr, timeout=15):
        for _ in range(int(timeout * 5)):
            v = await self.js(expr)
            if v:
                return v
            await asyncio.sleep(0.2)
        return await self.js(expr)

    async def goto(self, url):
        await self.call("Page.navigate", url=url)

    async def key(self, code, key, vk, down, shift=False):
        await self.call("Input.dispatchKeyEvent", type="keyDown" if down else "keyUp", code=code, key=key, windowsVirtualKeyCode=vk, modifiers=8 if shift else 0)

    async def shot(self, name):
        r = await self.call("Page.captureScreenshot", format="png")
        open(os.path.join(OUT, name), "wb").write(base64.b64decode(r["data"]))

    async def shot_of(self, name, selector):
        """One element alone (the reference UI's pieces, as visit/UI.md shows them); nothing if it is not shown."""
        r = await self.js("(e => e && (b => [b.x, b.y, b.width, b.height])(e.getBoundingClientRect()))(document.querySelector(%s))" % json.dumps(selector))
        if not r or r[2] < 1 or r[3] < 1:
            return
        d = await self.call("Page.captureScreenshot", format="png", clip={"x": r[0], "y": r[1], "width": r[2], "height": r[3], "scale": 1})
        open(os.path.join(OUT, name), "wb").write(base64.b64decode(d["data"]))


class Engine:
    """The engine (engine/SPEC.md 5) hosting a room, asked through its test aid (ENGINE_DEBUG: POST /debug/eval), as a
    Tab is asked: the page's values. It runs inside C's visit (V2.md 5), in the headless Chrome visit carries."""

    def __init__(self, port):
        self.port, self.name, self.errors = port, "C-engine", []

    def _post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=body.encode(), method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"null")

    async def js(self, expr):
        try:
            r = await asyncio.get_running_loop().run_in_executor(None, self._post, "/debug/eval", expr)
            return r.get("value") if isinstance(r, dict) and "value" in r else r
        except Exception:
            return None   # not hosting yet, or the expression threw: as a page without it

    async def until(self, expr, timeout=15):
        for _ in range(int(timeout * 5)):
            v = await self.js(expr)
            if v:
                return v
            await asyncio.sleep(0.2)
        return await self.js(expr)

    def health(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % self.port, timeout=5) as r:
                return json.loads(r.read())
        except Exception as e:
            return {"ok": False, "room": str(e)}

    async def close_window(self):
        pass


async def start_client(name, port, url, user_token=False, tokens=None, extra_args=()):
    """`user_token`: with a user token too (engine/SPEC.md 6.2): then (process, token, user token); `tokens`: (token,
    user token) given, instead of new ones; `extra_args`: more of visit's flags (the engine's)."""
    token = tokens[0] if tokens else secrets.token_hex(32)
    path = os.path.join(TMP, name + ".token")
    with open(path, "w") as f:
        f.write(token)
    extra, utoken = [], None
    if user_token:
        utoken = tokens[1] if tokens else secrets.token_hex(32)
        upath = os.path.join(TMP, name + ".user-token")
        with open(upath, "w") as f:
            f.write(utoken)
        extra = ["--user-token-file", upath]
    extra += list(extra_args)
    # Its sign-in (auth/SPEC.md), and its owner signed in once it is up: a workroom is in the lobby only then.
    service = secrets.token_hex(32)
    spath = os.path.join(TMP, name + ".auth-token")
    with open(spath, "w") as f:
        f.write(service)
    os.chmod(spath, 0o600)
    extra += ["--auth-token-file", spath, "--auth-port", str(AUTH[name])]
    # Its database: the Postgres inside visit, in a data directory of its own (unpacked once, under TMP/xdg).
    p = await asyncio.create_subprocess_exec(BIN, "--lobby", url, "--name", name, "--sim", "test-room/1", "--port", str(port),
                                             "--token-file", path, "--relays", "none", "--data-dir", os.path.join(TMP, name + ".data"), *extra,
                                             stdout=asyncio.subprocess.PIPE, stderr=open(os.path.join(OUT, name + ".log"), "w"),
                                             env=dict(os.environ, XDG_DATA_HOME=os.path.join(TMP, "xdg")))
    await asyncio.wait_for(p.stdout.readline(), 30)
    add = await asyncio.create_subprocess_exec(BIN, "auth", "--data-dir", os.path.join(TMP, name + ".data"), "user", "add", "owner", "--password-stdin",
                                               stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                               env=dict(os.environ, XDG_DATA_HOME=os.path.join(TMP, "xdg")))
    await asyncio.wait_for(add.communicate((PASSWORD + "\n").encode()), 60)
    def login():
        req = urllib.request.Request("http://127.0.0.1:%d/v1/login" % AUTH[name], method="POST",
                                     data=json.dumps({"user": "owner", "password": PASSWORD, "client": {"address": "127.0.0.1", "agent": "browser test"}}).encode(),
                                     headers={"Authorization": "Bearer " + service, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    assert await asyncio.to_thread(login) == 200, "%s's owner could not sign in" % name
    return (p, token, utoken) if user_token else (p, token)


# The WebGL renderer a page gets, by name: a software one draws on the processor, whose frames are not a GPU's.
RENDERER = """(() => { const c = document.createElement('canvas'), gl = c.getContext('webgl2') || c.getContext('webgl');
  if (!gl) return null; const x = gl.getExtension('WEBGL_debug_renderer_info'); return x ? gl.getParameter(x.UNMASKED_RENDERER_WEBGL) : 'masked'; })()"""
SOFTWARE = re.compile(r"swiftshader|llvmpipe|softpipe|software|basic render", re.I)


async def renderers(tabs):
    """Each Chrome's renderer, said; False if one draws in software or not at all: the test never runs on the processor.
    The GPU process can come up after the page does, so a software name is asked again for a few seconds."""
    for t in tabs:
        for _ in range(20):
            r = await t.js(RENDERER)
            if r and r != "masked" and not SOFTWARE.search(r):
                break
            await asyncio.sleep(0.5)
        print("%s draws with %s" % (t.name, r), flush=True)
        if not r or r == "masked" or SOFTWARE.search(r):
            print("%s has no GPU renderer: the test does not run in software (SwiftShader takes every core)" % t.name, file=sys.stderr)
            return False
    return True


async def main():
    a, b = Tab(CDP_A, "A"), Tab(CDP_B, "B")
    await a.start()
    await b.start()
    if not await renderers((a, b)):
        return 2
    class Handler(http.server.SimpleHTTPRequestHandler):
        """The test room's files, and /examples/ (visit/examples: the reference UI the room loads, as a room that
        copied it would); the room's server page (?role=host) only once the test says so (a `host-go` file in OUT):
        the engine, started before the test, is kept trying, with its backoff, until C's visit-client is up."""
        def translate_path(self, path):
            if path.startswith("/examples/"):
                rel = os.path.normpath(path.split("?", 1)[0].split("#", 1)[0][len("/examples/"):])
                if not rel.startswith(".."):
                    return os.path.join(EXAMPLES, rel)
            return super().translate_path(path)
        def do_GET(self):
            if "role=host" in self.path and not os.path.exists(os.path.join(OUT, "host-go")):
                return self.send_error(503, "not yet")
            return super().do_GET()
    handler = functools.partial(Handler, directory=HERE)
    web = http.server.ThreadingHTTPServer(("127.0.0.1", WEB_PORT), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    lobby = await asyncio.create_subprocess_exec(LOBBY_BIN, "--listen", "127.0.0.1", "--port", str(LOBBY_PORT), "--turn-port", str(TURN_PORT), "--turn-ip", lan_ip(), "--turn-host", lan_ip(),   # (Chrome takes no TURN server at 127.0.0.1)
                                                 "--turn-relay-ports", "50300-50399",
                                                 stdout=asyncio.subprocess.PIPE, stderr=open(os.path.join(OUT, "lobby.log"), "w"))
    await asyncio.wait_for(lobby.stdout.readline(), 10)
    try:
        return await run(a, b, "ws://127.0.0.1:%d" % LOBBY_PORT)
    finally:
        web.shutdown()
        lobby.terminate()
        await lobby.wait()


async def run(a, b, url):
    if True:
        (pa, ta), (pb, tb) = await start_client("A", PORT_A, url), await start_client("B", PORT_B, url)
        # A's room is made with three.js r128, B's with r160: each draws the other's with its own (a world), or with
        # the other's very release (a visit).
        room = "http://127.0.0.1:%d/room.html%s#port=%d&token=%s"
        await a.call("Page.addScriptToEvaluateOnNewDocument", source=RELAY_ONLY)
        await a.goto(room % (WEB_PORT, "", PORT_A, ta))
        await b.goto(room % (WEB_PORT, "?three=160", PORT_B, tb))

        print("\nconnecting")
        both = "window.v && v.state === 'online' && v.offices.filter(o => o.ready).length === 2"
        check("both rooms connect, host their scenes, and see each other, ready", (bool(await a.until(both, 30)), bool(await b.until(both, 30))), (True, True))
        check("the token left the address bar", "token" in (await a.js("location.href")), False)

        print("\n5.0 (#82): visit.js puts no UI in the page; the reference UI is the room's, on the public API alone")
        src, ui_src = open(os.path.join(HERE, "..", "web", "visit.js")).read(), open(os.path.join(EXAMPLES, "ui", "visit-ui.js")).read()
        check("visit.js makes no panel, chat window, card, map, stage, ⏱ corner nor lightbox (none of their classes, no style sheet)",
              sorted(set(re.findall(r'["\.]visit-(panel|chat|card|map|stage|perf|lightbox)\b', src))) + (["a style sheet"] if 'createElement("style")' in src else []), [])
        check("the reference UI reads nothing private of Visit's (no v._ anything)", sorted(set(re.findall(r"\bv\._[A-Za-z_]+", ui_src))), [])
        check("Visit.panel, .chat, .stage and .map are gone, and say where they went", await a.js(
            "['panel', 'chat', 'stage', 'map'].map(n => { try { Visit[n](); return 'ran'; } catch (e) { return /examples\\/ui\\/visit-ui\\.js/.test(e.message); } })"), [True] * 4)
        check("Visit.limits: what Visit and visit-client keep to, for a page's UI to say", await a.js(
            "[Visit.limits.chatLen, Visit.limits.chats, Visit.limits.maxPicks, Visit.limits.maxIgnored, Visit.limits.voiceSecs, Object.isFrozen(Visit.limits)]"), [200, 5, 8, 50, 60, True])
        check("what a page's UI is built on: its plot, the plots it watches, the room it is in, names, its phone, the ⏱, a figure's actions", await a.js(
            "[v.plotMetres.length, Array.isArray(v.watches), v.roomHere.key === v.key, v.roomHere.conversation === 'room:' + v.key, v.nameOf(v.offices.find(o => o.name === 'B').key),"
            " v.hasPhone, v.perfOn, JSON.stringify(v.actionsOf({ kind: 'figure', actions: ['spin', 'nope'] })), v.mayTalk({ talk: 'owner', room: 'elsewhere' })]"),
            [2, True, True, True, "B", True, False, '[{"type":"spin","label":"spin it"}]', False])
        check("the room's page holds the reference UI's handle: its chat window, its card, its ⏱ corner", await a.js(
            "[!!ui.chat.aim, !!ui.card.element, !!ui.perf.element, ui.map]"), [True, True, True, None])

        check("A's panel: Visit's version at its foot, and no Enter to chat line", await a.js(
            "(t => t.includes('Visit ' + Visit.version) && !t.includes('Enter to chat'))(document.querySelector('.visit-panel').textContent)"), True)
        await a.js("document.querySelector('.visit-panel button[data-v=fold]').click()")
        check("A folds its panel: its first line alone, remembered, the element given marked", await a.until(
            "document.querySelectorAll('.visit-panel .row').length === 1 && localStorage.getItem('visit:panel') === 'folded' && document.querySelector('.visit-panel').parentElement.classList.contains('visit-folded')"), True)

        print("\nfolded, the panel shows what asks you: someone who wants to talk (4.0: no knocks)")
        await b.js("v.talk(v.offices.find(o => o.name === 'A').key)")
        check("B rings A: A's folded panel shows it", await a.until("(r => !!r && r.textContent.includes('B wants to talk'))(document.querySelector('.visit-panel .ring'))"), True)
        check("folded, it shows that alone (no status, no door)", await a.js("!document.querySelector('.visit-panel select[data-v=status]')"), True)
        await a.js("document.querySelector('.visit-panel .ring button[data-v=hang_up]').click()")
        check("A: Not now. B's call is over", await b.until("v.calls.length === 0"), True)
        await a.js("document.querySelector('.visit-panel button[data-v=fold]').click()")
        check("and opened again: all of it back", await a.until(
            "!!document.querySelector('.visit-panel select[data-v=status]') && localStorage.getItem('visit:panel') === 'open' && !document.querySelector('.visit-panel').parentElement.classList.contains('visit-folded')"), True)

        print("\ngoing offline from the panel, and back")
        await a.js("document.querySelector('.visit-panel button[data-v=away]').click()")
        check("A clicks Go offline: A is away", await a.until("v.state === 'away' && v.state"), "away")
        check("and B's lobby no longer lists A", await b.until("!v.offices.some(o => o.name === 'A')"), True)
        check("A's panel offers Go online, and lists nobody", await a.until("!!document.querySelector('.visit-panel button[data-v=back]') && v.offices.length === 0"), True)
        await a.js("document.querySelector('.visit-panel button[data-v=back]').click()")
        check("A clicks Go online: online again", await a.until("v.state === 'online' && v.state"), "online")
        check("and B sees A again, ready to visit", await b.until("v.offices.some(o => o.name === 'A' && o.ready)"), True)

        print("\nguests (3.3: your worlds' members) and your world's picks, from the panel")
        pk = "document.querySelector('.visit-panel button.wpick[data-name=B]')"
        check("B in no world with A: no 🗺 by B in A's panel", await a.js("!%s" % pk), True)
        await a.js("v.createGroup('Yard', { open: true })")
        await a.until("v.groups.some(g => g.name === 'Yard')")
        await b.js("v.joinGroup('Yard')")
        check("B joins A's world: each is the other's guest (by the other's key)", (await a.until("v.isGuest(v.offices.find(o => o.name === 'B').key) && v.guests.map(g => g.name).join()"),
              await b.until("v.isGuest(v.offices.find(o => o.name === 'A').key)")), ("B", True))
        check("A's panel has 🗺 + by B: not in A's world", await a.until("%s && %s.textContent" % (pk, pk)), "🗺 +")
        await a.js("%s.click()" % pk)
        check("A clicks it: B's room picked for A's world, by B's key", await a.until("v.picks.length === 1 && v.picks[0].key === v.offices.find(o => o.name === 'B').key && v.picks[0].name"), "B")
        check("and it says so", await a.until("%s.textContent === '🗺 ✓'" % pk), True)
        await a.js("%s.click()" % pk)
        check("clicked again: out of A's world", await a.until("v.picks.length === 0 && %s.textContent === '🗺 +'" % pk), True)
        check("4.0: A's panel lists B (in a world with A) once, with 🎧 to talk and 🗺 +, and no Link nor Knock anywhere", await a.until(
            "document.querySelectorAll('.visit-panel button.wpick[data-name=B]').length === 1 && !!document.querySelector('.visit-panel button[data-v=talk]')"
            " && ![...document.querySelectorAll('.visit-panel button')].some(b => /^(Link|Knock|Let in)$/.test(b.textContent))"), True)
        await a.js("(s => { s.value = 'closed'; s.dispatchEvent(new Event('change', { bubbles: true })); })(document.querySelector('.visit-panel select[data-v=door]'))")
        check("A closes its door from the panel", await a.until("v.door === 'closed' && v.door"), "closed")
        await a.js("v.setDoor(true)")
        check("door open again", await a.until("v.door === 'open'"), True)

        print("\nthe lobby in a browser, from the panel (3.1): a QR code for a phone; a browser's code typed, allowed, unlinked")
        await a.js("document.querySelector('.visit-panel button[data-v=qr]').click()")
        check("A's panel: 📱 Phone shows a QR code (an SVG from its visit-client, as an image)", await a.until("(i => !!i && i.complete && i.naturalWidth > 0)(document.querySelector('.visit-panel .qr img'))"), True)
        await a.shot("a_qr.png")
        await a.js("document.querySelector('.visit-panel button[data-v=qr]').click()")
        check("and again: gone", await a.until("!document.querySelector('.visit-panel .qr')"), True)
        # B's Chrome stands in for a browser at the lobby's page: a key of its own (Web Crypto's Ed25519), proved; a code.
        await b.js("""window.pairing = (async () => {
          const hex = x => Array.from(new Uint8Array(x), y => y.toString(16).padStart(2, '0')).join('');
          const p = await crypto.subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify']);
          const pub = hex(await crypto.subtle.exportKey('raw', p.publicKey));
          const ws = window.pairWs = new WebSocket('%s'); window.pairMsgs = [];
          ws.onopen = () => ws.send(JSON.stringify({ type: 'hello', proto: 'basedverse-lobby/4', browser: pub }));
          ws.onmessage = async e => { const m = JSON.parse(e.data); window.pairMsgs.push(m);
            if (m.type === 'challenge') ws.send(JSON.stringify({ type: 'prove', signature: hex(await crypto.subtle.sign({ name: 'Ed25519' }, p.privateKey,
              new TextEncoder().encode('basedverse-lobby/4 browser\\n%s\\n' + m.nonce + '\\n' + pub))) })); };
          return pub; })(); 1""" % (url, url.split("//")[1].rstrip("/")))
        code = await b.until("(window.pairMsgs || []).find(m => m.type === 'browser_code') && window.pairMsgs.find(m => m.type === 'browser_code').code")
        pics = await b.js("window.pairMsgs.find(m => m.type === 'browser_code').pictures")
        await a.js("document.querySelector('.visit-panel button[data-v=browsers]').click()")
        check("A's panel: Link a browser asks for its code", await a.until("!!document.querySelector('.visit-panel form[data-f=code] input')"), True)
        await a.js("(f => { f.elements.code.value = '%s'; f.requestSubmit(); })(document.querySelector('.visit-panel form[data-f=code]'))" % (code[:4].lower() + "-" + code[4:]))
        check("A types it: the browser waiting, its pictures the same as on its screen", await a.until(
            "(r => r && r.textContent.includes(%s) && r.textContent.includes('Chrome on Linux'))(document.querySelector('.visit-panel .knock'))" % json.dumps(pics)), True)
        await a.js("document.querySelector('.visit-panel button[data-v=b_allow]').click()")
        check("3.2, A allows it: A's panel shows the confirmation that browser must show (a QR code, an image; its digits)", await a.until(
            "(i => !!i && i.complete && i.naturalWidth > 0 && !!v.browserConfirm && document.querySelector('.visit-panel .digits').textContent.replace(' ', '') === v.browserConfirm.confirm)(document.querySelector('.visit-panel .sub .qr img'))"), True)
        await a.shot("a_confirm.png")
        check("and the browser is told who would link it", await b.until("(window.pairMsgs.find(m => m.type === 'browser_confirming') || {}).office"), "A")
        digits = await a.js("v.browserConfirm.confirm")
        await b.js("window.pairWs.send(JSON.stringify({ type: 'browser_verify', confirm: '%s' })); 1" % (digits[:3] + " " + digits[3:]))
        check("the browser shows them (typed): linked, in the lobby as A", await b.until("(window.pairMsgs.find(m => m.type === 'web_welcome') || {}).name"), "A")
        check("and A's panel no longer shows the confirmation", await a.until("!v.browserConfirm && !document.querySelector('.visit-panel .digits')"), True)
        check("and A's panel lists it, in now, to unlink", await a.until("v.browsers.length === 1 && v.browsers[0].online && !!document.querySelector('.visit-panel button[data-v=b_unlink]')"), True)
        check("and says so, once: a browser linked to A just now", await a.until("[...document.querySelectorAll('.visit-panel .note')].some(n => n.textContent.includes('is linked to you now'))"), True)
        await a.js("document.querySelector('.visit-panel button[data-v=b_unlink]').click()")
        check("A unlinks it: the browser is told, and out", await b.until("(window.pairMsgs.find(m => m.type === 'error') || {}).code"), "unlinked")
        check("and A's list is empty", await a.until("v.browsers.length === 0"), True)
        await a.js("document.querySelector('.visit-panel button[data-v=browsers]').click()")

        print("\nthe world: friends' plots round your room, walked onto")
        world = "http://127.0.0.1:%d/room.html?world=1" % WEB_PORT
        await a.goto(world)
        await b.goto(world + "&three=160")
        up = "window.v && window.room && room.world && v.state === 'online' && v.offices.filter(o => o.ready).length === 2"
        check("both rooms open as worlds", (bool(await a.until(up, 30)), bool(await b.until(up, 30))), (True, True))
        await asyncio.sleep(1)
        check("in a world together, nothing picked: neither's world has the other's plot", (await a.js("room.world.plots.filter(p => !p.home).length"), await b.js("room.world.plots.filter(p => !p.home).length")), (0, 0))
        await a.js("v.pick(v.offices.find(o => o.name === 'B').key, 'B')")
        await b.js("v.pick(v.offices.find(o => o.name === 'A').key, 'A')")
        inworld = "room.world.plots.length === 1 && room.world.plots[0]"
        pa_, pb_ = await a.until(inworld, 20), await b.until(inworld, 20)
        check("picked both ways: each sees the other's plot, in its world", (pa_ and pa_["name"], pb_ and pb_["name"]), ("B", "A"))
        r = pa_["rect"]
        check("placed by itself round A's own, not on it", (round(r[2] - r[0], 3), round(r[3] - r[1], 3), not (r[0] < 10 and r[2] > 0 and r[1] < 8 and r[3] > 0)), (10, 8, True))
        check("A (r128) draws B's (r160) scene with A's own three.js: B's spinner there", await a.until(
            "(w => !!(w && w.scene && w.scene.T.REVISION === '128' && w.scene.root.getObjectByName('spinner')))([...v._watches.values()][0])"), True)
        check("and B (r160) A's (r128), with B's", await b.until(
            "(w => !!(w && w.scene && w.scene.T.REVISION === '160' && w.scene.root.getObjectByName('spinner')))([...v._watches.values()][0])"), True)
        check("B's lights (r160: lit the new way) over pi in A's world (r128 multiplies them by pi): as B sees them", await a.js(
            "(s => { let l; s.root.traverse(o => { if (o.isDirectionalLight) l = o; }); return Math.round(l.intensity * 1000) / 1000; })([...v._watches.values()][0].scene)"),
            round(0.6 / 3.14159265, 3))
        frames0 = await a.js("window.renders")
        await asyncio.sleep(0.5)
        check("and A's page goes on drawing", await a.js("window.renders") > frames0 + 10, True)
        # A friend's plot out of the camera's view is left out of the frame (8.4, "Drawing"): a camera 5 m west of it,
        # at eye height, facing it draws it, facing away leaves it out. Each page with its own three.js (r128, r160).
        aside = """(() => { const w = room.world, T = w.T, x = w._layout().find(p => !p.home), h = w._holder(x), z = (x.rect[1] + x.rect[3]) / 2;
          const at = look => { const c = new T.PerspectiveCamera(60, 1, 0.1, 500); c.position.set(x.rect[0] - 5, 1.6, z); c.lookAt(x.rect[0] + look, 1.6, z); c.updateMatrixWorld(); return w._neighbour(x, h, c, performance.now()); };
          const r = [at(10), at(-10)]; delete w._nb; return r; })()"""
        check("a friend's plot in view drawn, out of view left out (A, r128)", await a.js(aside), [False, True])
        check("and in B's world (r160)", await b.js(aside), [False, True])
        check("world.drawn: A's frame drew its room and B's plot", await a.until("room.world.drawn.plots + room.world.drawn.out === 2"), True)
        merged = "(s => !!s && s.root.children.filter(c => c.name === 'visit:merged').map(c => c.userData.visitMerged).sort())(([...v._watches.values()].find(w => w.host.name === 'B') || {}).scene)"
        check("4.0 (#42 B): B's landscape on its scene's root, drawn merged in A's world: its 8 wall halves (one material) one mesh, the floor and desk as they are",
              await a.until(merged), [8])
        # 4.3.1: the sky from a scene of its own (the room's scene no longer walked for it); its background still drawn,
        # each page with its own three.js. Looking straight up from high over the room: nothing but sky.
        sky = """(() => { const w = room.world, T = w.T, r = w.renderer, s = w.scene, was = s.background;
          s.background = new T.Color(0x00ff00);
          const c = new T.PerspectiveCamera(60, r.domElement.width / r.domElement.height, 0.1, 500); c.position.set(5, 80, 4); c.up.set(0, 0, 1); c.lookAt(5, 200, 4); c.updateMatrixWorld();
          try { w.render(c); const gl = r.getContext(), px = new Uint8Array(4); gl.readPixels(gl.drawingBufferWidth >> 1, gl.drawingBufferHeight >> 1, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); return [...px].slice(0, 3); }
          finally { s.background = was; } })()"""
        check("4.3.1: the sky drawn from a scene of its own, the room's background in it (A, r128)", await a.js(sky), [0, 255, 0])
        check("and B's (r160)", await b.js(sky), [0, 255, 0])
        await a.js("v.perf(true)")
        drew = await a.until("(r => r.length && r[r.length - 1].draws)(v.perfReport())", 6)
        check("4.3.1: ⏱ counts a frame's draw calls, its shadows apart (three.js's own count leaves them out)",
              bool(drew) and drew["colour"] > 0 and drew["shadow"] > 0, True)
        check("and says so", "draw calls a frame:" in (await a.js("document.querySelector('.visit-perf').textContent") or ""), True)
        await a.shot_of("ui-perf.png", ".visit-perf")
        await a.js("v.perf(false)")
        check("⏱ off: the renderer as it was, nothing counted", await a.until(
            "!room.world._count && !String(room.world.renderer.renderBufferDirect).includes('inShadow') && !String(room.world.renderer.shadowMap.render).includes('inShadow')"), True)
        await a.shot("a_world.png")

        async def walk(tab, x, z, yaw, secs, until=None):
            """From there, facing there, W held for `secs`, or (`until`, a JS test) until it holds, at most 3 times
            as long: a page drawing slowly (as these do, on the processor, under load) walks slowly."""
            await tab.js("Object.assign(room.me, { x: %f, z: %f, yaw: %f })" % (x, z, yaw))
            await asyncio.sleep(0.1)
            await tab.key("KeyW", "w", 87, True)
            if until:
                for _ in range(int(secs * 30)):
                    if await tab.js(until):
                        break
                    await asyncio.sleep(0.1)
                await asyncio.sleep(0.3)   # on past the edge a little
            else:
                await asyncio.sleep(secs)
            await tab.key("KeyW", "w", 87, False)
            await asyncio.sleep(0.4)

        midz = (r[1] + r[3]) / 2
        await walk(a, r[0] - 1.0, midz, -3.14159 / 2, 1.5, "!!room.where")   # west of B's plot, facing east, through its west doorway
        check("A walks onto B's plot: in B's room", await a.until("room.where && room.where.name"), "B")
        check("B has A as its visitor, where A stepped in and on", await b.until("v.visitors.length === 1 && v.visitors[0].pose.x > 0.5 && v.visitors[0].visitor.name"), "A")
        check("B draws A's body; A's copy of B's room hides it (A's page draws A as itself)", await a.until(
            "(s => s.own.length === 1 && !s.own[0].visible)([...v._watches.values()][0].scene)"), True)
        await a.shot("a_in_b.png")
        await b.shot("b_has_a.png")
        await b.js("room.lift = 3")
        check("B lifts A 3 m: A's world has A up there, as B's room has it", await a.until("Math.abs(room.atY - 3) < 0.05"), True)
        await b.js("room.lift = 0")
        check("and back down", await a.until("Math.abs(room.atY) < 0.05"), True)
        await a.key("Space", " ", 32, True)
        await asyncio.sleep(0.6)
        check("B lets nobody fly: A holding up stays on B's floor", (round(await b.js("v.visitors[0].pose.y"), 2), round(await a.js("room.atY"), 2)), (0.0, 0.0))   # (positions at the feet: 0.19)
        await b.js("room.fly = true")
        check("B lets visitors fly: A holding up rises, in B's room and in A's world", (await b.until("v.visitors[0].pose.y > 3"), await a.until("room.atY > 2.5")), (True, True))
        await a.key("Space", " ", 32, False)
        await asyncio.sleep(0.3)
        high = await b.js("v.visitors[0].pose.y")
        await asyncio.sleep(0.5)
        check("A lets go: it stays up there (flying, not falling)", abs(await b.js("v.visitors[0].pose.y") - high) < 0.05, True)
        await b.js("room.fly = false")
        await asyncio.sleep(0.4)
        check("B stops flying: A floats down, gently (not at once)", await b.js("v.visitors[0].pose.y") > 2.2, True)
        check("down to B's floor, in B's room and in A's world", (await b.until("Math.abs(v.visitors[0].pose.y) < 0.01", 15), await a.until("Math.abs(room.atY) < 0.05", 15)), (True, True))
        bx = await b.js("v.visitors[0].pose.x")
        await walk(a, await a.js("room.me.x"), await a.js("room.me.z"), 3.14159 / 2, 2.0, "room.where === null")   # turned round: back out west
        check("A walks back off B's plot: home again, and B's visitor gone", (await a.until("room.where === null && room.world.in === null"), await b.until("v.visitors.length === 0")), (True, True))
        check("A ends up west of B's plot, walking on in its own world", await a.js("room.me.x") < r[0], True)
        # B's height for A read where B takes it in (its spawn), as well as every 5 ms: a busy runner's timers are late,
        # and B floats A down from the moment it is in.
        await b.js("window.highest = 0; window.highT = setInterval(() => { for (const x of v.visitors) if (x.pose) highest = Math.max(highest, x.pose.y); }, 5);"
                   " v._hostEnter = m => { Object.getPrototypeOf(v)._hostEnter.call(v, m); for (const x of v.visitors) if (x.pose) highest = Math.max(highest, x.pose.y); }")
        await a.js("Object.assign(room.me, { y: 4, air: 4 })")   # A flying at home, 4 m up, crosses onto B's plot (no flying there)
        await walk(a, r[0] - 1.0, midz, -3.14159 / 2, 1.5, "!!room.where")
        await a.js("room.me.air = 0")
        k = await a.js("room.world._layout().find(p => p.key === %s).k" % json.dumps(await a.js("v.offices.find(o => o.name === 'B').key")))
        check("A crosses onto B's plot in the air: B has it 4 m above its floor (in B's units)", await b.until("v.visitors.length === 1 && Math.abs(highest - 4 / %f) < 0.05" % k), True)
        check("and floats it down to its floor, as A's world shows", (await b.until("v.visitors.length === 1 && Math.abs(v.visitors[0].pose.y) < 0.01", 15), await a.until("Math.abs(room.atY) < 0.05", 15)), (True, True))
        await b.js("clearInterval(highT); delete v._hostEnter")
        await walk(a, await a.js("room.me.x"), await a.js("room.me.z"), 3.14159 / 2, 2.0, "room.where === null")
        check("and A walks off again, home on the ground (not back up where it crossed)", (await b.until("v.visitors.length === 0"), abs(await a.js("room.me.y")) < 0.05), (True, True))
        await a.js("room.me.y = 0")   # on the ground again whatever the case above found, so what follows starts as it always has

        print("\nteleports (5.1, 8.15): the rooms that would take you, each by its own rule; onto a room's pad by teleport, and home; Visit's beam in each room")
        kb0 = json.dumps(await a.js("v.offices.find(o => o.name === 'B').key"))
        found = await a.js("v.findTeleports().then(l => l.map(t => [t.name, t.home]))")
        check("A asks where it may teleport: its own room (it has a pad) and B's (A its guest: Visit's own rule, B's room gives none)", sorted(found), [["A", True], ["B", False]])
        await b.js("room.tpAllow = who => who.name !== 'A'")
        await asyncio.sleep(1.6)
        found = await a.js("v.findTeleports().then(l => l.map(t => t.name))")
        check("B's room's own rule (not A, now): A's list has only its own room", found, ["A"])
        await b.js("room.tpAllow = who => who.guest || who.owner")
        await asyncio.sleep(1.6)
        await a.js("v.findTeleports()")
        await a.js("document.querySelector('.visit-panel button[data-v=tp]').click()")
        await a.until("document.querySelectorAll('.visit-panel button[data-v=tp_go]').length === 1 && !v.teleportAsking")
        await a.shot_of("ui-teleport.png", ".visit-panel")
        check("the panel's ✦ Teleport lists them, but the room A is in (its own)", await a.js("[...document.querySelectorAll('.visit-panel .row.tp b')].map(b => b.textContent)"), ["B"])
        await a.js("ui.teleports(false)")
        check("5.1.1: the panel's handle closes its list (ui.teleports(false)), and opens it (true), asking again",
              (await a.js("!!document.querySelector('.visit-panel .row.tp')"), await a.js("ui.teleports(true)"), await a.until("document.querySelectorAll('.visit-panel .row.tp b').length === 1", 5)), (False, True, True))
        await a.js("ui.teleports(false)")
        await a.key("KeyT", "t", 84, True)
        await a.key("KeyT", "t", 84, False)
        check("5.1.1: T turns it over", await a.until("!!document.querySelector('.visit-panel .row.tp')", 5), True)
        await a.key("KeyT", "t", 84, True)
        await a.key("KeyT", "t", 84, False)
        check("…and back", await a.until("!document.querySelector('.visit-panel .row.tp')", 5), True)
        check("5.1.1: v.world({ warp: false }) draws no warp", await a.js(
            "(w => { const was = w.warpOff; w.warpOff = true; w.warpState = null; w._warp('charge'); const none = !w.warpState; w.warpOff = was; return none; })(room.world)"), True)
        await a.js("window.tpEvents = []; v.on('teleport', e => tpEvents.push(e.state))")
        await a.js("window.tpGo = v.teleport(%s)" % kb0)
        await asyncio.sleep(0.15)
        await a.shot("a_teleport_warp.png")
        res = await a.js("tpGo")
        check("A teleports to B's room: there", (res["ok"], await a.js("room.where && room.where.name")), (True, "B"))
        check("B's room spawned A on its pad, by teleport (its spawn heard where the pad is)", await b.js(
            "(s => [s.at && Math.round(s.at.x * 10) / 10, s.at && Math.round(s.at.z * 10) / 10, Math.round(s.p.x * 10) / 10, Math.round(s.p.z * 10) / 10])(spawns[spawns.length - 1])"), [8.6, 1.4, 8.6, 1.4])
        check("and Visit's beam plays in B's room's scene", await b.js("!!room.scene.getObjectByName('visit:teleport')"), True)
        check("which reaches A as B's scene does: A's world draws it on B's plot", await a.until(
            "(w => !!(w && w.scene && w.scene.root.getObjectByName('visit:teleport')))([...v._watches.values()].find(w => w.host.name === 'B'))", 5), True)
        await asyncio.sleep(0.2)
        await a.shot("a_teleported.png")
        await b.shot("b_teleport_in.png")
        check("A's world has A on B's pad (its place through where A's world puts B's plot)", await a.js(
            "(x => { const p = new room.THREE.Vector3(8.6, 0, 1.4).applyMatrix4(room.world._holder(x).place.matrix); return Math.hypot(p.x - room.me.x, p.z - room.me.z) < 0.35; })(room.world._layout().find(p => p.key === %s))" % kb0), True)
        check("A's page heard it leave, then arrive", await a.js("tpEvents"), ["leaving", "arrived"])
        check("and B has A as its visitor", await b.until("v.visitors.length === 1 && v.visitors[0].visitor.name"), "A")
        await asyncio.sleep(1.8)
        check("the beam done: gone from B's room", await b.js("!room.scene.getObjectByName('visit:teleport')"), True)
        res = await a.js("v.teleport(v.key)")
        check("A teleports home: there, on its own pad", (res["ok"], await a.js("room.where"), await a.js("Math.hypot(room.me.x - 8.6, room.me.z - 1.4) < 0.35")), (True, None, True))
        check("B's room plays A going (its page heard A go by teleport)", await b.until("!!room.scene.getObjectByName('visit:teleport')", 3), True)
        check("and A's own room plays A coming home", await a.js("!!room.scene.getObjectByName('visit:teleport')"), True)
        await asyncio.sleep(0.2)
        await b.shot("b_teleport_out.png")
        await a.shot("a_home_by_teleport.png")
        check("B's visitor gone", await b.until("v.visitors.length === 0"), True)
        check("and B's plot still in A's world (A picked it: a room you picked stays)", await a.js("room.world.plots.filter(p => !p.home).map(p => p.name)"), ["B"])
        await asyncio.sleep(1.8)
        await b.js("room.tpAllow = () => false")
        res = await a.js("v.teleport(%s)" % kb0)
        check("B's room says no as A comes: A is not let in, and stays home", (res["ok"], res.get("reason"), await a.until("room.where === null && !room.world.inside")), (False, "not_allowed", True))
        await b.js("delete room.tpAllow")
        await asyncio.sleep(1.8)

        print("\neach one's view of the other: where the other has put it, nothing more")
        ka, kb = await b.js("v.offices.find(o => o.name === 'A').key"), await a.js("v.offices.find(o => o.name === 'B').key")
        theirs = await b.until("(p => p && [p.at.x - v._plot().center[0], p.at.z - v._plot().center[1], p.at.turn, ...v._plotMetres()].map(x => Math.round(x * 100) / 100))(room.world.plots[0])")
        got = await a.until("(s => s && [s.at.dx, s.at.dz, s.at.turn, ...s.at.size])(v.seen.find(s => s.name === 'B'))")
        check("A has where B's world puts A (from B's plot's middle, in metres), and B's plot's size", got, theirs)
        check("and B where A's puts B: one each, by key", await b.until("v.seen.length === 1 && v.seen[0].key"), ka)
        await b.js("v.place(%s, { x: 30, z: 4, turn: 1 })" % json.dumps(ka))
        check("B moves A's room in its world: A sees it move (25 m east of B's middle, turned a quarter left)",
              await a.until("(s => s && s.at.dx === 25 && s.at.dz === 0 && s.at.turn === 1 && s.at.dx)(v.seen.find(s => s.key === %s))" % json.dumps(kb)), 25)
        check("A's own world is unchanged by it: nobody agrees, each has its own", await a.js("room.world.plots[0].rect"), r)
        check("A's panel says so once it settles (Visit's own note): B put A's room east of B's", await a.until(
            "[...document.querySelectorAll('.visit-panel .note')].some(n => n.textContent.startsWith('B put your room east of theirs'))", 10), True)

        print("\nflush: walked straight across")
        await a.js("v.place(%s, { x: 15, z: 4, turn: 0 })" % json.dumps(kb))
        ea = await a.until("(p => p && p.at.x === 15 && p.rect)(room.world.plots[0])")
        check("A puts B's room against its own east side", [round(x, 3) for x in ea], [10, 0, 20, 8])
        await walk(a, 8.5, 4, -3.14159 / 2, 1.8, "!!room.where")   # in A's room, facing its east doorway: straight through into B's
        check("A walks out of its own room's east door and straight into B's, by its west door", await a.until("room.where && room.where.name"), "B")
        check("B has A", await b.until("v.visitors.length === 1 && v.visitors[0].visitor.name"), "A")
        await a.shot("a_across.png")
        # Back towards A's room's middle, through B's west doorway: from where B has A (a slow answer may have stepped it
        # in again, and its spawn moved it off the doorway's line), sliding along B's wall into the doorway.
        mx, mz = await a.js("room.me.x"), await a.js("room.me.z")
        await walk(a, mx, mz, math.atan2(-(5 - mx), -(4 - mz)), 3.0, "room.where === null")   # (at most 9 s: under load, slower)
        home = (await a.until("room.where === null"), 0 < await a.js("room.me.x") < 10.01)
        if home != (True, True):
            r0 = await a.js("window.renders"); await asyncio.sleep(1); r1 = await a.js("window.renders")
            print("      A:", await a.js("JSON.stringify({ me: room.me, where: room.where, in: room.world.in, keys: [...room.keys] })"), "frames/s:", r1 - r0,
                  "B has A at:", await b.js("JSON.stringify(v.visitors.map(x => x.pose))"), "B frames/s:", await b.js("window.renders"))
            print("      B's spawns:", await b.js("JSON.stringify((window.spawns || []).slice(-6))"))
        check("and back: home in A's own room", home, (True, True))
        check("both at home, each near the other in its own world (A's: B flush east; B's: A 25 m east): each hears the other near, across the side",
              (await a.until("v.voices.some(x => x.name === 'B') && JSON.parse(v._whereTold.sig).world.some(w => w[0] === %s)" % json.dumps(kb), 15),
               await b.until("v.voices.some(x => x.name === 'A')", 15)), (True, True))

        print("\nB away: its plot kept from its landscape, walked into, not through")
        await b.js("v.leaveLobby()")
        check("B goes offline: A's world keeps B's plot where it was, from B's landscape, marked offline", await a.until(
            "(p => !!p && p.offline && p.at.x === 15)(room.world.plots.find(p => p.name === 'B'))", 20), True)
        await walk(a, 8.5, 4, -3.14159 / 2, 2.5, "room.me.x > 12")   # out of A's east door, into B's kept plot by its west door
        check("A walks into B's kept plot by its doorway (at home all the while: nobody to have A there)", await a.js("room.me.x > 11 && room.where === null"), True)
        await walk(a, await a.js("room.me.x"), 4, 0, 2.5)   # north (-z), at B's north wall
        z = await a.js("room.me.z")
        check("and walks north into B's north wall: stopped at it, not through (its walls merged, 4.0: walked against all the same)", (0.1 < z < 2.5, z), (True, z))
        check("B's kept plot, drawn merged (it never moves): its wall halves one mesh", await a.js(
            "(k => !!k && !!k.built && k.built.merged.merged.map(m => m.userData.visitMerged))([...v._kept.values()].find(k => k.name === 'B'))"), [8])
        await a.shot("a_kept.png")
        await a.js("Object.assign(room.me, { x: 5, z: 4 })")   # home
        await b.js("v.joinLobby()")
        check("B back: its plot live again", await a.until("(p => !!p && !p.offline)(room.world.plots.find(p => p.name === 'B'))", 45), True)

        print("\nthe door shut")
        await walk(a, 8.5, 4, -3.14159 / 2, 1.8, "!!room.where")   # into B's again, to be lifted there
        check("A in B's room again", (await a.until("room.where && room.where.name"), await b.until("v.visitors.length === 1")), ("B", True))
        await b.js("room.lift = 3")
        check("B lifts A 3 m again", await a.until("Math.abs(room.atY - 3) < 0.05"), True)
        await b.js("v.setDoor(false)")
        check("B shuts its door with A up there: A is home, at its own height", await a.until("room.where === null && Math.abs(room.atY) < 0.05"), True)
        await b.js("room.lift = null")
        check("B shuts its door: B's room leaves A's world, and B is told it is no longer in it",
              (await a.until("room.world.plots.length === 0", 10), await b.until("v.seen.length === 0", 10)), (True, True))
        await b.js("v.setDoor(true)")

        print("\nthe map: placing B's room exactly, from above")
        await a.js("document.querySelector('.visit-panel button[data-v=map]').click()")
        # (B's door was shut: A's watch was refused, and A asks again 20 s after that)
        check("A's panel opens the map: A's room and B's, from above", await a.until("!!(ui.map && ui.map.shown && room.world.plots.some(p => p.name === 'B'))", 40), True)
        pick = "[...document.querySelectorAll('.visit-map button[data-m=pick]')].find(b => b.textContent.startsWith('B'))"
        await a.until("!!" + pick)
        await a.js(pick + ".click()")
        check("picked, B's room shows B's view of the two: A's room east of B's, turned, drawn", await a.until(
            "(s => s.textContent.includes('your room east of theirs, 25 m middle to middle, turned a quarter left') && s.querySelectorAll('svg rect').length)(document.querySelector('.visit-map .side'))"), 2)
        await a.until("!!document.querySelector('.visit-map input[data-f=x]')")
        await a.js("(i => { i.value = '-12'; i.dispatchEvent(new Event('change', { bubbles: true })); })(document.querySelector('.visit-map input[data-f=x]'))")
        await a.js("(i => { i.value = '-6'; i.dispatchEvent(new Event('change', { bubbles: true })); })(document.querySelector('.visit-map input[data-f=z]'))")
        check("typed: B's room's middle at (-12, -6), kept by A's visit-client", await a.until(
            "(p => p && p.x === -12 && p.z === -6 && p)(v.layout.find(p => p.key === %s))" % json.dumps(kb)), {"key": kb, "x": -12, "z": -6, "turn": 0})
        check("and A's world puts it there", await a.until("(p => p && p.at.x === -12 && p.at.z === -6)(room.world.plots.find(p => p.name === 'B'))"), True)
        await a.js("document.querySelector('.visit-map button[data-m=fit]').click()")
        await asyncio.sleep(0.3)
        p0 = await a.js("ui.map._px(%s)" % json.dumps(kb))
        await a.call("Input.dispatchMouseEvent", type="mousePressed", x=p0[0], y=p0[1], button="left", clickCount=1)
        for i in range(1, 11):
            await a.call("Input.dispatchMouseEvent", type="mouseMoved", x=p0[0] + i * 6, y=p0[1], button="left")
        await a.call("Input.dispatchMouseEvent", type="mouseReleased", x=p0[0] + 60, y=p0[1], button="left", clickCount=1)
        moved = await a.until("(p => p && p.x > -12 && p.z === -6 && p.x)(v.layout.find(p => p.key === %s))" % json.dumps(kb))
        check("dragged right on the map: further east, on a half unit, the same z", (moved > -12, moved * 2 == int(moved * 2)), (True, True))
        await a.js("document.querySelector('.visit-map button[data-m=cw]').click()")
        check("turned a quarter right (three quarter turns left)", await a.until("(p => p && p.turn)(v.layout.find(p => p.key === %s))" % json.dumps(kb)), 3)
        await a.js("(i => { i.value = '14'; i.dispatchEvent(new Event('change', { bubbles: true })); })(document.querySelector('.visit-map input[data-f=x]'))")
        await a.js("(i => { i.value = '4'; i.dispatchEvent(new Event('change', { bubbles: true })); })(document.querySelector('.visit-map input[data-f=z]'))")
        check("typed again: B's room flush against A's east side", await a.until(
            "(p => p && p.x === 14 && p.z === 4 && p.turn)(v.layout.find(p => p.key === %s))" % json.dumps(kb)), 3)
        check("and B told where it is now in A's world: its middle 9 m east of A's, turned a quarter right", await b.until(
            "(s => s && s.at.dx === 9 && s.at.dz === 0 && s.at.turn)(v.seen.find(s => s.key === %s))" % json.dumps(ka)), 3)
        await a.shot("a_map.png")
        await a.shot_of("ui-map.png", ".visit-map .box")
        await a.js("document.querySelector('.visit-map button[data-m=close]').click()")
        await a.goto(world)
        check("A's page reloads: where A put B's room is kept", await a.until(
            "window.v && (p => p && p.x === 14 && p.z === 4 && p.turn === 3)(v.layout.find(p => p.key === %s))" % json.dumps(kb), 30), True)
        await a.until("window.room && room.world && room.world.plots.some(p => p.name === 'B')", 30)
        check("and A has, again, where B has put it", await a.until("(s => s && s.at.dx)(v.seen.find(s => s.key === %s))" % json.dumps(kb), 20), 25)
        await a.js("v.unplace(%s)" % json.dumps(kb))
        await b.js("v.unplace(%s)" % json.dumps(ka))
        await a.until("v.layout.length === 0")
        # 3.3: a room picked comes out of your world from the Map (away or not), as from the panel's 🗺 ✓.
        await a.js("document.querySelector('.visit-panel button[data-v=map]').click()")
        await a.until("!!(%s)" % pick)
        await a.js("(%s).click()" % pick)
        await a.until("!!document.querySelector('.visit-map button[data-m=unpick]')")
        await a.js("document.querySelector('.visit-map button[data-m=unpick]').click()")
        check("the Map, B's room: Take out of my world: unpicked, its plot gone", await a.until("v.picks.length === 0 && !room.world.plots.some(p => p.name === 'B')"), True)
        await a.js("document.querySelector('.visit-map button[data-m=close]').click()")
        await b.js("v.unpick(v.picks[0].key)")
        await b.until("v.picks.length === 0 && !room.world.plots.some(p => p.name === 'A')")

        async def step_in(tab, host):
            """4.0 (#57): into `host`'s room the one way there is: its room picked for this world (the two in Yard), its plot
            stood on (here() is it)."""
            await tab.js("v.pick(v.offices.find(o => o.name === %s).key, %s)" % (json.dumps(host), json.dumps(host)))
            rect = await tab.until("(p => p && p.rect)(room.world.plots.find(p => p.name === %s))" % json.dumps(host), 30)
            await tab.js("Object.assign(room.me, { x: %f, z: %f, yaw: 0 })" % (rect[0] + 2, rect[1] + 6))   # (2 m in, 6 m back: clear of its desk, in the middle)
            await tab.until("room.where && room.where.name === %s && !!here() && !!here().scene" % json.dumps(host), 30)

        async def step_out(tab, host):
            """Home, and their room out of this world."""
            await tab.js("room.world.leave(); Object.assign(room.me, { x: 0, z: 0 })")   # (in a plot, its host has the last word on where you are)
            await tab.until("!room.where", 15)
            await tab.js("v.unpick(v.offices.find(o => o.name === %s).key)" % json.dumps(host))
            await tab.until("!room.world.plots.some(p => p.name === %s)" % json.dumps(host), 15)

        print("\navatars: a visitor as itself, when the room it visits says so")
        await step_in(b, "A")
        check("B visits A: A shows B as their own model (Visit's default)", await a.until(
            "v.avatars.default === 'theirs' && v.visitors.length === 1 && v.visitors[0].look === 'theirs' && !!room.scene.getObjectByName('avatar:B') && !room.scene.getObjectByName('body:B')"), True)
        check("read with A's three.js (r128, from B's r160): B's torso and head, its walk and idle", await a.js(
            "(h => [h.avatar.object.getObjectByName('torso').geometry.type, !!h.avatar.object.getObjectByName('head'), !!h.avatar.walk, !!h.avatar.idle])([...v._hosted.values()].find(h => h.avatar))"),
            ["CylinderGeometry", True, True, True])
        check("and B, in A's room, does not see itself", await b.until("(s => !!s && s.own.some(o => o.name === 'avatar:B'))(here() && here().scene)"), True)
        check("Visit draws it, not A's room: at B's feet, facing B's way", await a.until(
            "(o => !!o && o.parent === room.scene && Math.abs(o.position.x - v.visitors[0].pose.x) < 0.01 && Math.abs(o.position.y - v.visitors[0].pose.y) < 0.01 && Math.abs(o.rotation.y - v.visitors[0].pose.yaw) < 0.01)(room.scene.getObjectByName('avatar:B'))"), True)
        check("B sees from its own model's eyes (its \"eyes\" point, 1.5 m): A says so, and B hears it", (
            await a.until("Math.abs(v.visitors[0].eyes - 1.5) < 0.01 && v.visitors[0].look"),
            await b.until("(bd => !!bd && bd.look === 'theirs' && Math.abs(bd.eyes - 1.5) < 0.01)(v._bodies.get(here().channel))")), ("theirs", True))
        await a.shot("a_avatar.png")
        # 4.0 (#59, #60): A's room acts out an interaction on B's own model, and B's own page knows.
        await a.js("v.perform(v.visitors[0].id, 'sit')")
        if not await b.until("room.sit && room.act === 'sit'", 6):
            print("      sit: B", await b.js("JSON.stringify({ act: room.act, sit: room.sit, where: room.where, w: [...v._watches.values()].map(w => [w.host.name, w.inside, w.act, w.holding, w.channel]), in: room.world.in })"),
                  "A", await a.js("JSON.stringify(v.visitors.map(x => [x.visitor.name, x.act, x.id]))"))
        check("A's room seats B (perform sit): A's list says so, and B's world says B sits (B's page lowers its own body)",
              (await a.until("(x => !!x && x.clip === 'sit' && x.loop)(v.visitors[0].act)"), await b.until("room.sit && room.act === 'sit'")), (True, True))
        await a.js("window.cup = new room.THREE.Mesh(new room.THREE.CylinderGeometry(0.05, 0.04, 0.12, 12), new room.THREE.MeshLambertMaterial({ color: 0xffffff })); cup.name = 'cup'; v.hold(v.visitors[0].id, cup)")
        check("and puts a cup in B's hand: in A's room, in front of B's chest (B's model has no hand point), B's own copy of A's room has it",
              (await a.until("(c => !!c && c.parent === room.scene && Math.abs(c.position.y - (v.visitors[0].pose.y + 1.1)) < 0.05 && v.visitors[0].holding)(room.scene.getObjectByName('cup'))"),
               await b.until("!!(here() && here().scene && here().scene.root.getObjectByName('cup')) && v._watches.size > 0 && [...v._watches.values()].some(w => w.holding)")), (True, True))
        await b.js("document.activeElement && document.activeElement.blur()")
        await b.key("KeyW", "w", 87, True)
        check("B walks: no longer seated, there and in B's own world; the cup goes with B",
              (await a.until("!v.visitors[0].act && v.visitors[0].holding"), await b.until("!room.sit && !room.act")), (True, True))
        await b.key("KeyW", "w", 87, False)
        await a.js("v.hold(v.visitors[0].id, null)")
        check("A takes the cup back: out of A's room, and of B's copy", (await a.until("!room.scene.getObjectByName('cup') && !v.visitors[0].holding"),
              await b.until("!(here() && here().scene && here().scene.root.getObjectByName('cup'))")), (True, True))
        check("a clip not in the list: refused, saying which are", await a.js("(() => { try { v.perform(v.visitors[0].id, 'dance'); return 'taken'; } catch (e) { return e.message.slice(0, 40); } })()"),
              "visit.perform: the clip is one of sit, l")
        await a.js("document.querySelector('.visit-panel button.who[data-v=more]').click()")
        await a.js("document.querySelector('.visit-panel .sub button[data-v=look][data-use=mine]').click()")
        check("A opens B's row, our model: B set apart, one of A's own (a box)", await a.until(
            "v.avatarUse(v.visitors[0].visitor.key) === 'mine' && v.avatars.by.length === 1 && v.visitors[0].look === 'mine' && !!room.scene.getObjectByName('body:B')"), True)
        check("and B sees from A's room's eyes now (1.6 m), as B hears", await b.until("(bd => !!bd && bd.look === 'mine' && Math.abs(bd.eyes - 1.6) < 0.01)(v._bodies.get(here().channel))"), True)
        await a.js("document.querySelector('.visit-panel .sub button[data-v=look][data-use=theirs]').click()")
        check("their model again: the default, so no longer apart", await a.until("v.avatars.by.length === 0 && v.visitors[0].look === 'theirs' && !!room.scene.getObjectByName('avatar:B')"), True)
        await a.js("(s => { s.value = 'mine'; s.dispatchEvent(new Event('change', { bubbles: true })); })(document.querySelector('.visit-panel select[data-v=looks]'))")
        check("visitors look like: our model: B a box", await a.until(
            "v.avatars.default === 'mine' && v.visitors[0].look === 'mine' && !!room.scene.getObjectByName('body:B') && !room.scene.getObjectByName('avatar:B')"), True)
        await a.js("(s => { s.value = 'theirs'; s.dispatchEvent(new Event('change', { bubbles: true })); })(document.querySelector('.visit-panel select[data-v=looks]'))")
        await a.until("v.avatars.default === 'theirs' && v.visitors[0].look === 'theirs'")
        # 3.4.3: what a room says of your model is its word, shown as text: never run.
        body = "(h => v._send({ type: 'body', channel: h.channel, look: %s, eyes: 1.5, problem: %s }))([...v._hosted.values()].find(h => h.avatar))"
        await a.js(body % ("'mine'", "'<img src=x onerror=\"window.__pwned = 1\">'"))
        a_row = "[...document.querySelectorAll('.visit-panel button.who[data-v=more]')].find(x => x.querySelector('b') && x.querySelector('b').textContent === 'A')"
        check("a room's word on B's model, with HTML in it: B's panel shows it as text, runs none of it", await b.until(
            "(btn => { if (!btn) return false; const nx = btn.closest('.row').nextElementSibling; if (!nx || !nx.classList.contains('sub')) { btn.click(); return false; }"
            " return nx.textContent.includes(\"yours can't be shown: <img src=x\") && [!!nx.querySelector('img'), typeof window.__pwned]; })(%s)" % a_row), [False, "undefined"])
        await b.js("(btn => btn && btn.click())(%s)" % a_row)
        await a.js(body % ("'theirs'", "null"))
        await b.until("(bd => !!bd && bd.look === 'theirs' && !bd.problem)(v._bodies.get(here().channel))")
        print("\nmodels, checked as other rooms will read them (Visit.checkModel, MODELS.md)")
        check("B's own avatar: fine, its eyes at its eyes point, its clips", await b.js(
            "(c => [c.ok, c.eyes, c.clips.join()])(Visit.checkModel(v._avatarOwn.object, { three: v._hooks.scene.three }))"), [True, 1.5, "idle,walk"])
        # MODELS.md's own example body, as written there: it must pass (the doc and the check agree)
        models = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "MODELS.md")).read()
        example = next(b_ for b_ in models.split("```js")[1:] if "function myBody" in b_).split("```")[0]
        example = "\n".join(l for l in example.splitlines() if not l.startswith("visit."))
        check("MODELS.md's example body passes the check: its feet, its eyes point (1.7 m), its clips", await b.js(
            "(THREE => { %s; return (c => [c.ok, c.problems.join('; '), c.eyes, c.clips.join()])(Visit.checkModel(myBody(THREE), { three: THREE })); })(v._hooks.scene.three)" % example),
            [True, "", 1.7, "idle,walk,ride"])
        check("a model off its feet, set in a room, a clip moving a part it does not have: each said", await b.js("""(T => {
          const g = new T.Group(), m = new T.Mesh(new T.BoxGeometry(0.5, 1.6, 0.5), new T.MeshBasicMaterial()); m.position.y = 1.5; g.add(m); g.position.x = 3;
          g.animations = [new T.AnimationClip('walk', 1, [new T.NumberKeyframeTrack('leg0.rotation[x]', [0, 1], [0, 1])])];
          const c = Visit.checkModel(g, { three: T });
          return [c.ok, c.problems.some(p => p.startsWith('its feet are not at its origin')), c.problems.some(p => p.includes('"leg0"')), c.problems.some(p => p.startsWith('its own place or turn')), g.position.x];
        })(v._hooks.scene.three)"""), [False, True, True, True, 3])

        print("\nmounts: B rides in A's room, on B's room's own (8.11)")
        check("no mounts of Visit's own: none until B's room gives some, and no row for them in its panel", await b.js(
            "v.mounts.length === 0 && !document.querySelector('.visit-panel select[data-v=mount]')"), True)
        await b.js("""(T => {
          const box = (name, w, h, d, y, color) => { const m = new T.Mesh(new T.BoxGeometry(w, h, d), new T.MeshBasicMaterial({ color })); m.name = name; m.position.y = y; return m; };
          const bob = (name, a) => new T.AnimationClip(name, 0.5, [new T.NumberKeyframeTrack('pony-body.position[y]', [0, 0.25, 0.5], [0.9, 0.9 + a, 0.9])]);
          const pony = new T.Group(); pony.add(box('pony-body', 0.5, 0.6, 1.4, 0.9, 0x8b5a2b)); pony.animations = [bob('idle', 0.01), bob('walk', 0.12)];
          const rug = new T.Group(); rug.add(box('rug-body', 1, 0.04, 1.6, 0.4, 0x9b1b30));
          v.possessions([{ id: 'pony', name: 'Pony', object: pony, mount: true, speed: 2, lift: 0.55 },
            { id: 'rug', name: 'Rug', object: rug, mount: true, fly: true, speed: 2, lift: 0.45 },
            { id: 'crate', name: 'Crate', mount: true, lift: 0.3, object: box('crate-box', 1, 0.3, 1.4, 0.15, 0xaa7733) }]);
        })(v._hooks.scene.three)""")
        check("B's room gives its own: its mounts, and a row for them in its panel", (await b.js("v.mounts.map(m => m.id + (m.fly ? '^' : '')).join()"),
              await b.until("!!document.querySelector('.visit-panel select[data-v=mount]')")), ("pony,rug^,crate", True))
        y0 = await a.js("v.visitors[0].pose.y")
        await b.js("v.ride('pony')")
        check("B gets on its pony: A asks for it, has it under B, on A's floor, B lifted onto it", await a.until(
            "(o => !!o && !!o.getObjectByName('pony-body') && !!v.visitors[0].riding && v.visitors[0].riding.id === 'pony' && Math.abs(v.visitors[0].pose.y - %r - 0.55) < 0.01"
            " && Math.abs(o.position.x - v.visitors[0].pose.x) < 0.01 && Math.abs(o.position.y) < 0.01)(room.scene.getObjectByName('mount:B'))" % y0), True)
        await a.shot("a_pony.png")
        check("A's move hears what B rides, at the pony's pace", await a.until("window.movedWith && window.movedWith.riding && window.movedWith.riding.speed"), 2)
        await b.js("document.activeElement && document.activeElement.blur()")
        await b.key("KeyW", "w", 87, True)
        if not await a.until("(r => !!r && !!r.playing && r.playing === r.clips.walk)([...v._hosted.values()][0].ride)", 6):
            print("      walk: A's visitor", await a.js("JSON.stringify({ visitors: v.visitors.map(x => [x.visitor.name, x.pose, x.riding]), ride: (r => r && { playing: !!r.playing, clips: Object.keys(r.clips || {}) })([...v._hosted.values()][0] && [...v._hosted.values()][0].ride) })"),
                  "B:", await b.js("JSON.stringify({ me: room.me, where: room.where && room.where.name, riding: v.riding, keys: [...room.keys], active: document.activeElement && document.activeElement.tagName })"))
        check("B walks on: A plays the pony's own walk clip", await a.until("(r => !!r && !!r.playing && r.playing === r.clips.walk)([...v._hosted.values()][0].ride)"), True)
        await b.key("KeyW", "w", 87, False)
        await b.js("v.ride('rug')")   # at once: its model may come sooner than a host takes them, and waits its turn
        check("the flying rug instead: under B, the pony gone", await a.until(
            "(o => !!o && !!o.getObjectByName('rug-body') && v.visitors[0].riding.id === 'rug' && room.scene.children.filter(c => c.name === 'mount:B').length === 1)(room.scene.getObjectByName('mount:B'))"), True)
        await b.key("Space", " ", 32, True)
        check("A's room says nothing of flying: B's rug flies (Space, up)", await a.until("v.visitors[0].pose.y > %r + 0.45 + 1" % y0), True)
        await a.shot("a_rug.png")
        await b.key("Space", " ", 32, False)
        await a.js("window.room.fly = false")
        check("A's room says no flying (fly: false): B floats down, rug and all", await a.until("Math.abs(v.visitors[0].pose.y - %r - 0.45) < 0.01" % y0, 20), True)
        await a.js("window.room.fly = undefined; window.room.mounts = false")
        check("A's room keeps mounts out (mounts: false): B is off, on the floor", await a.until(
            "!room.scene.getObjectByName('mount:B') && !v.visitors[0].riding && Math.abs(v.visitors[0].pose.y - %r) < 0.01" % y0), True)
        await a.js("window.room.mounts = undefined")
        await b.js("v.ride('crate')")
        check("B rides its crate in A's room: under B", await a.until(
            "(o => !!o && !!o.getObjectByName('crate-box') && Math.abs(v.visitors[0].pose.y - %r - 0.3) < 0.01)(room.scene.getObjectByName('mount:B'))" % y0), True)
        await b.key("KeyM", "m", 77, True)
        await b.key("KeyM", "m", 77, False)
        check("M: off; A's room has B on the floor", (await b.until("v.riding === null"), await a.until("!room.scene.getObjectByName('mount:B') && Math.abs(v.visitors[0].pose.y - %r) < 0.01" % y0)), (True, True))
        await b.key("KeyM", "m", 77, True)
        await b.key("KeyM", "m", 77, False)
        check("M again: on the one B last rode", await b.until("v.riding"), "crate")
        await a.js("window.oldWs = v._ws, v._ws.close()")   # A's page loses its visit-client a moment (it connects again by itself)
        if not await a.until("v._ws !== window.oldWs && v._ws.readyState === 1 && v.state === 'online' && v.visitors.length === 1 && !!v.visitors[0].riding && !!room.scene.getObjectByName('crate-box')", 30):
            print("      rides: A", await a.js("JSON.stringify({ ws: v._ws && v._ws.readyState, state: v.state, visitors: v.visitors.map(x => [x.visitor.name, x.riding, !!x.pose]), crate: !!room.scene.getObjectByName('crate-box'), mounts: room.scene.children.filter(c => c.name === 'mount:B').length })"),
                  "B", await b.js("JSON.stringify({ riding: v.riding, where: room.where && room.where.name, w: [...v._watches.values()].map(w => [w.host.name, w.inside]) })"))
        check("A's page loses its visit-client and comes back while B rides: B's crate and B's body in A's room once each, not twice",
              await a.until("v._ws !== window.oldWs && v._ws.readyState === 1 && v.state === 'online' && v.visitors.length === 1 && !!v.visitors[0].riding && !!room.scene.getObjectByName('crate-box')"
                            " && room.scene.children.filter(c => c.name === 'mount:B').length === 1 && room.scene.children.filter(c => c.name === 'avatar:B').length <= 1", 30), True)
        await b.js("v.ride(null)")
        await a.until("!room.scene.getObjectByName('mount:B')")
        await step_out(b, "A")
        await a.until("v.visitors.length === 0")

        print("\nvoice: by who is near, in the same room")
        await a.until("v.offices.some(o => o.name === 'B')")   # A's page just came back: the lobby's list again, first
        await step_in(a, "B")
        await b.until("v.visitors.length === 1")
        await b.js("document.querySelector('.visit-panel button[data-v=mic]').click()")
        check("B's microphone on from its panel", await b.until("v.mic && !!v._mic"), True)
        await a.js("document.querySelector('.visit-panel button[data-v=mic]').click()")
        await a.until("v.mic && !!v._mic")
        check("A, in B's room, near B: each has the other near", (await a.until("v.voices.length === 1 && v.voices[0].name"), await b.until("v.voices.length === 1 && v.voices[0].name")), ("B", "A"))
        check("A hears B: B's voice decoded and played at A, from where B stands (the fake microphone's tone)", await a.until(
            "(s => !!s && s.frames > 50 && s.level > 0.01 && s.near)(v._voices.get(v.voices[0].key))"), True)
        check("and B hears A, from where B has A in its room", await b.until("(s => !!s && s.frames > 50 && s.level > 0.01 && s.near)(v._voices.get(v.voices[0].key))"), True)
        PATHS = """(async () => { const out = []; for (const c of v._rtc.values()) { const st = await c.pc.getStats(); let pair = null, kinds = {};
          st.forEach(r => { if (r.type === "transport" && r.selectedCandidatePairId) pair = st.get(r.selectedCandidatePairId); if (r.type === "local-candidate") kinds[r.id] = r.candidateType; });
          out.push(pair ? kinds[pair.localCandidateId] : "none"); } return out.join(); })()"""
        check("4.2 (#71): every connection of A's goes through the lobby's TURN (A's page allows no other way); B's directly",
              (await a.until(f"(async () => {{ const p = await {PATHS}; return !!p && p.split(',').every(x => x === 'relay'); }})()"),
               await b.until(f"(async () => {{ const p = await {PATHS}; return !!p && p.split(',').every(x => x === 'host' || x === 'srflx' || x === 'prflx'); }})()")),
              (True, True))
        await a.js("v.setName('Ann')")
        check("A gives the name its user goes by: B's panel row and who is near B say it", (await b.until(
            "[...document.querySelectorAll('.visit-panel button.who')].some(b => b.textContent.includes('Ann (A)'))"), await b.until("v.voices.some(x => x.name === 'Ann')")), (True, True))
        check("4.3.0: and B's room has its visitor A by that name, A's office beside it (v.visitors: visitor.name, visitor.office)",
              await b.until("(x => !!x && x.name === 'Ann' && x.office === 'A' && x.name)((v.visitors[0] || {}).visitor)"), "Ann")
        await a.js("v.say('named')")
        check("and A's own line, in A's chat window, by that name too (as B has it)", await a.until(
            "(t => t.endsWith('named') && t)(document.querySelector('.visit-chat .log').lastChild.textContent)"), "[Ann] says: named")
        await a.js("v.setName(null)")
        await b.until("v.voices.some(x => x.name === 'A')")
        check("4.3.0: none again: B's room has A by its office's name", await b.until("(x => !!x && x.name === 'A' && x.name)((v.visitors[0] || {}).visitor)"), "A")
        check("B's panel shows A near, speaking", await b.until("document.querySelector('.visit-panel').textContent.includes('🔊 A')"), True)
        check("A's world draws a 🔊 over B, in B's room, as A hears B (Visit's own, by default)", await a.until(
            "!!room.world.bubbles && [...room.world.bubbles.by.keys()].some(k => k.startsWith('speak:'))"), True)
        check("and B's world one over A's body, as B hears A", await b.until("!!room.world.bubbles && [...room.world.bubbles.by.keys()].some(k => k.startsWith('speak:'))"), True)
        check("heard within 45 m, as both visit-clients say", (await a.js("v.voiceRange"), await b.js("v.voiceRange")), (45, 45))
        bx0 = await b.js("room.me.x")
        await b.js("room.me.x += 30")
        far = "v.voices.length === 1 && v.voices[0].distance > 20 && v.voices[0].distance < 45"
        check("B walks 30 m off: still near each other, as B's visit-client has it (the old 15 m would not have reached)", (await b.until(far), await a.until(far)), (True, True))
        if not await b.js(far):
            print("      B's near:", await b.js("JSON.stringify(v.voices)"), "A's:", await a.js("JSON.stringify(v.voices)"))
        await b.js("room.me.x += 70")
        check("100 m off: neither hears the other, and neither sends a thing (B's visit-client says nobody is near)", (await b.until("v.voices.length === 0 && [...v._voices.values()].every(s => !s.near) && v._voiceTo === 0"),
              await a.until("v.voices.length === 0 && v._voiceTo === 0")), (True, True))
        await b.js("room.me.x = %f" % bx0)
        await a.until("v.voices.length === 1")

        print("\nchat: a line said, over its speaker's head, for everyone in the room")
        # A view's lines said, not its 🔊 marks (keyed speak:): the microphones are still on, and the fake one's tone
        # (a beep every 500 ms) can have its speaker marked as speaking at any moment.
        said = lambda bubbles: f"[...{bubbles}.by].filter(([k]) => !k.startsWith('speak:')).map(([, x]) => x)"
        await a.js("v.say('hello from A')")
        check("A says a line in B's room: B has it, from A", await b.until("(c => c && c.text)(v.chats.find(c => c.name === 'A'))"), "hello from A")
        check("and B's world draws it over A's body, above its head", await b.until(
            f"!!room.world.bubbles && (l => l.length === 1 && l[0].sprite.position.y > 1.8)({said('room.world.bubbles')})"), True)
        async def enter(pg):   # the Enter key, as a person presses it
            for t in ("keyDown", "keyUp"):
                await pg.call("Input.dispatchKeyEvent", type=t, code="Enter", key="Enter", windowsVirtualKeyCode=13, **({"text": "\r"} if t == "keyDown" else {}))
        cw = "document.querySelector('.visit-chat')"
        check("B's panel has the chat window, bottom left, its hint there", await b.js(
            f"(c => !!c && getComputedStyle(c).position === 'fixed' && c.getBoundingClientRect().left < 40 && innerHeight - c.getBoundingClientRect().bottom < 40)({cw})"), True)
        await enter(b)
        check("B presses Enter: the window opens, its box has the keys", await b.until(f"(c => c.classList.contains('typing') && document.activeElement === c.querySelector('input'))({cw})"), True)
        await b.call("Input.insertText", text="hi A, from my chat")
        await enter(b)
        check("B types a line, Enter: A has it, over the room's owner", await a.until("(c => c && c.owner && c.text)(v.chats.find(c => c.name === 'B'))"), "hi A, from my chat")
        check("and B's window shows it, the box gone again", await b.js(f"(c => [c.classList.contains('typing'), c.querySelector('.log').lastChild.textContent])({cw})"),
              [False, "[B] says: hi A, from my chat"])
        check("A's window too", await a.until(f"{cw}.querySelector('.log').lastChild.textContent"), "[B] says: hi A, from my chat")
        who = f"(n => n ? [n.textContent, n.style.color] : null)({cw}.querySelector('.log').lastChild.querySelector('.who'))"
        wa, wb = await a.js(who), await b.js(who)
        check("B's name in it in B's own colour, the same in both windows (by B's key, as WoW colours a name by its class)", (wa, bool(wb and wb[1])), (wb, True))
        check("A's world draws it over B, in B's room", await a.until(
            f"{said('room.world.bubbles')}.some(l => l.text === 'hi A, from my chat')"), True)
        if not await b.until(f"{said('room.world.bubbles')}.length === 2", 5):
            print("      B's world's bubbles:", await b.js(f"JSON.stringify({said('room.world.bubbles')}.map(l => l.text))"), "B at", await b.js("JSON.stringify([room.me, room.where])"))
        check("and B's world draws B's own over B's head too", await b.js(f"{said('room.world.bubbles')}.some(l => l.text === 'hi A, from my chat')"), True)
        await enter(b)
        await b.call("Input.insertText", text="/w A ")
        check("/w A and a space: the box whispers to A, the command gone", await b.until(
            f"(c => c.dataset.scope === 'whisper' && c.querySelector('select').selectedOptions[0].textContent === 'Tell A:' && c.querySelector('input').value === '')({cw})"), True)
        await b.call("Input.insertText", text="psst")
        await enter(b)
        check("whispered: A has it, from B, and nobody's bubble", await a.until(
            "(l => l && l.scope === 'whisper' && l.name === 'B' && !v.chats.some(c => c.text === 'psst') && l.text)(v.chatLog.at(-1))"), "psst")
        check("A's window: [B] whispers; B's: To [A]", (await a.js(f"{cw}.querySelector('.log').lastChild.textContent"), await b.js(f"{cw}.querySelector('.log').lastChild.textContent")),
              ("[B] whispers: psst", "To [A]: psst"))
        await enter(b)
        await b.call("Input.insertText", text="/s ")
        await b.call("Input.insertText", text="/r ")
        check("/r on B, whom nobody has whispered to yet: back to whom B last whispered (A)", await b.until(
            f"(c => c.dataset.scope === 'whisper' && c.querySelector('select').selectedOptions[0].textContent === 'Tell A:' && c.querySelector('input').value === '')({cw})"), True)
        await b.key("Escape", "Escape", 27, True)
        await b.key("Escape", "Escape", 27, False)
        await enter(a)
        await a.call("Input.insertText", text="/r back at you")
        await enter(a)
        check("A: /r, a whisper back to B", await b.until(f"(t => t.includes('whispers') && t)({cw}.querySelector('.log').lastChild.textContent)"), "[A] whispers: back at you")
        await enter(b)
        await b.call("Input.insertText", text="/room A ")
        check("/room A and a space: the box goes to A's room", await b.until(
            f"(c => c.dataset.scope === 'sim' && c.querySelector('select').selectedOptions[0].textContent === \"To A's room:\" && c.querySelector('input').value === '')({cw})"), True)
        await b.call("Input.insertText", text="into your room")
        await enter(b)
        check("said: A has it in its chat, into its room, from B (over nobody's head)", await a.until(
            "(l => l && l.scope === 'sim' && l.name === 'B' && l.to.key === v.key && l.text)(v.chatLog.at(-1))"), "into your room")
        check("A's window: [Your room] [B]; B's: [A's room] [B] (a room's chat, as a channel)", (await a.js(f"{cw}.querySelector('.log').lastChild.textContent"), await b.js(f"{cw}.querySelector('.log').lastChild.textContent")),
              ("[Your room] [B]: into your room", "[A's room] [B]: into your room"))
        check("the lobby carries chat (its welcome said so)", (await a.js("v.lobbyChat"), await b.js("v.lobbyChat")), (True, True))
        await enter(b)
        check("B's box remembers where it last said a line", await b.until(f"{cw}.dataset.scope"), "sim")
        await b.call("Input.insertText", text="/1 hello lobby")
        await enter(b)
        check("/1: a line to the lobby: A has it, and B (from the lobby)", (await a.until(f"(t => t.startsWith('[Lobby]') && t)({cw}.querySelector('.log').lastChild.textContent)"), await b.until(
            f"(t => t.startsWith('[Lobby]') && t)({cw}.querySelector('.log').lastChild.textContent)")), ("[Lobby] [B]: hello lobby", "[Lobby] [B]: hello lobby"))
        # 3.3, ignoring (as WoW's): a click on a name in the log, Ignore; their lines go, and come no more; the panel's list.
        kb_ = await b.js("v.key")
        await a.js(f"{cw}.querySelector('.log').lastChild.querySelector('.who.act').click()")
        check("A clicks B's name in its log: a menu, B's name, Whisper and Ignore", await a.until(
            f"(m => !m.hidden && [m.querySelector('.hd').textContent, [...m.querySelectorAll('button')].map(b => b.textContent)])({cw}.querySelector('.namemenu'))"), ["B", ["Whisper", "Ignore"]])
        await a.js(f"{cw}.querySelector('.namemenu button[data-m=ig]').click()")
        check("Ignore: B ignored, by B's key; B's lines out of A's log", await a.until(
            f"v.isIgnored({json.dumps(kb_)}) && v.ignored[0].name === 'B' && ![...{cw}.querySelectorAll('.log .line[data-k]')].some(d => d.dataset.k === {json.dumps(kb_)})"), True)
        await asyncio.sleep(5)   # (B's lines: at most 5 every 10 s)
        await enter(b)
        await b.call("Input.insertText", text="/1 not for A")
        await enter(b)
        await b.until(f"{cw}.querySelector('.log').lastChild.textContent.includes('not for A')")
        await asyncio.sleep(1)
        check("B's next line in the lobby never reaches A", await a.js("v.chatLog.some(l => l.text === 'not for A')"), False)
        check("A's panel: ignored, 1 of 50", await a.until("(r => r && r.textContent)(document.querySelector('.visit-panel button[data-v=ig_clear]') && document.querySelector('.visit-panel button[data-v=ig_clear]').parentElement)"),
              "ignored · 1 of 50ShowClear")
        await a.js("window.confirm = () => true; document.querySelector('.visit-panel button[data-v=ig_clear]').click()")
        check("Clear: A ignores nobody", await a.until("v.ignored.length === 0 && !document.querySelector('.visit-panel button[data-v=ig_clear]')"), True)
        await enter(b)
        await b.call("Input.insertText", text="half a thought")
        await b.key("Escape", "Escape", 27, True)
        await b.key("Escape", "Escape", 27, False)
        check("the window's 🔔: a sound with each line that comes; a click, none (kept in this browser)", (await b.js(f"{cw}.querySelector('button.sound').textContent"),
              await b.js(f"({cw}.querySelector('button.sound').click(), {cw}.querySelector('button.sound').textContent)"), await b.js(f"({cw}.querySelector('button.sound').click(), {cw}.querySelector('button.sound').textContent)")), ("🔔", "🔕", "🔔"))
        check("Esc: the box goes, its line unsaid", (await b.until(f"!{cw}.classList.contains('typing')"), await a.js("v.chatLog.some(l => l.text === 'half a thought')")), (True, False))
        await enter(b)
        check("B, with A in its room: its own room's chat among the choices (and Yard's, B's world)", await b.js(f"[...{cw}.querySelector('select').options].map(o => o.textContent)"),
              ["Say:", "Tell A:", "To your room:", "To A's room:", "Lobby:", "World Yard:"])
        await b.call("Input.insertText", text="kept")
        steps = []
        for shift in [False] * 5 + [True] * 5:
            await b.key("Tab", "Tab", 9, True, shift)
            await b.key("Tab", "Tab", 9, False, shift)
            steps.append(await b.js(f"{cw}.querySelector('select').selectedOptions[0].textContent"))
        check("Tab: the box on to the next place, round again (Shift+Tab: back); one Tell, to whom B last whispered with; the line kept, the box still typed in",
              (steps, await b.js(f"(c => [c.dataset.scope, c.querySelector('input').value, document.activeElement === c.querySelector('input')])({cw})")),
              (["World Yard:", "Say:", "Tell A:", "To your room:", "To A's room:", "To your room:", "Tell A:", "Say:", "World Yard:", "Lobby:"], ["lobby", "kept", True]))
        await b.js(f"{cw}.querySelector('input').value = ''")
        await b.call("Input.insertText", text="/s")
        await b.key("Tab", "Tab", 9, True)
        await b.key("Tab", "Tab", 9, False)
        check("not while a command is in the box: that says where", await b.js(f"(c => [c.dataset.scope, c.querySelector('input').value])({cw})"), ["lobby", "/s"])
        await b.js(f"{cw}.querySelector('input').value = ''")
        await b.call("Input.insertText", text="/w ")
        check("/w and a space: the names it could be, over the box, to pick", await b.until(f"[...{cw}.querySelectorAll('.names button')].map(b => b.textContent).join()"), "A")
        await b.key("Tab", "Tab", 9, True)
        await b.key("Tab", "Tab", 9, False)
        check("Tab finishes the name, not yet taken (a longer one could follow)", await b.until(f"(c => c.querySelector('input').value === '/w A' && c.dataset.scope)({cw})"), "lobby")
        await b.call("Input.insertText", text=" ")
        check("then a space: the box whispers to A, the names gone", await b.until(
            f"(c => c.dataset.scope === 'whisper' && c.querySelector('select').selectedOptions[0].textContent === 'Tell A:' && c.querySelector('input').value === '' && c.querySelector('.names').textContent)({cw})"), "")
        await b.call("Input.insertText", text="hey @")
        check("@ in a line: whom it could mention, over the box", await b.until(f"[...{cw}.querySelectorAll('.names button')].map(b => b.textContent).join()"), "A")
        await b.key("Enter", "Enter", 13, True)
        await b.key("Enter", "Enter", 13, False)
        check("Enter takes it: the name in the line, nothing said yet", await b.until(
            f"(c => c.querySelector('input').value === 'hey @A ' && c.classList.contains('typing') && !c.querySelector('.names button'))({cw})"), True)
        check("a line that mentions A (@A), however it comes: A's window shows it apart, and the line says so; @Ab is no mention", await a.js(
            "(v._chatMsg({ type: 'chat', scope: 'lobby', id: 'o-test', key: 'k', name: 'Zed', text: 'look, @A' }, false), v._chatMsg({ type: 'chat', scope: 'lobby', id: 'o-test', key: 'k', name: 'Zed', text: 'hey @Ab' }, false),"
            f" (l => [v.chatLog.at(-2).mention, v.chatLog.at(-1).mention, l.at(-2).classList.contains('mention'), l.at(-1).classList.contains('mention')])([...{cw}.querySelectorAll('.log .line')]))"), [True, False, True, False])
        await b.key("Escape", "Escape", 27, True)
        await b.key("Escape", "Escape", 27, False)
        await enter(a)
        await a.call("Input.insertText", text="/room")
        await enter(a)
        check("/room alone, Enter: A's box goes to its own room's chat, still open", await a.until(
            f"(c => c.dataset.scope === 'sim' && c.classList.contains('typing') && c.querySelector('select').selectedOptions[0].textContent)({cw})"), "To your room:")
        await a.call("Input.insertText", text="for everyone here")
        await enter(a)
        check("said: B, linked with A (A visits it), has it in its chat, A's room's", await b.until(
            "(l => l && l.scope === 'sim' && l.name === 'A' && l.to.name === 'A' && l.to.key !== v.key && l.text)(v.chatLog.at(-1))"), "for everyone here")
        check("B's window: [A's room] [A]; A's: [Your room] [A]", (await b.until(f"(t => t.includes('everyone') && t)({cw}.querySelector('.log').lastChild.textContent)"),
              await a.js(f"{cw}.querySelector('.log').lastChild.textContent")), ("[A's room] [A]: for everyone here", "[Your room] [A]: for everyone here"))
        await b.js("ui.chat.aim({ scope: 'say' })")
        check("4.0: B's box on Say: no 🎧 there (who is near hears you; 🎧 follows the box: a person, a world, a room)", await b.until(f"{cw}.querySelector('button.headset').hidden"), True)
        await b.js("ui.chat.aim({ scope: 'sim', key: v.key, name: v.office.name })")   # /room: B's own
        await b.js(f"{cw}.querySelector('button.headset').click()")
        check("B's box on its own room, its 🎧: into its own room's voice, a line over the box saying so", await b.until(
            f"!!v.roomVoice && v.roomVoice.key === v.key && {cw}.querySelector('.voice').textContent.startsWith('🎧 Your room: you')"), True)
        await a.js("v.joinVoice(here().host.key)")
        voice = f"{cw}.querySelector('.voice').textContent.replace(/🔊 /g, '')"   # who is in it, not who is speaking (the fake tones)
        check("A joins it: both lines say who is in it", (await a.until(f"(t => t.startsWith('🎧 B') && t.includes('B · you'))({voice})"),
              await b.until(f"{voice}.includes('you · A')")), (True, True))
        await b.js("document.querySelector('.visit-panel .sub') || document.querySelector('.visit-panel button.who[data-v=more]').click()")
        check("B opens A's row: ban voice, ban chat, Kick from voice", await b.until(
            "(s => !!s && [...s.querySelectorAll('button')].map(b => b.textContent).filter(t => /ban|voice/.test(t)).join())(document.querySelector('.visit-panel .sub'))"), "ban voice,ban chat,Kick from voice")
        await b.js("document.querySelector('.visit-panel .sub button[data-v=ban][data-what=chat]').click()")
        check("ban chat: kept, and A told (its window says so)", (await b.until("v.bans.length === 1 && v.bans[0].chat && !v.bans[0].voice && v.bans[0].name"),
              await a.until(f"v.bannedBy.length === 1 && {cw}.querySelector('.log').textContent.includes('keeps you out of its chat')")), ("A", True))
        await b.js("document.querySelector('.visit-panel .sub button[data-v=ban][data-what=chat]').click()")
        check("clicked again: lifted", (await b.until("v.bans.length === 0"), await a.until("v.bannedBy.length === 0")), (True, True))
        await b.js("document.querySelector('.visit-panel .sub button[data-v=voice_kick]').click()")
        check("Kick from voice: A is out, told why", await a.until(f"!v.roomVoice && {cw}.querySelector('.log').textContent.includes('took you out of their room')"), True)
        await b.js(f"{cw}.querySelector('.voice button[data-v=leave]').click()")
        check("B's Leave: out of its own voice, the line gone", await b.until(f"!v.roomVoice && {cw}.querySelector('.voice').textContent === ''"), True)
        await enter(a)
        await a.call("Input.insertText", text="/s ")
        await a.js("""(async () => {
            const c = document.createElement('canvas'); c.width = 64; c.height = 48;
            const g = c.getContext('2d'); g.fillStyle = '#e33'; g.fillRect(0, 0, 64, 48); g.fillStyle = '#3e3'; g.fillRect(16, 12, 32, 24);
            const blob = await new Promise(r => c.toBlob(r, 'image/png'));
            const dt = new DataTransfer(); dt.items.add(new File([blob], 'shot.png', { type: 'image/png' }));
            document.querySelector('.visit-chat input').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
        })()""")
        check("A pastes a picture into the box: it waits over the box, kept by A's visit-client", await a.until(
            f"(c => c.querySelectorAll('.attach .chip').length === 1 && !c.querySelector('.attach .chip.busy'))({cw})"), True)
        await a.call("Input.insertText", text="my screen")
        await enter(a)
        check("Enter: said with it, in B's room; B's window shows the line and the picture (from B's own visit-client, which had it from A's)", await b.until(
            f"(l => !!l && l.textContent.includes('my screen') && (i => !!i && i.src.startsWith('blob:') && i.complete && i.naturalWidth === 64)(l.querySelector('img.pic')))({cw}.querySelector('.log').lastChild)"), True)
        check("and A's own, the box empty again", await a.until(f"(c => (i => !!i && i.naturalWidth === 64)(c.querySelector('.log').lastChild.querySelector('img.pic')) && !c.querySelector('.attach .chip'))({cw})"), True)
        await b.js(f"{cw}.querySelector('.log').lastChild.querySelector('img.pic').click()")
        check("B clicks it: full size, over everything; a click, gone", (await b.until("!!document.querySelector('.visit-lightbox img')"),
              await b.js("(d => (d.click(), !document.querySelector('.visit-lightbox')))(document.querySelector('.visit-lightbox'))")), (True, True))
        await enter(b)
        await b.call("Input.insertText", text="/s ")
        await b.js(f"{cw}.querySelector('button.gallery').click()")
        check("B's 🖼 (its box on its own room): what was shared in it, from its database: A's picture", await b.until(
            f"(s => s.classList.contains('on') && s.querySelectorAll('.grid img').length === 1 && s.querySelector('.tabs b').textContent)({cw}.querySelector('.shared'))"), "Shared in your room")
        await b.js(f"{cw}.querySelector('.shared button[data-k=links]').click()")
        check("its links: none yet", await b.until(f"(t => t.includes('No links') && t)({cw}.querySelector('.shared .items').textContent)"), "No links shared here yet")
        await b.js(f"{cw}.querySelector('.shared button[data-k=close]').click()")
        await b.key("Escape", "Escape", 27, True)
        await b.key("Escape", "Escape", 27, False)

        print("\nthe mailbox (0.22.0): what came for B while nobody looked at its chat")
        await b.js("document.hasFocus = () => false; dispatchEvent(new Event('blur'))")
        check("B's page loses the focus: nobody looking at its chat, its visit-client told", await b.until("!v._attention"), True)
        await a.js("v.say('are you there?', { whisper: v.offices.find(o => o.name === 'B').id })")
        check("A whispers B: B's 📬 says so, there even as the window fades", await b.until(f"(m => m.classList.contains('on') && m.textContent)({cw}.querySelector('button.mail'))"), "📬 1")
        await b.js(f"{cw}.querySelector('button.mail').click()")
        check("B clicks it: the line, when it came; and it is read (the 📬 goes)", (await b.until(
            f"(m => m.classList.contains('on') && (l => !!l && /^\\d/.test(l.dataset.time) && l.textContent === '[A] whispers: are you there?')(m.querySelector('.line')))({cw}.querySelector('.mailbox'))"),
            await b.until(f"v.unseen.count === 0 && !{cw}.querySelector('button.mail').classList.contains('on')")), (True, True))
        await b.shot("b_mailbox.png")
        await b.js(f"{cw}.querySelector('.mailbox .line').click()")
        check("a click on the line: B's box open, answering there", await b.until(f"(c => c.classList.contains('typing') && !c.querySelector('.mailbox.on') && c.querySelector('select').selectedOptions[0].textContent)({cw})"), "Tell A:")
        await b.js("delete document.hasFocus; dispatchEvent(new Event('focus'))")
        check("focused again: looking", await b.until("v._attention"), True)

        print("\nvoice messages (0.22.0): recorded by the box, played from the line")
        await b.js(f"{cw}.querySelector('button.rec').click()")
        check("B's 🎙: recording (the fake microphone), how long so far", await b.until(f"(r => r.classList.contains('on') && r.textContent.startsWith('⏺'))({cw}.querySelector('button.rec'))"), True)
        await asyncio.sleep(1.7)
        await b.js(f"{cw}.querySelector('button.rec').click()")
        check("🎙 again: done; it waits over the box, kept by B's visit-client", await b.until(
            f"(c => (ch => !!ch && !ch.classList.contains('busy') && /^🎙 0:0[12]/.test(ch.textContent) && c.querySelector('button.rec').textContent === '🎙')(c.querySelector('.attach .chip')))({cw})"), True)
        await b.shot("b_voice.png")
        await enter(b)
        check("Enter: whispered to A with it (over their link); A's window shows ▶ and how long", await a.until(
            f"(l => !!l && l.textContent.startsWith('[B] whispers:') && (p => !!p && /^▶ 0:0[12]$/.test(p.textContent))(l.querySelector('button.play')))({cw}.querySelector('.log').lastChild)"), True)
        await a.js(f"{cw}.querySelector('.log').lastChild.querySelector('button.play').click()")
        check("A clicks ▶: it plays, from A's own visit-client (which had it from B's)", await a.until(
            f"/^■ 0:0\\d \\/ 0:0[12]$/.test({cw}.querySelector('.log').lastChild.querySelector('button.play').textContent)"), True)
        await b.js(f"{cw}.querySelector('button.gallery').click()")
        await b.js(f"{cw}.querySelector('.shared button[data-k=voice]').click()")
        check("B's 🖼, Voice: the voice message in its whispers with A", await b.until(f"(s => s.classList.contains('on') && s.querySelectorAll('.items button.play').length === 1 && s.querySelector('.items').textContent)({cw}.querySelector('.shared'))"), "you: ▶")
        await b.js(f"{cw}.querySelector('.shared button[data-k=close]').click()")
        await b.key("Escape", "Escape", 27, True)
        await b.key("Escape", "Escape", 27, False)
        await a.js("window.cw2 = VisitUI.chat(v, { fade: 300 })")
        check("a window idle a while fades away", await a.until("cw2.element.classList.contains('idle')"), True)
        await b.js("v.say('wake up')")
        check("and comes back with the next line", await a.until("!cw2.element.classList.contains('idle') && cw2.element.querySelector('.log').lastChild.textContent"), "[B] says: wake up")
        await a.js("cw2.release()")
        check("each line: when it came, dim before it (the time as this browser writes one)", await a.js(
            f"(l => l.dataset.time === new Date(Number(l.dataset.at)).toLocaleTimeString([], {{ hour: 'numeric', minute: '2-digit' }}) && getComputedStyle(l, '::before').content !== 'none')({cw}.querySelector('.log').lastChild)"), True)
        # Two lines from before this page, the newer first (a line kept for B, then one from another day): each goes
        # where its time puts it, not where it came; a day over each day's first line.
        await b.js("v._chatMsg({ type: 'chat', scope: 'lobby', id: 'o-late', key: 'k-old', name: 'Old', text: 'from a moment ago', at: Date.now() - 5000 }, true)")
        await b.js("v._chatMsg({ type: 'chat', scope: 'lobby', id: 'o-old', key: 'k-old', name: 'Old', text: 'from two days ago', at: Date.now() - 2 * 86400000 }, true)")
        check("a line from another day: its day over it, and today's over the next", await b.until(
            "(dayName => "   # as the window writes a day: its year too, when not this year's
            "(c => (d => !!d && [d.previousElementSibling.className, d.previousElementSibling.textContent === dayName(Number(d.dataset.at)),"
            f" (n => !!n && n.previousElementSibling.className === 'day' && n.previousElementSibling.textContent === dayName(Date.now()))"
            f"([...c.querySelectorAll('.log .line[data-at]')].find(x => Number(x.dataset.at) > Number(d.dataset.at)))])"
            f"([...c.querySelectorAll('.log .line')].find(x => x.textContent.endsWith('from two days ago'))))({cw})"
            ")(at => (x => x.toLocaleDateString([], Object.assign({ weekday: 'short', month: 'short', day: 'numeric' }, x.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {})))(new Date(at)))"), ["day", True, True])
        check("...and the two in time order, whichever came first", await b.js(
            f"(l => l.indexOf('from two days ago') >= 0 && l.indexOf('from two days ago') < l.indexOf('from a moment ago'))([...{cw}.querySelectorAll('.log .line')].map(x => x.textContent.endsWith('from two days ago') ? 'from two days ago' : x.textContent.endsWith('from a moment ago') ? 'from a moment ago' : ''))"), True)
        await a.shot("a_chat.png")
        await b.shot("b_chat.png")
        await a.shot_of("ui-panel.png", ".visit-panel")
        # one of B's room's own figures (a receptionist, a guide): its line over its object, in everyone's chat, kept, never as B's user
        await b.js("room.add('visit:npc:guide')")
        await a.until("!!(here() && here().scene && here().scene.root.getObjectByName('visit:npc:guide'))")
        check("a bad figure id is refused at once", await b.js("v.sayAs('../x', 'Guide', 'no')"), False)
        await b.js("window.sentAs = []; (s => { v._send = m => { if (m.type === 'say_as') sentAs.push(m); return s(m); }; })(v._send.bind(v))")
        check("B's room's guide says a line (sayAs)", await b.js("v.sayAs('guide', 'Guide', 'welcome to my room')"), True)
        check("4.0.1: with where the guide stands, in metres (heard within 45 m of it)", await b.js(
            "(m => (sc => (p => Array.isArray(m.at) && m.at.length === 3 && m.at.every((x, i) => Math.abs(x - p[i] * v._unit()) < 0.06))"
            "(sc.root.getObjectByName('visit:npc:guide').getWorldPosition(new sc.three.Vector3()).toArray()))(v._hooks.scene))(sentAs.at(-1))"), True)
        check("A, in B's room, has it from the guide, by its name, not from B", await a.until(
            "(c => c && c.npc === 'guide' && !c.owner && !c.id && c.text)(v.chats.find(c => c.name === 'Guide'))"), "welcome to my room")
        check("A's world draws it, in B's room", await a.until("[...room.world.bubbles.by.values()].some(x => x.text === 'welcome to my room')"), True)
        check("and A's window says it under the guide's name", await a.until(f"(t => t.includes('welcome') && t)({cw}.querySelector('.log').lastChild.textContent)"), "[Guide] says: welcome to my room")
        check("B has it too, its own room's figure's, not B's own line", await b.until("(l => l && l.npc === 'guide' && !l.mine && l.name === 'Guide' && l.text)(v.chatLog.at(-1))"), "welcome to my room")
        if not await b.until("[...room.world.bubbles.by.values()].some(x => x.text === 'welcome to my room')", 5):
            print("      B's world's bubbles:", await b.js("JSON.stringify([...room.world.bubbles.by.values()].map(x => x.text))"), "B at", await b.js("JSON.stringify([room.me, room.where])"))
        check("and B's world draws it over the guide", await b.js("[...room.world.bubbles.by.values()].some(x => x.text === 'welcome to my room')"), True)
        check("six lines at once: the sixth not said (5 every 10 s)", await a.js("(r => r[5])(Array.from({ length: 6 }, (_, i) => v.say('n' + i)))"), False)
        await a.call("Page.reload")
        check("A's page reloads: its chat window has the chat A had, kept by its visit-client, its own lines too, before the window's note; no bubbles, all old",
              await a.until(f"window.v && v.chatLog.length > 5 && (c => !!c && (t => [t.includes('[B] whispers: psst'), t.includes('[Your room] [A]: for everyone here'), "
                            f"t.includes('[B] says: wake up'), t.at(-1).startsWith('Enter to chat'), v.chats.length === 0, v.chatLog.every(l => l.old)])"
                            f"([...c.querySelectorAll('.log .line')].map(d => d.textContent)))({cw})", 30), [True] * 6)
        check("and the guide's line among it, kept by A's visit-client as the guide's", await a.js("v.chatLog.some(l => l.old && l.npc === 'guide' && l.name === 'Guide')"), True)
        check("and the box still says where A's lines went (this tab): Say, as A last chose", await a.js("JSON.parse(sessionStorage.getItem('visit:chat-to')).scope"), "say")
        await a.until("window.room && room.world && room.world.plots.some(p => p.name === 'B')", 30)
        await step_in(a, "B")   # (a world's page comes back at home: A walks in again)

        print("\nscene assets (4.1, #67): a big drawn picture and a big mesh go by name, once; A's page fetches them and draws them")
        await b.js("window.wholes = []; (s => { v._sendScene = (hs, edit, part) => { if (part === 'whole') wholes.push(JSON.stringify(edit)); return s(hs, edit, part); }; })(v._sendScene.bind(v))")
        await b.js("""(() => {
          const T = room.THREE, c = document.createElement('canvas');
          c.width = c.height = 512;
          const g = c.getContext('2d');
          for (let i = 0; i < 6000; i++) { g.fillStyle = 'hsl(' + ((i * 37) % 360) + ',70%,50%)'; g.fillRect((i * 97) % 512, (i * 53) % 512, 9, 9); }
          window.assetTex = new T.CanvasTexture(c);
          window.assetGeo = new T.BufferGeometry().copy(new T.SphereGeometry(0.3, 96, 64));
          const o = new T.Mesh(assetGeo, new T.MeshLambertMaterial({ map: assetTex }));
          o.name = 'assetball'; o.position.set(7, 1, 2); room.scene.add(o);
        })()""")
        ids = await b.until("(t => t && t.id && (g => g && g.id && [t.id, g.id])(v._ownAssets.geos.get(assetGeo)))(v._ownAssets.texs.get(assetTex))", 20)
        check("B's page makes them assets in the background (WebP, gzipped JSON), each named by its SHA-256", [len(x) for x in ids or []], [64, 64])
        ids = ids or ["0" * 64, "1" * 64]
        await b.js("for (const h of v._hosted.values()) if (!h.owner) h.sceneSent = false")   # (A's whole scene again: now by name)
        check("B's next whole scene for A names both, and carries neither", await b.until(
            f"(w => !!w && (j => j.images.some(im => im.asset === '{ids[0]}' && !('url' in im)) && j.geometries.some(g => g.asset === '{ids[1]}' && !g.data))(JSON.parse(w).json))(wholes.at(-1))", 20), True)
        check("A's page fetches them and draws them: the picture (512 px) on the ball, its mesh (6305 vertices)", await a.until(
            "(o => o && o.material.map && o.material.map.image && o.material.map.image.width === 512 && o.geometry.attributes.position && [o.material.map.image.width, o.geometry.attributes.position.count])"
            "(here() && here().scene && here().scene.root.getObjectByName('assetball'))", 30), [512, 6305])
        check("and keeps them: a scene that names them again draws them with nothing fetched", await a.js(f"v._have.has('{ids[0]}') && v._have.has('{ids[1]}')"), True)
        await b.js("room.remove('assetball')")

        print("\ntargets (3.3, #48): the guide described, picked by a click, its card; a whisper to it, its answer to A alone; @")
        await b.js("window.addressed = []; v.on('addressed', e => addressed.push(e)); "
                   "v.figures([{ id: 'guide', name: 'Guide', title: 'the room\\'s guide', about: 'Asks what you came for.', actions: ['spin'] }])")
        check("B describes its guide (v.figures): A, in B's room, has it", await a.until("(f => f && [f.name, f.title, f.actions])(v.figuresHere.find(f => f.id === 'guide'))", 10),
              ["Guide", "the room's guide", ["spin"]])
        await asyncio.sleep(6)   # (A's lines above: at most 5 every 10 s)
        await a.js("(w => { const s = here().scene, T = s.T, g = s.root.getObjectByName('visit:npc:guide').getWorldPosition(new T.Vector3()), c = w._cam.getWorldPosition(new T.Vector3()); "
                   "w._pickHere('click', g.sub(c)); })(room.world)")   # (4.0: A in B's plot, its world picks)
        check("A clicks the guide (B's room picks by a click, its default): A's target, private to A", await a.until("(t => t && [t.kind, t.id, t.name, t.title, t.room === here().host.key])(v.target)"),
              ["figure", "guide", "Guide", "the room's guide", True])
        card = "document.querySelector('.visit-card')"
        check("its card: name, title, line; the guide's action, Say, Whisper", await a.until(f"(c => !c.hidden && [c.querySelector('b').textContent, c.querySelector('.ti').textContent, c.querySelector('.ab').textContent, [...c.querySelectorAll('.bt button')].map(b => b.textContent)])({card})"),
              ["Guide", "the room's guide", "Asks what you came for.", ["spin it", "Say", "Whisper"]])
        await a.shot("a_target_card.png")
        await a.shot_of("ui-card.png", ".visit-card")
        await a.js(f"{card}.querySelector('button[data-c=whisper]').click()")
        check("Whisper: A's chat box opens, telling the guide", await a.until(f"(c => c.classList.contains('typing') && c.querySelector('select').selectedOptions[0].textContent)({cw})"), "Tell Guide:")
        await a.shot_of("ui-chat.png", ".visit-chat")
        await a.call("Input.insertText", text="where am I?")
        await enter(a)
        ad = await b.until("addressed.length === 1 && addressed[0]")
        check("sent: B's page alone hears it (addressed, a whisper, from A's visit), as no line in B's chat", (ad["figure"], ad["whisper"], ad["text"], ad["from"]["key"] == await a.js("v.key"),
              await b.js("v.chatLog.some(l => l.text === 'where am I?')")), ("guide", True, "where am I?", True, False))
        check("A's window: To [Guide]", await a.until(f"(t => t.startsWith('To [Guide]') && t)({cw}.querySelector('.log').lastChild.textContent)"), "To [Guide]: where am I?")
        await b.js("v.sayAs('guide', 'Guide', 'in my room, friend', { to: addressed[0].from.id })")
        check("B's guide answers A alone (sayAs to): A's window says it as the guide's whisper, and no bubble", await a.until(
            f"(t => t.includes('friend') && !v.chats.some(c => c.text.includes('friend')) && t)({cw}.querySelector('.log').lastChild.textContent)"), "[Guide] whispers: in my room, friend")
        await asyncio.sleep(0.5)
        check("and B's chat has none of it", await b.js("v.chatLog.some(l => l.text.includes('friend'))"), False)
        n0 = await b.js("window.spins.length")
        await a.js(f"{card}.querySelector('button[data-c=act]').click()")
        check("the card's 'spin it': B's room runs its spin on the guide, by its own rules, for A", await b.until(f"window.spins.length > {n0} && spins[spins.length - 1].type"), "spin")
        await a.js(f"{card}.querySelector('button[data-c=say]').click()")
        check("Say: the box says it aloud, to the guide", await a.until(f"{cw}.querySelector('select').selectedOptions[0].textContent"), "Say to Guide:")
        await a.call("Input.insertText", text="hello there")
        await enter(a)
        ad = await b.until("addressed.length === 2 && addressed[1]")
        check("said aloud, meant for the guide: B's page hears it so, and B's chat has it", (ad["whisper"], ad["text"], await b.until("v.chatLog.some(l => l.text === 'hello there' && l.target && l.target.id === 'guide')")),
              (False, "hello there", True))
        await enter(a)
        await a.call("Input.insertText", text="/s @Gu")
        check("@ in the box: the guide among whom a line may be for", await a.until(f"[...{cw}.querySelectorAll('.names button')].map(b => b.textContent).includes('Guide')"), True)
        await a.key("Escape", "Escape", 27, True)
        await a.key("Escape", "Escape", 27, False)
        await a.js(f"{card}.querySelector('button[data-c=clear]').click()")
        check("✕: no target, no card", await a.until(f"!v.target && {card}.hidden"), True)
        await b.js("v.figures([{ id: 'guide', name: 'Guide', title: 'the room\\'s guide', actions: ['spin'], talk: 'owner' }])")
        await a.until("(f => f && f.talk === 'owner')(v.figuresHere.find(f => f.id === 'guide'))", 10)
        await a.js("v.setTarget({ kind: 'figure', id: 'guide' })")
        check("a guide that answers its room's owner alone (talk: owner): A's card offers no Say or Whisper, and says why", await a.until(
            f"(c => !c.hidden && [(c.querySelector('.ab.no') || {{}}).textContent, [...c.querySelectorAll('.bt button')].map(b => b.textContent)])({card})"),
              ["only its room's owner may talk to it", ["spin it"]])

        print("\nroom sounds (3.4, #36): B's room's own sounds, a loop by A and a one-shot, heard by A in it")
        await b.js("""window.wav = secs => { const r = 8000, n = Math.round(r * secs), b = new ArrayBuffer(44 + n * 2), d = new DataView(b), s = (o, t) => [...t].forEach((c, i) => d.setUint8(o + i, c.charCodeAt(0)));
          s(0, 'RIFF'); d.setUint32(4, 36 + n * 2, true); s(8, 'WAVEfmt '); d.setUint32(16, 16, true); d.setUint16(20, 1, true); d.setUint16(22, 1, true); d.setUint32(24, r, true); d.setUint32(28, r * 2, true);
          d.setUint16(32, 2, true); d.setUint16(34, 16, true); s(36, 'data'); d.setUint32(40, n * 2, true);
          for (let i = 0; i < n; i++) d.setInt16(44 + i * 2, Math.round(Math.sin(i / r * 2 * Math.PI * 440) * 8000), true); return b; };
          window.soundIds = null; v.sounds({ hum: wav(1), ding: wav(0.3) }).then(t => { window.soundIds = t; }); 1""")
        check("B's room keeps its sounds (v.sounds): a hum and a ding, each by its id", await b.until("window.soundIds && Object.keys(soundIds).sort()"), ["ding", "hum"])
        await b.js("(T => { const p = v.visitors[0].pose, o = new T.Object3D(); o.name = 'hummer'; o.position.set(p.x + 0.5, 1, p.z); "
                   "o.userData.visitSound = { kind: 'hum', volume: 0.5, ref: 2, far: 40 }; room.scene.add(o); })(room.THREE)")
        check("B puts a humming thing by A (userData.visitSound): A, in B's room, plays its loop, its clip fetched from B", await a.until(
            "[...v._loops.values()].some(L => L.kind === 'hum' && !!L.src && L.src.buffer.duration > 0.9)", 20), True)
        n0 = await a.js("v._shotsPlayed")
        check("B plays its ding once at its spinner (v.soundAt): sent", await b.js("v.soundAt(room.spinner, 'ding')"), True)
        check("A hears it once", await a.until(f"v._shotsPlayed > {n0} && v._shotsPlayed - {n0}", 15), 1)
        check("A's panel: 🔈 (the room's sounds on)", await a.js("(b => b && b.textContent)(document.querySelector('.visit-panel button[data-v=sounds]'))"), "🔈")
        await a.js("document.querySelector('.visit-panel button[data-v=sounds]').click()")
        check("🔈 clicked: off in this browser (🔇), and the loop stopped", await a.until("!v.roomSounds && v._loops.size === 0 && document.querySelector('.visit-panel button[data-v=sounds]').textContent === '🔇'"), True)
        await a.js("document.querySelector('.visit-panel button[data-v=sounds]').click()")
        check("and on again: the loop back", await a.until("v.roomSounds && [...v._loops.values()].some(L => L.kind === 'hum' && !!L.src)", 15), True)
        check("B, at home in its own room, hears its hum too (Visit plays a room's sounds for its own user)", await b.until("[...v._loops.values()].some(L => L.kind === 'hum' && !!L.src)", 15), True)
        await b.js("v.ownSounds(false)")
        check("v.ownSounds(false): B's page plays its own room's itself; Visit's loop there stops, A's goes on", (await b.until("v._loops.size === 0"),
              await a.js("[...v._loops.values()].some(L => L.kind === 'hum' && !!L.src)")), (True, True))
        await b.js("v.ownSounds(true)")
        await b.js("room.scene.remove(room.scene.getObjectByName('hummer'))")
        check("B takes the humming thing away: A's loop stops", await a.until("![...v._loops.values()].some(L => L.kind === 'hum')", 15), True)
        await a.js("v.setTarget({ kind: 'figure', id: 'guide' })")
        await a.js("room.world.leave(); Object.assign(room.me, { x: 0, z: 0 })")   # A home again; B's room still in its world (4.0: what a link was)
        await a.until("!room.where")
        await b.until("v.visitors.length === 0")
        DIAG = "JSON.stringify({ me: room.me, where: room.where && room.where.name, in: room.world.in, roomKey: v._roomKey() && v._roomKey().slice(0, 8), target: v.target && v.target.room && v.target.room.slice(0, 8), voices: v.voices, near: v._nearHome, watches: [...v._watches.values()].map(w => [w.host.name, w.inside]) })"
        if not await a.until("!v.target", 5):
            print("      A:", await a.js(DIAG))
            print("      B:", await b.js(DIAG))
        check("A's target set again, then A leaves B's room: no target", await a.js("!v.target"), True)
        await a.js("v.setMic(false)")
        await b.js("v.setMic(false)")
        check("each back in its own room, microphones off: nobody near, nothing captured", (await a.until("v.voices.length === 0 && !v._mic"), await b.until("v.voices.length === 0 && !v._mic")), (True, True))

        print("\na phone: a call across rooms")
        await a.until("room.world.plots.some(p => p.name === 'B') && [...v._watches.values()].some(w => w.host.name === 'B')")   # (A, at home, B's room in its world)
        kb = await a.js("v.offices.find(o => o.name === 'B').key")
        check("B's plot in A's world (watched): A sees B's room has a phone (and B A's)", (await a.until("v.phones.length === 1 && v.phones[0].name"), await b.until("v.phones.length === 1 && v.phones[0].name")), ("B", "A"))
        await b.js("v.phone(true, { at: { x: room.me.x + 1, y: 1, z: room.me.z } })")   # B's phone, a step from B: Visit's ready-made one
        check("B's room has Visit's ready-made phone, where B said", await b.until("(p => !!p && !!p.g.parent && p.g.name === 'visit:phone-model')(v._phoneObj)"), True)
        check("A's panel offers B's phone to call", await a.until("(b => b && b.textContent)(document.querySelector('.visit-panel button[data-v=call]'))"), "B")
        await a.js("document.querySelector('.visit-panel button[data-v=call]').click()")
        check("A calls B: B's phone rings (its panel too)", await b.until("v.calls.length === 1 && v.calls[0].state === 'ringing' && document.querySelector('.visit-panel').textContent.includes('A is calling')"), True)
        check("B's phone shakes and says RING RING while it rings", await b.until("(p => Math.abs(p.handset.rotation.z) > 0.01 || p.ring.visible)(v._phoneObj)"), True)
        check("and A sees it in B's room (it is in B's scene: its plot in A's world)", await a.until("(w => !!(w && w.scene && w.scene.root.getObjectByName('visit:phone-model')))([...v._watches.values()].find(w => w.host.name === 'B'))"), True)
        await b.js("document.querySelector('.visit-panel button[data-v=pick_up]').click()")
        check("B picks up: on, both ways, each in its own room", (await a.until("v.calls.length === 1 && v.calls[0].state === 'on'"), await b.until("v.calls.length === 1 && v.calls[0].state === 'on'")), (True, True))
        check("A hears B, as a telephone (not by where anyone stands), and B A", (await a.until("(s => !!s && s.frames > 50 && s.level > 0.005)(v._voices.get('call:' + v.calls[0].key))"),
              await b.until("(s => !!s && s.frames > 50 && s.level > 0.005)(v._voices.get('call:' + v.calls[0].key))")), (True, True))
        check("the microphone on for the call only: nobody near either", (await a.js("!v.mic && !!v._mic && v.voices.length === 0"), await b.js("!v.mic && !!v._mic")), (True, True))
        ka = await b.js("v.calls[0].key")
        await b.js("v.phone(true, { at: { x: room.me.x + 1, y: 1, z: room.me.z } })")   # B's phone, a step from B
        check("B's handset off its cradle, at B's ear", await b.until("!v._phoneObj.handset.visible && !v._phoneObj.ring.visible"), True)
        await b.js("document.querySelector('.visit-panel button[data-v=speaker]').click()")
        check("B puts it on speaker: its lamp lit, its handset back", await b.until("v._phoneObj.lamp.visible && v._phoneObj.handset.visible"), True)
        check("B puts it on speaker: A's voice from B's phone, heard where B stands (its voice for the call only: its microphone is off)", await b.until(
            "v.calls[0].speaker && (s => !!s && s.near && s.frames > 10)(v._voices.get(" + json.dumps("call:" + ka) + ")) && v._voiceTo === 2"), True)
        heard = "(s => s ? s.frames : 0)(v._voices.get('call:' + v.calls[0].key))"
        async def growing(tab):
            f0 = await tab.js(heard)
            await asyncio.sleep(1.0)
            return await tab.js(heard) - f0 > 20
        check("and B, near it, heard by A", await growing(a), True)
        await b.js("v.phone(true, { at: { x: room.me.x + 100, y: 1, z: room.me.z } })")
        await b.until("!v._voices.get(" + json.dumps("call:" + ka) + ").near")
        await asyncio.sleep(0.5)
        check("B far from its phone: B not hearing the call, and (B's visit-client deciding) not heard in it", await growing(a), False)
        await b.js("document.querySelector('.visit-panel button[data-v=speaker]').click()")
        check("back to the handset: only B, to its ear, heard by A again", (await b.until("!v.calls[0].speaker && v._voiceTo === 2"), await growing(a)), (True, True))
        await a.js("v.hangUp(%s)" % json.dumps(kb))
        check("A hangs up: over for both, microphones off again", (await a.until("v.calls.length === 0 && !v._mic"), await b.until("v.calls.length === 0 && !v._mic")), (True, True))
        await a.js("v.unpick(%s)" % json.dumps(kb))
        await b.until("v.watchers.length === 0")

        print("\ntalking (4.0, #58): the chat window's 🎧 with its box on a whisper rings the other, as a phone; answered, each hears the other")
        cw = "document.querySelector('.visit-chat')"
        await a.js("ui.chat.aim((o => ({ scope: 'whisper', id: o.id, key: o.key, name: o.name }))(v.offices.find(o => o.name === 'B')))")
        check("A's box on a whisper to B: its 🎧 talks with B", await a.until(f"(h => !h.hidden && h.title.startsWith('talk with B'))({cw}.querySelector('button.headset'))"), True)
        await a.js(f"{cw}.querySelector('button.headset').click()")
        check("A clicks 🎧: calling B, its window says so", await a.until(f"v.calls.length === 1 && v.calls[0].voice && v.calls[0].state === 'calling' && {cw}.querySelector('.voice').textContent.includes('Calling B')"), True)
        check("B's chat window rings: a line above its box asking, pulsing, shown though the window is idle; its 🎧 shaking", await b.until(
            f"(c => c.querySelector('.ring').textContent.includes('A wants to talk') && getComputedStyle(c.querySelector('.ring .ask')).animationName === 'visit-ring'"
            f" && getComputedStyle(c.querySelector('.ring')).opacity === '1' && c.querySelector('button.headset').classList.contains('ring'))({cw})"), True)
        check("and B's panel: A wants to talk, Answer", await b.until("document.querySelector('.visit-panel').textContent.includes('A wants to talk')"), True)
        check("its room's phone (none here) not rung by it: talk is not the phone's", await b.js("v.calls[0].voice === true && v.calls[0].state === 'ringing'"), True)
        await b.shot("b_talk_ringing.png")
        await b.shot_of("ui-ring.png", ".visit-chat")
        await b.js(f"{cw}.querySelector('.ring button[data-v=answer]').click()")
        check("B answers from the ring: on, both ways; the ring gone", (await a.until("v.calls.length === 1 && v.calls[0].state === 'on'"),
              await b.until(f"v.calls.length === 1 && v.calls[0].state === 'on' && {cw}.querySelector('.ring').children.length === 0")), (True, True))
        check("A hears B, and B A (the fake microphones' tones), each in their own room", (await a.until("(s => !!s && s.frames > 50 && s.level > 0.005)(v._voices.get('call:' + v.calls[0].key))"),
              await b.until("(s => !!s && s.frames > 50 && s.level > 0.005)(v._voices.get('call:' + v.calls[0].key))")), (True, True))
        check("A's window: Talking with B, and Hang up", await a.until(f"{cw}.querySelector('.voice').textContent.includes('Talking with B') && !!{cw}.querySelector('.voice button[data-v=hangup]')"), True)
        await a.shot("a_talk_on.png")
        await b.js(f"{cw}.querySelector('.voice button[data-v=hangup]').click()")
        check("B hangs up from its window: over for both, microphones off again", (await a.until("v.calls.length === 0 && !v._mic"), await b.until("v.calls.length === 0 && !v._mic")), (True, True))
        await a.js(f"{cw}.querySelector('button.headset').click()")
        await b.until("v.calls.length === 1 && v.calls[0].state === 'ringing'")
        await b.js(f"{cw}.querySelector('.ring button[data-v=decline]').click()")
        check("A rings again, B: Not now. A's window says so", await a.until(f"v.calls.length === 0 && {cw}.querySelector('.log').textContent.includes('B: not now, they said')"), True)

        print("\na huddle (4.0, #58): 🎧 with the box on a world starts one; it rings for the world's members; joined from the ring")
        await b.js("v.joinGroup('Yard')")
        await b.until("v.groups.some(g => g.name === 'Yard')")
        await a.js("ui.chat.aim({ scope: 'group', key: 'yard', name: 'Yard' })")
        check("A's box on Yard: its 🎧 starts a huddle there", await a.until(f"{cw}.querySelector('button.headset').title.startsWith('start a huddle in Yard')"), True)
        await a.js(f"{cw}.querySelector('button.headset').click()")
        check("A clicks 🎧: in its huddle (its room's voice), its window says so", await a.until(f"!!v.inHuddle && v.inHuddle.mine && {cw}.querySelector('.voice').textContent.includes('Huddle in Yard')"), True)
        check("B's window rings: A started a huddle in Yard, Join", await b.until(f"{cw}.querySelector('.ring').textContent.includes('A started a huddle in Yard')"), True)
        await b.shot("b_huddle_ringing.png")
        await b.js(f"{cw}.querySelector('.ring button[data-v=join]').click()")
        check("B joins from the ring: in A's huddle, with A; the ring gone", await b.until(
            f"!!v.inHuddle && v.inHuddle.group === 'Yard' && v.roomVoice.members.length === 2 && {cw}.querySelector('.ring').children.length === 0"), True)
        check("A's huddle has B", await a.until("v.roomVoice && v.roomVoice.members.length === 2"), True)
        ka, kb = await a.js("v.key"), await b.js("v.key")
        check("each hears the other in it, microphones off (a huddle runs them, as a call does)", (await a.js("v.mic"), await b.js("v.mic"),
              await a.until("(s => !!s && s.frames > 50 && s.level > 0.005)(v._voices.get(%s))" % json.dumps("room:" + kb)),
              await b.until("(s => !!s && s.frames > 50 && s.level > 0.005)(v._voices.get(%s))" % json.dumps("room:" + ka))), (False, False, True, True))
        await a.shot("a_huddle_on.png")

        print("\n4.3.2: in a huddle and near in person too: heard all along, microphones off or on")
        SLOT = "[...v._rtc.values()].flatMap(c => [...c.slots.values()]).find(sl => !!sl.out && !!sl.voice && !!sl.label && sl.label.key === %s)"
        def heard_by(k, route=None):   # a slot of theirs played here (by that way)
            return "(sl => !!sl%s)(%s)" % ("" if route is None else " && sl.label.route === %d" % route, SLOT % json.dumps(k))
        async def growing_of(tab, k):   # and their voice's frames still coming through it
            q = "(sl => sl ? sl.voice.frames : 0)(%s)" % (SLOT % json.dumps(k))
            f0 = await tab.js(q)
            await asyncio.sleep(1.0)
            return await tab.js(q) - f0 > 20
        await step_in(b, "A")
        check("B steps into A's room, near A, both still in the huddle", (await a.until("v.visitors.length === 1 && v.voices.some(x => x.name === 'B')", 15),
              await b.until("v.voices.some(x => x.name === 'A') && !!v.inHuddle", 15)), (True, True))
        check("microphones off, A still hears B, and B A, by the huddle (before 4.3.2, A's visit-client held B's huddle voice back for B in person, which B's page sends only with its microphone on)",
              (await a.until(heard_by(kb, 2), 10), await growing_of(a, kb), await b.until(heard_by(ka, 2), 10), await growing_of(b, ka)), (True, True, True, True))
        check("B's huddle voice goes by the voice visit that joined it, not with B in person to A's room (where whoever is near B would hear it, its microphone off)",
              await b.js("[...v._rtc.values()].filter(c => c.to != null).every(c => !(c.want && c.want.room && c.want.voice)) && [...v._rtc.values()].some(c => c.to != null && c.want && c.want.voice)"), True)
        check("B, microphone off: its voice to the huddle only, not in person", await b.js("v._voiceTo"), 4)
        await b.js("v.setMic(true)")
        check("4.3.2: B's microphone on, its huddle running it already: in person too, at once (before, only once something else made its page look again)",
              await b.until("(v._voiceTo & 1) === 1", 2), True)
        check("B's microphone on: A hears B in person, from where B stands, as loud as the huddle (no fading: B is in it)", await a.until(
            heard_by(kb, 0) + " && (s => !!s && s.near && s.panner.rolloffFactor === 0)(v._voices.get(%s))" % json.dumps(kb), 10), True)
        await asyncio.sleep(1.0)
        check("and B's huddle voice no longer comes to A as well (one way a person, by what A's visit-client passes on)", await a.js(
            "![...v._rtc.values()].flatMap(c => [...c.slots.values()]).some(sl => !!sl.label && sl.label.key === %s && sl.label.route === 2 && performance.now() - (sl.liveAt || 0) < 600)" % json.dumps(kb)), True)
        await b.js("v.setMic(false)")
        check("4.3.2: off again: B's voice no longer in person, at once (before, it went on so while the huddle ran its microphone)",
              await b.until("v._voiceTo === 4", 2), True)
        await asyncio.sleep(1.0)
        check("and A hears B still, by the huddle again", (await a.until(heard_by(kb, 2), 10), await growing_of(a, kb)), (True, True))
        await step_out(b, "A")
        await a.until("v.visitors.length === 0")
        await a.js(f"{cw}.querySelector('.voice button[data-v=leave]').click()")
        check("A ends it from its window: both out, the huddle gone, microphones off", (await a.until("!v.roomVoice && !v.inHuddle && v.huddles.length === 0 && !v._mic"),
              await b.until(f"!v.roomVoice && !v.inHuddle && v.huddles.length === 0 && !v._mic && {cw}.querySelector('.log').textContent.includes('The huddle in Yard is over')")), (True, True))
        await b.js("v.leaveGroup('Yard')")   # (as it was: B joins Yard again below)
        await b.until("!v.groups.some(g => g.name === 'Yard')")
        await a.js("ui.chat.aim({ scope: 'say' })")
        await b.js("ui.chat.aim({ scope: 'say' })")
        await a.call("Input.dispatchKeyEvent", type="keyDown", code="Escape", key="Escape", windowsVirtualKeyCode=27)
        await a.call("Input.dispatchKeyEvent", type="keyUp", code="Escape", key="Escape", windowsVirtualKeyCode=27)
        await b.call("Input.dispatchKeyEvent", type="keyDown", code="Escape", key="Escape", windowsVirtualKeyCode=27)
        await b.call("Input.dispatchKeyEvent", type="keyUp", code="Escape", key="Escape", windowsVirtualKeyCode=27)

        engine_port = free_ports(1)[0] if os.environ.get("ENGINE", "1") != "0" else 0
        print("\nthe room and the person apart (engine/SPEC.md 3, 4, 7): C's room %s, C's person on a page of its own" % ("in the engine" if engine_port else "on a page of its own"))
        engine_flags = ("--engine-room-url", "http://127.0.0.1:%d/room.html?role=host&three=160&world=1" % WEB_PORT, "--engine-port", str(engine_port),
                        "--engine-debug") if engine_port else ()
        pc, tc, tuc = await start_client("C", PORT_C, url, user_token=True, extra_args=engine_flags)
        open(os.path.join(OUT, "host-go"), "w").close()   # the engine may have the room's page now (its next try: within its backoff, 30 s at most)
        room_c = "http://127.0.0.1:%d/room.html?role=%%s&three=160&world=1#port=%d&token=%%s" % (WEB_PORT, PORT_C)
        hc = Engine(engine_port) if engine_port else await Tab.spawn(CDP_B, "C-host", room_c % ("host", tc))
        await b.goto(room_c % ("user", tuc))
        await a.goto(world)
        check("the room's page hosts as the host, nobody in it yet; the person's page connects as the user",
              (await hc.until("window.v && v.role === 'host' && v.state === 'online' && v.role", 90), await b.until("window.v && v.role", 30)), ("host", "user"))
        reloads0 = None
        if engine_port:
            for _ in range(50):   # (its own look at the page comes once a second)
                h = hc.health()
                if h.get("room") == "hosting":
                    break
                await asyncio.sleep(0.2)
            reloads0 = h.get("reloads")   # (its loads before the room's visit-client was up: tried again, with backoff)
            check("the engine says so: /health hosting, ok", (h.get("room"), h.get("ok")), ("hosting", True))
            check("4.0 (#42 C): there, the room's own render does nothing (Visit reads its scene itself)", await hc.until("!!(v._hooks && v._hooks.scene.renderer && v._hooks.scene.renderer.visitQuiet)", 10), True)
        check("C is in the lobby, ready: its room's page hosts it", await a.until("v.offices.some(o => o.name === 'C' && o.ready)", 30), True)
        check("the owner's visit: the room has its person as a visitor of its own (who.owner), spawned by the desk", await hc.until(
            "v.owner && v.visitors.length === 1 && v.visitors[0].owner && v.visitors[0].visitor.owner && window.spawns && spawns.some(s => s.owner) && v.visitors[0].pose && Math.round(v.visitors[0].pose.z * 10) / 10", 20), 6.5)
        check("and the person has home: their room, drawn by their world as its own plot, where its scene has it", await b.until(
            "v.home && !v.home.paused && (p => p && p.home && p.rect.map(Math.round))(room.world.plots.find(p => p.home))", 30), [0, 0, 10, 8])
        check("the room's scene reached the person's page, with the desk and the spinner; its watchers do not count its owner",
              (await b.until("(s => !!s && !!s.root.getObjectByName('spinner') && !!s.root.getObjectByName('desk' in s.root ? 'desk' : 'spinner'))(v._homeScene())", 20), await hc.js("v.watchers.length")), (True, 0))
        check("the person stands where their room spawned them, walked by it (its move, who.owner)", await b.until(
            "room.world.inside && Math.abs(room.me.z - 6.5) < 0.1 && Math.abs(room.me.x - 5) < 0.1", 20), True)
        await b.shot("c_user_home.png")
        if engine_port:
            print("\n#37 (4.1, #67): a heavy room on the engine (three dozen drawn panels) reaches its person by name: a small scene, the panels fetched")
            inline = await hc.js("""(() => {
              const T = room.THREE; window.panels = [];
              for (let k = 0; k < 36; k++) {
                const c = document.createElement('canvas'); c.width = c.height = 512;
                const g = c.getContext('2d'), im = g.createImageData(512, 512);   // (a busy panel: 4-pixel blocks of colour, as a screen or a map has)
                let seed = k * 7919 + 1;
                const rnd = () => (seed = (Math.imul(seed, 1103515245) + 12345) >>> 0) / 4294967296;
                for (let by = 0; by < 128; by++) for (let bx = 0; bx < 128; bx++) {
                  const col = [rnd() * 255, rnd() * 255, rnd() * 255];
                  for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) { const i = ((by * 4 + y) * 512 + bx * 4 + x) * 4; im.data.set([...col, 255], i); }
                }
                g.putImageData(im, 0, 0);
                const m = new T.Mesh(new T.PlaneGeometry(0.5, 0.5), new T.MeshBasicMaterial({ map: new T.CanvasTexture(c) }));
                m.name = 'panel' + k; m.position.set(1 + (k % 6) * 1.4, 2.2, 0.5 + Math.floor(k / 6) * 1.2);
                room.scene.add(m); panels.push(m);
              }
              window.wholes = [];
              (s => { v._sendScene = (hs, edit, part) => { if (part === 'whole') wholes.push(JSON.stringify(edit).length); return s(hs, edit, part); }; })(v._sendScene.bind(v));
              return panels.reduce((n, m) => n + m.material.map.image.toDataURL().length, 0);
            })()""")
            ready = await hc.until("panels.every(m => (r => !!r && !!r.id)(v._ownAssets.texs.get(m.material.map)))", 60)
            check("the room's page makes each panel's picture an asset, in the background", ready, True)
            await hc.js("for (const h of v._hosted.values()) h.sceneSent = false")
            whole = await hc.until("wholes.length && wholes.at(-1)", 20)
            print("      (the panels inline: %s bytes; the whole scene by name: %s bytes)" % (inline, whole))
            check("its whole scene for its person: small (under 1 MiB), the panels by name (inline they are past it)",
                  (isinstance(whole, int) and whole < 1 << 20, isinstance(inline, int) and inline > 1 << 20), (True, True))
            check("and the person's page fetches and draws every panel's picture", await b.until(
                "(s => !!s && [...Array(36).keys()].every(k => (o => !!o && !!o.material.map.image && o.material.map.image.width === 512)(s.root.getObjectByName('panel' + k))))(v._homeScene())", 60), True)
            await hc.js("panels.forEach(m => room.scene.remove(m)); true")
        z0 = await b.js("room.me.z")
        await b.key("KeyW", "w", 87, True)
        await asyncio.sleep(1.0)
        await b.key("KeyW", "w", 87, False)
        await asyncio.sleep(0.5)
        z1, hz = await b.js("room.me.z"), await hc.js("v.visitors[0].pose.z")
        check("W: the room walks its owner forward, and the person's page follows", (z0 - z1 > 1.0, abs(z1 - hz) < 0.2, await hc.js("window.movedWith && movedWith.owner")), (True, True, True))
        await b.key("KeyW", "w", 87, True)
        await asyncio.sleep(2.0)
        await b.key("KeyW", "w", 87, False)
        await asyncio.sleep(0.5)
        check("into the desk ahead: the room stops them at it (0.3 short), and the page agrees", (round(await hc.js("v.visitors[0].pose.z"), 1), abs(await b.js("room.me.z") - await hc.js("v.visitors[0].pose.z")) < 0.2), (4.5, True))
        # The owner's wheel (SPEC 8.3): the spinner held east of them, clear of the desk, a type for the owner alone.
        await hc.js("room.frozen = true; room.spinner.position.set(7, 1.5, 4.5); v.interactions({ spin: { cosmetic: true, label: 'spin it', who: 'owner', wheel: true, "
                    "run: e => { window.spins.push({ type: e.type, owner: !!e.visitor.owner, wheel: e.wheel }); } } })")
        at = await b.until("(o => !!o && Math.abs(o.position.x - 7) < 0.01 && [o.position.x, o.position.y, o.position.z])(v._homeScene().root.getObjectByName('spinner'))", 10)
        n0 = await hc.js("window.spins.length")
        await b.js("(t => { room.world.point(t, { wheel: 1 }); room.world.point(t, { wheel: 2 }); })({ x: %f, y: %f, z: %f })" % tuple(at))
        check("the owner turns the wheel at it (world.point with wheel): the notches in one point, run for the owner, who alone may",
              await hc.until("window.spins.length > %d && spins[spins.length - 1]" % n0, 10), {"type": "spin", "owner": True, "wheel": 3})
        await hc.js("room.frozen = false; v.interactions({ spin: { cosmetic: true, label: 'spin it', run: e => { e.object.rotation.y += 1; "
                    "window.spins.push({ who: e.visitor.name, type: e.type, owner: !!e.visitor.owner }); } } })")
        await b.js("v.joinGroup('Yard')")   # (A's world: A and C each other's guests)
        await b.until("v.groups.some(g => g.name === 'Yard')")
        await b.js("v.pick(v.offices.find(o => o.name === 'A').key, 'A')")
        check("in a world with A, A picked: A's plot in the person's world, beside home", (await b.until("(p => p && p.name)(room.world.plots.find(p => !p.home))", 20)), "A")
        key_a = await b.js("v.offices.find(o => o.name === 'A').key")
        await b.js("v.place(%s, { x: -5.5, z: 4, turn: 0 })" % json.dumps(key_a))   # A's room flush against C's west wall, doorway to doorway
        check("placed flush west of home", await b.until("(p => !!p && JSON.stringify(p.rect.map(x => Math.round(x) || 0)) === '[-10,0,0,8]')(room.world.plots.find(p => !p.home))", 10), True)

        async def walk_until(yaw, expr, secs, on=0.3):
            """Facing `yaw`, W held until `expr` holds (at most `secs`), then `on` seconds more."""
            await b.js("room.me.yaw = %f" % yaw)
            await asyncio.sleep(0.1)
            await b.key("KeyW", "w", 87, True)
            for _ in range(int(secs * 20)):
                if await b.js(expr):
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(on)
            await b.key("KeyW", "w", 87, False)
            await asyncio.sleep(0.4)
        await walk_until(math.pi / 2, "room.me.x < 2", 4)   # west, clear of the desk
        await walk_until(0, "Math.abs(room.me.z - 4) < 0.15", 3, on=0)   # north, to the doorway's row (and not past it)
        await b.js("room.me.yaw = Math.PI / 2")   # west: out through C's doorway, in through A's
        await b.key("KeyW", "w", 87, True)
        for _ in range(100):
            if await b.js("!!room.where"):
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.4)
        await b.key("KeyW", "w", 87, False)
        check("the person walks out of their room onto A's plot: in A's room, A has them, and their own room's page no longer does",
              (await b.until("room.where && room.where.name", 10), await a.until("v.visitors.length === 1 && v.visitors[0].visitor.name", 10), await hc.until("v.visitors.length === 0", 10)), ("A", "C", True))
        await b.js("room.me.yaw = -Math.PI / 2")   # back east, home
        await b.key("KeyW", "w", 87, True)
        for _ in range(100):
            if await b.js("room.where === null"):
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        await b.key("KeyW", "w", 87, False)
        check("and back home: their room spawns them where they stepped in (at), and A's visitor is gone",
              (await b.until("room.where === null && room.world.inside", 10), await hc.until("v.visitors.length === 1 && v.visitors[0].owner && spawns[spawns.length - 1].owner && !!spawns[spawns.length - 1].at && spawns[spawns.length - 1].p.x < 1.5", 10), await a.until("v.visitors.length === 0", 10)), (True, True, True))
        await b.shot("c_user_back.png")
        print("\n4.3.2: A steps into C's room: C's person's page has A in it too, and hears A")
        await a.js("v.setMic(true)")
        await step_in(a, "C")
        check("A in C's room: its room's page has A, and so has its person's (before 4.3.2 \"enter\" went to the room's page alone: the person's had A outside)",
              (await hc.until("v.visitors.some(x => x.visitor.name === 'A')", 15), await b.until("v.visitors.some(x => x.visitor.name === 'A')", 15)), (True, True))
        check("and C's person hears A, from where the room has A", await b.until(
            "(s => !!s && s.frames > 50 && s.level > 0.01 && s.near)(v._voices.get(%s))" % json.dumps(key_a), 20), True)
        await step_out(a, "C")
        await a.js("v.setMic(false)")
        check("A out again: in neither of C's pages", (await hc.until("!v.visitors.some(x => x.visitor.name === 'A')", 15),
              await b.until("!v.visitors.some(x => x.visitor.name === 'A')", 15)), (True, True))
        await b.goto("about:blank")
        check("the person's page gone: the room's page has no owner, and no visitor", await hc.until("v.owner === null && v.visitors.length === 0", 10), True)
        await b.goto(room_c % ("user", tuc))
        check("back: home again, and the room has its owner again", (await b.until("window.v && v.home && room.world.inside", 30), await hc.until("v.owner && v.visitors.length === 1 && v.visitors[0].owner", 10)), (True, True))
        # 5.1 (8.15): the person teleports into A's room and home again, onto its own room's pad: its home a plot of its
        # world, its room the engine's page, which answers for its owner, spawns it on the pad and plays the beam (each
        # room's "teleported" kept, as the beams themselves last 1.6 s).
        await hc.js("window.tpd = []; v.on('teleported', e => tpd.push(e.way + ':' + (e.visitor.owner ? 'owner' : e.visitor.name)))")
        await a.js("window.tpdA = []; v.on('teleported', e => tpdA.push(e.way + ':' + e.visitor.name))")
        found = await b.js("v.findTeleports().then(l => l.map(t => [t.name, t.home]))")
        check("C's person asks where it may teleport: its own room (the engine's page answering for its owner) and A's", (["C", True] in found, ["A", False] in found), (True, True))
        shown = await b.until("[...v._watches.values()].some(w => w.host.name === 'A' && w.scene)", 20)
        check("5.1: C's person's page, back, draws A's room again (its scene came before the page made its world: asked for again then)", bool(shown), True)
        if not shown:
            print("C's person's page has no scene of A's room:", await b.js("JSON.stringify([...v._watches.values()].map(w => [w.host.name, !!w.scene, w.inside, w.paused, w.loading, w.failed]))"))
        res = await b.js("v.teleport(%s)" % json.dumps(key_a))
        if not res["ok"]:
            print("C's teleport into A:", res, await b.js("JSON.stringify([...v._watches.values()].map(w => [w.host.name, !!w.scene, w.inside, w.paused, w.loading, w.failed]))"))
        check("C's person teleports into A's room: there", (res["ok"], await b.until("room.world.inside && room.where && room.where.name", 10)), (True, "A"))
        check("A's room has C on its pad", await a.until("(s => s && Math.round(s.p.x * 10) / 10 === 8.6 && Math.round(s.p.z * 10) / 10 === 1.4 && s.who)(spawns[spawns.length - 1])", 10), "C")
        await asyncio.sleep(1.8)
        res = await b.js("v.teleport(v.key)")
        check("and home by teleport: there, in its own room (a user page's home: no `where`)", (res["ok"], await b.js("room.where"), await b.js("room.world.inside")), (True, None, True))
        check("its room (the engine's page) spawned its owner on its pad, by teleport", await hc.until(
            "(s => !!s && s.owner && Math.round(s.p.x * 10) / 10 === 8.6 && Math.round(s.p.z * 10) / 10 === 1.4)(spawns[spawns.length - 1])", 10), True)
        check("and played its owner going (to A) and coming home", await hc.js("tpd"), ["out:owner", "in:owner"])
        check("A's room played C coming, and going", await a.until("tpdA.length >= 2 && tpdA.join()", 5), "in:C,out:C")
        await b.shot("c_home_by_teleport.png")
        await asyncio.sleep(2.5)
        if engine_port:
            h = hc.health()
            check("the engine, all along: hosting, no reload needed meanwhile", (h.get("room"), h.get("reloads") == reloads0), ("hosting", True))
        check("no errors in the room's page nor the person's", (hc.errors, b.errors), ([], []))
        await hc.close_window()
        pc.terminate()
        await pc.wait()
        await a.goto(room % (WEB_PORT, "", PORT_A, ta))
        await b.goto(room % (WEB_PORT, "?three=160", PORT_B, tb))
        check("A and B as before, for what follows", (bool(await a.until("window.v && v.state === 'online'", 30)), bool(await b.until("window.v && v.state === 'online'", 30))), (True, True))

        print("\nwhat a host sends is only data")
        r = await b.js("""(() => { const c = Visit._test.cleanScene, budget = { vertices: 0, objects: 0 };
          const j = c({ materials: [{ uuid: "m", type: "RawShaderMaterial", vertexShader: "x" }],
            geometries: [{ uuid: "g", type: "SphereGeometry", parameters: { widthSegments: 1e9, heightSegments: 1e9 } }],
            images: [{ uuid: "i", url: "https://example.com/x.png" }, { uuid: "j", url: [1, { data: [1], width: 9999, height: 1, type: "Uint8Array" }] }],
            object: { type: "Scene", children: [{ type: "Mesh" }, 5] } }, budget);
          return [j.materials[0].type, j.geometries[0].parameters.widthSegments, j.images[0].url.slice(0, 15), j.images[1].url.map(u => typeof u === "string" ? u.slice(0, 15) : u), j.object.children.length, budget.objects]; })()""")
        check("cleanScene: a shader becomes grey, segments are capped, only data-URL images of a sane size, junk objects dropped", r,
              ["MeshLambertMaterial", 256, "data:image/png;", ["data:image/png;", "data:image/png;"], 1, 2])
        big = "data:image/png;base64," + base64.b64encode(bytes.fromhex("89504e470d0a1a0a0000000d49484452") + (20000).to_bytes(4, "big") + (20000).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00").decode()
        check("an image's size is read from its header, before anything decodes it", await b.js("Visit._test.imageDims(%s)" % json.dumps(big)), [20000, 20000])
        check("a room whose three.js visit.js does not know cannot host", await b.js(
            "(() => { try { v.host({ spawn() {}, move() {}, scene: { root: room.scene, three: { REVISION: '99' } } }); return 'hosted'; } catch (e) { return e.message.slice(0, 30); } })()"),
            "visit.host: three.js r99 is no")
        check("no errors in either page", (a.errors, b.errors), ([], []))
        for p in (pa, pb):
            p.terminate()
            await p.wait()
    print("\n%d failure(s)" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
