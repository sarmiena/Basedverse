"""Rows that left the board, and why. Pure, stdlib, no network.

A row that leaves the snapshot does not say whether it CLOSED or was taken off the board, and no
reader can tell from the snapshot alone. server.py reads each dropped row's issue ONCE and keeps a
short list here, served as `gone` on /api/board.
"""

WINDOW = 1800  # seconds a dropped row stays on the list
CAP = 50


def why(issue, truncated):
    """Why a row dropped: 'closed', 'off-board', 'moved' (open, still on the board's labels, yet out
    of the search: transferred, or a search that no longer returns it), 'deleted' (`issue` None: the
    forge answers 404 for it -- deleted, or its repo renamed or out of the token's reach), or None
    when nothing true can be said. When the board's search hit its
    LIMIT, a row may have dropped only because it fell off the end, so 'moved' is never claimed
    for that cycle."""
    if issue is None:
        return "deleted"
    if issue.get("state") == "closed":
        return "closed"
    if any(l.get("name") == "off-board" for l in issue.get("labels") or []):
        return "off-board"
    return None if truncated else "moved"


def keep(gone, now, on_board=()):
    """The list to serve: newest first, one entry per row (its latest drop), only the last WINDOW
    seconds, at most CAP, and never a row that is back on the board."""
    out, seen = [], set(on_board)
    for g in sorted(gone, key=lambda g: g["at"], reverse=True):
        if g["at"] >= now - WINDOW and g["id"] not in seen:
            seen.add(g["id"])
            out.append(g)
    return out[:CAP]
