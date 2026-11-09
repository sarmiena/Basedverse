"""Cases for workflow.py: loading, which column a row is in, and what a move writes. No network.

Run: python3 board/test_workflow.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
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
    print("[%s] %-66s %s" % ("ok " if ok else "FAIL", label, repr(got)[:60]))


FLOW = workflow.validate({"columns": [
    {"key": "next", "name": "Up next", "plan": True},
    {"key": "ready", "name": "Ready", "default": True, "pick": True},
    {"key": "doing", "name": "In progress", "label": "In Progress",
     "when": {"any": [{"label": "In Progress"}, {"claimed": True}]}, "limit": 3},
    {"key": "review", "name": "In review", "when": {"pr": True}},
    {"key": "blocked", "name": "Blocked", "when": {"blocked": True}},
    {"key": "done", "name": "Closed", "closed": True},
]})


def at(labels=(), claimed=False, pr=False, blocked=False, epic=False, wf=FLOW):
    return workflow.column_of({"labels": list(labels), "claimed": claimed, "pr": pr, "blocked": blocked,
                               "epic": epic}, wf)


print("which column")
case("nothing special: the default", lambda: at(), "ready")
case("the column's label", lambda: at(["In Progress"]), "doing")
case("a claim (any: label or claimed)", lambda: at(claimed=True), "doing")
case("an open PR is further along than a claim", lambda: at(claimed=True, pr=True), "review")
case("blocked is rightmost, so it wins over a PR", lambda: at(pr=True, blocked=True), "blocked")
case("an unrelated label changes nothing", lambda: at(["bug"]), "ready")
case("a label column with no when holds its label",
     lambda: at(["later"], wf=workflow.validate({"columns": [
         {"key": "r", "default": True}, {"key": "p", "label": "later"}]})), "p")
case("not", lambda: workflow.holds({"not": {"label": "x"}}, {"labels": ["y"]}), True)
case("a label list is any of them", lambda: workflow.holds({"label": ["a", "b"]}, {"labels": ["b"]}), True)
case("every key must hold", lambda: workflow.holds({"label": "a", "pr": True}, {"labels": ["a"], "pr": False}), False)

print("\nloading")
case("no file: today's board", lambda: [c["key"] for c in workflow.load(None)["columns"]],
     ["next", "ready", "blocked", "done"])
case("the shipped workflow.json loads",
     lambda: [c["key"] for c in workflow.load(os.path.join(HERE, "..", "..", "workflow.json"))["columns"]],
     ["later", "next", "ready", "doing", "review", "input", "blocked", "done"])
case("and names its claim label", lambda: workflow.load(os.path.join(HERE, "..", "..", "workflow.json"))["claim_label"],
     "status: in progress")
bad = os.path.join(tempfile.mkdtemp(), "workflow.json")
open(bad, "w").write("{nope")
case("a broken file is an error, never quietly the default", lambda: workflow.load(bad), "JSONDecodeError")


def _refused_top(wf):
    try:
        workflow.validate(wf)
    except workflow.Invalid as e:
        return str(e)
    return ""


def refused(cols):
    try:
        workflow.validate({"columns": cols})
    except workflow.Invalid as e:
        return str(e)
    return None


case("two defaults", lambda: "exactly one" in refused([{"key": "a", "default": True}, {"key": "b", "default": True}]), True)
case("no default", lambda: "exactly one" in refused([{"key": "a", "label": "x"}]), True)
case("a duplicate key", lambda: "unique" in refused([{"key": "a", "default": True}, {"key": "a", "label": "x"}]), True)
case("a column with no rule", lambda: "give it" in refused([{"key": "a", "default": True}, {"key": "b"}]), True)
case("an unknown condition", lambda: "unknown condition" in refused(
    [{"key": "a", "default": True}, {"key": "b", "when": {"assignee": "me"}}]), True)
case("an unknown column key", lambda: "unknown key" in refused([{"key": "a", "default": True, "colour": "red"}]), True)
case("a _comment key is allowed", lambda: refused([{"key": "a", "default": True, "_doc": "x"}]), None)
case("claimed must be a bool", lambda: "true or false" in refused(
    [{"key": "a", "default": True}, {"key": "b", "when": {"claimed": "yes"}}]), True)
case("two plan columns", lambda: "at most one plan" in refused(
    [{"key": "a", "default": True}, {"key": "p", "plan": True}, {"key": "q", "plan": True}]), True)
case("two columns setting one label", lambda: "same label" in refused(
    [{"key": "a", "default": True}, {"key": "b", "label": "x"}, {"key": "c", "label": "x"}]), True)
case("a claim_label that is no column's label", lambda: "claim_label" in str(
    (lambda: workflow.validate({"claim_label": "x", "columns": [{"key": "a", "default": True}]}))
    if False else _refused_top({"claim_label": "x", "columns": [{"key": "a", "default": True}]})), True)
case("an unknown top-level key", lambda: "unknown key" in _refused_top({"colour": 1, "columns": [{"key": "a", "default": True}]}), True)
case("a WIP limit is a positive whole number", lambda: "limit" in refused([{"key": "a", "default": True, "limit": 0}]), True)

print("\nmoving")
case("to a label column: its label on, the others off",
     lambda: workflow.moved_labels(["bug", "In Progress"], FLOW, workflow.drop_target(FLOW, "doing")),
     ["bug", "In Progress"])
LAB = workflow.validate({"columns": [{"key": "todo", "default": True}, {"key": "doing", "label": "status: doing"},
                                     {"key": "review", "label": "status: review"}]})
case("one column label replaces another",
     lambda: workflow.moved_labels(["bug", "status: doing"], LAB, workflow.drop_target(LAB, "review")),
     ["bug", "status: review"])
case("to the default: every column label off",
     lambda: workflow.moved_labels(["status: doing", "x"], LAB, workflow.drop_target(LAB, "todo")), ["x"])
MIG = workflow.validate({"columns": [{"key": "todo", "default": True},
                                     {"key": "doing", "label": "status: doing", "when": {"any": [
                                         {"label": ["status: doing", "Old Doing"]}, {"claimed": True}]}},
                                     {"key": "bugs", "label": "bug-board", "when": {"not": {"label": "wontfix"}, "label": "bug-board"}}]})
case("an old label kept in a `when` (a migration) comes off on a move too",
     lambda: workflow.moved_labels(["Old Doing", "x"], MIG, workflow.drop_target(MIG, "todo")), ["x"])
case("a label under `not` only keeps cards out: a move leaves it",
     lambda: workflow.moved_labels(["wontfix", "status: doing"], MIG, workflow.drop_target(MIG, "todo")), ["wontfix"])
case("the labels the workflow places by", lambda: sorted(workflow.placing_labels(MIG)),
     ["Old Doing", "bug-board", "status: doing"])
case("a column is found by its name too", lambda: workflow.drop_target(FLOW, "in progress")["key"], "doing")
case("a computed column is not a drop target", lambda: workflow.drop_target(FLOW, "review"), "Invalid")
case("nor the plan", lambda: workflow.drop_target(FLOW, "next"), "Invalid")
case("nor closed", lambda: workflow.drop_target(FLOW, "done"), "Invalid")
case("nor a column that does not exist", lambda: workflow.drop_target(FLOW, "qa"), "Invalid")

print("\nserved")
case("what the board serves of a column", lambda: workflow.served(FLOW)[2],
     {"key": "doing", "name": "In progress", "hint": "", "pick": False, "drop": True, "label": "In Progress",
      "plan": False, "closed": False, "limit": 3})
case("the default is a drop target", lambda: workflow.served(FLOW)[1]["drop"], True)
case("`when` is not served", lambda: any("when" in c for c in workflow.served(FLOW)), False)

print("\nall pass" if not fails else "\n%d FAILED" % fails)
sys.exit(1 if fails else 0)
