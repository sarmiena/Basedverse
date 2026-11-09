#!/usr/bin/env python3
"""The agents' status line, by way of the board: notes the account's usage limits, then shows the status line
you had.

Claude Code hands every status line command the subscription's limits (`rate_limits`, for Pro and Max, once
a session has had its first answer): `five_hour`, the current session, and `seven_day`, this week, each with
`used_percentage` (0 to 100) and `resets_at` (epoch seconds); not the per-model weeks /usage also shows. No
hook gets them (code.claude.com/docs/en/statusline). The board's first source is bin/usage, which asks Claude
Code what /usage shows; these notes are what it falls back to when bin/usage is not running. Each run writes
this session's to `.usage/<session id>.json` beside bin/ (only when they changed, replaced whole):

    {"at": <epoch seconds>, "five_hour": {"used_percentage", "resets_at"}, "seven_day": {...}}

The board reads them all (server.usage) and shows the account's: they are one account's, and a session idle a
while says what it heard last, so for each window the board takes the latest reset and, of the sessions on it,
the highest use. A file a week and a day old goes.

Then it runs the status line you had, with what Claude Code sent, and prints what that prints: the project's
own statusLine (.claude/settings.json), else yours (~/.claude/settings.json). Nothing here may stop the status
line: anything that goes wrong is skipped. seat/install.py sets it as the project's statusLine in
settings.local.json, unless that has one of its own already.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
DIR = os.path.join(ROOT, ".usage")
KEEP_S = 8 * 86400   # a week and a day: past any window's reset
WINDOWS = ("five_hour", "seven_day")


def limits(data):
    """The windows Claude Code sent, as the board keeps them: {window: {used_percentage, resets_at}}."""
    rl = data.get("rate_limits") if isinstance(data, dict) else None
    out = {}
    for w in WINDOWS:
        x = rl.get(w) if isinstance(rl, dict) else None
        if isinstance(x, dict) and isinstance(x.get("used_percentage"), (int, float)) and isinstance(x.get("resets_at"), (int, float)):
            out[w] = {"used_percentage": float(x["used_percentage"]), "resets_at": int(x["resets_at"])}
    return out


def note(data, now=None, folder=DIR):
    """This session's limits to <folder>/<session>.json, when they changed; and files too old, gone."""
    now = time.time() if now is None else now
    got, sid = limits(data), str(data.get("session_id") or "")
    if not got or not sid or "/" in sid or sid.startswith("."):
        return False
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, sid + ".json")
    try:
        with open(path) as f:
            if {w: v for w, v in json.load(f).items() if w in WINDOWS} == got:
                return False
    except (OSError, ValueError, AttributeError):
        pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dict(got, at=int(now)), f)
    os.replace(tmp, path)
    for e in os.scandir(folder):
        try:
            if e.name.endswith(".json") and now - e.stat().st_mtime > KEEP_S:
                os.remove(e.path)
        except OSError:
            pass
    return True


def mine(cmd):
    return "usage_line.py" in (cmd or "")


def next_line(data, home=None):
    """The status line command you had: yours (your own settings.json); never this one. Never a checkout's own
    (.claude/settings.json): a pull or a PR's branch could put a command there, which this would run at once with a
    shell, mid-session, with nothing to review it."""
    home = home or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    for path in (os.path.join(home, "settings.json"),):
        try:
            with open(path) as f:
                sl = json.load(f).get("statusLine") or {}
        except (OSError, ValueError, AttributeError):
            continue
        cmd = sl.get("command") if isinstance(sl, dict) and sl.get("type", "command") == "command" else None
        if cmd and not mine(cmd):
            return cmd
    return None


def main():
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    try:
        note(data)
    except Exception:   # the board's note is extra; the status line is not
        pass
    cmd = next_line(data)
    if cmd:
        try:
            r = subprocess.run(cmd, shell=True, input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
            sys.stdout.buffer.write(r.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
