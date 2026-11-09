"""Cases for the message store (mail.py) — a temp SQLite file, no network, no server.

Run: python3 board/test_mail.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mail  # noqa: E402

fails = 0


def case(label, fn, want):
    global fails
    try:
        got = fn()
    except Exception as e:  # a raise is a result too, by its type
        got = type(e).__name__
    ok = got == want
    fails += not ok
    print("[%s] %-60s %s" % ("ok " if ok else "FAIL", label, repr(got)[:60]))


def fresh(on_change=None):
    return mail.Store(os.path.join(tempfile.mkdtemp(), "board.db"), on_change=on_change)


print("messages")
s = fresh()
s.unread("amber")  # amber reads first: a message to all after this is news to it
s.send("brisk", "all", "fyi")
s.send("brisk", "amber", "for you", to_sessions=[["amber", "amber"]])
amb = s.unread("amber")
case("to all and to me", lambda: [m["body"] for m in amb], ["fyi", "for you"])
case("a message names its recipient by handle", lambda: amb[-1]["rcpt_name"], "amber")
s.mark_read("amber", amb[-1]["id"])
case("read stays read", lambda: s.unread("amber"), [])
case("my own message to all is not news to me", lambda: [m["body"] for m in s.unread("brisk")], [])
case("a new reader starts now, not at the beginning", lambda: s.unread("newcomer"), [])
got = {}
w = threading.Thread(target=lambda: got.update(r=s.unread_wait("amber", hold=5)))
w.start()
s.send("brisk", "amber", "ping", to_sessions=[["amber", "amber"]])
w.join()
case("a waiting reader gets a message the moment it is sent", lambda: [m["body"] for m in got["r"]], ["ping"])
case("the owner is a recipient", lambda: s.send("amber", "owner", "done"), ["owner"])
own = s.unread("owner")
case("addressed to it, it is news even before its first read", lambda: [m["body"] for m in own], ["done"])
s.mark_read("owner", own[-1]["id"])
s.send("amber", "owner", "again")
case("and what comes after", lambda: [m["body"] for m in s.unread("owner")], ["again"])

print("\nrefusals")
r = fresh()
case("an empty message is refused", lambda: r.send("amber", "all", "  "), "Refused")
case("a null recipient is refused", lambda: r.send("amber", None, "x"), "Refused")
case("a handle with no sessions is refused", lambda: r.send("amber", "brisk", "x"), "Refused")
case("a number is not a body", lambda: r.send("amber", "all", 5), "Refused")
r.send("brisk", "all", "old news")
r.mark_read("late", 0)
case("a reader first seen by mark_read follows the new-reader rule", lambda: r.unread("late"), [])
v = r.ver
r.unread("someone-new")
case("a first read wakes no waiter", lambda: r.ver, v)
r.send("x", "amber", "one", to_sessions=[["amber", "amber"]])
r.mark_read("amber", 10 ** 9)
r.send("x", "amber", "two", to_sessions=[["amber", "amber"]])
case("a mark past the newest message cannot hide what comes next", lambda: [m["body"] for m in r.unread("amber")], ["two"])
case("a mark is a message id, not True", lambda: r.mark_read("amber", True), "Refused")
case("a negative LIMIT is not 'all of it'", lambda: r.recent_mail(-1), [])
case("recent is oldest first", lambda: [m["body"] for m in r.recent_mail(2)], ["one", "two"])

print("\nidentity is the session")
z = fresh()
z.send("x", "twin", "for the dead one", to_sessions=[["s-dead", "twin"]])
case("a message to a session is not read by another session with the same handle", lambda: z.unread("s-live"), [])
case("the session it was for reads it", lambda: [m["body"] for m in z.unread("s-dead")], ["for the dead one"])

print("\nthe page")
calls = []
w = fresh(on_change=lambda: calls.append(w.lock.locked()))
w.send("a", "all", "hi")
case("on_change fires after a write, outside the store's lock", lambda: calls, [False])
n = len(calls)
w.mark_read("owner", 0)
case("mark_read wakes the page", lambda: len(calls), n + 1)

print("\nall pass" if not fails else "\n%d FAILED" % fails)
sys.exit(1 if fails else 0)
