"""Board parity: every key /api/board serves, and which view shows it.

Principle: everything the Agent Room shows == everything /api/board serves == everything the kanban
shows. One truth, several views; a fact one view hides is drift. So every served key is classed here,
and test_views.py fails on a key that is served and not classed, or classed `kanban` and not read by
the kanban (or `cli` and not read by forge.py or the hook). A field no view reads is not served at
all (server.UNSERVED): parity is kept by subtracting, never by drawing more.

Classes, each with its evidence:
  kanban    the snippet of index.html / common.js / party.js (your agents as a party, which it loads) that reads it
            (the test greps for it)
  cli       the snippet of seat/tools/forge.py or the SessionStart hook (seat/hooks/board_ready_set.py)
            that reads it: /go's views of the same snapshot (the test greps them too)
  caller    computed per caller (`?me=`); the kanban is the owner's view, with no session, and draws
            the owner's equivalent (`col`, `busy`). forge.py and the hook read them.
  plumbing  identity, transport or change detection: no person reads it, a program does
"""

CLASSES = {"kanban", "cli", "caller", "plumbing"}

# Dicts keyed by data rather than by name: their keys are values, so the test classes the map,
# written `{}`, not each key. None at present.
KEYED = set()

# Served only on a fault, so the test's fixture cannot produce them. Still classed.
FAULTS = {"pick_error", "mail_error", "boards[].error"}

