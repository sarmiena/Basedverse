"""Cases for forge.py's pure helpers — no network, no board mutation.

`prose()` is the one that earns a test. A body passed as `-b "...${x}..."` is
command-substituted by the shell before python sees it, and the damage is SILENT:
the issue posts, reads like a finished sentence, and the missing half is only found
by someone re-reading the artifact. The file path is the fix, so the case that
matters is that a file round-trips the exact bytes a shell would have eaten.

Run: python3 tools/test_forge.py
"""
import importlib.util
import os
import pathlib
import sys
import tempfile

# Derive from this file, never hardcode a machine's paths — this rides the seat repo and
# must pass on Windows, macOS and Linux alike.
HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("forge", HERE / "forge.py")
forge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forge)

# The literal that started this: backticks AND ${...}, i.e. both things a shell eats.
HOSTILE = "line one\n`note.threadId = `sms-${uuid}-${thread}``\n\n```js\nconst x = `a${b}`\n```\n"

SENTINEL = object()
fails = 0


def case(label, fn, want):
    """want: SENTINEL for 'must refuse', else the exact value it must return."""
    global fails
    try:
        got = fn()
    except SystemExit:
        got = SENTINEL
    ok = (got is SENTINEL) if want is SENTINEL else (got == want)
    fails += not ok
    if got is SENTINEL:
        shown = "<refused>"
    else:
        shown = repr(got)[:44]
    print(f"[{'ok ' if ok else 'FAIL'}] {label:38} {shown}")


with tempfile.TemporaryDirectory() as d:
    tmp = pathlib.Path(d)
    body = tmp / "body.md"
    body.write_text(HOSTILE, encoding="utf-8")

    print("prose()")
    # The whole point: what a shell would have mangled survives byte-for-byte.
    case("hostile file round-trips verbatim",
         lambda: forge.prose(None, str(body), "-b", "-F"), HOSTILE)
    case("plain literal passes through",
         lambda: forge.prose("hello", None, "-b", "-F"), "hello")
    case("both forms at once refused",
         lambda: forge.prose("x", str(body), "-b", "-F"), SENTINEL)
    case("neither, required, refused",
         lambda: forge.prose(None, None, "-b", "-F"), SENTINEL)

    print("last_own()")
    cs = [{"id": 1, "user": {"login": "me"}}, {"id": 2, "user": {"login": "them"}},
          {"id": 3, "user": {"login": "me"}}, {"id": 4, "user": {"login": "them"}}]
    # The newest of MINE, even when someone else spoke after it.
    case("newest own, not newest overall", lambda: forge.last_own(cs, "me")["id"], 3)
    case("none of mine -> None", lambda: forge.last_own(cs[1:2], "me"), None)
    case("empty thread -> None", lambda: forge.last_own([], "me"), None)
    case("neither, optional, is None",
         lambda: forge.prose(None, None, "-b", "-F", required=False), None)
    case("unreadable path refused",
         lambda: forge.prose(None, str(tmp / "nope.md"), "-b", "-F"), SENTINEL)
    # `-b @file` is not expanded by argparse; posting the 8 literal characters reads
    # as a broken tool rather than a typo, so the shape is refused...
    case("@existing-file refused",
         lambda: forge.prose("@" + str(body), None, "-b", "-F"), SENTINEL)
    # ...but ONLY when it really names a file, or every body opening with an @mention
    # would be refused for looking like a path.
    case("@mention prose still allowed",
         lambda: forge.prose("@<owner-handle> take a look", None, "-b", "-F"),
         "@<owner-handle> take a look")
    print("own_comment()")
    # An OLDER own comment is reachable; that is the whole point over --replace-last.
    case("older own comment found", lambda: forge.own_comment(cs, "me", 1)["id"], 1)
    case("someone else's refused", lambda: forge.own_comment(cs, "me", 2),
         "comment 2 is not yours -- only your own words can be removed")
    case("id not on this thread refused", lambda: forge.own_comment(cs, "me", 99),
         "comment 99 is not on this issue")

print("\nref()")
forge.CFG["owner"] = "acme"
case("repo#N", lambda: forge.ref("host#185"), ("acme", "host", 185))
case("owner/repo#N", lambda: forge.ref("other/docs#9"), ("other", "docs", 9))
case("a repo name may start uppercase", lambda: forge.ref("WebApp#87"), ("acme", "WebApp", 87))
case("bare N with no pinned repo refused", lambda: forge.ref("185"), SENTINEL)
case("non-numeric refused", lambda: forge.ref("host#abc"), SENTINEL)

print("\nmerge_body()")
# GitHub's field names are not ours, so a plausible-looking rename is a 4xx at the worst possible
# moment — mid-merge, with a trunk half-decided. Pin the wire shape here, where it is free.
case("default is a real merge commit",
     lambda: forge.merge_body("merge", "abc123")["merge_method"], "merge")
case("our short name maps to theirs",
     lambda: forge.merge_body("squash", "abc123")["merge_method"], "squash")
case("unknown style refused",
     lambda: forge.merge_body("yolo", "abc123"), SENTINEL)
