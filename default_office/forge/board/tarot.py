"""The tarot reader in the Agent Room's front right corner (room.html: Madame Vesna): one card (guidance) or three (the
past, the present and the future) drawn from a full deck (Rider-Waite's 78, a third of them reversed, on average), and
her reading of them, written by the model (llm.py) as it comes. One reading at a time, the room's: the last one is
kept (TAROT_FILE, beside room.json), so the cards stay on her table across a restart, and the inspection panel can show
it whole.

Asked for a reading without saying how many cards, she asks first, in character (the model writes it: CHOOSE):
one card, or three? The reading waits on her table, its cards not yet drawn (`choosing`, its `pre` the talk before
them), till whoever asked answers: their answer is sorted (pick: a plain "one card" or "three" by itself, else the
model) into one card, three, hers to choose (she picks), or no choice yet (she answers it and asks again; after
CHOOSE_MAX rounds, the cards choose). While they choose (CHOOSE_TTL from her last word to them), she is with them:
anyone else but the owner is told so (Busy).

After a reading she takes questions about it (ask): said aloud (the room's page says her answer, as it said the
reading) or whispered (her answer only on the person's page). What is said to her is sorted first (sort: the local
model, a keyword guess if it fails): a wish for new cards ("a new reading aimed at my career path") deals a new
reading for that focus, anything else is answered about the cards on her table; with none there, she reads for it.
A reading asked in a whisper is private: dealt on her table for all to see, its words for whoever asked alone.

Her table is the room's, shared as in a real world, not one reading each: whoever asks (the room's owner, or a visitor
through Visit: `by`) gets the next reading, and it is on her table for everyone. Each reading and turn says whom it was
for (`by`), and whom a whispered one goes back to (`to`: "owner", or a visitor's visit id). A whisper stays its
whisperer's: an answer said aloud never sees one, a whispered answer sees only its own whisperer's, a private reading's
words are only for whoever asked it, and the owner's page is served others' whispers without their words (redact).
Anyone but the owner who asks for more than RATE_N readings in RATE_WINDOW seconds rests RATE_COOL seconds (and so for
questions): the owner's GPU is not to be spammed. And nobody, the owner too, has the model called for them more than
INFER_N times in INFER_WINDOW seconds (each sort, each reading, each line she writes): one past it is told to wait the
seconds till one is free, and the model is not asked. Her memory is that one reading and the talk about it
(`talk`, its last TALK_MAX turns): a new reading wipes it. An answer said aloud never sees what was whispered, so she
cannot let it slip in front of the room.

Served by server.py: GET /api/tarot (?v=N: held until it changes, as /api/board is), POST /api/tarot (a new reading)
and POST /api/tarot/ask (a question). The room's page polls it: she deals the cards onto her table and says the
reading as it streams in, then her answers said aloud; the person's page reads it for her card and the cards' panel
(the reading on a scroll, and the talk after it).
"""
import json
import os
import random
import re
import sqlite3
import threading
import time

import llm
import mail

MAJOR = ["The Fool", "The Magician", "The High Priestess", "The Empress", "The Emperor", "The Hierophant", "The Lovers",
         "The Chariot", "Strength", "The Hermit", "Wheel of Fortune", "Justice", "The Hanged Man", "Death", "Temperance",
         "The Devil", "The Tower", "The Star", "The Moon", "The Sun", "Judgement", "The World"]
