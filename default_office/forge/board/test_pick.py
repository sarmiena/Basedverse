"""Cases for pick.py, the pickability rule /go reads: a fixture snapshot, no network.

Run: python3 board/test_pick.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pick  # noqa: E402
import workflow  # noqa: E402

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


WF = workflow.validate({"columns": [
    {"key": "next", "name": "Up next", "plan": True},
    {"key": "ready", "name": "Ready", "default": True, "pick": True},
    {"key": "later", "name": "Parked", "label": "later"},
    {"key": "doing", "name": "In progress", "label": "In Progress",
     "when": {"any": [{"label": "In Progress"}, {"claimed": True}]}},
    {"key": "review", "name": "In review", "when": {"pr": True}},
    {"key": "blocked", "name": "Blocked", "when": {"blocked": True}},
    {"key": "done", "name": "Closed", "closed": True},
]})


def row(id, tier=1, title="t", blocked=(), labels=()):
    repo, _, n = id.partition("#")
    return {"id": id, "repo": repo, "num": int(n), "title": title, "tier": tier, "rank": "r%d" % tier,
            "labels": list(labels), "epic": title.startswith("Epic:"), "blocked": list(blocked)}


ROWS = [
    row("app#1", tier=0),
    row("app#2", tier=0, blocked=["app#3"]),
    row("app#3", tier=1),
    row("app#4", tier=1),
    row("app#9", tier=1, title="Epic: x", blocked=["app#3", "app#4"]),
    row("app#6", tier=2, labels=["later"]),
    row("api#7", tier=99),
    row("app#10", title="Epic: claimed"),
    row("app#11", labels=["In Progress"]),
    row("app#12"),
]
AGENTS = [
    {"name": "me", "session": "S-me", "local": "L-me", "claims": ["app#1"]},
    {"name": "other", "session": "S-o", "claims": ["app#4", "app#10"]},
]
PRS = {"app#12"}
COLS = pick.columns(ROWS, AGENTS, PRS, WF)
BY = {r["id"]: r for r in COLS}


def v(me=("S-me",)):
    return {r["id"]: r for r in pick.view(COLS, AGENTS, set(me), WF)}


def why(id, **kw):
    return v(**kw)[id]["why_not"]


print("columns (the owner's view)")
case("unclaimed, unblocked: the default", lambda: BY["app#3"]["col"], "ready")
case("a claim moves it to In progress", lambda: BY["app#4"]["col"], "doing")
case("so does the In Progress label", lambda: BY["app#11"]["col"], "doing")
case("an open PR naming it: In review", lambda: BY["app#12"]["col"], "review")
case("blocked", lambda: BY["app#2"]["col"], "blocked")
case("an epic with open children is blocked", lambda: BY["app#9"]["col"], "blocked")
case("a label column", lambda: BY["app#6"]["col"], "later")
case("a claimed row carries who", lambda: BY["app#4"]["busy"], "claimed by other")
case("my OWN claim shows too (the columns are the owner's view)", lambda: BY["app#1"]["busy"], "claimed by me")
case("unclaimed carries no busy", lambda: BY["app#3"]["busy"], None)
case("no roster: still judged from labels and edges", lambda: pick.columns(ROWS, [], set(), WF)[3]["col"], "ready")

print("\nwhy_not (the caller's view)")
case("claimed by another", lambda: why("app#4"), "claimed by other")
case("claim by another wins over container", lambda: why("app#10"), "claimed by other")
case("my own claim is mine to resume, though it shows In progress", lambda: why("app#1"), None)
case("my claim under my OTHER id is mine (a hook knows only the local id)", lambda: why("app#1", me=("L-me",)), None)
case("owner view (no me): my claim counts", lambda: why("app#1", me=()), "claimed by me")
case("me with blanks is no one", lambda: why("app#1", me={"", None}), "claimed by me")
case("container over blocked", lambda: why("app#9"), "container")
case("blocked", lambda: why("app#2"), "blocked")
case("a column agents do not pick from", lambda: why("app#6"), "in Parked")
case("labelled In progress, unclaimed: not offered", lambda: why("app#11"), "in In progress")
case("in review: not offered", lambda: why("app#12"), "in In review")
case("pickable", lambda: why("app#3"), None)
case("claimed field names the other holder", lambda: v()["app#4"]["claimed"], "other")
case("claimed field is None for my own", lambda: v()["app#1"]["claimed"], None)
case("unblocks counts open rows blocked", lambda: (v()["app#3"]["unblocks"], v()["app#4"]["unblocks"]), (2, 1))
case("print order: tier, repo, num", lambda: [r["id"] for r in pick.view(COLS, AGENTS, set(), WF)],
     ["app#1", "app#2", "app#3", "app#4", "app#9", "app#10", "app#11", "app#12", "app#6", "api#7"])
AGED = [dict(row("app#30", tier=1), created="2026-10-20T00:00:00Z"), dict(row("app#31", tier=1), created="2026-10-01T00:00:00Z"),
        dict(row("app#32", tier=0), created="2026-10-22T00:00:00Z"), dict(row("api#2", tier=1), created="2026-10-10T00:00:00Z")]
case("within a rank, oldest first; across ranks, rank first",
     lambda: [r["id"] for r in pick.view(pick.columns(AGED, [], set(), WF), [], set(), WF)],
     ["app#32", "app#31", "api#2", "app#30"])
case("input rows are not mutated", lambda: (v(), "why_not" in COLS[0])[1], False)
case("view() leaves the columns alone", lambda: v()["app#4"]["col"], "doing")
case("a roster entry with no name does not crash; named by session",
     lambda: pick.view(pick.columns([row("app#5")], [], set(), WF), [{"session": "S-x", "claims": ["app#5"]}],
                       set(), WF)[0]["why_not"], "claimed by S-x")

print("\nall pass" if not fails else "\n%d FAILED" % fails)
sys.exit(1 if fails else 0)
