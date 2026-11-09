#!/usr/bin/env python3
"""Puts two facts no forge.py call can see onto this session's roster entry, for the board's agent
rows and the Agent Room:

- `ctx`: the context in use when a turn ENDS (Stop), from the transcript's last main-chain reply:
  input + cache-read + cache-creation tokens. Tokens only: the transcript names the model without
  its window (`claude-opus-5-5`, never `[1m]`), so a percentage would be a guess.
- `blocked`: what the session waits on the owner for. Set when a dialog opens: an AskUserQuestion
  (PreToolUse) or a permission prompt (PermissionRequest). Cleared when THAT tool call ends
  (PostToolUse / PostToolUseFailure with the same tool and input, so a parallel tool finishing
  cannot clear another's open dialog), when he sends a prompt (UserPromptSubmit), or when the turn
  ends (Stop). ⚠ No event marks a permission GRANTED, so an approved tool shows `blocked` until it
  finishes; an Esc at the prompt shows it until his next prompt.
  Set too when a turn ENDS asking him something in its words, not in a dialog (Stop: the last
  sentence ending in "?" of its reply's last paragraph, `asked`): "Want me to file it as an issue,
  or go straight to building it?". That one is cleared only by his next prompt (or SessionEnd): a
  turn a background shell or agent wakes after it cannot have had his answer, so its Stop keeps the
  question unless it asks another.
- `subs`: the subagents it has working (Agent tool calls), each `{id, type, what}`, for the room's
  desks: added at SubagentStart, taken off at SubagentStop. A background one outlives its turn, so
  Stop does not clear the list; it RECONCILES it with the Stop payload's `background_tasks` (the
  in-flight work), which drops one whose SubagentStop was missed and adds one started before the
  hooks were. SessionEnd clears it. `what` is the Agent call's description, read from the
  subagent's meta file beside the transcript (`<session>/subagents/agent-<id>.meta.json`).
- `working`: when the turn under way began (UTC), '' between turns, for the room's typing: set at the
  prompt (UserPromptSubmit), or at the first tool end of the main thread in a turn something else began
  (a background agent's report wakes it); cleared when the turn ends (Stop, StopFailure, SessionEnd).
  ⚠ No event marks an Esc interrupt, so an interrupted turn stays `working` until the next one ends:
  the room types only for an agent that is also live (acted within 5 minutes), and a working session
  keeps itself live with a heartbeat: a tool end flushes when the last flush is BEAT_S old, and the
  flush moves `seen` when it is that old (`forge.py agent state --beat-after`).
- `dir`: the directory the session runs in (every payload's `cwd`, the home directory as `~`: `short_dir`), for the
  room's list of agents; flushed when it changes (the first event, and a `cd` of the session's own).
- leaving: at SessionEnd (an exit, or /clear beginning another conversation) the flush takes this
  session's entry off the roster, as /stop would (`--ended`). And every flush, a SessionStart's too,
  clears entries of this machine's sessions that are no longer running (forge.py reap): the roster
  heals itself of sessions that crashed or were closed without /stop.

The hook never waits on the forge: it records the wanted state in local files under
~/.cache/forge-board/state and starts a DETACHED flush (this file, `--flush <sid>`), which takes a lock,
reads the files as they are THEN and writes them with `forge.py agent state`. A later event's flush
waits for the earlier one and reads the newer files, so the forge ends on the last state whatever
order the writes finish in. Stop always flushes, so a write lost to a forge blip is resent at the
turn's end. Only a dialog opening or closing, a subagent starting or ending, a turn starting or ending
and the heartbeat start a flush; every other tool end costs this interpreter's start and a stat.
Silent and exit 0 on any failure: a board badge is never worth a stalled dialog or a broken turn."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

TAIL = 4 << 20  # a reply's usage line sits near the end; read the whole file only if the tail has none
STATE = os.path.join(os.path.expanduser("~"), ".cache", "forge-board", "state")
LOCK_STALE_S = 30  # a flush is one forge read and one PATCH; a lock older than this was left by a crash
BEAT_S = 240  # a working session's heartbeat: under the board's 300 s mark for idle
TURN = "turn"  # the `blocked` file's owner for a question a turn ended on (never a tool call's id)


def forge_py():
    here, home = os.path.dirname(os.path.realpath(__file__)), os.path.expanduser("~")
    for p in (os.path.join(here, "..", "tools", "forge.py"), os.path.join(home, ".claude", "tools", "forge.py")):
        if os.path.isfile(p):
            return p
    return None


def last_ctx(lines):
    """PURE: context in use at the last main-chain assistant reply with a real usage, or None. A
    synthetic reply (an API error, an interrupt) carries all-zero usage and is skipped."""
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        u = (e.get("message") or {}).get("usage")
        n = sum(int(u.get(k) or 0) for k in
                ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) if u else 0
        if n:
            return n
    return None


def from_transcript(path, fn):
    """fn(lines) over the transcript's tail, and over the whole file if the tail gives None; None on any failure."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            for start in ((max(0, size - TAIL), 0) if size > TAIL else (0,)):
                f.seek(start)
                lines = f.read().decode("utf-8", "replace").splitlines()
                n = fn(lines[1:] if start else lines)  # a cut first line is not JSON anyway
                if n is not None:
                    return n
    except Exception:
        pass
    return None


