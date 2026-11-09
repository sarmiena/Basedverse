#!/usr/bin/env python3
"""Pure cases for agent_state.py: the context sum and the `blocked` text. python3 test_agent_state.py"""
import importlib.util
import json
import os
import pathlib
import sys

spec = importlib.util.spec_from_file_location("agent_state", pathlib.Path(__file__).with_name("agent_state.py"))
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)
bad = 0


def case(label, got, want):
    global bad
    ok = got == want
    bad += not ok
    print("%s  %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else "\n      got  %r\n      want %r" % (got, want)))


def reply(u, side=False):
    return json.dumps({"type": "assistant", "isSidechain": side, "message": {"usage": u}})


U = {"input_tokens": 3, "cache_read_input_tokens": 100000, "cache_creation_input_tokens": 2413, "output_tokens": 50}
case("ctx = input + cache read + cache creation, output excluded", st.last_ctx([reply(U)]), 102416)
case("the LAST main-chain reply wins over a later sidechain one",
     st.last_ctx([reply({"input_tokens": 1}), reply(U), reply({"input_tokens": 9}, side=True), '{"type":"user"}', "not json"]),
     102416)
case("no reply: None", st.last_ctx(['{"type":"user"}']), None)
case("a synthetic all-zero reply (API error) is skipped",
     st.last_ctx([reply(U), reply({"input_tokens": 0, "cache_read_input_tokens": 0})]), 102416)
Q = {"questions": [{"question": "Which first?", "header": "Scope"}]}
case("a question's end matches its open by tool_use_id though the input comes back changed",
     bool(set(st.call_keys({"tool_name": "AskUserQuestion", "tool_use_id": "toolu_1", "tool_input": Q})) &
          set(st.call_keys({"tool_name": "AskUserQuestion", "tool_use_id": "toolu_1",
                            "tool_input": dict(Q, answers={"Which first?": "a"})}))), True)
case("with no tool_use_id, a question still matches its end by its questions",
     bool(set(st.call_keys({"tool_name": "AskUserQuestion", "tool_input": Q})) &
          set(st.call_keys({"tool_name": "AskUserQuestion", "tool_input": dict(Q, answers={"Which first?": "a"})}))), True)
case("a permission prompt with no tool_use_id matches its tool end by tool + input",
     bool(set(st.call_keys({"tool_name": "Bash", "tool_input": {"command": "ls"}})) &
          set(st.call_keys({"tool_name": "Bash", "tool_use_id": "toolu_2", "tool_input": {"command": "ls"}}))), True)
case("another tool's end does not match",
     bool(set(st.call_keys({"tool_name": "Bash", "tool_use_id": "toolu_3", "tool_input": {"command": "ls"}})) &
          set(st.call_keys({"tool_name": "Bash", "tool_use_id": "toolu_4", "tool_input": {"command": "pwd"}}))), False)
case("a question's own permission prompt does not overwrite its text",
     st.blocked_text({"hook_event_name": "PermissionRequest", "tool_name": "AskUserQuestion"}), None)
case("a multi-line question collapses to one line",
     st.blocked_text({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                      "tool_input": {"questions": [{"question": "Pick\n  one "}]}}), "question: Pick one")
case("a question names itself",
     st.blocked_text({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                      "tool_input": {"questions": [{"question": "Which first?", "header": "Scope"}]}}),
     "question: Which first?")
case("a permission prompt names its tool",
     st.blocked_text({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}), "permission: Bash")
case("any other tool opens nothing",
     st.blocked_text({"hook_event_name": "PreToolUse", "tool_name": "Bash"}), None)
case("a malformed question still blocks",
     st.blocked_text({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": {"questions": "x"}}),
     "question: ?")
# A question a turn ends on, in its words (no dialog): waiting on the owner too.
VOLE = ("When the dialer says \"Calls from (714) 555-0188 aren't recorded\", an admin would\nhave one obvious place to look.\n\n"
        "The old address would redirect to the new tab. It's a small change.\n\n"
        "Want me to file it as an issue with this as the recommendation, or go straight to\nbuilding it?")
case("a turn ending on a question asks it (indigo-vole's)", st.asked(VOLE),
     "question: Want me to file it as an issue with this as the recommendation, or go straight to building it?")
case("a question then a statement in the last paragraph still asks",
     st.asked("Done.\n\nShould I merge it? Then I'll tag the release."), "question: Should I merge it?")
case("a question earlier, a statement last: asks nothing",
     st.asked("Want the long version?\n\nI'll keep it short: it's fixed."), None)
case("markup and inline code come off",
     st.asked("**Are you on `localhost:3001`, or on the tailnet?**"), "question: Are you on localhost:3001, or on the tailnet?")
case("a list item's question", st.asked("Two things:\n- the docs\n- Should I also bump the version?"),
     "question: Should I also bump the version?")
case("a quoted question is not one put to him",
     st.asked('Every chunk was answer text ("Hello, how are you?") and nothing else.'), None)
case("nor a quoted one at the very end", st.asked('She only said "why?"'), None)
case("an aside's question neither", st.asked("It ships tonight (or should it wait?)"), None)
case("a ? in a code block is not a question", st.asked("Run this:\n\n```\nls foo?\n```"), None)
case("nor a ? inside a link", st.asked("See https://x.example/a?b=1 for the rest."), None)
case("a long one is cut to ASKED_MAX", len(st.asked("Why " + "x" * 400 + "?")), len("question: ") + st.ASKED_MAX)
case("no words: nothing", (st.asked(""), st.asked(None)), (None, None))
said = lambda txt, side=False: json.dumps({"type": "assistant", "isSidechain": side,
                                          "message": {"content": [{"type": "text", "text": txt}]}})
used = json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}})
case("the last words: the last main-chain reply's text, a later subagent's aside",
     st.last_words([said("Old?"), said("Build it?"), said("Sub done?", side=True), '{"type":"system"}']), "Build it?")
