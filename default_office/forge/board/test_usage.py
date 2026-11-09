"""The account's usage limits: bin/usage, asking a fake `claude` (what get_usage answers, and how it fails), and
the board's reading (server.usage) of its plan.json, /usage's own rows, while it is fresh; else of what each
agent's status line noted (seat/hooks/usage_line.py), the session's and the week's. No network, no Claude Code:
a folder of files of its own.

Run: python3 board/test_usage.py
"""
import importlib.machinery
import importlib.util
import json
import os
import stat
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("REPO_TOKEN", "test")
os.environ["REPO_ENV"] = os.devnull
os.environ["BOARD_DB"] = os.path.join(tempfile.mkdtemp(), "board.db")
os.environ["USAGE_DIR"] = DIR = tempfile.mkdtemp()
WORKFLOW = os.path.join(tempfile.mkdtemp(), "workflow.json")   # a fixed project, as test_views.py has
with open(WORKFLOW, "w") as _f:
    json.dump({"columns": [{"key": "ready", "name": "Ready", "default": True, "pick": True},
                           {"key": "done", "name": "Closed", "closed": True}]}, _f)
os.environ.update(REPO_OWNER="testorg", REPO_NAMES="", REPO_HOME="docs", REPO_RANK="severity: high", REPO_WORKFLOW=WORKFLOW)
import server  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("usage", os.path.join(HERE, "..", "..", "bin", "usage"))
usage = importlib.util.module_from_spec(importlib.util.spec_from_loader("usage", _loader))
_loader.exec_module(usage)

FAILS = 0
NOW = 1_800_000_000
ISO = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def check(what, got, want):
    global FAILS
    ok = got == want
    FAILS += not ok
    print("[%s] %s%s" % ("ok" if ok else "FAIL", what, "" if ok else ": got %r, want %r" % (got, want)))


