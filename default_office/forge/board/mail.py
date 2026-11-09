"""The agents' messages: one SQLite file the board owns, reached over HTTP from every machine.

A message goes to `all`, to `owner` (the person at the kanban), or to an agent. An agent is addressed
by SESSION, never by handle: handles can collide and recur across sessions, so the sender resolves a
handle on the roster (forge.py tell) and passes the sessions it names now as `to_sessions`.

Why a database and not GitHub comments: several writers doing whole-comment read-modify-writes from
several machines cannot be made atomic there. Here one process owns one file and every operation is a
single transaction under one lock, and every stamp is this server's clock.

Pure of HTTP: server.py routes to it; test_mail.py drives it directly.
"""
import os
import sqlite3
import threading
import time

# Every table of the board's database (schema.sql, beside this file): run each time a Store opens it.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"), encoding="utf-8") as _f:
    SCHEMA = _f.read()
COLS = "id, at, sender, sender_session, rcpt, rcpt_name, body"


class Refused(Exception):
    """A request the rules turn down; the message says why and what to do instead."""


class Store:
    def __init__(self, path, on_change=None):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # Called after every write that wakes waiters, OUTSIDE the store's lock: the board page's
        # long-poll learns of it there (server.py).
        self.on_change = on_change
        self.lock = threading.Lock()
        # Long-polls wait on this and re-check their own mail on every change. `ver` only counts the
        # writes that woke them (the tests read it).
        self.changed = threading.Condition(self.lock)
        self.ver = 0

    # -- plumbing ---------------------------------------------------------------------------
    def _tx(self, fn, wake=True):
        """Run fn(db) as ONE transaction under the lock; bump ver and wake waiters if it wrote.
        `wake` False for bookkeeping no waiter is waiting on (a new reader's mark)."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self.db)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            if wake:
                self.ver += 1
                self.changed.notify_all()
        if wake and self.on_change:
            self.on_change()
        return out

    def _read(self, fn):
        with self.lock:
            return fn(self.db)

    def wait(self, ready, hold):
        """Block up to `hold` s until ready(db) returns something truthy; return it (or None).
        `ready` runs under the lock, so what it sees and what it returns are one state."""
        end = time.monotonic() + hold  # a wall-clock step must not end or stretch a wait
        with self.changed:
            while True:
                got = ready(self.db)
                left = end - time.monotonic()
                if got or left <= 0:
                    return got
                self.changed.wait(timeout=left)

    # -- messages ---------------------------------------------------------------------------
    def send(self, sender, to, body, sender_session="", to_sessions=()):
        """Send `to`: `owner`, `all`, or an agent handle, which the caller resolves on the roster and
        passes as `to_sessions` [[session, handle], ...]. Returns the recipients' names."""
        sender, to, body = text(sender, "actor"), text(to, "to"), text(body, "body")
        sender_session = text(sender_session, "session") or sender
        if not body:
            raise Refused("an empty message")
        if not to:
            raise Refused("a message needs a recipient: a handle, owner or all")
        targets = [(text(x, "session"), text(n, "handle")) for x, n in (to_sessions or [])]
        if to in ("owner", "all"):
            targets = [(to, to)]
        elif not targets:
            raise Refused("a message to a handle names its session(s): to_sessions")

        def t(db):
            for x, n in targets:
                db.execute("INSERT INTO mail (at, sender, sender_session, rcpt, rcpt_name, body)"
                           " VALUES (?,?,?,?,?,?)", (time.time(), sender, sender_session, x, n, body))
            return [n for _, n in targets]
        return self._tx(t)

    @staticmethod
    def _enrol(db, reader):
        """A new reader: a message to `all` sent before it existed is not news, but one addressed to
        IT is, whenever it was sent. Returns whether it was new."""
        if db.execute("SELECT 1 FROM reads WHERE reader=?", (reader,)).fetchone():
            return False
        top = db.execute("SELECT COALESCE(MAX(id), 0) FROM mail").fetchone()[0]
        db.execute("INSERT INTO reads (reader, last, since_all) VALUES (?,0,?)", (reader, top))
        return True

    @staticmethod
    def _unread_q(db, reader):
        """`reader` is a session id, or `owner`."""
        return [dict(m) for m in db.execute(
            "SELECT %s FROM mail m, reads r WHERE r.reader=? AND m.id>r.last AND m.sender_session!=?"
            " AND (m.rcpt=? OR (m.rcpt='all' AND m.id>r.since_all)) ORDER BY m.id"
            % ", ".join("m." + c.strip() for c in COLS.split(",")), (reader, reader, reader))]

    def unread(self, reader):
        reader = text(reader, "reader")
        got = self._read(lambda db: None if not db.execute(
            "SELECT 1 FROM reads WHERE reader=?", (reader,)).fetchone() else self._unread_q(db, reader))
        if got is not None:
            return got
        # Only a reader's first read writes, and it wakes nobody: no waiter waits on a mark.
        return self._tx(lambda db: (self._enrol(db, reader), self._unread_q(db, reader))[1], wake=False)

    def unread_wait(self, reader, hold=25):
        reader = text(reader, "reader")
        got = self.unread(reader)
        if got:
            return got
        return self.wait(lambda db: self._unread_q(db, reader), hold) or []

    def mark_read(self, reader, last):
        """Mark messages up to id `last` read: an integer, held to the newest message that exists,
        since a mark past it would hide every message still to come and MAX never lowers it."""
        reader = text(reader, "reader")
        if isinstance(last, bool) or not isinstance(last, int):
            raise Refused("last is a message id")

        def t(db):
            self._enrol(db, reader)
            top = db.execute("SELECT COALESCE(MAX(id), 0) FROM mail").fetchone()[0]
            db.execute("UPDATE reads SET last=MAX(last, ?) WHERE reader=?", (min(last, top), reader))
        self._tx(t)  # it wakes the page: the owner's unread badge must go when they mark it read

    def recent_mail(self, n=50):
        n = max(0, min(int(n), 500))  # SQLite reads a negative LIMIT as none
        return self._read(lambda db: [dict(m) for m in db.execute(
            "SELECT %s FROM mail ORDER BY id DESC LIMIT ?" % COLS, (n,))][::-1])


def text(v, what):
    """A request's string field, or Refused: a number, a list or an object is a client bug, said so
    rather than stored, or crashed on."""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise Refused("%s must be text, not %s" % (what, type(v).__name__))
    return v.strip()
