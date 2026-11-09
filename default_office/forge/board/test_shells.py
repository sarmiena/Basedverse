"""bin/shells and the board's reading of it: which agents have a command running.

bin/shells is run against a fake /proc: two Claude Code processes and their children, only the shell
tool's commands counting (not the message waiter), only those that have run MIN_S, the oldest per session. The server's shells()
reads its now.json: fresh, stale, missing and garbled. No network, no real processes.

Run: python3 board/test_shells.py
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("REPO_TOKEN", "test")
os.environ["REPO_ENV"] = os.devnull
os.environ["BOARD_DB"] = os.path.join(tempfile.mkdtemp(), "board.db")
os.environ["SHELLS_DIR"] = tempfile.mkdtemp()
WORKFLOW = os.path.join(tempfile.mkdtemp(), "workflow.json")   # a fixed project, as test_views.py has
with open(WORKFLOW, "w") as _f:
    json.dump({"columns": [{"key": "ready", "name": "Ready", "default": True, "pick": True},
                           {"key": "done", "name": "Closed", "closed": True}]}, _f)
os.environ.update(REPO_OWNER="testorg", REPO_NAMES="", REPO_HOME="docs", REPO_RANK="severity: high",
                  REPO_WORKFLOW=WORKFLOW)
import server  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("shells", os.path.join(HERE, "..", "..", "bin", "shells"))
shells = importlib.util.module_from_spec(importlib.util.spec_from_loader("shells", _loader))
_loader.exec_module(shells)

TICK = os.sysconf("SC_CLK_TCK")
BOOT = 1_700_000_000
TOOL = "/bin/bash\0-c\0source /home/u/.claude/shell-snapshots/snapshot-bash-1-abc.sh && eval 'make test'\0"
FAILS = 0


def check(what, got, want):
    global FAILS
    ok = got == want
    FAILS += not ok
    print("[%s] %s%s" % ("ok" if ok else "FAIL", what, "" if ok else ": got %r, want %r" % (got, want)))


def fake_proc(procs):
    """A /proc of `procs`: [(pid, name, ppid, started (epoch seconds), cmdline, environ dict)]."""
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "stat"), "w") as f:
        f.write("cpu  1 2 3\nbtime %d\nprocesses 9\n" % BOOT)
    for pid, name, ppid, started, cmd, env in procs:
        d = os.path.join(root, str(pid))
        os.mkdir(d)
        with open(os.path.join(d, "stat"), "w") as f:   # starttime is the 22nd field, in ticks since boot
            f.write("%d (%s) S %d %s %d 0 0\n" % (pid, name, ppid, " ".join(["0"] * 17), (started - BOOT) * TICK))
        with open(os.path.join(d, "cmdline"), "w") as f:
            f.write(cmd)
        with open(os.path.join(d, "environ"), "w") as f:
            f.write("".join("%s=%s\0" % kv for kv in env.items()))
    os.mkdir(os.path.join(root, "self"))   # not a pid: skipped
    return root


def main():
    now = BOOT + 10_000
    s1, s2 = {"CLAUDE_CODE_SESSION_ID": "s1", "HOME": "/home/u"}, {"CLAUDE_CODE_SESSION_ID": "s2"}
    proc = fake_proc([
        (100, "claude", 1, BOOT + 10, "claude\0", {}),
        (101, "claude", 1, BOOT + 20, "claude\0", {}),
        (200, "bash", 100, now - 300, TOOL, s1),            # s1's test run, five minutes in
        (201, "bash", 100, now - 40, TOOL, s1),             # and a newer one: s1 counts from the older
        (202, "bash", 101, now - 2, TOOL, s2),              # s2's, two seconds in: too quick to count yet
        (203, "sh", 100, now - 600, "sh\0-c\0statusline.sh\0", s1),   # not the shell tool's: never counts
        (204, "bash", 1, now - 900, TOOL, {"CLAUDE_CODE_SESSION_ID": "s3"}),   # a claude's no more: never counts
        (205, "bash", 101, now - 60, TOOL, {"HOME": "/home/u"}),    # no session to say whose it is
        (206, "we (ird) one", 101, now - 50, "x\0", {}),     # a name with spaces and parentheses parses
        (207, "bash", 101, now - 800, TOOL.replace("make test", "python3 ~/.claude/tools/forge.py inbox --wait"),
         s2),                                               # s2's message waiter, all day: never counts
    ])
    check("the oldest of each session's commands that have run long enough", shells.scan(proc, now), {"s1": now - 300})
    check("s2's command once it has run MIN_S", shells.scan(proc, now + shells.MIN_S), {"s1": now - 300, "s2": now - 2})

    # The server's side: now.json, fresh, stale, missing and garbled.
    path, t = os.path.join(os.environ["SHELLS_DIR"], "now.json"), time.time()
    check("no now.json: no one", server.shells(t), {})
    with open(path, "w") as f:
        json.dump({"at": t - 3, "shells": {"s1": "2026-10-25T19:26:55Z", "": "x"}}, f)
    check("a fresh one", server.shells(t), {"s1": "2026-10-25T19:26:55Z"})
    check("a stale one: bin/shells stopped", server.shells(t + server.SHELLS_STALE + 5), {})
    with open(path, "w") as f:
        f.write("{half a fi")
    check("a garbled one: no one", server.shells(t), {})
    with open(path, "w") as f:
        json.dump(["not", "a", "dict"], f)
    check("the wrong shape: no one", server.shells(t), {})
    print("%d fail" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