def transcript_ctx(path):
    return from_transcript(path, last_ctx)


def last_words(lines):
    """PURE: what the last main-chain assistant entry says (its text blocks), or '' when it says nothing (a
    turn that ended on a tool call, an interrupt): the turn's last words. None: no reply at all."""
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        c = (e.get("message") or {}).get("content")
        if isinstance(c, str):
            return c
        return "\n".join(b.get("text") or "" for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
    return None


ASKED_MAX = 160


def asked(text):
    """PURE: the question a turn's last words put to the owner, or None: the last sentence ending in "?" of
    their last paragraph (code blocks aside, markup taken off), so "Want me to file it, or build it?" and
    "Should I? Then I'll merge." count, and a reply that asked something earlier but ends on a statement
    does not. One line, at most ASKED_MAX characters."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"```.*?(```|$)", "\n\n", text, flags=re.S)
    paras = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
    if not paras:
        return None
    para = re.sub(r"(\*\*|__|`)", "", paras[-1])
    q, items = None, []
    for line in para.splitlines():  # a wrapped line goes on its sentence; a list item starts anew
        if items and not re.match(r"\s*(?:[-*+]|\d+[.)])\s", line):
            items[-1] += " " + line
        else:
            items.append(line)
    for line in items:
        # each sentence with the closers after its end: a "?" closed by a quote or a bracket is quoted or
        # an aside ('the reply was "how are you?"'), not his to answer
        bits = re.split(r"(?<=[.!?])([*_)\]\"'\u201d\u2019]*)\s+", line)
        last = re.search(r"[*_)\]\"'\u201d\u2019\s]*$", bits[-1])
        bits[-1:] = [bits[-1][:last.start()], last.group().strip()]
        for sent, closers in zip(bits[0::2], bits[1::2]):
            sent = " ".join(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", sent).split())
            if sent.endswith("?") and len(sent) > 1 and not re.search(r"[)\]\"'\u201d\u2019]", closers):
                q = sent
    if not q:
        return None
    return "question: " + (q if len(q) <= ASKED_MAX else q[:ASKED_MAX - 1].rstrip() + "\u2026")


def stop_question(p):
    """The question the turn that just ended (a Stop payload) asks in its words, or None. Claude Code's own
    `last_assistant_message` where the payload has it, else the transcript's last reply."""
    words = p.get("last_assistant_message")
    if not isinstance(words, str):
        words = from_transcript(p.get("transcript_path") or "", last_words)
    return asked(words)


def call_keys(p):
    """PURE: the ids a payload's tool call answers to, so its end clears only its own dialog: the
    harness's `tool_use_id` where the payload has one, and a hash of the tool and its input for a
    payload that may not (PermissionRequest)."""
    ti = p.get("tool_input") or {}
    if p.get("tool_name") == "AskUserQuestion" and isinstance(ti, dict):
        ti = ti.get("questions")  # its input comes back carrying the answers; the questions do not change
    blob = json.dumps([p.get("tool_name"), ti], sort_keys=True, ensure_ascii=False)
    return [k for k in (p.get("tool_use_id"), hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]) if k]


def blocked_text(p):
    """PURE: the `blocked` line for a hook payload that opens a dialog, else None."""
    ev, tool = p.get("hook_event_name"), p.get("tool_name") or "?"
    if ev == "PreToolUse" and tool == "AskUserQuestion":
        qs = (p.get("tool_input") or {}).get("questions") or [{}]
        q = qs[0] if isinstance(qs, list) and qs and isinstance(qs[0], dict) else {}
        return " ".join(("question: " + (q.get("question") or q.get("header") or "?")[:120]).split())
    if ev == "PermissionRequest" and tool != "AskUserQuestion":  # its PreToolUse already said the question
        return "permission: " + tool
    return None


def next_blocked(cur, p, question=None):
    """PURE: the `blocked` file after hook payload `p`, given what it holds (`cur`; None: no file) and, at a Stop,
    the question the turn ended on (`stop_question`): what it holds next, '' for no file, or None when `p` leaves
    it. Line 1 says whose it is (a dialog's call ids, or TURN), line 2 what it says. A dialog's goes when its
    call ends, at his prompt, at the turn's end, at SessionEnd; a turn's question only at his prompt or
    SessionEnd, or for another the next turn asks."""
    ev = p.get("hook_event_name")
    text, keys = blocked_text(p), None
    if text:
        keys = " ".join(call_keys(p))
    elif ev == "Stop" and question:
        text, keys = question, TURN
    if text:
        new = keys + "\n" + text
        return new if new != cur else None
    if cur is None:
        return None
    had = cur.split("\n", 1)[0].split()
    mine = ev in ("PostToolUse", "PostToolUseFailure") and set(call_keys(p)) & set(had)
    if mine or ev in ("UserPromptSubmit", "SessionEnd") or (ev == "Stop" and had != [TURN]):
        return ""
    return None


