"""The board's workflow: its columns, which column each row is in, and what a move writes.
Pure, stdlib, no network. server.py loads it (workflow.json, beside the project .env) and serves
the columns; pick.py judges pickability from it; the kanban and the Agent Room draw what is served.

A column is one of:
  plan     THE Up next column: rows the RESUME issue lists, in its order (at most one)
  closed   THE Closed-lately column: rows that left the board lately (at most one)
  a rule   `when` says which rows it holds; exactly one rule column is the `default` and holds
           every row no other rule takes

PRECEDENCE: a row sits in the RIGHTMOST rule column whose `when` it meets, else in the default.
Further right is further along, so Blocked placed after In review means a blocked row with a PR
reads Blocked, and In review after In progress means a claimed row with a PR reads In review.

`when` is a dict whose keys must ALL hold:
  label: "x" or ["x", "y"]   the issue carries that label (any of the list)
  claimed: true|false        an agent on the roster claims it
  pr: true|false             an open PR names it (its title, or the first ref in its body)
  blocked: true|false        it has an open blocked-by edge
  epic: true|false           its title starts "Epic:"
  any: [when, ...]           at least one of these holds
  not: when                  that does not hold
A column with a `label` and no `when` holds the rows carrying that label.

MOVING: a column with a `label` is a drop target, and so is the default column. A move sets the
target's label and removes every other label the workflow places cards by (placing_labels: the
columns' labels and the labels their `when`s ask for), so an old label kept for a migration goes too.
Whether the row then SHOWS there is still the rules' call: a claimed row stays In progress.

`claim_label` (top level, optional): the label an agent's claim puts on the issue and its release
(or the owner's × on a dead agent) takes off, so GitHub itself shows the issue is taken. It must be
one of the columns' labels, so a move can also take it off.
"""
import json
import os

KEYS = {"key", "name", "hint", "plan", "closed", "default", "when", "label", "pick", "limit"}
PREDICATES = {"label", "claimed", "pr", "blocked", "epic", "any", "not"}

# The board with no workflow.json: today's columns.
DEFAULT = {"columns": [
    {"key": "next", "name": "Up next", "plan": True, "hint": "What a bare /go picks, top first."},
    {"key": "ready", "name": "Ready", "default": True, "pick": True,
     "hint": "Open, unclaimed, unblocked. Highest rank first."},
    {"key": "blocked", "name": "Blocked", "when": {"blocked": True}, "hint": "Waiting on an open blocked-by edge."},
    {"key": "done", "name": "Closed lately", "closed": True, "hint": "Left the board in the last 30 min."},
]}


class Invalid(ValueError):
    """workflow.json says something the board cannot run: the message says what and where."""


def _check_when(w, where):
    if not isinstance(w, dict) or not w:
        raise Invalid("%s: `when` must be a non-empty object" % where)
    for k, v in w.items():
        if k not in PREDICATES:
            raise Invalid("%s: unknown condition %r (one of: %s)" % (where, k, ", ".join(sorted(PREDICATES))))
        if k == "label" and not (isinstance(v, str) or (isinstance(v, list) and v and all(isinstance(x, str) for x in v))):
            raise Invalid("%s: label is a name or a list of names" % where)
        if k in ("claimed", "pr", "blocked", "epic") and not isinstance(v, bool):
            raise Invalid("%s: %s is true or false" % (where, k))
        if k == "any":
            if not isinstance(v, list) or not v:
                raise Invalid("%s: any is a non-empty list of conditions" % where)
            for i, x in enumerate(v):
                _check_when(x, "%s.any[%d]" % (where, i))
        if k == "not":
            _check_when(v, where + ".not")


def validate(wf):
    """`wf` with every column normalised (all keys present), or Invalid naming the problem."""
    if not isinstance(wf, dict) or not isinstance(wf.get("columns"), list) or not wf["columns"]:
        raise Invalid("workflow: want {\"columns\": [ ... ]} with at least one column")
    cols, seen = [], set()
    for i, c in enumerate(wf["columns"]):
        where = "columns[%d]" % i
        if not isinstance(c, dict):
            raise Invalid("%s: a column is an object" % where)
        unknown = set(c) - KEYS - {k for k in c if k.startswith("_")}
        if unknown:
            raise Invalid("%s: unknown key(s) %s (one of: %s)" % (where, ", ".join(sorted(unknown)), ", ".join(sorted(KEYS))))
        key = c.get("key")
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise Invalid("%s: every column needs a unique `key`" % where)
        seen.add(key)
        where = "column %r" % key
        kinds = [k for k in ("plan", "closed", "default") if c.get(k)]
        if len(kinds) > 1:
            raise Invalid("%s: a column is at most one of plan / closed / default" % where)
        if kinds and kinds[0] in ("plan", "closed") and (c.get("when") or c.get("label") or c.get("pick")):
            raise Invalid("%s: a %s column takes no when / label / pick" % (where, kinds[0]))
        if c.get("when") is not None:
            if c.get("default"):
                raise Invalid("%s: the default column holds what no rule takes; it has no `when`" % where)
            _check_when(c["when"], where)
        label = c.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise Invalid("%s: label is a label name" % where)
        limit = c.get("limit")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise Invalid("%s: limit is a whole number of cards, 1 or more" % where)
        when = c.get("when") or ({"label": label} if label and not c.get("default") else None)
        if not kinds and when is None:
            raise Invalid("%s: give it a `when`, a `label`, or make it the plan / closed / default column" % where)
        cols.append({"key": key, "name": str(c.get("name") or key), "hint": str(c.get("hint") or ""),
                     "plan": bool(c.get("plan")), "closed": bool(c.get("closed")), "default": bool(c.get("default")),
                     "when": when, "label": label, "pick": bool(c.get("pick")), "limit": limit})
    for kind in ("plan", "closed"):
        if sum(c[kind] for c in cols) > 1:
            raise Invalid("workflow: at most one %s column" % kind)
    if sum(c["default"] for c in cols) != 1:
        raise Invalid("workflow: exactly one column is the `default` (it holds every row no rule takes)")
    labels = [c["label"] for c in cols if c["label"]]
    if len(labels) != len(set(labels)):
        raise Invalid("workflow: two columns set the same label")
    claim = wf.get("claim_label")
    if claim is not None and claim not in labels:
        raise Invalid("workflow: claim_label %r is not any column's label (%s)" % (claim, ", ".join(labels) or "none"))
    unknown = {k for k in wf if not k.startswith("_")} - {"columns", "claim_label"}
    if unknown:
        raise Invalid("workflow: unknown key(s) %s (columns, claim_label)" % ", ".join(sorted(unknown)))
    return {"columns": cols, "claim_label": claim}