K, L, C, X = "kanban", "cli", "caller", "plumbing"
SERVED = {
    # -- the snapshot's own state
    "ver": (X, "the long-poll version (?v=N); no person reads it"),
    "at": (K, "lastAt = d.at"),
    "now": (X, "the server's clock, for a CLI's ages; the page ages from its own clock"),

    # -- the room's settings (/api/room): the room's page builds its scene by them; settings, not facts of the board
    "room": (X, "the Agent Room's settings, which its pages build the room by; no person reads them as the board's"),
    "room.env": (K, "roomOf(DATA).env"),   # your agents' pictures dressed as the room dresses them (party.js); room.html builds its scenery by it
    "room.dark": (X, "the owner's theme: the room's page takes it as its own"),
    "room.look": (X, "the owner's look: the room's page draws them with it"),
    "room.board": (X, "the board the wall shows (its switch): room.html draws that board's columns"),
    "room.music": (X, "the music on or muted: the pool club's DJ plays or not, the crowd dances or not, and your page's music with it"),
    "room.acts": (K, "roomOf(d).acts"),   # what each agent is up to between turns, as the room's page plays it (a doze, its phone, a chat, an errand, out by the pool): drawn on its picture and said on its card (party.js)
    "room.bino": (X, "the owner's binoculars up: the room's page has their figure hold them to its eyes"),
    "room.guest": (X, "the owner As a visitor (room.html's switch): their clicks and their body in the room are a visitor's, on every page of theirs and the room's"),
    "room.teleport": (X, "who may teleport into the room (its Teleport popup's Who can come): the room's page answers Visit by it"),
    "room.mix": (X, "the owner's mixer (each channel's level and mute): every page of the owner's plays by it, and the room's page gives it to Visit for the visitors"),
    "error": (K, "DATA.error"),
    "truncated": (K, "DATA.truncated"),
    "forge": (K, "DATA.forge"),
    "owner": (K, "DATA.owner"),
    "columns_error": (K, "DATA.columns_error"),

    # -- the boards: the home board, and one per other repo the agents work in (REPO_BOARDS); `board` is the one served
    "board": (K, "DATA.board"),
    "boards": (K, "DATA.boards"),
    "boards[].key": (K, "b.key"),
    "boards[].name": (K, "b.name"),
    "boards[].url": (X, "the board's GitHub page; its rows carry their own links"),
    "boards[].repos": (X, "which repos are on it, so a page tells which board a repo#N is on (common.js boardOf)"),
    "boards[].error": (K, "b.error"),
    "boards_error": (K, "DATA.boards_error"),

    # -- columns: the workflow (workflow.json), as the kanban and the room draw it
    "columns": (K, "DATA.columns"),
    "columns[].key": (K, "c.key"),
    "columns[].name": (K, "c.name"),
    "columns[].hint": (K, "c.hint"),
    "columns[].pick": (K, "c.pick"),
    "columns[].drop": (K, "c.drop"),
    "columns[].label": (K, "c.label"),
    "columns[].plan": (K, "c.plan"),
    "columns[].closed": (K, "c.closed"),
    "columns[].limit": (K, "c.limit"),
    "pick_error": (L, 'd.get("pick_error")'),

    # -- rows: the board
    "rows": (K, "d.rows"),
    "rows[].id": (K, "r.id"),
    "rows[].repo": (K, "r.repo"),
    "rows[].num": (K, "r.num"),
    "rows[].title": (K, "r.title"),
    "rows[].labels": (K, "r.labels"),
    "rows[].rank": (K, "r.rank"),
    "rows[].tier": (K, "r.tier"),
    "rows[].blocked": (K, "r.blocked"),
    "rows[].epic": (K, "r.epic"),
    "rows[].comments": (K, "r.comments"),
    "rows[].created": (K, "r.created"),
    "rows[].url": (K, "r.url"),
    "rows[].col": (K, "r.col"),
    "rows[].busy": (K, "r.busy"),
    "rows[].claimed": (C, "another agent's claim, as this caller sees it; the owner's is rows[].busy"),
    "rows[].why_not": (C, "pickability for this caller; the owner's view is rows[].col + busy"),

    # -- gone: rows that left the board lately: the 'Closed lately' column
    "gone": (K, "(DATA.gone || []).filter(g => !inPlan.has(g.id) && visible(g))"),
    "gone[].id": (K, "g.id"),
    "gone[].title": (K, "g.title"),
    "gone[].url": (K, "g.url"),
    "gone[].why": (K, "g.why"),
    "gone[].at": (K, "g.at"),
    "gone[].col": (K, "g.col"),
    "gone[].rank": (K, "g.rank"),
    "gone[].tier": (K, "g.tier"),
    # the filters read these through visible(), as they read the rows'
    "gone[].labels": (K, 'r.labels.join(" ")'),
    "gone[].epic": (K, 'r.epic && !$("#epics").checked'),

    # -- plan: Up next
    "plan": (K, "DATA.plan"),
    "plan.num": (K, "DATA.plan.num"),
    "plan.url": (K, "DATA.plan.url"),
    "plan.hash": (X, "the plan POST's base, so a stale reorder is refused; no person reads it"),
    "plan.steps": (K, "DATA.plan.steps"),
    "plan.steps[].ref": (K, "s.ref"),
    "plan.steps[].owner": (K, "s.owner"),
    "plan.steps[].text": (K, "s.text"),

    # -- agents: the roster strip
    "agents": (K, "DATA.agents"),
    "agents_error": (K, "DATA.agents_error"),
    "agents[].name": (K, "a.name"),
    "agents[].session": (K, "a.session"),
    "agents[].local": (X, "the agent's second session id, for pick.widen; no person reads it"),
    "agents[].msgname": (L, 'ag.get("msgname")'),
    "agents[].seat": (K, "a.seat"),
    "agents[].host": (K, "a.host"),
    "agents[].link": (K, "a.link"),
    "agents[].seen": (K, "a.seen"),
    "agents[].claims": (K, "a.claims"),
    "agents[].last": (K, "a.last"),
    "agents[].doing": (K, "a.doing"),
    "agents[].ctx": (K, "a.ctx"),
    "agents[].blocked": (K, "a.blocked"),
    "agents[].subs": (K, "a.subs"),
    "agents[].subs[].id": (X, "keeps each subagent at the same desk in the Agent Room between snapshots; no person reads it"),
    "agents[].subs[].type": (K, "x.type"),
    "agents[].subs[].what": (K, "x.what"),
    "agents[].working": (K, "a.working"),
    "agents[].board": (K, "a.board"),
    "agents[].dir": (K, "a.dir"),   # the directory the agent runs in (its hook's, ~ for home), over its picture (party.js's legend)
    "agents[].shell": (K, "a.shell"),
    "agents[].fresh": (K, "a.fresh"),
    "agents[].id": (K, "a.id"),
    "agents[].you": (C, "the caller's own entry; the owner has none"),

    # -- prs
    "prs": (K, "DATA.prs"),
    "prs_error": (K, "DATA.prs_error"),
    "prs[].id": (K, "p.id"),
    "prs[].title": (K, "p.title"),
    "prs[].url": (K, "p.url"),
    "prs[].author": (K, "p.author"),
    "prs[].comments": (K, "p.comments"),
    "prs[].draft": (K, "p.draft"),
    "prs[].link": (X, "the session link the server attributes the PR by; shown as prs[].owner"),
    "prs[].owner": (K, "p.owner"),
    "prs[].owner.state": (K, "o.state"),
    "prs[].owner.name": (K, "a ? a.name : p.author"),
    "prs[].owner.session": (K, "a ? avatar(a.session"),
    "prs[].owner.via": (K, "a.via"),
    "prs[].owner.was": (K, "o.was"),

    # -- messages
    # -- the account's usage limits, a row per bar /usage draws (bin/usage; else the agents' status lines: server.usage)
    "usage": (K, "DATA.usage"),
    "usage.limits": (K, "u.limits"),
    "usage.limits[].kind": (K, "w.kind"),
    "usage.limits[].group": (K, "w.group"),
    "usage.limits[].name": (K, "w.name"),
    "usage.limits[].used": (K, "w.used"),
    "usage.limits[].resets_at": (K, "w.resets_at"),
    "usage.limits[].passed": (K, "w.passed"),
    "usage.limits[].severity": (K, "w.severity"),
    "usage.at": (K, "u.at"),
    "usage.from": (K, "u.from"),

    "mail": (K, "DATA.mail"),
    "mail_error": (K, "DATA.mail_error"),
    "owner_unread": (K, "DATA.owner_unread"),
    "mail[].id": (K, "m.id"),
    "mail[].at": (K, "m.at"),
    "mail[].sender": (K, "m.sender"),
    "mail[].rcpt": (K, "m.rcpt"),
    "mail[].rcpt_name": (K, "m.rcpt_name"),
    "mail[].body": (K, "m.body"),
}