case("a turn that ended on a tool call said nothing last", st.last_words([said("Old?"), used]), "")
case("no reply at all: None", st.last_words(['{"type":"user"}']), None)
stop = {"hook_event_name": "Stop"}
QD = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "toolu_9", "tool_input": Q}
dialog = st.next_blocked(None, QD)
turn = st.next_blocked(None, stop, "question: Build it?")
case("a Stop that asks: waiting, the turn's", turn, "turn\nquestion: Build it?")
case("a Stop that asks nothing, none before: nothing", st.next_blocked(None, stop, None), None)
case("a dialog still goes at the turn's end", st.next_blocked(dialog, stop, None), "")
case("a dialog the turn's end asks after: the question replaces it", st.next_blocked(dialog, stop, "question: Build it?"), turn)
case("a turn's question outlives a woken turn's Stop that asks nothing", st.next_blocked(turn, stop, None), None)
case("another question replaces it", st.next_blocked(turn, stop, "question: Tag it?"), "turn\nquestion: Tag it?")
case("the same question again changes nothing", st.next_blocked(turn, stop, "question: Build it?"), None)
case("his prompt answers it", st.next_blocked(turn, {"hook_event_name": "UserPromptSubmit"}), "")
case("SessionEnd takes it", st.next_blocked(turn, {"hook_event_name": "SessionEnd"}), "")
case("a tool ending in a woken turn leaves it",
     st.next_blocked(turn, {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_1", "tool_input": {}}), None)
case("a dialog in a woken turn takes over", st.next_blocked(turn, QD), dialog)
case("its call's end clears the dialog",
     st.next_blocked(dialog, dict(QD, hook_event_name="PostToolUse")), "")
case("Claude Code's last_assistant_message is read first",
     st.stop_question({"hook_event_name": "Stop", "last_assistant_message": "Ship it?", "transcript_path": "/nonexistent"}),
     "question: Ship it?")

# dir: where it runs, home as ~.
case("a directory under home: ~/...", st.short_dir("/home/u/work/WebApp-2/", home="/home/u"), "~/work/WebApp-2")
case("home itself: ~", st.short_dir("/home/u", home="/home/u"), "~")
case("elsewhere: as it is", st.short_dir("/srv/app", home="/home/u"), "/srv/app")
case("not one that only starts like home", st.short_dir("/home/user2/x", home="/home/u"), "/home/user2/x")
case("none: ''", (st.short_dir(None), st.short_dir("  ")), ("", ""))

# subs: the subagents a session has working.
T = "/home/u/.claude/projects/-p/sess-1.jsonl"
start = lambda aid, typ="general-purpose": {"hook_event_name": "SubagentStart", "agent_id": aid, "agent_type": typ,
                                            "transcript_path": T}
one = st.next_subs([], start("a1"))
case("a start adds it, its meta beside the transcript",
     one, [{"id": "a1", "type": "general-purpose", "what": "", "meta": "/home/u/.claude/projects/-p/sess-1/subagents/agent-a1.meta.json"}])
case("the same start twice changes nothing", st.next_subs(one, start("a1")), None)
two = st.next_subs(one, start("b2", "Explore"))
case("a stop takes off only its own", [x["id"] for x in st.next_subs(two, {"hook_event_name": "SubagentStop", "agent_id": "a1"})], ["b2"])
case("a stop of one not on the list changes nothing", st.next_subs(two, {"hook_event_name": "SubagentStop", "agent_id": "zz"}), None)
case("Stop without background_tasks (an older Claude Code) keeps the list", st.next_subs(two, {"hook_event_name": "Stop"}), None)
bg = {"hook_event_name": "Stop", "background_tasks": [
    {"id": "b2", "type": "subagent", "status": "running", "description": "Find callers", "agent_type": "Explore"},
    {"id": "c3", "type": "subagent", "status": "running", "description": "Review  the\ndiff", "agent_type": "general-purpose"},
    {"id": "sh1", "type": "shell", "status": "running", "description": "npm test"}]}
case("Stop reconciles: a finished one goes (its SubagentStop missed), a running one unseen comes, a shell is not one",
     [(x["id"], x["what"]) for x in st.next_subs(two, bg)], [("b2", ""), ("c3", "Review the diff")])
case("Stop with nothing in flight empties it", st.next_subs(two, dict(bg, background_tasks=[])), [])
case("SessionEnd empties it", st.next_subs(two, {"hook_event_name": "SessionEnd"}), [])
case("SessionEnd with none changes nothing", st.next_subs([], {"hook_event_name": "SessionEnd"}), None)
case("a tool end is not a subagent event", st.next_subs(two, {"hook_event_name": "PostToolUse", "agent_id": "a1"}), None)
case("the line sent leaves the local meta path out",
     st.sent([dict(one[0], what="Trace callers")]), '[{"id":"a1","type":"general-purpose","what":"Trace callers"}]')
case("none: an empty line", st.sent([]), "")
import tempfile
d = tempfile.mkdtemp()
m = os.path.join(d, "agent-a1.meta.json")
with open(m, "w") as fh:
    json.dump({"agentType": "general-purpose", "description": "Adversarial  review of fix-93"}, fh)
case("its what is read from the meta file", st.described({"id": "a1", "what": "", "meta": m})["what"], "Adversarial review of fix-93")
case("no meta file yet: what stays empty", st.described({"id": "a1", "what": "", "meta": m + ".no"})["what"], "")

# working: when the turn under way began.
NOW = "2026-10-24T06:30:00Z"
case("a prompt starts the turn", st.next_working("", {"hook_event_name": "UserPromptSubmit"}, NOW), NOW)
case("a prompt mid-turn keeps the turn's start", st.next_working("T0", {"hook_event_name": "UserPromptSubmit"}, NOW), None)
case("a main-thread tool end starts a turn a report woke", st.next_working("", {"hook_event_name": "PostToolUse"}, NOW), NOW)
case("a failed tool end too", st.next_working("", {"hook_event_name": "PostToolUseFailure"}, NOW), NOW)
case("a subagent's tool end is not its agent working",
     st.next_working("", {"hook_event_name": "PostToolUse", "agent_id": "a1"}, NOW), None)
case("a tool end mid-turn changes nothing", st.next_working("T0", {"hook_event_name": "PostToolUse"}, NOW), None)
for ev in ("Stop", "StopFailure", "SessionEnd"):
    case("%s ends the turn" % ev, st.next_working("T0", {"hook_event_name": ev}, NOW), "")
case("Stop between turns changes nothing", st.next_working("", {"hook_event_name": "Stop"}, NOW), None)
case("a subagent starting is not a turn starting", st.next_working("", {"hook_event_name": "SubagentStart", "agent_id": "a1"}, NOW), None)


# The flush is DETACHED (no console), so on Windows each console program it starts gets a NEW,
# VISIBLE window unless told otherwise: a flash on the owner's screen at every turn's end. Spawned
# exactly as the flush is, a child started with no_window() must have no visible console.
def child_window_visible():
    import subprocess, tempfile, time
    d = tempfile.mkdtemp()
    out, child, parent = (os.path.join(d, n) for n in ("out.txt", "child.py", "parent.py"))
    with open(child, "w") as fh:
        fh.write("import ctypes, sys\n"
                 "k = ctypes.windll.kernel32; u = ctypes.windll.user32; h = k.GetConsoleWindow()\n"
                 "open(sys.argv[1], 'w').write(str(bool(h and u.IsWindowVisible(h))))\n")
    with open(parent, "w") as fh:
        fh.write("import subprocess, sys, importlib.util\n"
                 "s = importlib.util.spec_from_file_location('a', %r)\n"
                 "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                 "subprocess.run([sys.executable, %r, %r], **m.no_window())\n" % (spec.origin, child, out))
    subprocess.Popen([sys.executable, parent], creationflags=0x00000008 | 0x00000200,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if os.path.exists(out) and open(out).read():
            return open(out).read()
        time.sleep(0.1)
    return "no answer"


if os.name == "nt":
    case("the flush's child gets no visible console window", child_window_visible(), "False")

sys.exit(1 if bad else 0)
