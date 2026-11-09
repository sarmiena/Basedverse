// Shared presentation for every board page: the identicon, the platform badge, the context ring,
// and the formatters. Loaded by index.html and the Agent Room (room.html) before their own scripts,
// so the same agent looks the same everywhere. Pure functions and tables only: no DOM, no page state. test_common.mjs pins them.
"use strict";

// HTML-escape for text and attribute values.
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function hash32(s) { let h = 0x811c9dc5; for (const c of String(s)) { h ^= c.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; } return h; }
// A seeded random sequence: the scenery is the same at every redraw, never a new jungle per snapshot.
function rng(seed) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
// A 5×5 mirrored pattern from a seed, as SVG cells on a 7×7 grid, plus the seed's hue.
function identCells(seed) {
  const h = hash32(seed), bits = hash32(seed + "·");
  let cells = "";
  for (let y = 0; y < 5; y++) for (let x = 0; x < 3; x++) {
    if (!((bits >>> (y * 3 + x)) & 1)) continue;
    cells += `<rect x="${x + 1}" y="${y + 1}" width="1" height="1"/>`;
    if (x < 2) cells += `<rect x="${5 - x}" y="${y + 1}" width="1" height="1"/>`;
  }
  return { cells, hue: h % 360 };
}
// An agent's identicon, from the session id: the same agent looks the same on every machine.
function avatar(session, px) {
  const { cells, hue } = identCells(session);
  return `<svg width="${px}" height="${px}" viewBox="0 0 7 7" aria-hidden="true"><rect width="7" height="7" fill="hsl(${hue} 45% 88%)"/><g fill="hsl(${hue} 55% 38%)" shape-rendering="crispEdges">${cells}</g></svg>`;
}
// A platform's icon: a pixel glyph per known platform, in its colour, on a dark SQUARE tile (an
// agent's identicon is a light circle). It badges the corner of an agent's identicon with the
// platform its machine runs (the roster entry's `seat`). A platform with no glyph falls back to the
// identicon pattern seeded by its name.
const PLATFORM_GLYPHS = {
  mac:     ["#d0d7de", ".........", ".XXXXXXX.", ".X.....X.", ".X.....X.", ".X.....X.", ".XXXXXXX.", "XXXXXXXXX", "XXXXXXXXX", "........."],
  windows: ["#3b9eff", ".........", ".XXX.XXX.", ".XXX.XXX.", ".XXX.XXX.", ".........", ".XXX.XXX.", ".XXX.XXX.", ".XXX.XXX.", "........."],
  linux:   ["#e6edf3", "...XXX...", "..X.X.X..", "..XXXXX..", ".XXX.XXX.", ".XX...XX.", ".XX...XX.", ".XXX.XXX.", "..XXXXX..", ".XX...XX."],
};
function agentAvatar(a, px) {
  const os = PLATFORM_GLYPHS[a.seat] ? a.seat : null;
  return `<span class="av" title="${esc(os || a.seat || "unknown platform")}">${avatar(a.session, px)}${os ? platformIcon(os, Math.round(px * 0.55)) : ""}</span>`;
}
function platformIcon(name, px) {
  const g = PLATFORM_GLYPHS[name];
  if (g) {
    let cells = "";
    g.slice(1).forEach((row, y) => [...row].forEach((c, x) => { if (c === "X") cells += `<rect x="${x + 1}" y="${y + 1}" width="1" height="1"/>`; }));
    return `<svg class="osi" width="${px}" height="${px}" viewBox="0 0 11 11" aria-hidden="true"><rect width="11" height="11" rx="1.5" fill="#161b22"/><g fill="${g[0]}" shape-rendering="crispEdges">${cells}</g></svg>`;
  }
  const { cells, hue } = identCells("os:" + name);
  return `<svg class="osi" width="${px}" height="${px}" viewBox="0 0 7 7" aria-hidden="true"><rect width="7" height="7" rx="1" fill="hsl(${hue} 40% 26%)"/><g fill="hsl(${hue} 70% 72%)" shape-rendering="crispEdges">${cells}</g></svg>`;
}
// A context ring: the arc is the share of CTX_FULL in use.
const CTX_FULL = 1e6;
function ctxRing(n, px) {
  const r = 6, c = 2 * Math.PI * r, f = Math.min(1, (Number(n) || 0) / CTX_FULL);
  return `<svg class="ring" width="${px}" height="${px}" viewBox="0 0 16 16" aria-hidden="true"><circle class="bg" cx="8" cy="8" r="${r}" fill="none" stroke-width="2.5"/><circle class="fg" cx="8" cy="8" r="${r}" fill="none" stroke-width="2.5" stroke-dasharray="${(f * c).toFixed(2)} ${c.toFixed(2)}"/></svg>`;
}
// Tokens, short: 102416 -> "102k", 1234567 -> "1.2M".
function fmtTok(n) { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : Math.round(n / 1e3) + "k"; }
function ageSecs(iso) { const t = Date.parse(iso); return isNaN(t) ? null : Math.max(0, Math.round((Date.now() - t) / 1000)); }
function fmtAge(s) { return s === null ? "?" : s >= 86400 ? Math.floor(s / 86400) + "d" : s >= 3600 ? Math.floor(s / 3600) + "h" : s >= 60 ? Math.floor(s / 60) + "m" : s + "s"; }

