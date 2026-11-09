"""The board's sign-in (basedverse's auth/INTEGRATING.md): auth-client, the `auth` service in compose.yml, on the
compose network only (no port published), where the board asks it by name (AUTH_HOST), with the service token
bin/start writes each run (../.auth/token). Every route but the sign-in page's asks it
first (server.py's gate): the owner's browser by its session cookie, an agent's forge.py by its API key
(REPO_BOARD_KEY, sent as `Authorization: Bearer`). A valid answer is kept 5 s at most; never a refusal.
"""
import json
import os
import re
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOST = os.environ.get("AUTH_HOST", "127.0.0.1")   # compose.yml: `auth`, the service's name
PORT = int(os.environ.get("AUTH_PORT", "7403"))
# bin/start writes it each run (in the container, compose says where it is mounted; run by hand, ../.auth/).
TOKEN_FILE = os.environ.get("AUTH_TOKEN_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".auth", "token")
COOKIE = "session"   # over HTTP on this machine: no __Host- prefix, no Secure
COOKIE_REMOTE = "__Host-session"   # over HTTPS, from another device (the LAN's proxy): Secure, this host only
KEEP = 5             # seconds a valid answer is kept
# The routes anyone may reach, signed in or not: the sign-in page and its form. Nothing else, by default.
PUBLIC = {("GET", "/login"), ("POST", "/login")}
# The board's own pages, as compose.yml publishes it on this machine: a write signed in with the cookie comes from one
# of them. Through an HTTPS proxy (the board's other listener), its page is https:// and the name it was asked by.
ORIGINS = {"http://localhost:3001", "http://127.0.0.1:3001"}


class Unreachable(Exception):
    """auth-client did not answer (or not as it should): the request is refused, never let through."""


_token = {"value": None}
_seen, _lock = {}, threading.Lock()


def _service_token():
    if _token["value"] is None:
        try:
            with open(TOKEN_FILE) as f:
                t = f.read().strip()
        except OSError as e:
            raise Unreachable("no service token (%s): bin/start writes it" % e)
        if len(t) < 32:
            raise Unreachable("the service token in %s is too short" % TOKEN_FILE)
        _token["value"] = t
    return _token["value"]


def call(path, body):
    """(status, JSON) from auth-client."""
    req = Request("http://%s:%d%s" % (HOST, PORT, path), json.dumps(body).encode(),
                  {"Authorization": "Bearer " + _service_token(), "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except HTTPError as e:
        try:
            answer = json.loads(e.read() or b"{}")
        except ValueError:
            answer = {}
        if answer.get("error") == "service_token":
            _token["value"] = None   # read it again next time: bin/start may have made a new one
            raise Unreachable("auth-client refused the board's service token")
        return e.code, answer
    except (URLError, OSError, ValueError) as e:
        raise Unreachable("auth-client did not answer: %s" % e)


def who(session=None, key=None):
    """Whose a request is ({"user": ...} for a session, {"key": <label>} for an API key), or None."""
    ask = {"session": session} if session else {"key": key} if key else None
    if not ask:
        return None
    k, now = tuple(ask.items()), time.monotonic()
    with _lock:
        hit = _seen.get(k)
        if hit and hit[1] > now:
            return hit[0]
    code, answer = call("/v1/check", ask)
    if code == 200:
        with _lock:
            if len(_seen) > 1000:
                _seen.clear()
            _seen[k] = (answer, now + KEEP)
        return answer
    with _lock:
        _seen.pop(k, None)
    if code in (400, 401):
        return None
    raise Unreachable("auth-client answered %d" % code)


def login(user, password, address, agent):
    return call("/v1/login", {"user": user, "password": password, "client": {"address": address, "agent": agent}})


def logout(session):
    with _lock:
        _seen.pop((("session", session),), None)
    return call("/v1/logout", {"session": session})


def visit(session):
    """visit-client's {port, token} for a signed-in session: (status, JSON)."""
    return call("/v1/visit", {"session": session})


def session_of(cookie_header, name=COOKIE):
    try:
        c = SimpleCookie(cookie_header or "")
    except CookieError:
        return None
    m = c.get(name)
    return m.value if m and m.value else None


def bearer(header):
    h = header or ""
    return h[7:].strip() or None if h.startswith("Bearer ") else None


def cookie(session, max_age, remote=False):
    """The session cookie (basedverse's auth/SPEC.md 4): over HTTPS (remote), Secure and __Host-."""
    return "%s=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d%s" % (COOKIE_REMOTE if remote else COOKIE, session, max(0, int(max_age)),
                                                                      "; Secure" if remote else "")


def safe_next(n):
    """Where to go once signed in: a path on this site (it starts with / but not // or /\\), else /."""
    n = n or "/"
    return n if re.match(r"/(?![/\\])", n) and not re.search(r"[\\\x00-\x1f\x7f]", n) else "/"
