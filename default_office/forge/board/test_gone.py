"""Cases for why a row left the board (gone.py) and which agent owns a PR (roster.py
pr_refs / pr_owner). Fixtures only, no network.

Run: python3 board/test_gone.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gone  # noqa: E402
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
    print("[%s] %-62s %s" % ("ok " if ok else "FAIL", label, repr(got)[:60]))


OPEN = {"state": "open", "labels": [{"name": "depth/3-feature"}]}
case("closed", lambda: gone.why({"state": "closed", "labels": []}, False), "closed")
case("closed even when the search was truncated", lambda: gone.why({"state": "closed"}, True), "closed")
case("relabelled off the board", lambda: gone.why(dict(OPEN, labels=[{"name": "off-board"}]), False),
     "off-board")
case("deleted (the forge no longer has it)", lambda: gone.why(None, False), "deleted")
case("open, on the board's labels, yet out of the search: moved", lambda: gone.why(OPEN, False), "moved")
case("...but NOT when the search hit its LIMIT (it may just have fallen off the end)",
     lambda: gone.why(OPEN, True), None)

G = lambda i, at: {"id": "host#%d" % i, "title": "t", "why": "closed", "at": at}
case("keep drops what is older than the window",
     lambda: [g["id"] for g in gone.keep([G(1, 0), G(2, 5000)], 5000 + gone.WINDOW - 1)], ["host#2"])
case("keep is newest first", lambda: [g["id"] for g in gone.keep([G(1, 10), G(2, 20)], 30)],
     ["host#2", "host#1"])
case("keep drops a row that is back on the board",
     lambda: [g["id"] for g in gone.keep([G(1, 10), G(2, 20)], 30, {"host#2"})], ["host#1"])
case("keep holds one entry per row: its latest drop",
     lambda: [(g["id"], g["at"]) for g in gone.keep([G(1, 10), G(1, 20)], 30)], [("host#1", 20)])
case("keep caps the list", lambda: len(gone.keep([G(i, 100 + i) for i in range(gone.CAP + 9)], 200)),
     gone.CAP)

# PR -> owner.
case("refs: title refs in order, then the FIRST body ref; never the PR itself",
     lambda: roster.pr_refs("forge#36", "Board decides (forge#31)",
                            "forge#31, the server half. See forge#36, epic forge#35."),
     ["forge#31"])
case("refs: a body-only PR takes its first body ref",
     lambda: roster.pr_refs("host#688", "build_node.sh: fetch zig", "For host#640. Also touches host#12."),
     ["host#640"])
case("refs: a body that opens with its own PR still yields the issue it is for",
     lambda: roster.pr_refs("forge#36", "Board decides", "forge#36: implements forge#31. Also forge#35."),
     ["forge#31"])
case("refs: none", lambda: roster.pr_refs("x#1", "no refs", ""), [])
AG = [{"name": "amber", "session": "s1", "link": "https://claude.ai/code/session_A", "claims": ["host#7"]},
      {"name": "coral", "session": "s2", "link": "https://claude.ai/code/session_B", "claims": ["host#9"]}]
case("owned: the claimant of the ref named", lambda: roster.pr_owner(["host#9"], "", AG),
     {"state": "owned", "name": "coral", "session": "s2", "via": "claim of host#9"})
case("owned: a PR naming TWO claimed issues belongs to the claimant of the one named first",
     lambda: roster.pr_owner(["host#9", "host#7"], "", AG)["name"], "coral")
case("owned: an unclaimed first ref falls through to a claimed later one",
     lambda: roster.pr_owner(["host#1", "host#7"], "", AG)["name"], "amber")
case("owned: no claim, but the body's session link is a roster agent's",
     lambda: roster.pr_owner(["host#1"], "https://claude.ai/code/session_B", AG),
     {"state": "owned", "name": "coral", "session": "s2", "via": "session link"})
case("owned: the session link matches whatever else either link carries",
     lambda: roster.pr_owner([], "https://claude.ai/code/session_B/", AG)["name"], "coral")
case("owned: a DEAD roster entry still owns (the owner has not removed it)",
     lambda: roster.pr_owner(["host#7"], "", [dict(AG[0], fresh="dead")])["state"], "owned")
case("orphaned: the session link names a session NOT on the roster (its agent left)",
     lambda: roster.pr_owner(["host#1"], "https://claude.ai/code/session_GONE", AG),
     {"state": "orphaned", "was": "session_GONE", "via": "session link"})
case("orphaned: on an EMPTY roster too",
     lambda: roster.pr_owner([], "https://claude.ai/code/session_A", [])["state"], "orphaned")
case("owned: a session whose roster link is BLANK (local-only) still owns by its id",
     lambda: roster.pr_owner([], "https://claude.ai/code/session_L",
                             [{"name": "lo", "session": "session_L", "link": "", "claims": []}])["name"], "lo")
case("unknown, not orphaned, when the roster could not be read",
     lambda: roster.pr_owner([], "https://claude.ai/code/session_GONE", AG, roster_known=False),
     {"state": "unknown"})
case("a claimed ref beats an absent session link (the claim is live)",
     lambda: roster.pr_owner(["host#7"], "https://claude.ai/code/session_GONE", AG)["state"], "owned")
case("unattributed: a passing body mention is not a ref, and there is no link",
     lambda: roster.pr_owner(roster.pr_refs("x#5", "Fix x", "For x#4. Unrelated: host#7."), "", AG),
     {"state": "unattributed"})
case("unattributed: nothing at all", lambda: roster.pr_owner([], "", AG), {"state": "unattributed"})

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