def note(name, at, five=None, seven=None, raw=None):
    with open(os.path.join(DIR, name), "w") as f:
        if raw is not None:
            f.write(raw)
        else:
            d = {"at": at}
            if five: d["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
            if seven: d["seven_day"] = {"used_percentage": seven[0], "resets_at": seven[1]}
            json.dump(d, f)


def plan(at, rows=None, raw=None):
    with open(os.path.join(DIR, "plan.json"), "w") as f:
        f.write(raw if raw is not None else json.dumps({"at": at, "rows": rows}))


def row(kind, group, name, pct, reset, severity="normal"):
    return {"kind": kind, "group": group, "name": name, "percent": pct, "resets_at": reset, "severity": severity}


def served(kind, group, name, used, reset, severity="normal", passed=False):
    return {"kind": kind, "group": group, "name": name, "used": used, "resets_at": ISO(reset) if reset else None, "passed": passed, "severity": severity}


# What Claude Code's get_usage answered (2.1.310), cut to what bin/usage reads.
LIMITS = {"five_hour": {"utilization": 4, "resets_at": "2026-10-26T22:09:59.722786+00:00"}, "limits": [
    {"kind": "session", "group": "session", "percent": 4, "severity": "normal", "resets_at": "2026-10-26T22:09:59.722786+00:00", "scope": None, "is_active": False},
    {"kind": "weekly_all", "group": "weekly", "percent": 20, "severity": "normal", "resets_at": "2026-10-31T01:59:59.722807+00:00", "scope": None, "is_active": True},
    {"kind": "weekly_scoped", "group": "weekly", "percent": 0, "severity": "normal", "resets_at": "2026-10-31T02:00:00+00:00",
     "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}, "is_active": False}]}
ROWS = [{"kind": "session", "group": "session", "name": None, "percent": 4.0, "resets_at": 1793052599, "severity": "normal"},
        {"kind": "weekly_all", "group": "weekly", "name": None, "percent": 20.0, "resets_at": 1793411999, "severity": "normal"},
        {"kind": "weekly_scoped", "group": "weekly", "name": "Fable", "percent": 0.0, "resets_at": 1793412000, "severity": "normal"}]


def fake_claude(reply, exit_code=0, other=False):
    """A `claude` first on the PATH that answers bin/usage's ask with `reply` (a control response's body; `other`:
    answering some other request), and notes the request and its own environment beside it."""
    bindir = tempfile.mkdtemp()
    path = os.path.join(bindir, "claude")
    with open(path, "w") as f:
        f.write("#!%s\nimport json, os, sys\nreq = sys.stdin.readline()\n" % sys.executable +
                "json.dump({'req': json.loads(req), 'argv': sys.argv[1:], 'env': sorted(os.environ)}, open(%r, 'w'))\n" % (path + ".got") +
                "print(json.dumps({'type': 'system', 'subtype': 'init'}))\n" +
                "print(json.dumps({'type': 'control_response', 'response': dict(%r, request_id=%s)}))\n" % (reply, "'other'" if other else "json.loads(req)['request_id']") +
                "sys.exit(%d)\n" % exit_code)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    os.environ["PATH"] = bindir + os.pathsep + PATH
    return path + ".got"


def asked():
    try:
        return usage.ask()
    except Exception as e:
        return str(e)


PATH = os.environ["PATH"]


def main():
    # -- bin/usage: what it makes of get_usage's answer
    check("rows: the server's, as sent; a scoped row named by its model", usage.rows(LIMITS), ROWS)
    old = {"five_hour": {"utilization": 4, "resets_at": "2026-10-26T22:09:59.722786+00:00"}, "seven_day": None,
           "model_scoped": [{"display_name": "Fable", "utilization": 0, "resets_at": "2026-10-31T02:00:00+00:00"}]}
    check("rows: a server before rows: the session's, and each model's week", [(r["kind"], r["name"], r["percent"]) for r in usage.rows(old)],
          [("session", None, 4.0), ("weekly_scoped", "Fable", 0.0)])
    got = fake_claude({"subtype": "success", "response": {"rate_limits": LIMITS}})
    os.environ["CLAUDE_CODE_SESSION_ID"] = "an-agent's"   # bin/start run by an agent
    check("ask: the rows, from a headless claude", asked(), ROWS)
    with open(got) as f:
        g = json.load(f)
    check("ask: one get_usage request, behaviours skipped", g["req"], usage.ASK)
    check("ask: no session kept, no hook run", ["--no-session-persistence" in g["argv"], json.loads(g["argv"][g["argv"].index("--settings") + 1])],
          [True, {"disableAllHooks": True}])
    check("ask: not a child of the agent that ran bin/start", "CLAUDE_CODE_SESSION_ID" in g["env"], False)
    fake_claude({"subtype": "error", "error": "get_usage is not supported in this context"})
    check("ask: an error says what", asked(), "get_usage: get_usage is not supported in this context")
    fake_claude({"subtype": "success", "response": {"rate_limits": None}})
    check("ask: no plan limits (an API key)", asked(), "no plan limits: Claude Code is not signed in to a Pro or Max account here")
    fake_claude({"subtype": "success", "response": {"rate_limits": {"limits": []}}})
    check("ask: no rows", asked(), "get_usage answered with no limits")
    fake_claude({"subtype": "success", "response": {"rate_limits": LIMITS}}, exit_code=1, other=True)
    check("ask: no answer to it", asked().startswith("claude exited 1 without answering get_usage"), True)
    os.environ["PATH"] = tempfile.mkdtemp()
    check("ask: no claude", asked(), "no `claude` on the PATH")
    os.environ["PATH"] = PATH

    check("nothing heard: no usage", server.usage(NOW), {})
    S, W = NOW + 3600, NOW + 4 * 86400

    # -- the agents' status lines' notes, with no bin/usage
    note("a.json", NOW - 60, (18, S), (17, W))
    note("b.json", NOW - 900, (12, S), (16, W))   # idle a while: it says what it heard last
    check("notes: the highest use on the current reset, as of the newest note", server.usage(NOW),
          {"limits": [served("session", "session", None, 18.0, S), served("weekly_all", "weekly", None, 17.0, W)],
           "at": ISO(NOW - 60), "from": "status lines"})
    note("c.json", NOW - 30, (3, S + 5 * 3600))   # a newer session window: a later reset wins, whatever the older said
    used = lambda u, kind: next(w["used"] for w in u["limits"] if w["kind"] == kind)
    check("notes: a later reset is the current window", used(server.usage(NOW), "session"), 3.0)
    note("d.json", NOW - 10, (4, S + 5 * 3600 + 20))   # the same reset, its clock a little off
    check("notes: …the same window give or take a minute", used(server.usage(NOW), "session"), 4.0)
    check("notes: a window whose reset has passed reads 0% used, passed",
          {k: v for k, v in server.usage(S + 5 * 3600 + 30)["limits"][0].items() if k in ("used", "passed")}, {"used": 0.0, "passed": True})
    note("e.json", NOW, raw="{half a no")
    note("f.json", NOW, raw=json.dumps({"at": NOW, "five_hour": {"used_percentage": "lots"}}))
    note("g.txt", NOW, raw="not a note")
    check("notes: garbled ones are passed over", used(server.usage(NOW), "weekly_all"), 17.0)
    note("h.json", NOW, (140, S + 5 * 3600 + 20))
    check("notes: a use past 100% reads 100, critical", [(w["used"], w["severity"]) for w in server.usage(NOW)["limits"] if w["kind"] == "session"], [(100.0, "critical")])
    note("h.json", NOW, (85, S + 5 * 3600 + 20))
    check("notes: 85% is a warning", [w["severity"] for w in server.usage(NOW)["limits"] if w["kind"] == "session"], ["warning"])

    # -- bin/usage's plan.json: /usage's rows, Fable's week among them, ahead of the notes while fresh
    rows = [row("session", "session", None, 4, S), row("weekly_all", "weekly", None, 20, W), row("weekly_scoped", "weekly", "Fable", 0, W + 1)]
    plan(NOW - 30, rows)
    check("plan: /usage's rows, as they came, ahead of the notes", server.usage(NOW),
          {"limits": [served("session", "session", None, 4.0, S), served("weekly_all", "weekly", None, 20.0, W),
                      served("weekly_scoped", "weekly", "Fable", 0.0, W + 1)], "at": ISO(NOW - 30), "from": "usage"})
    check("plan: the server's severity is kept, whatever the use", [w["severity"] for w in (plan(NOW, [row("session", "session", None, 50, S, "warning")]) or server.usage(NOW))["limits"]], ["warning"])
    plan(NOW, [row("weekly_scoped", "weekly", "Claude Design", 7, None)])
    check("plan: a row with no reset", server.usage(NOW)["limits"], [served("weekly_scoped", "weekly", "Claude Design", 7.0, None)])
    plan(NOW, [row("session", "session", None, 90, NOW + 100, "warning")] + rows[1:])
    check("plan: a row whose reset has passed reads 0% used, passed, normal", [(w["used"], w["passed"], w["severity"]) for w in server.usage(NOW + 101)["limits"]],
          [(0.0, True, "normal"), (20.0, False, "normal"), (0.0, False, "normal")])
    plan(NOW, rows)
    check("plan: ten minutes old, still current", server.usage(NOW + 600)["from"], "usage")
    check("plan: older than that (bin/usage stopped): the notes", server.usage(NOW + 601)["from"], "status lines")
    plan(NOW, [])
    check("plan: no rows: the notes", server.usage(NOW)["from"], "status lines")
    plan(NOW, raw="{half a pl")
    check("plan: garbled: the notes", server.usage(NOW)["from"], "status lines")
    plan(NOW, [{"kind": "session", "percent": "lots"}])
    check("plan: a garbled row: the notes", server.usage(NOW)["from"], "status lines")
    # -- a page's long-poll: a change in the bars is a change of the board's (its `ver`); only a later hearing of the same is not
    keep = ("rows", "plan", "agents", "agents_error", "prs", "prs_error", "truncated", "gone", "columns", "columns_error", "boards", "boards_error")
    snap = lambda u: dict({k: server.SNAP.get(k) for k in keep}, usage=u)
    server.publish(snap({})); v0 = server.SNAP["ver"]
    plan(NOW, rows); u1 = server.usage(NOW); server.publish(snap(u1)); v1 = server.SNAP["ver"]
    plan(NOW + 120, rows); server.publish(snap(server.usage(NOW + 120))); v2 = server.SNAP["ver"]
    plan(NOW + 240, [row("session", "session", None, 9, S)] + rows[1:]); server.publish(snap(server.usage(NOW + 240))); v3 = server.SNAP["ver"]
    check("publish: bin/usage's first word is news to every page; the same heard again is not; new bars are", [v1 - v0, v2 - v1, v3 - v2], [1, 0, 1])
    print("%d fail" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
