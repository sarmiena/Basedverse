// Cases for common.js, the shared presentation every board page loads.
// The SVG digests pin the markup, so a change to how an agent or its platform badge LOOKS fails
// here, not on the owner's screen. Re-pin one only for a change that is meant.
//
// Run: node board/test_common.mjs
import { createRequire } from "module";
import { createHash } from "crypto";
import { readFileSync } from "fs";
const require = createRequire(import.meta.url);
const c = require("./common.js");

let fails = 0;
function caseOf(label, fn, want) {
  let got;
  try { got = fn(); } catch (e) { got = e.name; }
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log(`[${ok ? "ok " : "FAIL"}] ${label.padEnd(58)} ${JSON.stringify(got).slice(0, 60)}`);
}
const digest = s => createHash("sha256").update(s).digest("hex").slice(0, 16);

caseOf("identicon of a known session id", () => digest(c.avatar("<session-1>", 20)), "471b4d70035a70c4");
caseOf("the same session looks the same twice", () => c.avatar("s", 20) === c.avatar("s", 20), true);
caseOf("platform glyph: linux", () => digest(c.platformIcon("linux", 11)), "e648e4b6241c1dd1");
caseOf("platform glyph: a name with no glyph falls back to a pattern", () => digest(c.platformIcon("mystery", 11)), "7af76805dfd18337");
caseOf("agent avatar with its platform badge (mac)", () => digest(c.agentAvatar({ seat: "mac", session: "s1" }, 26)), "567672b92d078ba6");
caseOf("an unknown platform gets no badge", () => c.agentAvatar({ seat: "plan9", session: "s1" }, 26).includes("osi"), false);
caseOf("only the three platforms have glyphs", () => Object.keys(c.PLATFORM_GLYPHS).sort(), ["linux", "mac", "windows"]);
caseOf("context ring at a quarter of CTX_FULL", () => digest(c.ctxRing(250000, 16)), "23907f0f6bc04768");
caseOf("fmtTok", () => [c.fmtTok(102416), c.fmtTok(1234567), c.fmtTok(0)], ["102k", "1.2M", "0k"]);
{ const bs = [{ key: "o/home" }, { key: "o/two" }, { key: "o/three" }];
  const snap = { ver: 4, now: 1, rows: [{ id: "a#1" }], room: { env: "pool", acts: { s1: "doze" } } };
  caseOf("a snapshot whose only news is its agents' acts (and its ver, its clock): the same to a page",
    () => c.sameButActs(snap, { ...snap, ver: 5, now: 2, room: { env: "pool", acts: { s1: "phone" } } }), true);
  caseOf("…anything else, not", () => [c.sameButActs(snap, { ...snap, rows: [] }), c.sameButActs(snap, { ...snap, room: { env: "cave", acts: null } }), c.sameButActs(null, snap)], [false, false, false]);
  caseOf("a board's colour: by its place, the home board first", () => bs.map(b => c.boardColor(bs, b.key)), c.BOARD_COLORS.slice(0, 3));
  caseOf("…no board, or one not listed: grey", () => [c.boardColor(bs, "none"), c.boardColor(bs, "o/gone"), c.boardColor(bs, null)], [c.NO_BOARD_COLOR, c.NO_BOARD_COLOR, c.NO_BOARD_COLOR]);
  caseOf("…none of them a rank's red, yellow, green or blue, nor the same twice", () => new Set(c.BOARD_COLORS).size === c.BOARD_COLORS.length
    && !c.BOARD_COLORS.some(x => ["#e5534b", "#e3b341", "#3fb950", "#3b9eff"].includes(x)), true); }