def paths(sid):
    return {k: os.path.join(STATE, "%s-%s" % (k, sid)) for k in ("blocked", "ctx", "subs", "working", "beat", "lock", "dir")}


def short_dir(cwd, home=None):
    """PURE: the directory a payload says the session runs in, the home directory as `~` (the roster is on GitHub: no
    user's name in it), or '' for none."""
    if not isinstance(cwd, str) or not cwd.strip():
        return ""
    cwd, home = os.path.normpath(cwd.strip()), os.path.normpath(home or os.path.expanduser("~"))
    if cwd == home:
        return "~"
    if cwd.startswith(home + os.sep):
        return "~" + os.sep + cwd[len(home) + 1:]
    return cwd


def next_working(cur, p, now):
    """PURE: the `working` stamp after hook payload `p`, given the stamp `cur` ('' between turns), or
    None when `p` does not change it. A subagent's tool ends (they carry its agent_id) are not the
    main thread working: its turn may have ended while a background agent works on."""
    ev = p.get("hook_event_name")
    if ev in ("Stop", "StopFailure", "SessionEnd"):
        return "" if cur else None
    starts = ev == "UserPromptSubmit" or (ev in ("PostToolUse", "PostToolUseFailure") and not p.get("agent_id"))
    return now if starts and not cur else None


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def meta_path(transcript, agent_id):
    """Where Claude Code keeps a subagent's meta (its description, its type), beside the transcript."""
    return os.path.join(os.path.splitext(transcript)[0], "subagents", "agent-%s.meta.json" % agent_id) \
        if transcript and agent_id else ""


def described(sub):
    """`sub` with its `what` filled from its meta file, when it has none yet and the file says one."""
    if sub.get("what") or not sub.get("meta"):
        return sub
    try:
        with open(sub["meta"], encoding="utf-8") as f:
            what = (json.load(f) or {}).get("description")
    except (OSError, ValueError, AttributeError):
        return sub
    return dict(sub, what=" ".join(str(what or "").split())[:120])


def next_subs(subs, p):
    """PURE: the subagent list after hook payload `p`, or None when `p` does not change it.
    Each is {id, type, what, meta}; `meta` is the local file `what` is read from, never sent."""
    ev, aid = p.get("hook_event_name"), p.get("agent_id")
    if ev == "SubagentStart" and aid:
        if any(s["id"] == aid for s in subs):
            return None
        return subs + [{"id": aid, "type": p.get("agent_type") or "agent", "what": "",
                        "meta": meta_path(p.get("transcript_path"), aid)}]
    if ev == "SubagentStop" and aid:
        out = [s for s in subs if s["id"] != aid]
        return out if len(out) != len(subs) else None
    if ev == "Stop" and isinstance(p.get("background_tasks"), list):
        tasks = {t.get("id"): t for t in p["background_tasks"]
                 if isinstance(t, dict) and t.get("type") == "subagent" and t.get("id")}
        out = [s for s in subs if s["id"] in tasks]
        have = {s["id"] for s in out}
        out += [{"id": k, "type": t.get("agent_type") or "agent",
                 "what": " ".join(str(t.get("description") or "").split())[:120], "meta": ""}
                for k, t in tasks.items() if k not in have]
        return out if out != subs else None
    if ev == "SessionEnd":
        return [] if subs else None
    return None


def sent(subs):
    """PURE: the roster's `subs` value: one line of JSON, the local `meta` left out; '' for none."""
    return json.dumps([{k: s.get(k) or "" for k in ("id", "type", "what")} for s in subs],
                      ensure_ascii=False, separators=(",", ":")) if subs else ""


def read_subs(path):
    try:
        out = json.loads(read(path) or "[]")
        return [s for s in out if isinstance(s, dict) and s.get("id")] if isinstance(out, list) else []
    except ValueError:
        return []


def retry(fn, *args):
    """Windows refuses a replace or remove while a flush has the file open for its read: retry,
    then give up quietly, so the flush still starts (Stop resends whatever this missed)."""
    for _ in range(10):
        try:
            return fn(*args)
        except FileNotFoundError:
            return None
        except PermissionError:
            time.sleep(0.05)
    return None


