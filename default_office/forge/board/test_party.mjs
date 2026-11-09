// party.js, your agents as a party: the Agent Room's figures and its list of your agents, which the kanban draws too.
// It is one making for both pages, so what it says of the room must be what the room says: each environment's clothes
// (LOOKS) as room.html's ENVS has them, each thing an agent does between turns as the board takes them (server.py
// ACT_KINDS). Both pages load it after three.js and common.js; the room's server page may fetch it with the engine's
// key. It reaches nothing of yours: no request, no storage (the room's page runs it). No browser, no network.
//
// Run: node board/test_party.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url)), read = f => readFileSync(join(here, f), "utf8");
const party = read("party.js"), room = read("room.html"), kanban = read("index.html"), server = read("server.py");
let fails = 0;
const check = (what, ok, detail = "") => { fails += !ok; console.log(`[${ok ? "ok" : "FAIL"}] ${what}${ok || !detail ? "" : ": " + detail}`); };

// party.js, run as a page runs it: a window, its globals from common.js, THREE only when something is made.
const window = {};
new Function("window", "document", party)(window, { createElement: () => ({}) });
const P = window.Party;
check("it gives the pages Figures and Party", typeof window.Figures === "function" && typeof P.make === "function");

// LOOKS: each of the room's environments, its clothes as room.html's ENVS dresses its agents.
const envs = /^const ENVS = \{\n([\s\S]*?)\n\};/m.exec(room);
check("room.html has ENVS", !!envs);
const roomLooks = {};
let cur = null;
for (const line of (envs ? envs[1] : "").split("\n")) {
  const k = /^  (\w+): \{/.exec(line);
  if (k) { cur = roomLooks[k[1]] = {}; continue; }
  if (cur) for (const f of ["ragged", "militant", "pool"]) if (new RegExp(`\\b${f}: true\\b`).test(line)) cur[f] = true;
}
check("every environment of the room's is in LOOKS, and no other", JSON.stringify(Object.keys(roomLooks).sort()) === JSON.stringify(Object.keys(P.LOOKS).sort()),
      Object.keys(roomLooks).join(",") + " vs " + Object.keys(P.LOOKS).join(","));
for (const [k, want] of Object.entries(roomLooks)) check(`…${k}: dressed as the room dresses it`, JSON.stringify(P.LOOKS[k]) === JSON.stringify(want), JSON.stringify(P.LOOKS[k]) + " vs the room's " + JSON.stringify(want));

// What an agent does between turns: what the board takes (server.py ACT_KINDS), each said on its card and in its legend.
const kinds = /^ACT_KINDS = \{([^}]*)\}/m.exec(server);
const server_kinds = kinds ? [...kinds[1].matchAll(/"(\w+)"/g)].map(m => m[1]).sort() : [];
check("the board's act kinds read", server_kinds.length > 0);
check("…each said on a card (ACT_SAID), no other", JSON.stringify(Object.keys(P.ACT_SAID).sort()) === JSON.stringify(server_kinds), Object.keys(P.ACT_SAID).join(","));
check("…each in the legend (ACT_LEGEND), no other", JSON.stringify(Object.keys(P.ACT_LEGEND).sort()) === JSON.stringify(server_kinds), Object.keys(P.ACT_LEGEND).join(","));
check("…each errand drawn on its picture (PIC_ERRANDS)", Object.keys(P.PIC_ERRANDS).every(k => server_kinds.includes(k)) && ["coffee", "cooler", "cocktail", "bananas", "pool"].every(k => P.PIC_ERRANDS[k]));

// Loaded by both pages, after three.js and common.js; served to them, and to the room's server page by the engine's key.
const order = (html, names) => names.map(n => html.indexOf(`<script src="${n}"`));
const inOrder = xs => xs.every(x => x >= 0) && xs.every((x, i) => !i || x > xs[i - 1]);
check("the room loads it after common.js and three.js", inOrder(order(room, ["common.js", "vendor/three.min.js", "party.js"])), order(room, ["common.js", "vendor/three.min.js", "party.js"]).join(" "));
check("the kanban too", inOrder(order(kanban, ["vendor/three.min.js", "common.js", "party.js"])), order(kanban, ["vendor/three.min.js", "common.js", "party.js"]).join(" "));
check("the board serves it", /if u\.path in \([^)]*"\/party\.js"[^)]*\):/.test(server));
check("…and the engine's key may fetch it (the room's server page makes its figures with it)", /"engine": \{[^}]*\("GET", "\/party\.js"\)/.test(server));

// The room's figures are made here, and only here: the room keeps its poses, nothing of the making.
for (const f of ["figureLook", "tatters", "militantKit", "weaponModel", "head", "cap", "halo", "poolKit", "blanket", "agentLegend", "agentPic", "headPic", "picRain", "waitCanvas"])
  check(`room.html does not make its own ${f}`, !new RegExp(`^function ${f}\\(`, "m").test(room));
check("the room makes its figures with party.js's, its own materials and textures given", /const FIG = Figures\(\{ M, box, texMat, svgTex, label, dispose: disposeGroup, env: \(\) => ENV, sick: s => sickNow\(s\) \}\);/.test(room));

// Nothing of yours from here: no request, no storage, no console (the pages' own clicks do those).
const code = party.replace(/\/\/[^\n]*/g, "");
const sinks = ["fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "localStorage", "sessionStorage", "indexedDB", "openPane", "consoleCall", "AgentConsole"].filter(x => code.includes(x));
check("party.js reaches no request, no storage, no console", !sinks.length, sinks.join(", "));
console.log(`\n${fails} failure(s)`);
process.exit(fails ? 1 : 0);