// The account's usage limits (the board's `usage`, a row per bar /usage draws). A row's name: /usage's in
// full ("Current session", "All models", "Fable"), or short for the kanban's header ("Session", "Week", "Fable").
function usageName(w, long) {
  if (w.kind === "session") return long ? "Current session" : "Session";
  if (w.kind === "weekly_all") return long ? "All models" : "Week";
  return w.name || w.kind;
}
// The week for all models: the row the week's pace (below) is told from.
const usageWeek = u => ((u && u.limits) || []).find(w => w.kind === "weekly_all");
// A row's reset, as "Mon 12:20 AM" (the session's) or "Friday 7:00 PM" (a week's), in the viewer's own time.
function fmtReset(iso, long) {
  const t = Date.parse(iso);
  return isNaN(t) ? "?" : new Date(t).toLocaleString([], { weekday: long ? "long" : "short", hour: "numeric", minute: "2-digit" });
}
// The week's pace: at the rate it has been used so far this window (USAGE_WEEK long), where it ends up by the
// reset. "fine": with room to spare (under 85%); "close": near the limit; "over": the limit comes first, at
// `hitMs`; "out": reached. Too early in the week to tell (a day in), null.
const USAGE_WEEK = 7 * 86400e3;
function usagePace(used, resetMs, nowMs) {
  const gone = USAGE_WEEK - (resetMs - nowMs);
  if (!(used >= 0) || !(resetMs > nowMs)) return null;
  if (used >= 100) return { state: "out" };
  if (gone < 86400e3) return null;
  const end = used / gone * USAGE_WEEK;
  if (end <= 85) return { state: "fine" };
  if (end <= 100) return { state: "close" };
  return { state: "over", hitMs: nowMs + (100 - used) / used * gone };
}
// What /usage says above its bars, for the week: one line.
function usagePaceText(w, nowMs) {
  const p = w && !w.passed ? usagePace(w.used, Date.parse(w.resets_at), nowMs) : null, day = w ? fmtReset(w.resets_at, true).split(" ")[0] : "";
  if (!p) return "";
  if (p.state === "fine") return `On track. You should reach ${day}'s reset with room to spare.`;
  if (p.state === "close") return `Cutting it close: at this pace you reach ${day}'s reset near the limit.`;
  if (p.state === "over") return `At this pace you reach the weekly limit around ${fmtReset(new Date(p.hitMs).toISOString(), false)}, before ${day}'s reset.`;
  return `Weekly limit reached. It resets ${fmtReset(w.resets_at, true)}.`;
}