case("the old forge's ff-only is refused, not guessed",
     lambda: forge.merge_body("ff-only", "abc123"), SENTINEL)
# The safety that makes "I ran the gates on the merge result" an honest claim: the sha that
# was checked is the sha that merges, so a branch moving in between is refused, not merged.
case("head sha is pinned",
     lambda: forge.merge_body("merge", "abc123")["sha"], "abc123")
case("no sha -> no pin rather than an empty one",
     lambda: "sha" in forge.merge_body("merge", ""), False)
# An absent message must be ABSENT, not empty: an empty field makes the forge write a blank
# merge body over the default one it would otherwise compose.
case("absent title/message are omitted",
     lambda: sorted(forge.merge_body("merge", "s")),
     ["merge_method", "sha"])
case("title and message ride under their own names",
     lambda: sorted(forge.merge_body("merge", "s", "t", "m")),
     ["commit_message", "commit_title", "merge_method", "sha"])

print("\nscoped labels — exclusive by forge.py's rule, GitHub has none")
case("a scoped label replaces its sibling",
     lambda: forge.relabelled(["area/api", "team/infra", "epic"], ["area/core"]),
     ["team/infra", "epic", "area/core"])
case("an unscoped label is only added",
     lambda: forge.relabelled(["area/api"], ["epic"]), ["area/api", "epic"])
case("re-adding a label it carries changes nothing",
     lambda: forge.relabelled(["env/staging", "os/windows"], ["env/staging"]), ["os/windows", "env/staging"])
case("a `scope: value` label is scoped too, and replaces its sibling",
     lambda: forge.relabelled(["severity: low", "bug"], ["severity: high"]), ["bug", "severity: high"])
case("a label with no separator is unscoped", lambda: forge.scope_of("In Progress"), None)
case("a nested scope is its own: env/a/b does not replace env/c",
     lambda: forge.relabelled(["env/c"], ["env/a/b"]), ["env/c", "env/a/b"])
case("two in one scope is a clash",
     lambda: forge.scope_clash(["area/api", "env/staging", "area/web"]),
     ("area", "area/api, area/web"))
case("one per scope is no clash", lambda: forge.scope_clash(["area/api", "env/staging", "epic"]), None)
case("the same label twice is no clash", lambda: forge.scope_clash(["env/staging", "env/staging"]), None)
case("label_names refuses a clash before any request",
     lambda: forge.label_names(["area/api", "area/web"], "acme", "host"), SENTINEL)

print("\nrepo_of() / api_all()")
case("from the org listing's repository object",
     lambda: forge.repo_of({"repository": {"name": "host"}, "repository_url": "x/other"}), "host")
case("from repository_url (repo listing, search, dependencies)",
     lambda: forge.repo_of({"repository_url": "https://api.github.com/repos/acme/docs"}), "docs")


def pages_of(n, cap=forge.PAGES):
    """api_all over a stub list of n rows: (rows got, pages asked)."""
    asked, saved = [], forge.api
    forge.api = lambda path, method="GET", body=None, allow=(): asked.append(path) or [
        i for i in range(n)][(len(asked) - 1) * forge.PER_PAGE:len(asked) * forge.PER_PAGE]
    try:
        return len(forge.api_all("/x?state=open", cap)), len(asked)
    finally:
        forge.api = saved


case("one short page is the whole list", lambda: pages_of(7), (7, 1))
case("a full page is followed by the next", lambda: pages_of(150), (150, 2))
case("an exact multiple costs one empty page", lambda: pages_of(200), (200, 3))
case("past the cap EXITS, never a list that looks complete", lambda: pages_of(250, cap=2), SENTINEL)

print("\ncolumns_line() — the kanban at a glance")
COLS_D = {"columns": [{"key": "next", "name": "Up next", "plan": True}, {"key": "ready", "name": "Ready"},
                      {"key": "doing", "name": "In progress", "limit": 1}, {"key": "done", "name": "Closed", "closed": True}],
          "plan": {"steps": [{}, {}]}, "gone": [{}],
          "rows": [{"col": "ready"}, {"col": "doing"}, {"col": "doing"}]}
case("counts per column, plan and closed included, WIP over flagged", lambda: forge.columns_line(COLS_D),
     "COLUMNS — Up next 2 · Ready 1 · In progress 2/1 OVER · Closed 1")
case("a board that serves no columns says so", lambda: "restart" in forge.columns_line({"rows": []}), True)

print("\nmark_claims() — a claim shows on the issue as the workflow's claim label")


def marks(refs, on, have, lab="status: in progress"):
    calls, saved = [], (forge.claim_label, forge.add_labels, forge.api)
    forge.claim_label = lambda: lab
    def add(o, r, n, names):
        if names[0] != "status: in progress":
            sys.exit("no such label(s) in o/r: %s" % names[0])
        calls.append(("add", n, names[0]))
    forge.add_labels = add
    forge.api = lambda path, method="GET", body=None, allow=(): (
        calls.append((method, path.rsplit("/", 1)[-1])) if method != "GET" else {"labels": [{"name": x} for x in have]})
    try:
        forge.mark_claims(refs, on)
    finally:
        forge.claim_label, forge.add_labels, forge.api = saved
    return calls