caseOf("fmtAge at each unit edge", () => [null, 59, 60, 3599, 3600, 86400 * 2 + 5].map(c.fmtAge), ["?", "59s", "1m", "59m", "1h", "2d"]);
caseOf("ageSecs of an unreadable stamp is null", () => c.ageSecs("garbage"), null);
caseOf("esc", () => c.esc(`<a href="x">'&'</a>`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");

// The page side of the move: index.html loads common.js BEFORE its own script, and defines none of
// the shared names itself (a second definition would shadow or clash with the shared one).
const page = readFileSync(new URL("./index.html", import.meta.url), "utf8");
caseOf("index.html loads common.js before its inline script",
  () => { const at = page.indexOf('<script src="common.js"'); return at >= 0 && at < page.indexOf("<script>"); }, true);
caseOf("index.html defines none of the shared names",
  () => Object.keys(c).filter(n => new RegExp(`(function|const|let|var)\\s+${n}\\b`).test(page)), []);

// The week's pace (usagePace): where the use so far, at its rate, ends up by the reset.
const DAY = 86400e3, RESET = Date.UTC(2026, 9, 30, 19);
caseOf("pace: 17% two days in (of 7): on track", () => c.usagePace(17, RESET, RESET - 5 * DAY), { state: "fine" });
caseOf("pace: 60% five days in: 84% by the reset, still fine", () => c.usagePace(60, RESET, RESET - 2 * DAY).state, "fine");
caseOf("pace: 68% five days in: close (95%)", () => c.usagePace(68, RESET, RESET - 2 * DAY).state, "close");
caseOf("pace: 80% four days in: over, the limit a day later", () => { const p = c.usagePace(80, RESET, RESET - 3 * DAY); return [p.state, (p.hitMs - (RESET - 3 * DAY)) / DAY]; }, ["over", 1]);
caseOf("pace: 100%: out", () => c.usagePace(100, RESET, RESET - DAY), { state: "out" });
caseOf("pace: half a day in: too early to tell", () => c.usagePace(5, RESET, RESET - 6.5 * DAY), null);
caseOf("pace: the reset passed: nothing to say", () => c.usagePace(5, RESET, RESET + 1), null);
caseOf("pace text: on track names the reset's day", () => c.usagePaceText({ used: 17, resets_at: new Date(RESET).toISOString(), passed: false }, RESET - 5 * DAY).startsWith("On track. You should reach "), true);
caseOf("pace text: a passed window says nothing", () => c.usagePaceText({ used: 0, resets_at: new Date(RESET).toISOString(), passed: true }, RESET + 1), "");
// A row's name (usageName), as /usage gives it and short for the kanban's header; the week for all models (usageWeek).
const ROWS = [{ kind: "session" }, { kind: "weekly_all" }, { kind: "weekly_scoped", name: "Fable" }, { kind: "monthly_new" }];
caseOf("row names, in full", () => ROWS.map(w => c.usageName(w, true)), ["Current session", "All models", "Fable", "monthly_new"]);
caseOf("row names, short", () => ROWS.map(w => c.usageName(w, false)), ["Session", "Week", "Fable", "monthly_new"]);
caseOf("the week for all models", () => c.usageWeek({ limits: ROWS }), { kind: "weekly_all" });
caseOf("no usage: no week", () => [c.usageWeek({}), c.usageWeek(undefined)], [undefined, undefined]);

// The boards: which a ref is on, its repo's owner, and a GitHub link read back as a ref.
const D = { forge: "https://github.com", owner: "Acme", boards: [{ key: "Acme/App", repos: ["App"] }, { key: "sam/Tool", repos: ["Tool"] }] };
caseOf("a ref on another board is that board's", () => c.boardOf(D.boards, "tool#3").key, "sam/Tool");
caseOf("any other ref is the home board's", () => [c.boardOf(D.boards, "App#1").key, c.boardOf(D.boards, "Other#1").key], ["Acme/App", "Acme/App"]);
caseOf("no boards (an older server): none", () => c.boardOf(undefined, "App#1"), null);
caseOf("a ref's owner: its board's, else the snapshot's", () => [c.refOwner(D, "Tool#3"), c.refOwner(D, "App#1"), c.refOwner({ owner: "Old" }, "App#1")], ["sam", "Acme", "Old"]);
caseOf("a link under any board's owner reads back as a ref", () => [c.hrefRef(D, "https://github.com/sam/Tool/issues/3"), c.hrefRef(D, "https://github.com/Acme/App/pull/9#x")], ["Tool#3", "App#9"]);
caseOf("a link under no board's owner is none", () => c.hrefRef(D, "https://github.com/else/Tool/issues/3"), null);
caseOf("a screen's gap of blank rows is one row", () => c.squeezed("top\r\n\r\n\r\n\r\n  \r\n───\r"), "top\r\n\n───\r");
caseOf("...two blank rows, or one, stay as they are", () => c.squeezed("a\n\nb\n\n\nc"), "a\n\nb\n\n\nc");
caseOf("...a gap's escapes kept, so the colours after it hold", () => c.squeezed("a\n\x1b[31m\n\n\x1b[1m \nb"), "a\n\x1b[31m\x1b[1m\nb");
caseOf("...and rows of escapes alone count as blank", () => c.squeezed("a\n\x1b[0m\n\x1b]0;t\x07\n\x1b[0m\nb").split("\n").length, 3);
caseOf("...a blank screen stays blank", () => c.squeezed("\n\n\n\n"), "");

console.log(`\n${fails} failure(s)`);
process.exit(fails ? 1 : 0);