// The boards (/api/board's `boards`: the home board first, then one per other repo, each with its repos): the one a
// `repo#N` is on (another board whose repos include it, else the home board, every repo of its owner when it names
// none), the owner a ref's repo is under (for a link to it), and a GitHub issue or PR link read back as `repo#N` when
// it is under one of the boards' owners (else null). `d` is a snapshot: its `boards`, and `owner` for an older server.
function boardOf(boards, ref) {
  const repo = String(ref || "").split("#")[0].toLowerCase(), bs = boards || [];
  return bs.find((b, i) => i && (b.repos || []).some(r => r.toLowerCase() === repo)) || bs[0] || null;
}
// A board's colour, to tell its agents from another board's at a glance (the user: "we need a way to visually tell
// workrooms apart ... color code the agent cards"): the kanban's agent rows and board tabs, the Agent Room's name cards,
// its wall's board and its legend. By the board's place among `boards` (the home board first), from colours clear of
// the ranks' red, yellow, green and blue; an agent set up for no board ("none", or one not listed), grey.
const BOARD_COLORS = ["#8250df", "#0e9aa7", "#e8590c", "#d6336c", "#5c940d", "#9c6b30"], NO_BOARD_COLOR = "#8b949e";
// Whether two snapshots differ only in what changes with every serve (ver, now) and in the room's agents' acts (room.acts:
// what the room's page plays out between turns, for your agents' list): a page that shows none of it need not draw again.
function sameButActs(a, b) {
  const plain = d => JSON.stringify(Object.assign({}, d, { ver: 0, now: 0, room: d.room ? Object.assign({}, d.room, { acts: null }) : d.room }));
  return !!a && !!b && plain(a) === plain(b);
}
function boardColor(boards, key) {
  const i = key && key !== "none" ? (boards || []).findIndex(b => b.key === key) : -1;
  return i < 0 ? NO_BOARD_COLOR : BOARD_COLORS[i % BOARD_COLORS.length];
}
function refOwner(d, ref) { const b = boardOf(d && d.boards, ref); return b ? b.key.split("/")[0] : (d && d.owner) || ""; }
function hrefRef(d, href) {
  if (!d || !d.forge) return null;
  for (const o of new Set([d.owner, ...(d.boards || []).map(b => b.key.split("/")[0])].filter(Boolean))) {
    const base = `${d.forge}/${o}/`;
    if (!String(href).startsWith(base)) continue;
    const m = String(href).slice(base.length).match(/^([^/]+)\/(?:issues|pull)\/(\d+)\/?(?:[?#].*)?$/);
    if (m) return m[1] + "#" + m[2];
  }
  return null;
}

// An agent's screen (a pane's rows, ANSI and all) with each run of more than two blank rows made one. A pane taller
// than what is on it (an agent new in a tall herdr tab) has its lines at the top and a gap of blank rows under them,
// down to whatever is on its last row; a console that keeps to the foot of the screen would show only the gap. A row
// is blank when it is nothing but whitespace and escapes; a run's escapes are kept, so the colours after it stay right.
function squeezed(text) {
  const out = [], plain = l => l.replace(/\x1b\[[0-?]*[ -\/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]?/g, "");
  let run = [];
  const flush = () => { out.push(...(run.length > 2 ? [run.map(l => l.replace(/\s+/g, "")).join("")] : run)); run = []; };
  for (const l of String(text).split("\n")) {
    if (!plain(l).trim()) { run.push(l); continue; }
    flush(); out.push(l);
  }
  flush();
  return out.join("\n");
}

// Signed out while the page is open (the session ran out, or was revoked: bin/auth): any call the board refuses
// for it (401, X-Sign-In) takes the page to the sign-in page, which brings it back here once signed in.
if (typeof window !== "undefined" && window.fetch && !window.fetch.signIn) {
  const fetched = window.fetch.bind(window);
  window.fetch = Object.assign(async (...args) => {
    const r = await fetched(...args);
    if (r.status === 401 && r.headers.get("X-Sign-In")) location.href = "/login?e=expired&next=" + encodeURIComponent(location.pathname + location.search);
    return r;
  }, { signIn: true });
}

// Node (test_common.mjs) reads the same file; a browser ignores this (its `window` is defined, so an
// element named "module" on some page cannot be mistaken for node's).
if (typeof window === "undefined" && typeof module !== "undefined") module.exports = { sameButActs, esc, hash32, rng, identCells, avatar, PLATFORM_GLYPHS, agentAvatar, platformIcon, CTX_FULL, ctxRing, fmtTok, ageSecs, fmtAge, usageName, usageWeek, fmtReset, USAGE_WEEK, usagePace, usagePaceText, boardOf, BOARD_COLORS, NO_BOARD_COLOR, boardColor, refOwner, hrefRef, squeezed };