case("a claim adds it", lambda: marks(["app#1"], True, []), [("add", 1, "status: in progress")])
case("a release takes it off when it is there", lambda: marks(["app#1"], False, ["status: in progress"]),
     [("DELETE", "status%3A%20in%20progress")])
case("a release leaves an issue someone moved on alone", lambda: marks(["app#1"], False, ["status: in review"]), [])
case("no claim_label in the workflow: nothing is written", lambda: marks(["app#1"], True, [], lab=None), [])
case("a repo without the label costs a warning, never the claim", lambda: marks(["app#1"], True, [], lab="nope"), [])

print("\nretarget_body()")
case("moves the base", lambda: forge.retarget_body("develop", "node-mux-proxy"), {"base": "develop"})
case("a PR cannot target its own head", lambda: forge.retarget_body("develop", "develop"), SENTINEL)
case("an empty base is refused", lambda: forge.retarget_body("", "x"), SENTINEL)

print("\nsplit_present()")
case("present and absent split",
     lambda: forge.split_present(["env/dev", "env/prod"], ["area/api", "env/dev"]),
     (["env/dev"], ["env/prod"]))
case("nothing present -> nothing to delete",
     lambda: forge.split_present(["env/prod"], []), ([], ["env/prod"]))
case("a name given twice is deleted once",
     lambda: forge.split_present(["env/prod", "env/prod"], ["env/prod"]), (["env/prod"], []))
case("request order is kept",
     lambda: forge.split_present(["b", "a"], ["a", "b"]), (["b", "a"], []))

print("\nplan_problems()")
GOOD = "1) host#289 ✋ land the node stack\n2) android#305 browser CONNECT timeout\n"
case("a referential plan passes", lambda: forge.plan_problems(GOOD), [])
case("prose is refused",
     lambda: bool(forge.plan_problems("Review host#266 with me, then land the stack in order.")), True)
case("a step with no ref is refused",
     lambda: bool(forge.plan_problems("1) fix the flaky thing")), True)
case("a long line is refused",
     lambda: bool(forge.plan_problems("1) host#1 " + "x" * 200)), True)
case("a long plan passes: no step cap (owner call 2026-10-14)",
     lambda: forge.plan_problems("".join("%d) host#%d x\n" % (i, i) for i in range(1, 30))), [])
case("empty is refused", lambda: forge.plan_problems("  \n"), ["the plan is empty"])

print("\npick_resume()")
case("none open", lambda: forge.pick_resume([{"number": 1, "title": "Epic: x"}]), None)
case("the one RESUME is picked",
     lambda: forge.pick_resume([{"number": 1, "title": "x"}, {"number": 9, "title": "RESUME — y"}])["number"], 9)
case("two RESUMEs refused",
     lambda: forge.pick_resume([{"number": 1, "title": "RESUME a"}, {"number": 2, "title": "RESUME b"}]),
     SENTINEL)

print("\nagent roster")
# The name is DERIVED, so every reader (forge.py, the hook, the kanban) agrees on it.
case("agent_name is stable",
     lambda: forge.agent_name("session_abc") == forge.agent_name("session_abc"), True)
case("agent_name differs per session",
     lambda: forge.agent_name("session_abc") != forge.agent_name("session_abd"), True)
case("link only for a claude.ai session", lambda: (forge.agent_link("session_x"), forge.agent_link("d049fec1")),
     ("https://claude.ai/code/session_x", ""))
AG = {"name": "amber-heron", "session": "session_x", "local": "", "msgname": "app-6d [82b1bf]",
      "seat": "windows", "host": "host-a",
      "link": "", "seen": "2026-10-22T23:00:00Z", "claims": ["host#660", "docs#269"],
      "doing": "kernel lane: box_list", "ctx": "", "blocked": "", "subs": [], "working": "", "board": "", "dir": ""}
case("body round-trips", lambda: forge.parse_agent(forge.agent_body(AG)) == AG, True)
case("an entry written before msgname existed still parses, msgname blank",
     lambda: forge.parse_agent(forge.agent_body(AG).replace("msgname: app-6d [82b1bf]\n", ""))
     .get("msgname", ""), "")
case("a plain comment is not an agent", lambda: forge.parse_agent("claims: host#1\nsession: s"), None)
case("doing cannot inject a line",
     lambda: forge.parse_agent(forge.agent_body(dict(AG, doing="x\nclaims: host#1")))["claims"],
     ["host#660", "docs#269"])
C = [{"id": 1, "body": forge.agent_body(dict(AG, seen="2026-10-22T20:00:00Z"))},
     {"id": 2, "body": forge.agent_body(dict(AG, session="session_y", seen="2026-10-22T22:00:00Z"))},
     {"id": 3, "body": "an ordinary comment"},
     {"id": 4, "body": forge.agent_body(dict(AG, seen="2026-10-22T21:00:00Z"))}]
case("roster: freshest first, one per session", lambda: [d["id"] for d in forge.roster(C)], [2, 4])
case("claim_holders skips me",
     lambda: [d["session"] for d in forge.claim_holders(forge.roster(C), "host#660", {"session_x"})],
     ["session_y"])
