"""Cases for the roster's store (rosterdb.py) — a temp SQLite file, no network, no server.

Run: python3 board/test_rosterdb.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roster  # noqa: E402
import rosterdb  # noqa: E402

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


def fresh(path=None):
    return rosterdb.Store(path or os.path.join(tempfile.mkdtemp(), "board.db"))


def entry(session, claims="", seen="2026-11-09T21:00:00Z", **more):
    """An entry as forge.py writes it (agent_body's shape: the mark, then a fenced record)."""
    lines = dict({"name": "n-" + session, "session": session, "local": "", "seat": "linux", "host": "h", "link": "",
                  "seen": seen, "claims": claims, "doing": ""}, **more)
    return "%s\n```\n%s\n```\n" % (roster.AGENT_MARK, "\n".join("%s: %s" % kv for kv in lines.items()))


print("put, get, rows, delete")
s = fresh()
case("a new store is empty", lambda: (s.empty(), s.rows()), (True, []))
a = s.put(entry("sa", "host#1"))
case("a new entry gets an id and both stamps", lambda: (a["id"] > 0, bool(a["created_at"]), a["updated_at"] == a["created_at"]),
     (True, True, True))
case("it reads back as roster.py parses it", lambda: [d["claims"] for _, d in roster.entries(s.rows())], [["host#1"]])
b = s.put(entry("sb"))
case("rows come in the order they were made (roster.agents merges by it)", lambda: [r["id"] for r in s.rows()], [a["id"], b["id"]])
case("get: one row", lambda: s.get(b["id"])["body"], entry("sb"))
case("get: a row that is not there is None", lambda: s.get(999), None)
case("put with an id rewrites that row, keeping its id", lambda: (s.put(entry("sa", "host#2"), a["id"])["id"], s.get(a["id"])["body"]),
     (a["id"], entry("sa", "host#2")))
s.delete(b["id"])
case("delete takes the row off", lambda: [r["id"] for r in s.rows()], [a["id"]])
case("a deleted row is not brought back by a rewrite (a touch after the owner's x)", lambda: s.put(entry("sb"), b["id"]), "LookupError")
case("...nor deleted twice", lambda: s.delete(b["id"]), "LookupError")
case("ids are never reused (a stale id never names another agent)", lambda: s.put(entry("sc"))["id"] > b["id"], True)

print("\nonly agents' entries")
case("text that is not an entry is refused", lambda: s.put("hello"), "Refused")
case("an entry with no session is refused", lambda: s.put("%s\n```\nname: x\n```\n" % roster.AGENT_MARK), "Refused")
case("not text is refused", lambda: s.put(None), "Refused")
case("a huge entry is refused", lambda: s.put(entry("sd", doing="x" * (rosterdb.BODY_MAX + 1))), "Refused")
case("Refused is a ValueError (the board answers 422)", lambda: issubclass(rosterdb.Refused, ValueError), True)

print("\nthe move from GitHub (adopt)")
g = fresh()
comments = [{"id": 101, "body": entry("s1", "host#7")}, {"id": 102, "body": "a human's comment"},
            {"id": 103, "body": entry("s2")}]
case("adopt copies the agent entries, oldest first, and nothing else", lambda: (g.adopt(comments), [d["session"] for _, d in roster.entries(g.rows())]),
     (2, ["s1", "s2"]))
case("...and the claims come with them", lambda: [d["claims"] for _, d in roster.entries(g.rows())], [["host#7"], []])
case("adopt again does nothing: only into an empty roster", lambda: (g.adopt(comments), len(g.rows())), (0, 2))

print("\none file, two stores (mail.py's and this one), and many writers")
path = os.path.join(tempfile.mkdtemp(), "board.db")
import mail  # noqa: E402
m, r = mail.Store(path), fresh(path)
m.send("a", "all", "hi")
case("the messages and the roster share the board's database", lambda: (len(m.recent_mail(5)), r.put(entry("x"))["id"] > 0), (1, True))


def writer(k):
    for i in range(20):
        r.put(entry("w%d-%d" % (k, i)))


ts = [threading.Thread(target=writer, args=(k,)) for k in range(5)]
[t.start() for t in ts]
[t.join() for t in ts]
case("100 writes from 5 threads: 100 rows, ids all different", lambda: (len(r.rows()) - 1, len({x["id"] for x in r.rows()}) - 1), (100, 100))

print("\nall pass" if not fails else "\n%d FAILED" % fails)
sys.exit(1 if fails else 0)
