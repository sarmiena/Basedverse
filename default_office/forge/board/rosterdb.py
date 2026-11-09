"""The agent roster's store: the `roster` table of the board's database (schema.sql), one row per working agent.

A row is the entry forge.py writes (agent_body: the `<!-- board-agent -->` record) under an id; only that agent
rewrites it, and /stop, a reap or the owner's x deletes it. roster.py reads the rows exactly as it read comments.

Why here and not on GitHub: the roster was a comment per agent on an `AGENTS` issue in the home repo until 2026-11-09.
Every new comment (each session's /go) emailed everyone who watches that repository, and every touch spent the
account's API quota. Here one process owns one file and each operation is one transaction under one lock (mail.py's
reasons), and nothing about who is working leaves this machine. Agents on other machines reach it over HTTP
(REPO_BOARD_URL), as they reach their messages.

Pure of HTTP: server.py routes to it; test_rosterdb.py drives it directly.
"""
import datetime
import sqlite3
import threading
import time

import roster
from mail import SCHEMA

BODY_MAX = 16 * 1024   # an entry is a few hundred bytes; `subs` and `doing` are the long lines


def iso(t):
    """Epoch seconds as GitHub's stamp shape (2026-11-09T21:01:56Z): what roster.py reads `updated_at` as."""
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


class Refused(ValueError):
    """Not an entry: the table holds agents' records, never anything else a caller sends (HTTP 422)."""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _tx(self, fn):
        """fn(db) as ONE transaction under the lock."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self.db)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return out

    @staticmethod
    def _row(r):
        return {"id": r["id"], "body": r["body"], "created_at": iso(r["created"]), "updated_at": iso(r["updated"])}

    @staticmethod
    def _check(body):
        if not isinstance(body, str) or len(body.encode("utf-8")) > BODY_MAX:
            raise Refused("an entry is text, at most %d bytes" % BODY_MAX)
        if roster.parse_agent(body) is None:
            raise Refused("not an agent entry (forge.py agent_body's record, with its session)")

    def rows(self):
        """Every entry, in the order they were made (comment order: roster.agents merges by it)."""
        with self.lock:
            return [self._row(r) for r in self.db.execute("SELECT id, body, created, updated FROM roster ORDER BY id")]

    def get(self, rid):
        with self.lock:
            r = self.db.execute("SELECT id, body, created, updated FROM roster WHERE id = ?", (rid,)).fetchone()
        return self._row(r) if r else None

    def put(self, body, rid=None):
        """A new entry (`rid` None), or entry `rid` rewritten. LookupError when `rid` is gone: an agent rewriting an entry
        the owner's x or a reap just took off must not bring it back (forge.py's touch never re-creates one)."""
        self._check(body)
        now = time.time()

        def tx(db):
            if rid is None:
                cur = db.execute("INSERT INTO roster (body, created, updated) VALUES (?, ?, ?)", (body, now, now))
                new = cur.lastrowid
            else:
                if db.execute("UPDATE roster SET body = ?, updated = ? WHERE id = ?", (body, now, rid)).rowcount == 0:
                    raise LookupError("no roster entry %s (taken off: /stop, a reap or the owner's x)" % rid)
                new = rid
            return db.execute("SELECT id, body, created, updated FROM roster WHERE id = ?", (new,)).fetchone()
        return self._row(self._tx(tx))

    def delete(self, rid):
        """Entry `rid` taken off; LookupError when it is already gone."""
        def tx(db):
            if db.execute("DELETE FROM roster WHERE id = ?", (rid,)).rowcount == 0:
                raise LookupError("no roster entry %s (already gone?)" % rid)
        self._tx(tx)

    def empty(self):
        with self.lock:
            return self.db.execute("SELECT 1 FROM roster LIMIT 1").fetchone() is None

    def adopt(self, comments):
        """The entries among `comments` (GitHub's, from the AGENTS issue) copied in, oldest first, when the table is
        still empty: a board that kept its roster on GitHub keeps who holds what across the move. The count adopted."""
        ents = roster.entries(comments)

        def tx(db):
            if db.execute("SELECT 1 FROM roster LIMIT 1").fetchone() is not None:
                return 0
            now = time.time()
            for c, _ in ents:
                db.execute("INSERT INTO roster (body, created, updated) VALUES (?, ?, ?)", (c["body"], now, now))
            return len(ents)
        return self._tx(tx)
