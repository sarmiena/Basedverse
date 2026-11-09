"""Cases for roster.py: freshness at each threshold, the dedupe, and the CONTRACT with
forge.py, which writes the entries this parses. No network.

Run: python3 board/test_roster.py   (finds forge.py at $FORGE_PY, ../../seat/tools/forge.py,
or ~/.claude/tools/forge.py)
"""
import datetime
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import roster  # noqa: E402

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


NOW = datetime.datetime(2026, 10, 23, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()


def at(secs_ago, fmt="%Y-%m-%dT%H:%M:%SZ"):
    return datetime.datetime.fromtimestamp(NOW - secs_ago, datetime.timezone.utc).strftime(fmt)


# Freshness: one second either side of every threshold.
for secs, want in ((0, ""), (299, ""), (300, "idle"), (1799, "idle"), (1800, "stale"),
                   (7199, "stale"), (7200, "dead"), (86400 * 3, "dead")):
    case("fresh at %ds ago" % secs, lambda s=secs: roster.fresh(at(s), NOW), want)
case("an offset stamp (the forge's updated_at) is read as its instant",
     lambda: roster.fresh(datetime.datetime.fromtimestamp(NOW - 400, datetime.timezone(
         datetime.timedelta(hours=-7))).isoformat(), NOW), "idle")
case("an unreadable stamp is dead", lambda: roster.fresh("yesterday", NOW), "dead")
case("an empty stamp is dead", lambda: roster.fresh("", NOW), "dead")
case("a future stamp (clock skew) is live", lambda: roster.fresh(at(-60), NOW), "")

# Parse + dedupe.
E = lambda session, seen, cid, **kw: {"id": cid, "updated_at": "", "body": "%s\n```\n%s\n```\n" % (
    roster.AGENT_MARK, "\n".join("%s: %s" % kv for kv in dict(session=session, seen=seen, **kw).items()))}
case("not an agent comment is skipped", lambda: roster.entries([{"id": 1, "body": "hello"}]), [])
case("an entry without session is skipped",
     lambda: roster.entries([{"id": 1, "body": roster.AGENT_MARK + "\nname: x"}]), [])
case("two entries for one session: the fresher wins",
     lambda: [(d["id"], d["seen"]) for d in roster.agents(roster.entries([E("s", at(100), 1), E("s", at(10), 2)]))],
     [(2, at(10))])
case("freshest first",
     lambda: [d["session"] for d in roster.agents(roster.entries([E("a", at(100), 1), E("b", at(10), 2)]))], ["b", "a"])
# One session entered under its local id (a process started before its claude.ai link was up), then
# under the bridge id: one agent, never two desks with one name.
TWO = [E("loc-1", at(10), 1, claims="host#1", doing="now"), E("session_b", at(100), 2, local="loc-1", claims="host#2",
                                                              link="https://claude.ai/code/session_b", doing="before")]
case("one session under each of its ids: once, the fresher's comment and doing, both claims, the bridge id",
     lambda: [(d["id"], d["session"], d["local"], d["link"], d["claims"], d["doing"]) for d in roster.agents(roster.entries(TWO))],
     [(1, "session_b", "loc-1", "https://claude.ai/code/session_b", ["host#1", "host#2"], "now")])
case("two sessions that share no id stay two",
     lambda: len(roster.agents(roster.entries([E("loc-1", at(10), 1), E("session_c", at(100), 2, local="loc-2")]))), 2)


# THE CONTRACT: forge.py writes the entry, roster.py reads it. A format change on one side alone
# goes red here.
def forge_py():
    for p in (os.environ.get("FORGE_PY"),
              os.path.join(HERE, "..", "..", "seat", "tools", "forge.py"),
              os.path.join(os.path.expanduser("~"), ".claude", "tools", "forge.py")):
        if p and os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("forge", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    return None


forge = forge_py()


def hook_sent(subs):
    """The seat hook's serializer (seat/hooks/agent_state.py sent), given the hook's own entries."""
    p = os.path.join(HERE, "..", "..", "seat", "hooks", "agent_state.py")
    spec = importlib.util.spec_from_file_location("agent_state", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.sent([dict(x, meta="/x") for x in subs])
if forge is None:
    print("[FAIL] forge.py not found: the contract cannot be checked")
    fails += 1
else:
    full = {"name": "amber-ibex", "session": "sess-bridge", "local": "sess-local",
            "msgname": "app-6d [82b1bf]", "seat": "windows", "host": "host-a",
            "link": "https://claude.ai/code/session_X", "seen": at(5),
            "claims": ["forge#32", "host#7"], "doing": "forge#32: roster reads", "ctx": "123456",
            "blocked": "a taste call",
            "subs": [{"id": "a4d0c07", "type": "general-purpose", "what": "Adversarial review: \"fix\" #93"},
                     {"id": "b81", "type": "Explore", "what": ""}],
            "working": at(40), "board": "acme/tool", "dir": "~/work/tool"}
    got = roster.entries([{"id": 9, "body": forge.agent_body(full)}])
    case("forge.py's keys == roster.py's keys", lambda: tuple(forge.AGENT_KEYS), roster.AGENT_KEYS)
    case("forge.py's mark == roster.py's mark", lambda: forge.AGENT_MARK, roster.AGENT_MARK)
    case("every field forge.py writes reads back unchanged", lambda: got[0][1] if got else None, full)
    case("forge.py's own parse agrees", lambda: forge.parse_agent(forge.agent_body(full)), full)
    case("no subs: the line is empty and reads back as []",
         lambda: roster.entries([{"id": 9, "body": forge.agent_body(dict(full, subs=[]))}])[0][1]["subs"], [])
    case("a garbled subs line costs only the subs",
         lambda: [(d["name"], d["subs"]) for _, d in roster.entries([{"id": 9, "body": forge.agent_body(full).replace(
             forge.subs_line(full["subs"]), "[{oops")}])], [("amber-ibex", [])])
    case("the hook's line (agent_state.sent) is the line forge.py writes", lambda: hook_sent(full["subs"]),
         forge.subs_line(full["subs"]))
    pick = lambda ds: [{k: d.get(k) for k in ("id", "session", "local", "link", "claims", "doing", "seen")} for d in ds]
    case("one session's two entries: forge.py merges them as the board does",
         lambda: pick(forge.roster(TWO)), pick(roster.agents(roster.entries(TWO))))

# The ticket each agent held last (roster.lasts): what the Agent Room opens on a click on one holding none.
held = [{"session": "s1", "claims": ["WebApp#520", "WebApp#7"]}, {"session": "s2", "claims": []}]
case("last: the first claim while it holds any", lambda: roster.lasts({}, held), {"s1": "WebApp#520"})
case("last: kept once it holds none", lambda: roster.lasts({"s1": "WebApp#520"}, [{"session": "s1", "claims": []}]), {"s1": "WebApp#520"})
case("last: a new claim takes its place", lambda: roster.lasts({"s1": "WebApp#520"}, [{"session": "s1", "claims": ["WebApp#9"]}]), {"s1": "WebApp#9"})
case("last: a session off the roster is forgotten", lambda: roster.lasts({"s1": "WebApp#520", "gone": "WebApp#1"}, [{"session": "s1", "claims": []}]), {"s1": "WebApp#520"})
case("last: ...but not when the roster could not be read", lambda: roster.lasts({"gone": "WebApp#1"}, [], known=False), {"gone": "WebApp#1"})

# A restart of this machine (roster.rebooted): its agents' entries, last seen before it booted, are taken off.
BOOT = NOW - 3600   # this machine started an hour ago


def ent(cid, name, session, local="", host="home", seen=7200):
    body = ("<!-- board-agent -->\nname: %s\nsession: %s\nlocal: %s\nhost: %s\nseen: %s\nclaims: \n"
            % (name, session, local, host, at(seen) if isinstance(seen, int) else seen))
    return {"id": cid, "body": body, "updated_at": at(0)}


def reaped(cs, host="home", boot=BOOT, live=lambda: set(), here=lambda sid: True):
    return [d["name"] for _, d in roster.rebooted(roster.entries(cs), host, boot, live, here)]


asked = []
case("rebooted: an entry of this machine's, last seen before it started", lambda: reaped([ent(1, "otter", "s-1")]), ["otter"])
case("rebooted: one seen since it started stays", lambda: reaped([ent(1, "otter", "s-1", seen=60)]), [])
case("rebooted: ...so does one seen the second it started", lambda: reaped([ent(1, "otter", "s-1", seen=3600)]), [])
case("rebooted: another machine's stays", lambda: reaped([ent(1, "otter", "s-1", host="laptop")]), [])
case("rebooted: one a Claude Code running now holds (resumed since) stays",
     lambda: reaped([ent(1, "otter", "s-1")], live=lambda: {"s-1"}), [])
case("rebooted: ...by its local id, under a bridge id",
     lambda: reaped([ent(1, "otter", "session_b", local="s-1")], live=lambda: {"s-1"}), [])
case("rebooted: a bridge id alone says nothing Claude Code here knows: it stays", lambda: reaped([ent(1, "otter", "session_b")]), [])
case("rebooted: one whose transcript is not here (a machine of the same name) stays",
     lambda: reaped([ent(1, "otter", "s-1")], here=lambda sid: False), [])
case("rebooted: the transcript is looked for by its local ids",
     lambda: (reaped([ent(1, "otter", "session_b", local="s-1")], here=lambda sid: asked.append(sid) or True), asked), (["otter"], ["s-1"]))
case("rebooted: an unreadable seen proves nothing: it stays", lambda: reaped([ent(1, "otter", "s-1", seen="whenever")]), [])
case("rebooted: no boot time, nothing proven", lambda: reaped([ent(1, "otter", "s-1")], boot=None), [])
case("rebooted: no registry of running sessions: the boot alone proves it",
     lambda: reaped([ent(1, "otter", "s-1")], live=lambda: None), ["otter"])
case("rebooted: the registry is not read when nothing is old",
     lambda: reaped([ent(1, "otter", "s-1", seen=60)], live=lambda: 1 / 0), [])
case("rebooted: only the dead of three",
     lambda: reaped([ent(1, "otter", "s-1"), ent(2, "koala", "s-2", seen=60), ent(3, "dingo", "s-3")]), ["otter", "dingo"])
case("rebooted: one session's two entries go together",
     lambda: reaped([ent(1, "otter", "s-1"), ent(2, "otter", "session_b", local="s-1", seen=5000)]), ["otter", "otter"])
case("rebooted: ...and stay together when one of them was seen since",
     lambda: reaped([ent(1, "otter", "s-1"), ent(2, "otter", "session_b", local="s-1", seen=60)]), [])

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
