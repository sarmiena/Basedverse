"""Who can pick each row: THE rule /go reads. Pure, stdlib, no network.

server.py serves what this computes on /api/board; `forge.py board` and the SessionStart hook
(seat/hooks/board_ready_set.py) only print it. Nothing else may decide pickability: a second copy
is how a kanban's Ready column and an agent's ready set come to mean two things.

The workflow (workflow.py) says which column each row is in and which columns agents pick from.
Some answers depend on WHO asks: a claim of your own is not a wall to you. So `view()` takes the
caller's ids and runs per request; with none, it is the owner's view, where every claim counts.
"""
import workflow


def is_me(agent, me):
    """An entry records both its ids (bridge and local), so a caller holding either is that agent."""
    return bool(set(me) & {agent.get("session"), agent.get("local")} - {None, ""})


def widen(me, agents):
    """Every id of the caller. A caller may know only ONE of its ids (a hook sees the local id, never
    the bridge id), while a claim records whichever id forge.py had. The caller's roster entry holds
    both, so widen through it. THE one place this is done: pickability and the served `you` flag both
    call it, so they cannot disagree on who the caller is."""
    me = set(me) - {None, ""}
    for d in agents:
        if is_me(d, me):
            me |= {d.get("session"), d.get("local")} - {None, ""}
    return me


def claims(agents, skip=()):
    """{ref: holder name} over the roster, skipping the agents `skip` names (the caller's ids). On a
    double claim the LAST (stalest) wins, as forge.py did; agents arrive freshest first."""
    return {c: d.get("name") or d.get("session") or "?"
            for d in agents if not is_me(d, skip) for c in d.get("claims") or []}


# ⚠ THE RULE /go READS. The first reason that holds is the row's why_not; None means pickable.
# Order is precedence: a claimed epic reads "claimed", not "container". Each takes (row, ctx) where
# ctx = {"claimed": {id: name}, "mine": {id}, "pick": {column key: (pickable?, name)}}.
RULES = [
    lambda r, c: c["claimed"].get(r["id"]) and "claimed by %s" % c["claimed"][r["id"]],
    lambda r, c: r["epic"] and "container",
    lambda r, c: r["blocked"] and "blocked",
    # Your own claim is yours to resume wherever the workflow shows it (a claim puts it In progress).
    lambda r, c: (r["id"] not in c["mine"] and not c["pick"].get(r["col"], (False, r["col"]))[0]
                  and "in %s" % c["pick"].get(r["col"], (False, r["col"]))[1]),
]


def why_not(r, ctx):
    for rule in RULES:
        why = rule(r, ctx)
        if why:
            return why
    return None


def columns(rows, agents, pr_refs, wf):
    """`rows` as copies, each with its kanban `col` (workflow.column_of: the rightmost rule column
    that takes it) and `busy` (`claimed by X`, or None). The owner's view, the same for every caller,
    so server.py judges it ONCE per snapshot and keeps it on the rows: a later fault in view() never
    costs the columns, and a row that drops off the board still says which column it left from.
    `pr_refs` is every `repo#N` an open PR names."""
    held = claims(agents)
    out = []
    for r in rows:
        facts = {"labels": r["labels"], "claimed": r["id"] in held, "pr": r["id"] in pr_refs,
                 "blocked": bool(r["blocked"]), "epic": r["epic"]}
        out.append(dict(r, col=workflow.column_of(facts, wf),
                        busy="claimed by %s" % held[r["id"]] if r["id"] in held else None))
    return out


def _order(r):
    """THE order of rows, here and on the kanban and in the room: highest rank first, then oldest first
    (a first-in, first-out queue within each rank), then repo and number to break a tie."""
    return (r["tier"], r.get("created") or "", r["repo"], r["num"])


def view(rows, agents, me, wf):
    """Copies of `rows` (each already with its `col`) for the caller `me` (a set of session ids), in
    print order, each with `unblocks` (open rows it blocks), `claimed` (holder's name when ANOTHER
    agent claims it) and `why_not` (the reason it is not pickable for this caller, or None)."""
    me = widen(me, agents)
    claimed = claims(agents, skip=me)
    mine = {c for d in agents if is_me(d, me) for c in d.get("claims") or []}
    pick = {c["key"]: (c["pick"], c["name"]) for c in workflow.rule_columns(wf)}
    unblocks = {}
    for r in rows:
        for t in r["blocked"]:
            unblocks[t] = unblocks.get(t, 0) + 1
    out = []
    for r in sorted(rows, key=_order):
        r = dict(r, unblocks=unblocks.get(r["id"], 0), claimed=claimed.get(r["id"]))
        r["why_not"] = why_not(r, {"claimed": claimed, "mine": mine, "pick": pick})
        out.append(r)
    return out
