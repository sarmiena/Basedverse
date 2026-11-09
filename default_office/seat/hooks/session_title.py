#!/usr/bin/env python3
"""SessionStart: title this Claude Code session with its board handle (`bold-lynx`), so the
session list, the terminal title and the kanban's AGENTS row name a session the same way. The handle is forge.py's own agent_name() of the LOCAL session id, the id
forge.py's my_name() names the roster entry from, so the two cannot disagree. A session that
already has a title (/rename, a resumed titled session) keeps it, except across a /clear: that is a
new local id, so a new agent and a new handle, and a title that is a handle (one this hook gave) is
replaced, as the roster entry is on the agent's next `forge.py agent` write. An owner's /rename is
kept even then. Inside a herdr pane the agent is
named by the same handle there, titled or not. Silent on any failure: a title is never worth a
broken start."""
import importlib.util
import json
import os
import subprocess
import sys


def forge():
    here, home = os.path.dirname(os.path.realpath(__file__)), os.path.expanduser("~")
    for p in (os.path.join(here, "..", "tools", "forge.py"), os.path.join(home, ".claude", "tools", "forge.py")):
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("forge", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    return None


def main():
    try:
        p = json.load(sys.stdin)
    except Exception:
        p = {}
    f = forge()
    # The LOCAL id, as forge.py's my_name() does: the bridge id is not up yet at SessionStart.
    sid = p.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not (f and sid):
        return
    handle = f.agent_name(sid)
    herdr_name(handle)
    title = p.get("session_title")
    if not title or (p.get("source") == "clear" and is_handle(f, title)):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "sessionTitle": handle}}))


def is_handle(f, title):
    """A title shaped like a board handle, i.e. one this hook gave, not the owner's own."""
    adj, sep, noun = str(title).partition("-")
    return bool(sep) and adj in f._ADJ and noun in f._NOUN


def herdr_name(handle):
    """Inside a herdr pane, name the agent there by its handle too, in place of herdr's plain
    "claude". Display-only metadata that holds while Claude is that pane's agent;
    herdr's own SessionStart hook does the same dance for the session id."""
    pane = os.environ.get("HERDR_PANE_ID")
    if os.environ.get("HERDR_ENV") != "1" or not pane:
        return
    try:
        subprocess.run([os.environ.get("HERDR_BIN_PATH") or "herdr", "pane", "report-metadata", pane,
                        "--source", "forge:handle", "--agent", "claude", "--display-agent", handle],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