case("claim_holders none for unclaimed",
     lambda: forge.claim_holders(forge.roster(C), "host#1", {"session_z"}), [])
dupes = []
forge.roster(C, dupes)
case("roster names the stale duplicate", lambda: dupes, [1])
case("is_me by bridge id", lambda: forge.is_me(AG, {"session_x"}), True)
case("is_me by local id (a hook)", lambda: forge.is_me(dict(AG, local="d049"), {"d049"}), True)
case("is_me never on empty ids", lambda: forge.is_me(dict(AG, local=""), {"", None}), False)
# One session, two entries: entered under its local id before the claude.ai link was up (a mailbox waiter,
# an early /go), then under the bridge id. Shown once, merged, under the bridge id.
L2 = [{"id": 10, "body": forge.agent_body(dict(AG, session="loc-1", local="", claims=["host#1"], doing="new task",
                                               seen="2026-10-22T22:00:00Z"))},
      {"id": 11, "body": forge.agent_body(dict(AG, session="session_b", local="loc-1", link=forge.agent_link("session_b"),
                                               msgname="", claims=["host#2"], doing="old task", seen="2026-10-22T21:00:00Z"))}]
dupes = []
case("one session entered under each of its ids: once, the fresher's comment and doing, both claims, the bridge id",
     lambda: [(d["id"], d["session"], d["local"], d["link"], d["claims"], d["doing"], d["msgname"]) for d in forge.roster(L2, dupes)],
     [(10, "session_b", "loc-1", "https://claude.ai/code/session_b", ["host#1", "host#2"], "new task", "app-6d [82b1bf]")])
case("...the other comment named for deletion", lambda: dupes, [11])
case("its mail goes to the local id, the one every process of it has", lambda: forge.mail_to(forge.roster(L2)[0]), "loc-1")
case("an entry keyed by the local id alone is mailed at that", lambda: forge.mail_to(dict(AG, session="loc-2", local="")), "loc-2")
case("my_entry: keyed by the bridge id, found by a process with only the local id",
     lambda: forge.my_entry([dict(AG, session="session_b", local="loc-1", id=3)], "loc-1", {"loc-1"})["id"], 3)
case("my_entry: the one keyed by the session first",
     lambda: forge.my_entry([dict(AG, session="loc-1", local="", id=4), dict(AG, session="session_b", local="loc-1", id=5)],
                            "session_b", {"session_b", "loc-1"})["id"], 5)
case("my_entry: never another session's", lambda: forge.my_entry([dict(AG, session="session_c", local="loc-9")], "loc-1", {"loc-1"}), None)
case("claim_holders: my own entry, under my other id, is not another agent",
     lambda: forge.claim_holders([dict(AG, session="session_b", local="loc-1")], "host#660", {"loc-1"}), [])
case("touch rewrites ONLY seen",
     lambda: forge.parse_agent(forge.with_seen(forge.agent_body(AG), "2026-10-23T00:00:00Z")),
     dict(AG, seen="2026-10-23T00:00:00Z", local=""))
case("state rewrites ONLY its lines",
     lambda: forge.parse_agent(forge.with_fields(forge.agent_body(AG), {"ctx": "102416", "blocked": "permission: Bash"})),
     dict(AG, ctx="102416", blocked="permission: Bash", local=""))
OLD = forge.agent_body(AG).replace("ctx: \n", "").replace("blocked: \n", "")
case("an entry written before ctx/blocked gains them inside the fence",
     lambda: forge.parse_agent(forge.with_fields(OLD, {"ctx": "5", "blocked": "question: which?"})),
     dict(AG, ctx="5", blocked="question: which?", local=""))
SUBS = [{"id": "a1", "type": "Explore", "what": "Find callers"}]
case("state writes the subs line, and it reads back as the list",
     lambda: forge.parse_agent(forge.with_fields(forge.agent_body(AG), {"subs": forge.subs_line(SUBS)}))["subs"], SUBS)
case("an entry written before subs existed parses with none",
     lambda: forge.parse_agent(forge.agent_body(AG).replace("subs: \n", ""))["subs"], [])
case("the roster line says it has subagents working",
     lambda: "[1 subagent working]" in forge.agent_line(dict(AG, subs=SUBS)), True)
# reap: an entry is taken off only when its session has provably left.
R = lambda **kw: dict(dict(AG, session="sess-dead", local="", host="home", seen="old", claims=[]), **kw)
AGE = lambda seen: {"old": 9999, "new": 30}.get(seen, 9999)
reap = lambda agents, live, ids=("sess-me",), host="home": [d["session"] for d in forge.reap_targets(agents, live, ids, host, AGE)]
case("reap: this machine's, not running, quiet: taken off", lambda: reap([R()], {"sess-me"}), ["sess-dead"])
case("reap: still running: kept", lambda: reap([R(session="sess-live")], {"sess-me", "sess-live"}), [])
case("reap: running under its local id: kept", lambda: reap([R(session="session_X", local="loc-1")], {"sess-me", "loc-1"}), [])
case("reap: a bridge id alone cannot be checked: kept", lambda: reap([R(session="session_X")], {"sess-me"}), [])
case("reap: another machine's: kept", lambda: reap([R(host="laptop")], {"sess-me"}), [])
case("reap: acted lately: kept", lambda: reap([R(seen="new")], {"sess-me"}), [])
case("reap: never the caller itself", lambda: reap([R(session="sess-me")], {"sess-x"}, ids=("sess-me",)), [])
case("reap: a registry without the caller's own session reaps nothing", lambda: reap([R()], {"sess-other"}), [])
case("reap: no registry reaps nothing", lambda: reap([R()], None), [])
case("the added line lands before the CLOSING fence",
     lambda: forge.with_fields(OLD, {"ctx": "5"}).rstrip().endswith("ctx: 5\n```"), True)