def load(path):
    """The validated workflow at `path`, or DEFAULT when there is no such file. Invalid / ValueError
    (bad JSON) when the file is there but wrong: a typo must be said, not quietly replaced."""
    if not path or not os.path.isfile(path):
        return validate(DEFAULT)
    with open(path, encoding="utf-8") as f:
        return validate(json.load(f))


def holds(when, facts):
    """Whether `when` holds for a row's facts {labels, claimed, pr, blocked, epic}."""
    for k, v in when.items():
        if k == "label":
            names = [v] if isinstance(v, str) else v
            if not any(n in facts["labels"] for n in names):
                return False
        elif k == "any":
            if not any(holds(x, facts) for x in v):
                return False
        elif k == "not":
            if holds(v, facts):
                return False
        elif bool(facts[k]) != v:
            return False
    return True


def rule_columns(wf):
    return [c for c in wf["columns"] if not c["plan"] and not c["closed"]]


def column_of(facts, wf):
    """The key of the rule column a row with these facts is in: the rightmost whose `when` holds,
    else the default."""
    cols = rule_columns(wf)
    for c in reversed(cols):
        if c["when"] and holds(c["when"], facts):
            return c["key"]
    return next(c["key"] for c in cols if c["default"])


def column(wf, key):
    """The column with key `key`, also found by its name (case-insensitive), or None."""
    k = str(key or "").strip().lower()
    return next((c for c in wf["columns"] if c["key"].lower() == k or c["name"].lower() == k), None)


def drop_target(wf, key):
    """The column a card may be moved to by `key` (or name), or Invalid saying why not."""
    c = column(wf, key)
    if not c:
        raise Invalid("no column %r (one of: %s)" % (key, ", ".join(x["key"] for x in rule_columns(wf))))
    if c["plan"]:
        raise Invalid("%s is the plan: add to it with the + on a card, or `forge.py resume -F`" % c["name"])
    if c["closed"]:
        raise Invalid("%s is closed work: close the issue instead (`forge.py close`)" % c["name"])
    if not c["label"] and not c["default"]:
        raise Invalid("%s is computed (%s), not set by hand" % (c["name"], describe(c["when"])))
    return c


def placing_labels(wf):
    """Every label the workflow PLACES cards by: each column's drop label, and each label its `when`
    asks for (inside `any` too, never inside `not`, which only keeps cards out). An old label kept in
    a column's `when` during a migration (`"label": ["status: in progress", "In Progress"]`) is one."""
    def found(w):
        out = set()
        for k, v in (w or {}).items():
            if k == "label":
                out |= {v} if isinstance(v, str) else set(v)
            elif k == "any":
                for x in v:
                    out |= found(x)
        return out
    out = set()
    for c in rule_columns(wf):
        out |= ({c["label"]} if c["label"] else set()) | found(c["when"])
    return out


def moved_labels(labels, wf, target):
    """PURE: an issue's labels once moved to column `target`: every label the workflow places cards
    by comes off (placing_labels), so none can hold the card in its old column, and the target's
    goes on. Every other label stays. Order kept."""
    ours = placing_labels(wf)
    out = [n for n in labels if n not in ours]
    if target["label"]:
        out.append(target["label"])
    return out


def describe(when):
    """A short reading of a `when`, for refusals and hints."""
    parts = []
    for k, v in when.items():
        if k == "label":
            parts.append("label " + (v if isinstance(v, str) else " or ".join(v)))
        elif k == "any":
            parts.append("(" + " or ".join(describe(x) for x in v) + ")")
        elif k == "not":
            parts.append("not " + describe(v))
        else:
            parts.append(("" if v else "not ") + {"claimed": "claimed by an agent", "pr": "named by an open PR",
                                                   "blocked": "blocked", "epic": "an epic"}[k])
    return " and ".join(parts)


def served(wf):
    """The columns as /api/board serves them: what the kanban and the room draw. `when` stays here."""
    return [{"key": c["key"], "name": c["name"], "hint": c["hint"], "pick": c["pick"],
             "drop": bool(c["label"]) or c["default"], "label": c["label"], "plan": c["plan"],
             "closed": c["closed"], "limit": c["limit"]} for c in wf["columns"]]