SUITS = [("w", "Wands"), ("c", "Cups"), ("s", "Swords"), ("p", "Pentacles")]
RANKS = ["Ace", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Page", "Knight", "Queen", "King"]
# The key is what room.html paints a face by: m00..m21 the major arcana, then w|c|s|p and 01..14 (Ace..King).
DECK = [{"key": "m%02d" % i, "name": n} for i, n in enumerate(MAJOR)] + \
       [{"key": "%s%02d" % (s, r + 1), "name": "%s of %s" % (RANKS[r], suit)} for s, suit in SUITS for r in range(14)]
POSITIONS = ["Past", "Present", "Future"]
ONE = "Guidance"   # a single card's place
REVERSED = 1 / 3
NAME, NPC = "Madame Vesna", "vesna"   # what she goes by, and her id (room.html: visit:npc:vesna)
QUESTION_MAX = 300
TALK_MAX = 20     # turns of talk kept about the reading (the oldest go first)
CONTEXT = 8       # of those, the most an answer is written from
BUSY_MAX = 180   # seconds: a reading not done by then is taken for stuck, and another may start
BUMP = 0.2       # seconds at least between the changes a stream tells its pollers of
RATE_N, RATE_WINDOW, RATE_COOL = 10, 60, 180   # more than RATE_N readings (or questions) in RATE_WINDOW s: RATE_COOL s off
INFER_N, INFER_WINDOW = 15, 60   # the model called for one person at most INFER_N times in INFER_WINDOW s (the owner too)
CHOOSE_TTL = 120   # seconds a querent has to answer "one card, or three?" before she is free for others
CHOOSE_MAX = 4     # her lines asking it, at most: past that, the cards choose
OWNER = {"name": "", "key": "owner", "id": None, "owner": True}

SYSTEM = (
    "You are " + NAME + ", a tarot reader with a small velvet-draped booth in the corner of an office where AI coding "
    "agents build software for the person who keeps it, your querent. You speak aloud, warm and a little theatrical, "
    "but every line has substance: the card's traditional meaning, turned to the querent's life and work. Never say "
    "you are an AI, never hedge about whether tarot is real.\n\n"
    "Give a three-card reading: past, present, future. If the querent asks about something in particular, read every "
    "card for that, and say so as you open. Plain spoken prose only: no markdown, no headings, no lists, "
    "no asterisks, no emojis, no stage directions. Five short paragraphs, separated by blank lines: an opening of one "
    "or two sentences that greets the querent and says what the cards speak to (never describe shuffling, dealing or "
    "turning them); one paragraph for "
    "each card in order, naming its position and the card (and that it is reversed, when it is), what it means, and what "
    "it says here; and a closing paragraph that weaves the three into one piece of counsel. Short to medium sentences, "
    "as said aloud. About 140 to 180 words in all, never more.")
SYSTEM_ONE = (
    "You are " + NAME + ", a tarot reader with a small velvet-draped booth in the corner of an office where AI coding "
    "agents build software for the person who keeps it, your querent. You speak aloud, warm and a little theatrical, "
    "but every line has substance: the card's traditional meaning, turned to the querent's life and work. Never say "
    "you are an AI, never hedge about whether tarot is real.\n\n"
    "Give a single-card reading: one card, drawn for guidance. If the querent asks about something in particular, read "
    "the card for that, and say so as you open. Plain spoken prose only: no markdown, no headings, no lists, no "
    "asterisks, no emojis, no stage directions. Three short paragraphs, separated by blank lines: an opening of one or "
    "two sentences that greets the querent and says what the card speaks to (never describe shuffling, dealing or "
    "turning it); a paragraph naming the card (and that it is reversed, when it is), what it means, and what it says "
    "here; and a closing paragraph of counsel drawn from it. Short to medium sentences, as said aloud. About 80 to 110 "
    "words in all, never more.")


def _answer(spread):
    return (
    "You are " + NAME + ", a tarot reader with a small velvet-draped booth in the corner of an office where AI coding "
    "agents build software for the person who keeps it, your querent. You have just given the querent a " + spread + " "
    "reading, and now they ask you about it. Answer in character, aloud, warm and a little theatrical, with substance: "
    "one to three short sentences, under 60 words. Plain spoken prose only: no markdown, no lists, no asterisks, no "
    "emojis, no stage directions. Stay with the cards on the table: never draw or invent new ones (for a new reading, "
    "the querent must ask you for one). If a question strays from the reading, answer it briefly in your own way and "
    "bring it back to the cards. Never say you are an AI.")


ANSWER, ANSWER_ONE = _answer("three-card"), _answer("single-card")
CHOOSE = (
    "You are " + NAME + ", a tarot reader with a small velvet-draped booth in the corner of an office where AI coding "
    "agents build software for the person who keeps it. Someone has come to your table for a reading. Before you deal, "
    "ask them, in character, whether they want a single card or three: a single card is one lantern held up to one "
    "question; three cards trace what was, what is and what will be. Mystical, warm and a little theatrical, said "
    "aloud: one to three short sentences, under 45 words. If they said what the reading is for, weave it in. If they "
    "answered without choosing, or asked you something, answer it briefly and ask again. Never deal, draw or name a "
    "card yet. Plain spoken prose only: no markdown, no lists, no asterisks, no emojis, no stage directions. Never say "
    "you are an AI.")
WHISPER = " (whispered: only you and the querent hear this)"
SORT = (
    "You sort what a querent says to a tarot reader. Reply with JSON only, no other words: {\"new_reading\": true or "
    "false, \"focus\": \"...\", \"cards\": 1, 3 or 0}. new_reading is true when they want new cards dealt: they ask "
    "for a new, another, or fresh reading, a redraw, or a reading about or for something. It is false when they ask "
    "about the cards already on the table, what they mean or what to do about them, or say anything else (thanks, a "
    "greeting, small talk). focus: what the new reading should look at, a few words of their own (\"\" if none, or if "
    "new_reading is false). cards: how many cards they ask for, if they say: 1 for a single card (one card, a quick "
    "one), 3 for three (three cards, the full spread, past present and future); 0 if they do not say, or if "
    "new_reading is false.")
PICK = (
    "You sort a querent's reply to a tarot reader who has just asked them whether they want a single card or three "
    "cards (past, present and future). Reply with JSON only, no other words: {\"cards\": 1, 3, 0 or -1, \"focus\": "
    "\"...\"}. cards is 1 when they choose a single card (one, just one, a single card, a quick one); 3 when they choose "
    "three (three, all three, the full spread, past present and future); -1 when they leave it to her (you choose, "
    "surprise me, whatever the cards want, either); 0 when they do not choose (a question, hesitation, small talk, "
    "anything else). focus: what they now say the reading should look at, a few words of their own (\"\" if they say "
    "nothing about it).")
# What sort guesses from when the model cannot say: a new reading asked for in so many words.
NEW_WORDS = re.compile(r"\b(?:new|another|fresh|different|second)\s+(?:reading|spread|cards?|draw|pull)\b|\bre-?draw\b|\bdraw again\b|"
                       r"\bread (?:for|to) me\b|\breading (?:about|for|on|aimed at|focused on)\b", re.I)
# How many cards, in so many words (sort's and pick's guess when the model cannot say).
ONE_WORDS = re.compile(r"\b(?:one|1|a single|single|just the one)[- ]card\b|\bsingle\b|\bjust (?:the )?one\b|\bone[- ]card\b", re.I)
THREE_WORDS = re.compile(r"\b(?:three|3)[- ]cards?\b|\b(?:all|the) three\b|\b(?:the )?(?:full|whole) (?:spread|reading)\b|\bpast,? present,? (?:and )?future\b", re.I)
# A reply that is plainly a choice and nothing else ("One card, please", "three"): taken without asking the model.
PLAIN = re.compile(r"(?:(?:just|only)\s+)?(?:(?:the\s+)?(?P<one>one|1|a single|single)(?:\s+cards?)?|(?P<three>three|3)(?:\s+cards?)?|(?:the\s+)?(?:full|whole)\s+(?P<spread>spread))"
                   r"(?:\s+(?:reading|draw|pull))?", re.I)


class Busy(Exception):
    pass


class Limited(Exception):
    pass


def person(by):
    """Who asked, as the board takes it: the owner (None, or owner true), or someone else: their name, their key (their
    office's, else their visit's), the visit id a whisper goes back to."""
    if not isinstance(by, dict) or by.get("owner") is True:
        return dict(OWNER, name=str((by or {}).get("name") or "")[:40] if isinstance(by, dict) else "")
    vid = str(by.get("id") or "")[:80] or None
    key = str(by.get("key") or "")[:80] or ("visit:" + vid if vid else "someone")
    return {"name": str(by.get("name") or "a visitor")[:40], "key": key, "id": vid, "owner": False}


def whose(x):
    """The key of whoever asked for a reading or a turn (one from before `by`: the owner's)."""
    return (x.get("by") or {}).get("key") or "owner"


class NoReading(Exception):
    pass


class NotReady(Exception):
    pass


def draw(rng=random, n=3):
    """`n` different cards (3: past, present, future; 1: guidance), each perhaps reversed, in the spread's order."""
    return [dict(c, reversed=rng.random() < REVERSED, pos=p) for c, p in zip(rng.sample(DECK, n), POSITIONS if n == 3 else [ONE])]


def plain_pick(text):
    """1 or 3 for a reply that is plainly that choice and nothing else ("One card, please", "three"); else 0."""
    t = re.sub(r"[^\w\s]", " ", str(text or "").lower())
    t = re.sub(r"\b(?:please|madame|vesna|thanks|thank you|ok|okay|yes|i want|i'll take|i ll take|let s do|lets do|i d like)\b", " ", t)
    m = PLAIN.fullmatch(re.sub(r"\s+", " ", t).strip())
    return 0 if not m else 1 if m.group("one") else 3


def clean(text):
    """The reading as said: markdown the model slipped in taken out (bold, headings, bullets), spaces tidied."""
    t = re.sub(r"\*\*|__|^[ \t]*#+[ \t]*|^[ \t]*[-*•][ \t]+", "", text or "", flags=re.M)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def spread_of(cards):
    return "\n".join("%s: %s%s" % (c["pos"], c["name"], " (reversed)" if c["reversed"] else "") for c in cards)


def asking(focus="", asked="", name=""):
    """What the querent asked of a reading (named, if they have a name): their own words, if they asked in words, and
    its focus."""
    who = ("The querent, %s," % name.strip()) if name and name.strip() else "The querent"
    said = ('%s says: "%s" ' % (who, asked.strip())) if asked and asked.strip() else ("%s is here. " % who if name and name.strip() else "")
    return said + (("Read the cards for: %s." % focus.strip()) if focus and focus.strip() else "The querent asks for a general reading." if not said else "")


def messages(cards, focus="", when=None, asked="", name=""):
    when = time.localtime(when)
    part = "morning" if when.tm_hour < 12 else "afternoon" if when.tm_hour < 18 else "evening" if when.tm_hour < 23 else "night"
    return [{"role": "system", "content": SYSTEM_ONE if len(cards) == 1 else SYSTEM},
            {"role": "user", "content": "It is a %s %s. %s\n\nThe cards, as dealt:\n%s" % (time.strftime("%A", when), part, asking(focus, asked, name), spread_of(cards))}]


def counted(text):
    """1 or 3 for words that say how many cards (ONE_WORDS, THREE_WORDS: one and not the other); else 0."""
    one, three = bool(ONE_WORDS.search(text or "")), bool(THREE_WORDS.search(text or ""))
    return 1 if one and not three else 3 if three and not one else 0


def _json(out):
    return json.loads(re.search(r"\{.*\}", out, re.S).group(0))


def sort_with(chat):
    """sort(text, reading) -> (a new reading is wanted, its focus, how many cards they asked for: 1, 3, or 0 if they
    did not say), by the model's `chat`; by NEW_WORDS (and counted) when the model fails or answers nonsense."""
    def sort(text, reading):
        table = spread_of(reading["cards"]) if reading and reading.get("cards") else "none"
        try:
            out = chat([{"role": "system", "content": SORT}, {"role": "user", "content": "The cards on her table:\n%s\n\nThe querent says: %s" % (table, text)}],
                       max_tokens=80, temperature=0)
            j = _json(out)
            if isinstance(j.get("new_reading"), bool):
                n = j.get("cards") if j.get("cards") in (1, 3) and not isinstance(j.get("cards"), bool) else 0
                return (j["new_reading"], str(j.get("focus") or "").strip()[:120], n) if j["new_reading"] else (False, "", 0)
        except (llm.LLMError, AttributeError, ValueError, TypeError):
            pass
        new = bool(NEW_WORDS.search(text))
        return new, "", counted(text) if new else 0
    return sort


def pick_with(chat):
    """pick(text, reading) -> (cards, focus): the querent's answer to "one card, or three?" (the reading's last line
    of hers, `pre`), sorted by the model's `chat`: 1, 3, -1 (hers to choose) or 0 (no choice yet), and what they
    now say it should look at ("" if nothing). By counted when the model fails or answers nonsense."""
    def pick(text, reading):
        last = next((t["text"] for t in reversed((reading or {}).get("pre") or []) if t.get("text")), "One card, or three?")
        try:
            out = chat([{"role": "system", "content": PICK}, {"role": "user", "content": "She asked: %s\n\nThe querent replies: %s" % (clean(last), text)}],
                       max_tokens=60, temperature=0)
            j = _json(out)
            if j.get("cards") in (1, 3, 0, -1) and not isinstance(j.get("cards"), bool):
                return j["cards"], str(j.get("focus") or "").strip()[:120]
        except (llm.LLMError, AttributeError, ValueError, TypeError):
            pass
        return counted(text), ""
    return pick


def choose_messages(reading, private, by):
    """What her line asking "one card, or three?" is written from: what was asked (its words, unless the reading was
    asked in a whisper and this line is said aloud), the talk about it since (aloud, only what was said aloud), and the
    querent's last words (the turn being written: `pre`'s last)."""
    by = by or OWNER
    said = lambda words: ("%s: %s" % (by["name"], words)) if by.get("name") else words
    who = ("The querent, %s," % by["name"]) if by.get("name") else "The querent"
    pre, mine = reading.get("pre") or [], not reading.get("private") or private
    first = ('%s says: "%s"' % (who, reading["ask"])) if mine and reading.get("ask") else \
        "%s asks you for a reading%s." % (who, " about: " + reading["question"] if mine and reading.get("question") else "")
    out = [{"role": "system", "content": CHOOSE}, {"role": "user", "content": first}]
    for i, t in enumerate(pre[:-1]):
        if t["private"] and not private:
            continue
        if i:
            out.append({"role": "user", "content": said(t["ask"]) + (WHISPER if t["private"] else "")})
        out.append({"role": "assistant", "content": clean(t["text"]) or "(No answer.)"})
    if len(pre) > 1:
        out.append({"role": "user", "content": said(pre[-1]["ask"]) + (WHISPER if private else "")})
    return out


def answer_messages(reading, turns, ask, private, by=None):
    """What an answer for `by` (the owner, or a visitor: person()) is written from: the reading (its cards; its words,
    unless it was whispered to someone else), the talk about it before (the last CONTEXT turns: those said aloud, and,
    for a whispered answer, its own whisperer's whispers), and the question, with the asker's name."""
    by = by or OWNER
    said = lambda name, words: ("%s: %s" % (name, words)) if name else words
    rb = reading.get("by") or {}
    # A whispered reading's question, focus and words go only into its own asker's whispered answers: never into one
    # said aloud (theirs either: the room would hear it), nor anyone else's.
    mine = not reading.get("private") or (private and whose(reading) == by["key"])
    words = clean(reading["text"]) if mine else "(That reading was given in a whisper: only its cards are known here.)"
    asked = asking(reading.get("question"), reading.get("ask"), rb.get("name") or "") if mine else "A reading was asked for in a whisper; what was asked is not known here."
    out = [{"role": "system", "content": ANSWER_ONE if len(reading["cards"]) == 1 else ANSWER},
           {"role": "user", "content": "%s\n\nThe cards, as dealt:\n%s" % (asked, spread_of(reading["cards"]))},
           {"role": "assistant", "content": words or "(The reading broke off.)"}]
    for t in [t for t in turns if not t["private"] or (private and whose(t) == by["key"])][-CONTEXT:]:
        out += [{"role": "user", "content": said((t.get("by") or {}).get("name"), t["ask"]) + (WHISPER if t["private"] else "")},
                {"role": "assistant", "content": clean(t["text"]) or "(No answer.)"}]
    return out + [{"role": "user", "content": said(by.get("name"), ask) + (WHISPER if private else "")}]


class Readings:
    """The room's reading: the last one (from `path`, if kept), and a new one on start(), streamed from `stream` (llm's)
    on a thread of its own; then the talk about it, a turn (a question and her answer) at a time on ask(). One asked
    for without its number of cards waits for it (`choosing`): her lines asking it are turns of its `pre`, and the
    answer, sorted by `pick`, deals its cards into it. `ver` goes up at each change; wait(v) holds until it is not v."""

    def __init__(self, path=None, stream=llm.stream, ready=llm.configured, rng=None, clock=time.time, sort=None, pick=None):
        self.path, self.stream, self.ready, self.rng, self.clock = path, stream, ready, rng or random.SystemRandom(), clock
        self.sort, self.pick = sort or sort_with(llm.chat), pick or pick_with(llm.chat)
        self.cond = threading.Condition()
        self.ver, self.cur, self.bumped = 1, None, 0.0
        self.times, self.cool = {}, {}   # (kind, key) -> when each of their last readings (questions) was; until when they rest
        self.calls, self.infer_n = {}, INFER_N   # key -> when the model was called for them, in the last INFER_WINDOW s
        if path:
            try:
                with open(path) as f:
                    r = json.load(f)
                if isinstance(r, dict) and isinstance(r.get("cards"), list) and (len(r["cards"]) in (1, 3) or (r.get("choosing") and not r["cards"])):
                    turns = lambda k: [dict(t, done=True) for t in r.get(k) or [] if isinstance(t, dict) and isinstance(t.get("ask"), str)]
                    self.cur = dict(r, done=True, talk=turns("talk"), pre=turns("pre"))   # one cut short by a restart is as far as it got
            except (OSError, ValueError):
                pass

    def view(self, redact=False):
        with self.cond:
            return self._view(redact)

    def _view(self, redact=False):
        """The reading and its talk; `redact` (the owner's page): another's whisper without its words (`hidden`)."""
        hide = lambda x: redact and x.get("private") and whose(x) != "owner"
        turns = lambda k: [dict(t, ask="", text="", hidden=True) if hide(t) else dict(t) for t in self.cur.get(k) or []]
        r = self.cur and dict(self.cur, cards=[dict(c) for c in self.cur["cards"]], talk=turns("talk"), pre=turns("pre"))
        if r and hide(r):
            r.update(text="", ask="", question="", hidden=True)
        return {"ver": self.ver, "reading": r or None,
                "ready": bool(self.ready()), "name": NAME, "npc": NPC}

    def wait(self, v, timeout, redact=False):
        """The view once ver is not v, or after timeout (None then, for a 204)."""
        with self.cond:
            if str(self.ver) == str(v) and not self.cond.wait_for(lambda: str(self.ver) != str(v), timeout=timeout):
                return None
            return self._view(redact)

    def _resting(self, by, now):
        """Limited, for anyone but the owner resting from readings or questions (_limit). Under the lock."""
        if by["owner"]:
            return
        for kind in ("reading", "ask"):
            until = self.cool.get((kind, by["key"]), 0)
            if now < until:
                raise Limited("%s needs a rest from %s: ask again in %d minute%s" % (NAME, by["name"], -(-(until - now) // 60), "" if until - now <= 60 else "s"))

    def _budget(self, by, now, need):
        """Limited, for anyone (the owner too) for whom the model would be called more than infer_n times in INFER_WINDOW
        s with `need` more calls now: told how many seconds till there is room. Under the lock."""
        times = [t for t in self.calls.get(by["key"], []) if now - t < INFER_WINDOW]
        self.calls[by["key"]] = times
        over = len(times) + need - self.infer_n
        if over > 0:
            wait = max(1, -(-(times[min(over, len(times)) - 1] + INFER_WINDOW - now) // 1)) if times else INFER_WINDOW
            raise Limited("%s needs a moment's quiet from %s: ask again in %d second%s" % (NAME, by["name"] or "you", wait, "" if wait == 1 else "s"))

    def _spend(self, by, now, n):
        """`n` calls of the model for `by`, counted (_budget). Under the lock."""
        self.calls.setdefault(by["key"], []).extend([now] * n)

    def _limit(self, kind, by, now):
        """Limited, for anyone but the owner resting (more than RATE_N of `kind` in RATE_WINDOW s); else counted. Under
        the lock."""
        if by["owner"]:
            return
        k = (kind, by["key"])
        if now < self.cool.get(k, 0):
            raise Limited("%s needs a rest from %s: ask again in %d minute%s" % (NAME, by["name"], -(-(self.cool[k] - now) // 60), "" if self.cool[k] - now <= 60 else "s"))
        times = [t for t in self.times.get(k, []) if now - t < RATE_WINDOW]
        if len(times) >= RATE_N:
            self.cool[k], self.times[k] = now + RATE_COOL, []
            raise Limited("%s needs a rest from %s: ask again in %d minutes" % (NAME, by["name"], RATE_COOL // 60))
        self.times[k] = times + [now]

    def start(self, question="", private=False, asked="", by=None, redact=False, cards=3):
        """A new reading of `cards` cards (1 or 3: drawn now, the words to follow; None: she asks which first, and it
        waits for the answer, ask()). `question`: what it is to look at (her card's box, or the focus sort found);
        `asked`: the words that asked for it, if it was asked in words; private: asked in a whisper (its words for
        whoever asked alone); `by`: who asked (person(): None, the owner). The view, with `did`: "reading" (dealt) or
        "choose" (she asks). Busy while she is still writing (or with another querent choosing); Limited for one who has
        asked too many (_limit), or had the model called too often (_budget)."""
        if not self.ready():
            raise NotReady(llm.NEED)
        q, a = (re.sub(r"\s+", " ", str(x or "")).strip()[:QUESTION_MAX] for x in (question, asked))
        by = person(by)
        now = self.clock()
        with self.cond:
            self._idle(now, by)
            self._resting(by, now)
            self._budget(by, now, 1)
            self._limit("reading", by, now)
            self._spend(by, now, 1)
            view, job = self._new(q, a, private, by, cards, now, redact)
        return self._run(view, job)

    def _new(self, q, a, private, by, n, now, redact):
        """The new reading on her table: dealt (n 1 or 3), or waiting for its number, her line asking it the first of its
        `pre`. (view, the job that writes it). Under the lock."""
        rid = "r%d%04d" % (int(now * 1000), self.rng.randrange(10000))
        whom = {"name": by["name"], "key": by["key"], "owner": by["owner"]}
        to = None if not private else "owner" if by["owner"] else by["id"]
        self.cur = {"id": rid, "at": now, "question": q, "ask": a, "private": bool(private), "cards": [], "text": "", "done": True,
                    "error": None, "talk": [], "pre": [], "by": whom, "to": to, "choosing": True}
        if n in (1, 3):
            self.cur.update(cards=draw(self.rng, n), done=False, choosing=False)
            job = (rid, None, messages(self.cur["cards"], q, now, a, by["name"]), 900, "reading")
        else:
            tid = "p%d%04d" % (int(now * 1000), self.rng.randrange(10000))
            self.cur["pre"] = [{"id": tid, "at": now, "ask": a, "private": bool(private), "text": "", "done": False, "error": None, "by": whom, "to": to}]
            job = (rid, tid, choose_messages(self.cur, bool(private), by), 160, "choose")
        self._bump(now)
        return self._view(redact), job

    def _run(self, view, job):
        """`job` (rid, tid, messages, max_tokens, did) written on a thread of its own; the view, with what it does."""
        rid, tid, msgs, max_tokens, did = job
        threading.Thread(target=self._write, args=(rid, tid, msgs, max_tokens), daemon=True, name="tarot " + (tid or rid)).start()
        return dict(view, did=did)

    def _choosing(self, now):
        """Whose answer to "one card, or three?" she waits for (their key), till CHOOSE_TTL from her last line to them;
        None. Under the lock."""
        r = self.cur
        if not (r and r.get("choosing")):
            return None
        last = max([r["at"]] + [t["at"] for t in r.get("pre") or []])
        return whose(r) if now - last < CHOOSE_TTL else None

    def _idle(self, now, by=None):
        """Busy, unless she is writing nothing (or what she was writing is past BUSY_MAX: stuck), and (for `by`, anyone
        but the owner) is not waiting for another's answer to "one card, or three?". Under the lock."""
        r = self.cur
        if r and not r["done"] and now - r["at"] < BUSY_MAX:
            raise Busy(NAME + " is still reading the cards")
        if r and any(not t["done"] and now - t["at"] < BUSY_MAX for t in r["talk"] + (r.get("pre") or [])):
            raise Busy(NAME + " is still answering")
        chooser = self._choosing(now)
        if by and not by["owner"] and chooser and chooser != by["key"]:
            raise Busy(NAME + " is with another querent")

    def ask(self, text, private=False, by=None, redact=False):
        """Something said to her, aloud (private false) or whispered. From whoever she asked "one card, or three?": their
        answer (pick), which deals the cards, or has her answer it and ask again. Else: a wish for new cards deals a new
        reading for its focus (of as many cards as they said; none said, she asks); anything else is answered about the
        cards on her table (with none there, she reads for it). The view, with `did`: "reading", "choose" (she asks how
        many) or "answer". `by`: who said it (person()). Busy while she is still writing the reading or another answer,
        or is with another querent; Limited for one who has asked too many, or had the model called too often (two calls
        a line: its sort, and what she writes; one for a plain "one card" to her question)."""
        if not self.ready():
            raise NotReady(llm.NEED)
        q = re.sub(r"\s+", " ", str(text or "")).strip()
        if not q or len(q) > QUESTION_MAX:
            raise ValueError("a question is 1 to %d characters" % QUESTION_MAX)
        by = person(by)
        with self.cond:   # busy, or one resting: refused before the model is asked anything (sort is a call to it)
            now = self.clock()
            self._idle(now, by)
            self._resting(by, now)
            r = self.cur
            mine = self._choosing(now) == by["key"]   # their answer to "one card, or three?"
            plain = plain_pick(q) if mine else 0
            self._budget(by, now, 1 if plain else 2)
            self._limit("ask", by, now)   # every line said to her counts, a reading asked for in words too
            self._spend(by, now, 1 if plain else 2)
        if mine:
            n, focus = (plain, "") if plain else self.pick(q, r)   # outside the lock: the model takes a moment
            return self._chose(r["id"], q, bool(private), by, n, focus, redact)
        out = tuple(self.sort(q, r))   # outside the lock: the model takes a moment
        new, focus, n = (out + (0,))[:3]
        if new or not r or not r["cards"]:
            now = self.clock()
            with self.cond:
                self._idle(now, by)
                self._limit("reading", by, now)
                view, job = self._new(focus, q, private, by, n if n in (1, 3) else None, now, redact)
            return self._run(view, job)
        now = self.clock()
        with self.cond:
            self._idle(now, by)
            r = self.cur
            msgs = answer_messages(r, r["talk"], q, bool(private), by)
            tid = "t%d%04d" % (int(now * 1000), self.rng.randrange(10000))
            r["talk"] = (r["talk"] + [{"id": tid, "at": now, "ask": q, "private": bool(private), "text": "", "done": False, "error": None,
                                       "by": {"name": by["name"], "key": by["key"], "owner": by["owner"]},
                                       "to": None if not private else "owner" if by["owner"] else by["id"]}])[-TALK_MAX:]
            self._bump(now)
            view = self._view(redact)
        threading.Thread(target=self._write, args=(r["id"], tid, msgs, 300), daemon=True, name="tarot " + tid).start()
        return dict(view, did="answer")

    def _chose(self, rid, q, private, by, n, focus, redact):
        """Their answer `q` to "one card, or three?" (n: 1, 3, -1 hers to choose, 0 none yet; `focus`: what it should
        look at now, if they said): the cards dealt into the reading that waited for it (a turn of its `pre`, their
        answer), or her answer to it, asking again (one more turn); past CHOOSE_MAX of hers, the cards choose."""
        now = self.clock()
        with self.cond:
            self._idle(now, by)
            r = self.cur
            if not r or r["id"] != rid or not r.get("choosing"):
                raise Busy(NAME + " has moved on: ask her again")
            if focus:
                r["question"] = re.sub(r"\s+", " ", focus).strip()[:QUESTION_MAX]
            if n not in (1, 3) and (n == -1 or len(r["pre"]) >= CHOOSE_MAX):
                n = self.rng.choice((1, 3))   # hers to choose, or still undecided: the cards choose
            tid = "p%d%04d" % (int(now * 1000), self.rng.randrange(10000))
            turn = {"id": tid, "at": now, "ask": q, "private": private, "text": "", "done": n in (1, 3), "error": None,
                    "by": {"name": by["name"], "key": by["key"], "owner": by["owner"]}, "to": None if not private else "owner" if by["owner"] else by["id"]}
            if n in (1, 3):
                turn["chose"] = n
            r["pre"] = r["pre"] + [turn]
            if n in (1, 3):
                r.update(cards=draw(self.rng, n), choosing=False, done=False, text="", error=None, at=now)
                job = (rid, None, messages(r["cards"], r["question"], now, r["ask"], (r.get("by") or {}).get("name") or ""), 900, "reading")
            else:
                job = (rid, tid, choose_messages(r, private, by), 160, "choose")
            self._bump(now)
            view = self._view(redact)
        return self._run(view, job)

    def _box(self, rid, tid):
        """What a stream writes into: the reading, or one of its turns (after it, or before its cards); None once it has
        been replaced."""
        if not self.cur or self.cur["id"] != rid:
            return None
        return self.cur if tid is None else next((t for t in self.cur["talk"] + (self.cur.get("pre") or []) if t["id"] == tid), None)

    def _bump(self, now=None):
        self.ver += 1
        self.bumped = self.clock() if now is None else now
        self.cond.notify_all()

    def _write(self, rid, tid, msgs, max_tokens):
        err = None
        try:
            for piece in self.stream(msgs, max_tokens=max_tokens, temperature=0.95 if tid is None else 0.8):
                with self.cond:
                    box = self._box(rid, tid)
                    if box is None:
                        return   # another took its place (a new reading, a stuck one past BUSY_MAX)
                    box["text"] += piece
                    if self.clock() - self.bumped >= BUMP:
                        self._bump()
        except llm.LLMError as e:
            err = str(e)
        except Exception as e:   # never a reading left hanging: it ends, saying why
            err = "%s: %s" % (type(e).__name__, e)
        with self.cond:
            box = self._box(rid, tid)
            if box is None:
                return
            if not clean(box["text"]) and not err:
                err = "the model said nothing"
            box.update(done=True, error=err)   # its text as streamed (the room's page counts how far it has said it)
            # Kept before anyone is told it is done, and one stream's save at a time (they share the file).
            self._save(dict(self.cur, text=clean(self.cur["text"]), talk=[dict(t, text=clean(t["text"])) for t in self.cur["talk"]],
                            pre=[dict(t, text=clean(t["text"])) for t in self.cur.get("pre") or []]))
            self._bump()

    def _save(self, r):
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(r, f)
            os.replace(tmp, self.path)
        except OSError:
            pass   # kept in memory all the same


class Kept:
    """The readings the owner saved (her card's Save): each a copy of the reading as the owner's page has it (another's
    whispers still without their words: the server gives it view(redact=True)'s), in the board's SQLite file, kept across
    restarts and new readings, newest first. A saved reading still on her table takes its later talk as it comes
    (refresh: the server's, at each look and before the next reading). At most MAX."""
    MAX = 500

    def __init__(self, path, clock=time.time):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
        self.db.executescript(mail.SCHEMA)   # its table (tarot_kept) with the rest of the board's: schema.sql
        self.lock, self.clock = threading.Lock(), clock

    @staticmethod
    def _plain(r):
        """The reading as kept: done, every turn of its talk done (one cut short is as far as it got)."""
        return dict(r, done=True, talk=[dict(t, done=True) for t in r.get("talk") or []], pre=[dict(t, done=True) for t in r.get("pre") or []])

    def has(self, rid):
        with self.lock:
            return bool(rid) and self.db.execute("SELECT 1 FROM tarot_kept WHERE reading = ?", (rid,)).fetchone() is not None

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM tarot_kept").fetchone()[0]

    def keep(self, r):
        """Save reading `r` (a view's, done); again: its copy brought up to date, its time saved kept."""
        if not isinstance(r, dict) or not r.get("id") or not r.get("done") or not r.get("cards"):
            raise ValueError("only a reading she has finished can be saved")
        with self.lock:
            had = self.db.execute("SELECT 1 FROM tarot_kept WHERE reading = ?", (r["id"],)).fetchone()
            if not had and self.db.execute("SELECT COUNT(*) FROM tarot_kept").fetchone()[0] >= self.MAX:
                raise ValueError("%d readings saved already: remove one first" % self.MAX)
            self.db.execute("INSERT INTO tarot_kept (reading, kept, body) VALUES (?, ?, ?) "
                            "ON CONFLICT(reading) DO UPDATE SET body = excluded.body", (r["id"], self.clock(), json.dumps(self._plain(r))))

    def refresh(self, r):
        """A saved reading still on her table: its copy with the talk since (nothing, for one not saved)."""
        if isinstance(r, dict) and r.get("id") and r.get("done") and self.has(r["id"]):
            self.keep(r)

    def drop(self, rid):
        with self.lock:
            return self.db.execute("DELETE FROM tarot_kept WHERE reading = ?", (str(rid or ""),)).rowcount > 0

    def list(self):
        """Each saved reading in brief, newest saved first: for the list on her card."""
        with self.lock:
            rows = self.db.execute("SELECT reading, kept, body FROM tarot_kept ORDER BY kept DESC").fetchall()
        out = []
        for rid, kept, body in rows:
            try:
                r = json.loads(body)
            except ValueError:
                continue
            out.append({"id": rid, "kept": kept, "at": r.get("at"), "by": r.get("by"), "private": bool(r.get("private")),
                        "hidden": bool(r.get("hidden")), "question": r.get("question") or "", "ask": r.get("ask") or "",
                        "cards": [{k: c.get(k) for k in ("key", "name", "pos", "reversed")} for c in r.get("cards") or []],
                        "talk": len(r.get("talk") or [])})
        return out

    def get(self, rid):
        with self.lock:
            row = self.db.execute("SELECT kept, body FROM tarot_kept WHERE reading = ?", (str(rid or ""),)).fetchone()
        if not row:
            return None
        try:
            return dict(json.loads(row[1]), kept=row[0])
        except ValueError:
            return None
