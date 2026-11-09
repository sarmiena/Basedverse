// What a visitor's click may reach in the Agent Room (room.html): only the fun. Every click, yours or a visitor's,
// goes through INTERACTIONS; a visitor's only through those marked `visitors: true`. This follows every call that
// each of those can make, through room.html's own functions, and fails if any path reaches something of yours: a
// request (fetch, a WebSocket, a beacon), the console (bin/consoles), Visit's lobby (knock, kick, ...), a line in the room's
// chat (sayAs: the tarot reader's, said on the room's page as she reads, never at a visitor's click), or what this
// browser keeps for you (store). It also checks the gate itself: interact skips the rest for a visitor, the
// console's entry is never a visitor's, and openConsole takes only a click of your own. No browser, no network.
//
// Run: node board/test_visitors.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "room.html"), "utf8");
let fails = 0;
const check = (what, ok, detail = "") => { fails += !ok; console.log(`[${ok ? "ok" : "FAIL"}] ${what}${ok || !detail ? "" : ": " + detail}`); };

// The room's own functions, at the left margin: `function f(` and `const f = (...) =>` / `const f = x =>`. Each one's
// text runs to the next thing at the left margin (what is nested in it is indented, and counted as its own).
const lines = html.split("\n"), defs = new Map();
const DEF = /^(?:async\s+)?function\s+([\w$]+)\s*\(|^const\s+([\w$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>/;
for (let i = 0, cur = null; i < lines.length; i++) {
  const m = DEF.exec(lines[i]);
  if (m) { cur = m[1] || m[2]; defs.set(cur, lines[i] + "\n"); continue; }
  if (/^\S/.test(lines[i])) { cur = null; continue; }   // anything else at the margin ends the one before
  if (cur) defs.set(cur, defs.get(cur) + lines[i] + "\n");
}
const strip = t => t.replace(/\/\/[^\n]*/g, "");   // comments name calls they do not make
const calls = t => new Set([...strip(t).matchAll(/(?<![\w$.])([\w$]+)\s*\(/g)].map(m => m[1]));

// What a visitor's click must never reach. A method of these names counts too (v.kick(...), ws.send is Visit's own).
const SINKS = ["fetch", "XMLHttpRequest", "WebSocket", "sendBeacon", "consoleCall", "openConsole", "pollConsole", "openPane", "store",
  "askName", "knock", "kick", "answer", "leaveLobby", "joinLobby", "setStatus", "setMaxVisitors", "rename", "localStorage", "sayAs"];
const sinkIn = t => SINKS.find(s => new RegExp(`(?<![\\w$])${s}\\s*\\(|\\.${s}\\s*\\(|\\b${s}\\.`).test(strip(t)));

// The room's own records, which anything that happens in it may add to: the safety board's tally (accident: a death
// by exhaustion, malaria), kept in this browser with store. Not yours: the walk does not go into them.
const ROOMS_OWN = new Set(["saveStats"]);
// From some code: the first path to a sink, through the room's own functions, or null.
function reaches(code, start) {
  const seen = new Set(ROOMS_OWN), queue = [[start, code, [start]]];
  while (queue.length) {
    const [, text, path] = queue.shift();
    const sink = sinkIn(text);
    if (sink) return [...path, sink].join(" → ");
    for (const f of calls(text)) if (defs.has(f) && !seen.has(f)) { seen.add(f); queue.push([f, defs.get(f), [...path, f]]); }
  }
  return null;
}

// INTERACTIONS: one entry a line, each with its name and `visitors` said outright.
const reg = /^const INTERACTIONS = \[\n([\s\S]*?)\n\];/m.exec(html);
check("room.html has INTERACTIONS", !!reg);
const entries = reg ? reg[1].split("\n").filter(l => /^\s*\{ name:/.test(l)).map(l => ({
  name: /name: "([^"]+)"/.exec(l)[1], visitors: (/visitors: (true|false)/.exec(l) || [])[1], code: l })) : [];
check("each entry says whether it is a visitor's (visitors: true or false)", entries.length > 0 && entries.every(e => e.visitors),
  entries.filter(e => !e.visitors).map(e => e.name).join(", "));
const open = entries.filter(e => e.visitors === "true").map(e => e.name);
// The list a visitor may reach is a decision: opening another is done here too, on purpose. "spread": the tarot reader's
// cards up in the room, in 3D, for whoever is there (the user, 2026-11-02: "if you're a visitor ... render a 3d version
// of the tarot reading"): it changes how the room looks, and asks the board nothing (it reaches nothing, below).
check("a visitor's are the fun ones, and only those", JSON.stringify(open) === JSON.stringify(["throw", "spread", "pet", "thing", "dozer"]), open.join(", "));
check("the console's entry is yours alone", entries.some(e => e.name === "console" && e.visitors === "false"));
check("a card's ticket (an issue's body) is yours alone", entries.some(e => e.name === "ticket" && e.visitors === "false"));
check("the wall's switch (the room's board, kept by the board) is yours alone", entries.some(e => e.name === "board" && e.visitors === "false"));
check("the tarot reader (a reading asked of the board; her cards on your screen) is yours alone", entries.some(e => e.name === "tarot" && e.visitors === "false"));
check("your teleport pad (where you may teleport, the Visit panel's list on your screen) is yours alone", entries.some(e => e.name === "teleport" && e.visitors === "false" && /screen: true/.test(e.code)));
// Her talk: Visit's chat reaches your page alone (the engine), with everyone's lines; only one of yours, said aloud in
// your own room, asks her (tarotHeard), and nothing else in it reaches the board.
const heard = defs.get("tarotHeard") || "";
check("only your own line, said aloud at home, asks the tarot reader (tarotHeard)",
  /if \(!l\.mine \|\| l\.old \|\| l\.scope !== "say" \|\| l\.conversation !== "room:" \+ v\.key\) return;/.test(heard)
  && heard.indexOf("return;") < heard.indexOf("tarotAsk(") && /v\.on\("chat", l => \{ try \{ tarotHeard\(l, v\);/.test(html));
// Visit 3.3 (basedverse#48): a line meant for her (at her, or whispered to her) reaches the room's page, whoever said it
// (addressed). The user agreed that visitors may ask her too (2026-11-01: "i want my visitors to be able to interact
// with madame vesna"): tarotAddressed asks the board for anyone, a visitor as a visitor (owner false: the board rests
// one who asks too much, tarot.py's limit, held by test_tarot.py), and nothing but her lines reaches it.
const addr = defs.get("tarotAddressed") || "";
check("a line meant for the tarot reader asks her for whoever said it, a visitor as a visitor (tarotAddressed)",
  /if \(e\.figure !== TAROT_NPC \|\| !e\.from\) return;/.test(addr) && addr.indexOf("return;") < addr.indexOf("tarotPost(")
  && /owner \? \{ owner: true, name \} : \{ owner: false,/.test(addr) && /v\.on\("addressed", e => \{ tarotAddressed\(e, v\); clubAddressed\(e\); \}\)/.test(html));
// The Players Club's Penny (the user, 2026-11-05: visitors' stats, "come up with a clever solution"): a line meant for
// her answered with the sayer's own stats, whispered to them alone, and nothing else (test_casino.mjs has the rest).
const clubAddr = defs.get("clubAddressed") || "";
check("a line meant for Penny (the Players Club) answered to its sayer alone with their own stats, nothing else reached (clubAddressed)",
  /if \(e\.figure !== CLUB_NPC \|\| !e\.from \|\| !cas \|\| !cas\.pclub\) return;/.test(clubAddr) && clubAddr.includes('to = e.from.id || "owner"')
  && [...calls(clubAddr)].filter(f => defs.has(f)).every(f => ["clubAddressed", "casinoOf", "ownerBody", "clubPlayers", "clubSay", "clubLines", "clubShow"].includes(f)), [...calls(clubAddr)].filter(f => defs.has(f)).join(" "));
for (const e of entries.filter(e => e.visitors === "true")) {
  const path = reaches(e.code, e.name);
  check(`a visitor's "${e.name}" reaches nothing of yours`, !path, path);
}
// The gate: interact passes a visitor only what is marked visitors: true; a visitor's click goes through interact,
// with its own body as the doer, through Visit's point.
const interact = defs.get("interact") || "";
check("interact skips, for anyone but you, what is not visitors: true (on your own page As a visitor, what Visit's page does for a visitor)",
  /if \(!yours && it\.visitors !== true && !\(doer === me && it\.asVisitor === true\)\) continue;/.test(interact)
  && entries.filter(e => /asVisitor: true/.test(e.code)).map(e => e.name).sort().join() === "tarot,typed"
  && entries.filter(e => /asVisitor: true/.test(e.code)).every(e => /screen: true/.test(e.code)));   // (a screen entry never runs for a point)
check("…you: this page's click, or your own body's point on the room's page (engine/SPEC.md 4), and only owns says so", /const yours = owns\(doer\);/.test(interact)
  && /^const owns = doer => !asVisitor && \(doer === me \|\| doer\.owner === true\);$/m.test(html));
// As a visitor (the room's setting, room.guest): it only ever takes away, never adds what a visitor could not do.
check("…As a visitor is the board's word (room.guest) on every page, set from your page's switch alone",
  /!!\(d\.room && d\.room\.guest\) !== asVisitor\) \{ asVisitor = !asVisitor;/.test(html)   // (its declaration, the board's word, the switch, and the switch undone when the board will not take it) && (html.match(/(?<![\w$.])asVisitor = /g) || []).length === 4
  && (html.match(/body: JSON\.stringify\(\{ guest: on \}\)/g) || []).length === 1);
check("…an entry that opens on your screen never runs for a point", /if \(it\.screen && doer !== me\) continue;/.test(interact) && entries.filter(e => /screen: true/.test(e.code)).map(e => e.name).sort().join() === "board,console,tarot,teleport,ticket,typed");
check("your body's point is marked yours by Visit's word alone (visitor.owner)", /if \(e\.visitor && e\.visitor\.owner === true\) b\.owner = true;/.test(defs.get("visitorPoint") || ""));
check("a visitor's click goes through interact, its body the doer", /interact\(visitorPointer\(e\), b\)/.test(defs.get("visitorPoint") || ""));
check("…and is limited to VISITOR_POINTS a second", /b\.points\.length >= VISITOR_POINTS/.test(defs.get("visitorPoint") || ""));
check("Visit's point is taken there and nowhere else", (html.match(/\.on\("point"/g) || []).length === 1 && /v\.on\("point", visitorPoint\)/.test(html));
check("openConsole takes only a click of your own, while it is handled", /if \(!yourClick \|\| !\(navigator\.userActivation && navigator\.userActivation\.isActive\)\) return;/.test(defs.get("openConsole") || ""));
const sets = [...html.matchAll(/(?<!let )yourClick = ([^;]+);/g)].map(m => m[1]);
check("…which only a real click of yours sets, and unsets: on the room (your pointerup), on a ticket's own buttons, or on your agents' list",
      JSON.stringify(sets) === JSON.stringify(["e.isTrusted", "false", "e.isTrusted", "false", "e.isTrusted", "false"]) && /let yourClick = false;/.test(html), sets.join(", "));
check("…your agents' list's: set for a row's click, only around its ticket and openConsole (as its card in the room), unset after, and none As a visitor",
      /if \(!owns\(me\)\) return say\([^)]*\);\n\s*yourClick = e\.isTrusted;[^\n]*\n\s*try \{ agentTicket\(row\.dataset\.ag\); openConsole\(row\.dataset\.ag\); \} finally \{ yourClick = false; \}/.test(html) && /agentsEl\.addEventListener\("click"/.test(html));
check("…a ticket's: set for its panel's own buttons, only around ticketAct, unset after",
      /yourClick = e\.isTrusted;[^\n]*\n\s*try \{ ticketAct\(t, tk\.ref\); \} finally \{ yourClick = false; \}/.test(html) && /tk\.el\.addEventListener\("click"/.test(html));
const direct = [...html.matchAll(/(?<![\w$])openConsole\(/g)].length, viaTicket = (defs.get("ticketAct") || "").match(/(?<![\w$])openConsole\(/g) || [];
check("openConsole is called only from the console's entry, a ticket's acts and your agents' list (and defined once)", direct === 3 + viaTicket.length && viaTicket.length === 3, `${direct} mentions, ${viaTicket.length} in ticketAct`);
check("…and ticketAct only from its panel's listener", (html.match(/(?<![\w$])ticketAct\(/g) || []).length === 2);
// (and no visitor's entry reaches it: each reaches nothing of yours, above, and ticketAct reaches the console)
// Visit 0.9's interactions: each type a visitor's crosshair shows is cosmetic, and runs through the same gate.
const typed = defs.get("visitorTyped") || "";
check("a typed interaction goes through visitorPoint (the gate), marked typed", /return visitorPoint\(\{[^}]*\}, true\);/.test(typed));
const tpath = reaches(typed, "visitorTyped");
check("…and reaches nothing of yours", !tpath, tpath);
check("visitorPoint leaves to Visit what it runs as an interaction (no click runs twice)", /if \(!typed && typedHit\(e\)\) \{[^\n]*return null; \}/.test(defs.get("visitorPoint") || ""));
const hpath = reaches(defs.get("typedHit") || "", "typedHit");
check("typedHit reaches nothing of yours", !hpath, hpath);
const reg2 = /const types = Object\.fromEntries\(Object\.entries\(VISITOR_TYPES\)\.map\(\(\[type, label\]\) => \[type, \{ cosmetic: true, label, run: visitorTyped \}\]\)\);/.test(html);
check("Visit is given VISITOR_TYPES, every one cosmetic, every one run by visitorTyped", reg2);
// Visit 2.1.1: and the board's decks, the wheel's, for the owner alone (who: "owner": no visitor runs it, nor sees it).
const deckType = /if \(visitAtLeast\("2\.1\.1"\)\) types\.deck = \{ cosmetic: true, label: "scroll", who: "owner", wheel: true, run: deckWheel \};/.test(html);
// And the tarot reader's scroll, up in the room (the spread): cosmetic, anyone's, the wheel's (spreadWheel) or a click's
// (visitorTyped, the gate).
const scrollType = /if \(visitAtLeast\("2\.1\.1"\)\) types\.tarotscroll = \{ cosmetic: true, label: VISITOR_TYPES\.tarotscroll, wheel: true, run: e => e\.wheel \? spreadWheel\(e\) : visitorTyped\(e\) \};/.test(html);
// And the Players Club's: a click on its kiosk or on Penny (visitorTyped, the gate), or her card's button (clubAct: the
// presser's card on the kiosk's screen): cosmetic, anyone's.
const playersType = /types\.players = \{ cosmetic: true, label: VISITOR_TYPES\.players, run: e => e\.figure \? clubAct\(e\) : visitorTyped\(e\) \};/.test(html);
check("…and the decks: cosmetic, the owner's alone, the wheel's, run by deckWheel; the tarot reader's scroll; the Players Club's card; no other type",
  deckType && scrollType && playersType && (html.match(/(?<![\w$])types\.\w+ = /g) || []).length === 3);
const wpath = reaches(defs.get("spreadWheel") || "", "spreadWheel");
check("…the scroll's wheel reaches nothing of yours", !!defs.get("spreadWheel") && !wpath, wpath);
const dpath = reaches(defs.get("deckWheel") || "", "deckWheel");
check("…which reaches nothing of yours", !!defs.get("deckWheel") && !dpath, dpath);
check("…and v.interactions is called only there, with those", (html.match(/\.interactions\(/g) || []).length === 1 && /v\.interactions\(types\);/.test(html));
check("a turn of the wheel is never a click (visitorPoint)", /^\s*if \(e\.wheel\) return null;/m.test(defs.get("visitorPoint") || ""));
check("no phone (the user took it out of the room): no click entry of this room's is one", !entries.some(e => e.name === "phone") && !/\.phone\(true/.test(html));

// And the check itself sees a sink when there is one.
check("(the check finds a sink through a call)", !!reaches("x => openConsole(x)", "probe") && !!reaches("x => store('k', x)", "probe"));

// What a visitor sees stays current: a picture painted afresh (the usage screen, the tally, the safety board: boardFace)
// is never the landscape, which Visit sends once (markLand skips a mesh marked `live`), or a page keeps its first painting.
{
  const at = [...html.matchAll(/boardFace\(/g)].map(m => html.slice(0, m.index).split("\n").length).filter(n => !/^function boardFace/.test(lines[n - 1]));
  const bad = at.filter(n => !lines.slice(n - 1, n + 2).some(l => /\.userData\.live = true/.test(l)));
  check("every painted board's face is live, never the landscape", at.length >= 2 && !bad.length, bad.map(n => "line " + n).join(", ") || "no boardFace calls found");
}

console.log(`${fails} fail`);
process.exit(fails ? 1 : 0);
