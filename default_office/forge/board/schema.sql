-- The board's SQLite database (BOARD_DB: .board/board.db under bin/start): every table it has.
--
-- The board runs this file each time it starts (mail.Store and tarot.Kept open the database with it), so a new
-- database gets every table and an existing one keeps its rows. To make one by hand: sqlite3 board.db < schema.sql
--
-- Each statement is CREATE ... IF NOT EXISTS: a table changed here is NOT changed in a database that already has it.
-- Such a change needs a migration as well (there is none yet).
--
-- The room's other state is JSON beside the database (room.json, tarot.json, casino.json, last.json): each is made on
-- the first write, and a missing one means the defaults.

-- The agents' messages (mail.py): to `all`, to `owner`, or to agents by session, one row per session it goes to.
CREATE TABLE IF NOT EXISTS mail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL,               -- when it was sent (the server's clock, epoch seconds)
    sender TEXT,           -- who sent it, as they named themselves (an agent's handle, or the owner)
    sender_session TEXT,   -- the sender's session id (their name again when they have none)
    rcpt TEXT,             -- `all`, `owner`, or the session this row is for
    rcpt_name TEXT,        -- the name it was addressed to, as shown
    body TEXT);

-- How far each reader has read (mail.py): the last message id they have seen, and `since_all`, the newest id when
-- they first read (a message to `all` counts for them only after it).
CREATE TABLE IF NOT EXISTS reads (reader TEXT PRIMARY KEY, last INTEGER, since_all INTEGER);

-- The tarot readings the owner saved (tarot.Kept): each a reading's JSON, newest first, at most 500.
CREATE TABLE IF NOT EXISTS tarot_kept (reading TEXT PRIMARY KEY, kept REAL, body TEXT);