def write(path, text):
    tmp = path + ".%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    retry(os.replace, tmp, path)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def flush(sid, ended=False):
    """Detached: write the state files' CURRENT contents to the roster, one flush at a time; or, for
    a session that `ended`, take its entry off."""
    ps, f = paths(sid), forge_py()
    if not f:
        return
    deadline = time.time() + 2 * LOCK_STALE_S
    while True:
        try:
            os.close(os.open(ps["lock"], os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except OSError:  # exists; on Windows also PermissionError while its remove is pending
            try:
                if time.time() - os.path.getmtime(ps["lock"]) > LOCK_STALE_S:
                    os.remove(ps["lock"])
            except OSError:
                pass
        if time.time() > deadline:  # every miss, whatever it was: a lock that can never be made ends here
            return
        time.sleep(0.2)
    try:
        b = read(ps["blocked"])
        args = ["--blocked", b.split("\n", 1)[1] if b and "\n" in b else ""]
        ctx = read(ps["ctx"])
        if ctx:
            args += ["--ctx", ctx.strip()]
        # A subagent's meta file may not be written yet at its start: wait a little for it (the flush
        # is detached, so nobody waits with it), and send what there is.
        subs = read_subs(ps["subs"])
        for _ in range(15):
            subs = [described(x) for x in subs]
            if all(x.get("what") or not x.get("meta") or os.path.exists(x["meta"]) for x in subs):
                break
            time.sleep(0.2)
        args += ["--subs", sent([described(x) for x in subs])]
        args += ["--working", (read(ps["working"]) or "").strip(), "--beat-after", str(BEAT_S)]
        here = (read(ps["dir"]) or "").strip()
        if here:
            args += ["--dir", here]
        if ended:
            args += ["--ended"]
        subprocess.run([sys.executable, f, "agent", "state", "--hook-session", sid] + args,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=LOCK_STALE_S - 5, **no_window())
    finally:
        try:
            os.remove(ps["lock"])
        except OSError:
            pass
    prune()


def prune(days=7):
    """Ended sessions leave their small state files: drop any untouched for a week."""
    cut = time.time() - days * 86400
    for n in os.listdir(STATE):
        q = os.path.join(STATE, n)
        try:
            if n.split("-", 1)[0] in ("blocked", "ctx", "subs", "working", "beat", "lock", "dir") and os.path.getmtime(q) < cut:
                os.remove(q)
        except OSError:
            pass


def no_window():
    """Windows: a console program started from this DETACHED flush (it has no console to share)
    gets a NEW console, a visible window that flashes up and takes the owner's keystrokes, on every
    turn's end. CREATE_NO_WINDOW gives it a hidden one."""
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def spawn_flush(sid, ended=False):
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, os.path.realpath(__file__), "--flush-ended" if ended else "--flush", sid], **kw)


def main():
    try:
        p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return
    sid, ev = p.get("session_id"), p.get("hook_event_name")
    if not sid or not isinstance(sid, str) or not sid.replace("-", "").isalnum():
        return
    ps = paths(sid)
    os.makedirs(STATE, exist_ok=True)
    changed = False
    b = next_blocked(read(ps["blocked"]), p, stop_question(p) if ev == "Stop" else None)
    if b is not None:
        if b:
            write(ps["blocked"], b)
        else:
            retry(os.remove, ps["blocked"])
        changed = True
    subs = next_subs(read_subs(ps["subs"]), p)
    if subs is not None:
        write(ps["subs"], json.dumps([described(x) for x in subs], ensure_ascii=False))
        changed = True
    w = next_working((read(ps["working"]) or "").strip(), p, utcnow())
    if w is not None:
        if w:
            write(ps["working"], w)
        else:
            retry(os.remove, ps["working"])
        changed = True
    here = short_dir(p.get("cwd"))
    if here and (read(ps["dir"]) or "").strip() != here:
        write(ps["dir"], here)
        changed = True
    if ev == "Stop":
        n = transcript_ctx(p.get("transcript_path") or "")
        if n is not None and (read(ps["ctx"]) or "").strip() != str(n):
            write(ps["ctx"], str(n))
            changed = True
    try:
        beat = time.time() - os.path.getmtime(ps["beat"]) >= BEAT_S
    except OSError:
        beat = True
    if ev == "SessionEnd":
        spawn_flush(sid, ended=True)
        return
    # Any tool end, a subagent's too, shows the session is alive: the heartbeat. A session starting
    # flushes too: it is on the roster only after /go, but its flush clears sessions that have left.
    if changed or ev in ("Stop", "SessionStart") or (beat and ev in ("PostToolUse", "PostToolUseFailure")):
        # every turn's end resends, so a flush that failed is not left standing
        write(ps["beat"], "")
        spawn_flush(sid)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] in ("--flush", "--flush-ended"):
            flush(sys.argv[2], ended=sys.argv[1] == "--flush-ended")
        else:
            main()
    except Exception:
        pass