NOW = forge.datetime.datetime(2026, 10, 22, 23, 0, 0, tzinfo=forge.datetime.timezone.utc)
case("ago", lambda: [forge.ago(s, NOW) for s in ("2026-10-22T22:59:48Z", "2026-10-22T22:55:00Z",
                                                 "2026-10-22T20:00:00Z", "junk")],
     ["12s", "5m", "3h", "?"])

print("\ntouch() — at most once per TOUCH_EVERY_S, so agents do not spend the quota on `seen`")


def touch_run(force=False, entered=True, ent=None):
    """One touch() against a fake forge, as session sess-t with HOME in a temp dir: (result, calls)."""
    calls = []
    saved = (forge.agents_now, forge.api, os.environ.get("HOME"), os.environ.get("CLAUDE_CODE_SESSION_ID"),
             os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID"))
    ent = ent or dict(AG, session="sess-t", id=5)
    forge.agents_now = lambda *a, **k: calls.append("roster") or ({"number": 1}, [ent] if entered else [])
    forge.api = lambda path, method="GET", body=None, **k: calls.append(method) or ({"body": forge.agent_body(ent)} if method == "GET" else {})
    os.environ.update(HOME=TOUCH_HOME, CLAUDE_CODE_SESSION_ID="sess-t")
    os.environ.pop("CLAUDE_CODE_BRIDGE_SESSION_ID", None)
    try:
        return forge.touch(force), calls
    finally:
        forge.agents_now, forge.api = saved[0], saved[1]
        for k, v in zip(("HOME", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_BRIDGE_SESSION_ID"), saved[2:]):
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


import os
import tempfile
TOUCH_HOME = tempfile.mkdtemp()
case("the first touch reads the roster and writes seen", lambda: touch_run(), (True, ["roster", "GET", "PATCH"]))
case("another within TOUCH_EVERY_S costs GitHub nothing", lambda: touch_run(), (None, []))
case("a forced touch (the waiter's, on its own clock) touches", lambda: touch_run(force=True), (True, ["roster", "GET", "PATCH"]))
case("a session not on the roster: False, nothing written", lambda: touch_run(force=True, entered=False), (False, ["roster"]))
case("a process with only the local id (a waiter started before the link) touches the entry the bridge id keys",
     lambda: touch_run(force=True, ent=dict(AG, session="session_b", local="sess-t", id=5)), (True, ["roster", "GET", "PATCH"]))


print("\nagent_line() — the board's freshness verdict is shown, never a dead agent as live")
ENTRY = {"name": "old-owl", "seat": "mac", "host": "host-b", "seen": "2026-10-23T09:00:00Z",
         "claims": ["host#7"], "doing": "x", "you": False}
for fresh, tag in (("", None), ("idle", "[idle]"), ("stale", "[STALE]"), ("dead", "[DEAD]")):
    case("fresh=%r marks %s" % (fresh, tag or "nothing"),
         lambda f=fresh: [t for t in ("[idle]", "[STALE]", "[DEAD]") if t in forge.agent_line(dict(ENTRY, fresh=f))],
         [tag] if tag else [])
case("an entry with no served fresh (a write path's own line) marks nothing",
     lambda: [t for t in ("[idle]", "[STALE]", "[DEAD]") if t in forge.agent_line(ENTRY)], [])
case("a class this side does not know is shown, never read as live",
     lambda: "[GONE]" in forge.agent_line(dict(ENTRY, fresh="gone")), True)
case("a dead entry is still listed with its claims",
     lambda: "claims host#7" in forge.agent_line(dict(ENTRY, fresh="dead")), True)

# A /clear keeps the session (bridge id) but starts a new LOCAL id: a new conversation, so a new
# agent. Stubbed forge: no network. The stored name is deliberately NOT agent_name(local), so a
# re-derivation that should not happen shows.
def upsert_with(stored_local, new_local):
    writes, entry = [], {"name": "stored-name", "session": "session_X", "local": stored_local,
                         "seat": "windows", "host": "h", "link": "", "claims": ["forge#1"], "id": 7,
                         "doing": "old task", "ctx": "500000", "blocked": "a question",
                         "seen": "2026-01-01T00:00:00Z"}
    saved = forge.agents_now, forge.api

    def api(path, method="GET", body=None):
        if method == "PATCH":
            writes.append(forge.parse_agent(body["body"]))
        return {}
    forge.agents_now = lambda create=False, dupes=None: ({"number": 1}, [entry])
    forge.api = api
    try:
        forge.upsert_agent("session_X", create=False, local=new_local)
    finally:
        forge.agents_now, forge.api = saved
    if not writes:
        return "no write"
    w = writes[-1]
    return w["name"], w["local"], w["claims"], w.get("doing"), w.get("ctx"), w.get("blocked")


case("a /clear renames to the new local id's handle, keeps claims, drops the old conversation's state",
     lambda: upsert_with("old-local", "new-local"),
     (forge.agent_name("new-local"), "new-local", ["forge#1"], "", "", ""))
case("the same local id keeps the stored name and state",
     lambda: upsert_with("old-local", "old-local"),
     ("stored-name", "old-local", ["forge#1"], "old task", "500000", "a question"))
case("an entry with no local id only gains one: never renamed mid-life",
     lambda: upsert_with("", "new-local"),
     ("stored-name", "new-local", ["forge#1"], "old task", "500000", "a question"))


def upsert_as(comments, bridge, local):
    """One `agent` write by a process that has `bridge` (or none) and `local` for ids, against a stubbed
    forge holding `comments`: [(method, comment, (session, local, claims) written)]."""
    calls, keys = [], ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_BRIDGE_SESSION_ID")
    saved = forge.agents_now, forge.api, {k: os.environ.get(k) for k in keys}

    def api(path, method="GET", body=None):
        d = forge.parse_agent((body or {}).get("body"))
        calls.append((method, path.rsplit("/", 1)[1], d and (d["session"], d["local"], d["claims"])))
        return {"id": 99}
    forge.agents_now = lambda create=False, dupes=None: ({"number": 1}, forge.roster(comments, dupes))
    forge.api = api
    os.environ["CLAUDE_CODE_SESSION_ID"] = local
    os.environ.pop("CLAUDE_CODE_BRIDGE_SESSION_ID", None) if not bridge else os.environ.update(CLAUDE_CODE_BRIDGE_SESSION_ID=bridge)
    try:
        s = forge.agent_session()
        forge.upsert_agent(s, local=forge.local_id(s))
    finally:
        forge.agents_now, forge.api = saved[0], saved[1]
        for k, v in saved[2].items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return calls


case("entered under the local id, then a write with the bridge id: the same comment, keyed by the bridge id now",
     lambda: upsert_as(L2[:1], "session_b", "loc-1"), [("PATCH", "10", ("session_b", "loc-1", ["host#1"]))])
case("two entries for one session: the stray one deleted, the other holding both claims",
     lambda: upsert_as(L2, "session_b", "loc-1"), [("DELETE", "11", None), ("PATCH", "10", ("session_b", "loc-1", ["host#1", "host#2"]))])
case("a write without the bridge id (a process started before it) keeps the bridge id's key",
     lambda: upsert_as(L2[1:], None, "loc-1"), [("PATCH", "11", ("session_b", "loc-1", ["host#2"]))])
case("another session's entry is never taken: a new one",
     lambda: upsert_as(L2[1:], None, "loc-9"), [("POST", "comments", ("loc-9", "", []))])
TITLE_SPEC = importlib.util.spec_from_file_location("session_title", HERE.parent / "hooks" / "session_title.py")
TITLE = importlib.util.module_from_spec(TITLE_SPEC)
TITLE_SPEC.loader.exec_module(TITLE)
case("is_handle: the hook's own handles yes, an owner's title no",
     lambda: [TITLE.is_handle(forge, t) for t in (forge.agent_name("x"), "android#618 work", "my-title", "")],
     [True, False, False, False])

print("\npr_owner_note() — every PR line names who holds it, or says it cannot")
case("owned: the board's owner and why", lambda: forge.pr_owner_note(
    "host#1", {"host#1": {"state": "owned", "name": "amber", "via": "claim of host#7"}}),
    "  — held by amber (claim of host#7)")
case("orphaned: the session that left", lambda: forge.pr_owner_note(
    "host#1", {"host#1": {"state": "orphaned", "was": "session_X", "via": "session link"}}),
    "  — ORPHANED: session_X left (its session is not on the roster)")
case("unattributed: nobody to name", lambda: forge.pr_owner_note("host#1", {"host#1": {"state": "unattributed"}}),
     "  — no agent holds it")
case("no state at all (a board that judged no owner): UNKNOWN, with that reason",
     lambda: forge.pr_owner_note("host#1", {"host#1": None}), "  — owner UNKNOWN (the board judged no owner for it)")
case("an edit citing another session still ends with the author's link",
     lambda: forge.signed_body("fix; see https://claude.ai/code/session_OTHER", "session_AUTHOR").endswith(
         "https://claude.ai/code/session_AUTHOR\n"), True)
case("unknown (roster unread): UNKNOWN, never 'nobody'",
     lambda: forge.pr_owner_note("host#1", {"host#1": {"state": "unknown"}}),
     "  — owner UNKNOWN (the board could not read the roster)")

print("\nsigned_body() — every PR opened in a session carries its link")
case("appends the session link", lambda: forge.signed_body("What landed.", "session_AB"),
     "What landed.\n\nhttps://claude.ai/code/session_AB\n")
case("an empty body becomes just the link", lambda: forge.signed_body(None, "session_AB"),
     "https://claude.ai/code/session_AB\n")
case("already the LAST link: unchanged", lambda: forge.signed_body("x https://claude.ai/code/session_AB y", "session_AB"),
     "x https://claude.ai/code/session_AB y")
case("present but not last (a cited session follows): appended so ours is last",
     lambda: forge.signed_body("mine https://claude.ai/code/session_AB, see https://claude.ai/code/session_CD",
                               "session_AB").endswith("https://claude.ai/code/session_AB\n"), True)
case("a longer session id that starts with ours is not ours",
     lambda: forge.signed_body("see https://claude.ai/code/session_ABC", "session_AB").endswith(
         "https://claude.ai/code/session_AB\n"), True)
case("a whitespace-only body becomes just the link", lambda: forge.signed_body("  \n", "session_AB"),
     "https://claude.ai/code/session_AB\n")
case("outside a claude.ai session: unchanged", lambda: forge.signed_body("x", "local-uuid"), "x")
case("no session at all: unchanged", lambda: forge.signed_body("x", None), "x")
case("the board did not answer: UNKNOWN, never silence", lambda: forge.pr_owner_note("host#1", None),
     "  — owner UNKNOWN (the board did not answer, or its read is stale)")
case("a PR the board has not seen yet: UNKNOWN, never silence",
     lambda: forge.pr_owner_note("host#2", {"host#1": None}), "  — owner UNKNOWN (the board has not seen this PR yet)")
case("the board holds NO PRs yet: still UNKNOWN for this one, not silence",
     lambda: forge.pr_owner_note("host#2", {}), "  — owner UNKNOWN (the board has not seen this PR yet)")
case("no board server: nothing said", lambda: forge.pr_owner_note("host#1", False), "")
case("a stale PR list on the board reads as unknown",
     lambda: forge.pr_owners({"at": 1, "prs_error": "X", "prs": [{"id": "host#1", "owner": None}]}), None)
case("a board whose forge read failed reads as unknown",
     lambda: forge.pr_owners({"at": 1, "error": "down", "prs": [{"id": "host#1", "owner": None}]}), None)

# Leaving sticks: a touch on a session with NO entry (it ran `agent exit`, or the owner removed it)
# writes nothing. Before, it re-created the entry, so the leaver's next forge.py call put it back.
def touch_writes(entries):
    import os as _os
    writes = []
    saved = forge.agents_now, forge.api, _os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID")
    forge.agents_now = lambda create=False, dupes=None: ({"number": 1}, entries)
    forge.api = lambda path, method="GET", body=None: writes.append(method) or ({"body": forge.agent_body(entries[0])} if entries else {})
    _os.environ["CLAUDE_CODE_BRIDGE_SESSION_ID"] = "session_LEFT"
    home = _os.environ.get("HOME")
    _os.environ["HOME"] = TOUCH_HOME  # its stamp file goes there, never in the real home
    try:
        forge.touch(force=True)
    finally:
        _os.environ["HOME"] = home
        forge.agents_now, forge.api = saved[0], saved[1]
        if saved[2] is None:
            _os.environ.pop("CLAUDE_CODE_BRIDGE_SESSION_ID", None)
        else:
            _os.environ["CLAUDE_CODE_BRIDGE_SESSION_ID"] = saved[2]
    return [w for w in writes if w != "GET"]


case("a touch never re-creates an entry that left", lambda: touch_writes([]), [])
case("a touch on a live entry still bumps its seen",
     lambda: touch_writes([{"name": "x", "session": "session_LEFT", "local": "", "claims": [], "id": 3,
                            "seen": "2026-01-01T00:00:00Z"}]), ["PATCH"])


print("\nboards — REPO_BOARD picks this session's board; the roster and every plan stay in the home repo")


def configured(board=None, boards="acme2/Tool, sam/Lib"):
    """forge.py configured from a throwaway .env (the home board acme/app), with REPO_BOARD as given: the CFG it made."""
    d = tempfile.mkdtemp()
    with open(os.path.join(d, ".env"), "w") as f:
        f.write("REPO_OWNER=acme\nREPO_NAMES=app\nREPO_TOKEN=t\nREPO_BOARDS=%s\n" % boards)
    saved = {k: os.environ.get(k) for k in ("REPO_ENV", "REPO_BOARD")}
    os.environ["REPO_ENV"] = os.path.join(d, ".env")
    os.environ.pop("REPO_BOARD", None) if board is None else os.environ.__setitem__("REPO_BOARD", board)
    try:
        forge.configure()
        return dict(forge.CFG), forge.HOME_OWNER, forge.AGENTS_REPO, forge.RESUME_REPO
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


pick = lambda c: (c[0]["owner"], c[0]["repos"], c[0]["repo"], c[0]["board"], c[1], c[2])  # noqa: E731
case("no REPO_BOARD: the home board, as ever", lambda: pick(configured()), ("acme", ["app"], "app", "", "acme", "app"))
case("the home board named is the home board", lambda: pick(configured("acme/app")), ("acme", ["app"], "app", "", "acme", "app"))
case("another board: its owner and repo; the roster and plans stay home",
     lambda: pick(configured("sam/Lib")), ("sam", ["Lib"], "Lib", "sam/Lib", "acme", "app"))
case("no board: no repo of its own for a bare #N", lambda: pick(configured("none")), ("acme", ["app"], None, "none", "acme", "app"))
def refused(fn):
    try:
        return fn()
    except OSError as e:
        return type(e).__name__


case("a REPO_BOARD that is no owner/repo is refused", lambda: refused(lambda: configured("Lib")), "OSError")


def as_board(board, fn):
    configured(board)
    os.environ.pop("REPO_BOARD", None)
    try:
        return fn()
    finally:
        configured()


case("on another board, its own repo#N is under its owner",
     lambda: as_board("sam/Lib", lambda: forge.ref("Lib#4")), ("sam", "Lib", 4))
case("...a bare #N is its repo's", lambda: as_board("sam/Lib", lambda: forge.ref("#4")), ("sam", "Lib", 4))
case("...and the home board's repo#N under the home owner", lambda: as_board("sam/Lib", lambda: forge.ref("app#9")), ("acme", "app", 9))
case("on the home board, another board's repo#N is under ITS owner (REPO_BOARDS)",
     lambda: as_board(None, lambda: [forge.ref("Tool#2"), forge.ref("lib#3")]), [("acme2", "Tool", 2), ("sam", "lib", 3)])
case("no board: a bare #N is refused", lambda: as_board("none", lambda: forge.ref("4")), SENTINEL)
case("no board: the board is refused, saying why", lambda: as_board("none", lambda: forge.need_board("board")), SENTINEL)
case("...but the home board needs nothing", lambda: as_board(None, lambda: forge.need_board("board")), None)

PLANS = [{"number": 1, "title": "RESUME — the next session's critical path"},
         {"number": 2, "title": "RESUME [sam/Lib] — the next session's critical path"}]
case("the home board's plan is the untagged RESUME issue", lambda: forge.pick_resume(PLANS, "")["number"], 1)
case("another board's is the one tagged with it (any case)", lambda: forge.pick_resume(PLANS, "SAM/lib")["number"], 2)
case("a board with no plan yet has none", lambda: forge.pick_resume(PLANS, "acme2/Tool"), None)
case("by default, this session's board's", lambda: as_board("sam/Lib", lambda: forge.pick_resume(PLANS)["number"]), 2)


def entry_board(board):
    """The `board` an own-session roster write records, on `board`."""
    def run():
        writes, saved = [], (forge.agents_now, forge.api, os.environ.get("CLAUDE_CODE_SESSION_ID"))
        forge.agents_now = lambda create=False, dupes=None: ({"number": 1}, [])
        forge.api = lambda path, method="GET", body=None: writes.append(forge.parse_agent(body["body"])) or {"id": 5}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-board"
        try:
            forge.upsert_agent("sess-board", local=None)
        finally:
            forge.agents_now, forge.api = saved[0], saved[1]
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None) if saved[2] is None else os.environ.__setitem__("CLAUDE_CODE_SESSION_ID", saved[2])
        return writes[-1]["board"]
    return as_board(board, run)


case("an agent's own entry says its board", lambda: [entry_board("sam/Lib"), entry_board("none"), entry_board(None)], ["sam/Lib", "none", ""])


def read_path(board):
    """The /api/board path board_read asks, on `board`."""
    def run():
        asked, saved = [], forge.board
        forge.board = lambda path, body=None, timeout=0, quiet=False, net=False: asked.append(path) or {"at": 1}
        try:
            forge.board_read()
        finally:
            forge.board = saved
        return asked[0].split("&board=")[1] if "&board=" in asked[0] else ""
    return as_board(board, run)


case("board_read asks for its own board's rows; the home board's and no board's ask as ever",
     lambda: [read_path("sam/Lib"), read_path(None), read_path("none")], ["sam/Lib", "", ""])



def dotenv_first():
    """A checkout's committed settings fill the environment: they must never point the token elsewhere."""
    keep = dict(forge.DOTENV), {k: os.environ.get(k) for k in ("REPO_URL", "REPO_TOKEN", "REPO_BOARD", "REPO_RANK")}
    try:
        forge.DOTENV.clear(); forge.DOTENV.update(REPO_URL="https://api.github.com", REPO_TOKEN="mine", REPO_RANK="from-dotenv")
        os.environ.update(REPO_URL="https://evil.example/api", REPO_TOKEN="theirs", REPO_BOARD="sam/Lib", REPO_RANK="from-env")
        return [forge.seat_cfg("url"), forge.seat_cfg("token"), forge.seat_cfg("board"), forge.seat_cfg("rank")]
    finally:
        forge.DOTENV.clear(); forge.DOTENV.update(keep[0])
        for k, v in keep[1].items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


case("the forge's address and token from the .env, never a checkout's environment; REPO_BOARD (and the rest) from it as ever",
     dotenv_first, ["https://api.github.com", "mine", "sam/Lib", "from-env"])

print("\nall pass" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
