#!/usr/bin/env python3
"""usage_line.py: what it notes of the usage limits a status line is handed, and the status line it hands on to.

    python3 seat/hooks/test_usage_line.py

No Claude Code, no board: a temp folder for .usage/, and settings files of its own.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HOOK = pathlib.Path(__file__).with_name("usage_line.py")
spec = importlib.util.spec_from_file_location("usage_line", HOOK)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FAILS = 0


def check(what, got, want):
    global FAILS
    ok = got == want
    FAILS += not ok
    print("  %s %s%s" % ("ok  " if ok else "FAIL", what, "" if ok else ": got %r, want %r" % (got, want)))


def main():
    rl = {"five_hour": {"used_percentage": 18, "resets_at": 1792990800}, "seven_day": {"used_percentage": 17.5, "resets_at": 1793329200},
          "spend_limit": {"used_percentage": 3, "resets_at": 1}}
    check("both windows, as numbers; nothing else", m.limits({"rate_limits": rl}),
          {"five_hour": {"used_percentage": 18.0, "resets_at": 1792990800}, "seven_day": {"used_percentage": 17.5, "resets_at": 1793329200}})
    check("none before the first answer (no rate_limits)", m.limits({"session_id": "s"}), {})
    check("a window not a number: left out", m.limits({"rate_limits": {"five_hour": {"used_percentage": "18%", "resets_at": 1}}}), {})

    d = tempfile.mkdtemp()
    data = {"session_id": "s-1", "rate_limits": rl}
    check("noted, the first time", m.note(data, now=1000, folder=d), True)
    with open(os.path.join(d, "s-1.json")) as f:
        check("as the board reads it", json.load(f), {"at": 1000, "five_hour": {"used_percentage": 18.0, "resets_at": 1792990800},
                                                      "seven_day": {"used_percentage": 17.5, "resets_at": 1793329200}})
    check("the same again: not written", m.note(data, now=2000, folder=d), False)
    rl2 = dict(rl, five_hour={"used_percentage": 19, "resets_at": 1792990800})
    check("changed: written", m.note({"session_id": "s-1", "rate_limits": rl2}, now=3000, folder=d), True)
    check("a session id that is a path: refused", m.note({"session_id": "../x", "rate_limits": rl}, folder=d), False)
    old = os.path.join(d, "gone.json")
    with open(old, "w") as f:
        f.write("{}")
    os.utime(old, (0, 0))
    m.note({"session_id": "s-2", "rate_limits": rl}, folder=d)
    check("a file a week and a day old goes", sorted(os.listdir(d)), ["s-1.json", "s-2.json"])

    # The status line handed on to: the project's, else yours; never this one.
    home, proj = tempfile.mkdtemp(), tempfile.mkdtemp()
    os.makedirs(os.path.join(proj, ".claude"))
    with open(os.path.join(home, "settings.json"), "w") as f:
        json.dump({"statusLine": {"type": "command", "command": "echo yours"}}, f)
    w = {"workspace": {"project_dir": proj}}
    check("yours, when the project has none", m.next_line(w, home), "echo yours")
    with open(os.path.join(proj, ".claude", "settings.json"), "w") as f:
        json.dump({"statusLine": {"type": "command", "command": "echo the project's"}}, f)
    check("never the project's own (a pull could put a command there): yours", m.next_line(w, home), "echo yours")
    with open(os.path.join(proj, ".claude", "settings.json"), "w") as f:
        json.dump({"statusLine": {"type": "command", "command": "python3 /x/seat/hooks/usage_line.py"}}, f)
    check("never this one (yours instead)", m.next_line(w, home), "echo yours")

    # Whole: what it prints is what the status line you had prints, from the same input.
    with open(os.path.join(home, "settings.json"), "w") as f:
        json.dump({"statusLine": {"type": "command", "command": "python3 -c \"import sys,json; print('model', json.load(sys.stdin)['model'])\""}}, f)
    os.remove(os.path.join(proj, ".claude", "settings.json"))
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(dict(w, model="opus", session_id="s-3")).encode(),
                       capture_output=True, env=dict(os.environ, CLAUDE_CONFIG_DIR=home), timeout=20)
    check("it prints the status line you had, given what Claude Code sent", r.stdout.decode().strip(), "model opus")
    print("%d fail" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
