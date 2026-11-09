// The pool club's casino (room.html casinoShell), as far as it can be read without a browser: that it stands on the
// land (past LAND, in a world, nothing is drawn), beside the room and clear of it, the club's palms off its lot; that
// every environment's sounds fit what Visit takes (at most 32: the casino's own are the pool club's alone); that your
// visitors may play its slots, sit at its bar and put a chip on its roulette table, by the types Visit shows them;
// that its canopy is lifted off for the overview (and found by its name on your page); its games' rules (a chip on
// the roulette layout: the spot under it, and what it pays; a blackjack hand's total; craps' calls); its odds; and
// that the room's page declares each of its names once (a second function of the same name silently replaces the
// first: the casino's cards once replaced the tarot reader's card). No browser, no network.
//
// Run: node board/test_casino.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "room.html"), "utf8");
let fails = 0;
const check = (what, ok, detail = "") => { fails += !ok; console.log(`[${ok ? "ok" : "FAIL"}] ${what}${ok || !detail ? "" : ": " + detail}`); };
const block = (start, end = "\n};") => { const a = html.indexOf(start); if (a < 0) return ""; return html.slice(a, html.indexOf(end, a) + end.length); };
const value = re => { const m = re.exec(html); return m ? Function(`return (${m[1]})`)() : null; };

// ---- on the land, beside the room
const LAND = value(/^const LAND = (\{[^}]*\});/m), CAS = value(/^const CAS = (\{[^}]*\});/m), ROOM_R = value(/^const ROOM_R = (\d+)/m);
check("LAND, CAS and ROOM_R read", !!LAND && !!CAS && ROOM_R !== null);
if (LAND && CAS) {
  const [tx0, tx1, tz0, tz1] = CAS.TOWER;
  check("the casino stands on the land", CAS.X0 >= LAND.x0 && CAS.X1 <= LAND.x1 && CAS.Z1 >= LAND.z0 && CAS.Z0 <= LAND.z1, JSON.stringify(CAS));
  check("…its tower too, behind it", tx0 >= LAND.x0 && tx1 <= LAND.x1 && tz0 >= LAND.z0 && tz1 <= CAS.Z1 && tx0 >= CAS.X0 && tx1 <= CAS.X1, JSON.stringify(CAS.TOWER));
  check("…beside the room's open right side, clear of it and of its back wall (z -8.5)", CAS.X0 > ROOM_R + 1 && CAS.Z0 < -8.5 - 1, `x from ${CAS.X0}, the room to ${ROOM_R}`);
  check("its gate in its front edge, the aisle through it", CAS.GATE[0] > CAS.X0 && CAS.GATE[1] < CAS.X1 && CAS.AISLE > CAS.GATE[0] && CAS.AISLE < CAS.GATE[1]);
  check("the club's palms and free daybeds keep off its lot (offLot)", /if \(!offLot\(x, z\)\) return; palm\(/.test(html) && /!offLot\(x, z\) \|\| obstacles\.some/.test(html));
}

// ---- Visit takes at most 32 sounds a room: each loop (LOOPS, but another environment's own: `only`), the bed by day
// and by night, each pop (POPS, but another's own: POP_ONLY)
const envs = [...block("const ENVS = {").matchAll(/^  (\w+): \{/gm)].map(m => m[1]);
const loops = [...block("const LOOPS = {").matchAll(/^  (\w+): \{([^\n]*)/gm)].map(m => [m[1], (/only: "(\w+)"/.exec(m[2]) || [])[1] || null]);
const pops = [...block("const POPS = {").matchAll(/^  (\w+): \(/gm)].map(m => m[1]);
const popOnly = value(/^const POP_ONLY = (\{[^}]*\});/m) || {};
check("the environments, loops and pops read", envs.length >= 4 && loops.length > 5 && pops.length > 5, `${envs.length} ${loops.length} ${pops.length}`);
check("vsRoomSounds leaves out another environment's own", /LOOPS\[k\]\.only === env\) files\[k\]/.test(html) && /POP_ONLY\[k\] === env\) files\["pop-" \+ k\]/.test(html));
for (const env of envs) {
  const n = loops.filter(([, only]) => !only || only === env).length + 2 + pops.filter(k => !popOnly[k] || popOnly[k] === env).length;
  check(`${env}: ${n} sounds, at most Visit's 32`, n <= 32);
}
check("the casino's floor and its JACKPOT! are the pool club's alone", loops.some(([k, only]) => k === "casino" && only === "pool") && popOnly.jackpot === "pool");
check("JACKPOT! has its burst (BURSTS) and its sound (POPS)", /^\s+jackpot: \["JACKPOT!"/m.test(html) && pops.includes("jackpot"));

// ---- your visitors: its slots, its bar's stools, its roulette table, by the types Visit shows them
const types = value(/^const VISITOR_TYPES = (\{[\s\S]*?\});/m);
check("Visit shows a visitor the slots, the roulette layout, HIT and STAND, the dice and the cashier", !!types && ["slots", "roulette", "bjhit", "bjstand", "craps", "cashier"].every(k => types[k]), types ? Object.keys(types).join(",") : "unread");
const shell = block("function casinoShell(", "\n}\n");
check("casinoShell marks its seats (a slot machine: slots; the bar's stools: sit)", shell.includes('s.obj.userData.visitPoint = s.slot ? "slots" : "sit"'));
check("…its roulette table, its bar, the craps table, the cashier's window, HIT and STAND", ['t.userData.visitPoint = "roulette"', 't.userData.visitPoint = "cocktail"', 't.userData.visitPoint = "craps"', 'window_.userData.visitPoint = "cashier"', 'pl.userData.visitPoint = "bj" + act'].every(x => shell.includes(x)));
check("a click finds where on the roulette layout it is (thingOn), and a chip goes there (useThing: rouletteBet)", /hits\.push\(\[h\.distance, \{ roulette: R, point: h\.point \}\]\)/.test(html) && /if \(kind\.roulette\) return rouletteBet\(kind\.roulette, doer, kind\.point\);/.test(html));
check("…HIT and STAND, the dice and the cashier too, before a seat's own click (sat, you stay sat)", ["if (kind.bj) return bjAct(", "if (kind.craps) return crapsTake(", "if (kind.cashier) return cashierChips("].every(x => block("function useThing(", "\n}\n").indexOf(x) >= 0 && block("function useThing(", "\n}\n").indexOf(x) < block("function useThing(", "\n}\n").indexOf("if (sitting(doer))")));
check("sat at a slot machine, a click on it pulls again", /if \(doer\.act\.seat === kind\) \{ if \(kind\.slot\) slotPull\(kind\.slot, doer\); return; \}/.test(html));
check("the bar's counter is among the bars (a cocktail: barAt)", shell.includes("bars.push({ group: t, seg:"));
check("…and no name in casinoShell hides the room's own bars", !/\b(const|let) bars\b/.test(shell));

// ---- the overview looks down onto it: its canopy is the shell's lid, named so your page finds it in Visit's copy
check("its canopy is its lid, named lid:", shell.includes('canopy.name = "canopy"') && shell.includes("cas.lid.push(canopy)") && shell.includes('for (const o of cas.lid) o.name = "lid:" + o.name;'));
check("…which the pool club's shell hands to liftLid", /g\.userData\.casino = cas; g\.userData\.lid = cas\.lid;/.test(html) && block("function liftLid(", "\n}\n").includes("scenery.userData.lid"));
check("…and its chandeliers hang from it (lifted with it)", /merged\(chGold, vcol, AISLE, H, z, 0, canopy\)/.test(shell) && /merged\(chLit, vglow, AISLE, H, z, 0, canopy\)/.test(shell));

// ---- its odds
const fn = name => block(`function ${name}(`, "\n}\n");
const SLOT_SYMS = value(/^const SLOT_SYMS = (\[[^\]]*\]);/m), SLOT_ODDS = value(/^const SLOT_ODDS = (\{[^}]*\});/m);
const slotRoll = Function("SLOT_SYMS", "SLOT_ODDS", fn("slotRoll") + "\nreturn slotRoll;")(SLOT_SYMS, SLOT_ODDS);
let sevens = 0, alike = 0; const N = 200000;
for (let i = 0; i < N; i++) { const [a, b, c] = slotRoll(); if (a === b && b === c) { alike++; if (a === 0) sevens++; } }
check(`a pull: three 7s ${(sevens / N * 100).toFixed(2)}% (${SLOT_ODDS.sevens * 100}%), three alike ${(alike / N * 100).toFixed(1)}% (${SLOT_ODDS.win * 100}%)`,
      Math.abs(sevens / N - SLOT_ODDS.sevens) < 0.003 && Math.abs(alike / N - SLOT_ODDS.win) < 0.005);
const ROULETTE = value(/^const ROULETTE = (\[[^\]]*\]);/m), RED = new Set(value(/^const RED = new Set\((\[[^\]]*\])\);/m));
check("the wheel: 0 to 36, each once", ROULETTE.length === 37 && new Set(ROULETTE).size === 37 && ROULETTE.every(n => n >= 0 && n <= 36));
check("…red and black alternating round it (the zero apart)", ROULETTE.slice(1).every((n, i, a) => !i || RED.has(n) !== RED.has(a[i - 1])) && RED.size === 18);

// ---- the roulette ball: rolled (rouletteRun), it never jumps, and it comes to rest in the middle of a pocket before the
// number is called, the wheel round with it; the pocket it lies in is the number, each as likely as the next
const ROULETTE_S = value(/^const ROULETTE_S = (\{[^}]*\});/m), WHEEL = value(/^const ROULETTE_WHEEL = (\{[\s\S]*?\});/m);
const rouletteRun = Function("ROULETTE", "ROULETTE_S", "ROULETTE_WHEEL", fn("rouletteRun") + "\nreturn rouletteRun;")(ROULETTE, ROULETTE_S, WHEEL);
const rouletteAt = Function(fn("rouletteAt") + "\nreturn rouletteAt;")();
let seed = 20261105; const rand = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
{
  const STEP = Math.PI * 2 / 37, at = (o, j) => { const b = o.w[j] + o.phi[j]; return [Math.sin(b) * o.r[j], o.y[j], Math.cos(b) * o.r[j]]; };
  const counts = new Array(37).fill(0), M = 3700; let kink = 0, late = 0, offMid = 0, low = 9, high = 0, phi = -0.5 * STEP;
  for (let i = 0; i < M; i++) {
    const o = rouletteRun(rand, 0.4 + 0.2 * rand(), phi, WHEEL.rest), last = o.w.length - 1;
    counts[o.k]++; late = Math.max(late, o.at);
    for (let j = 1; j < last; j++) {   // how far a step strays from the line of the steps either side of it
      const a = at(o, j - 1), b = at(o, j), c = at(o, j + 1); kink = Math.max(kink, Math.hypot(a[0] - 2 * b[0] + c[0], a[1] - 2 * b[1] + c[1], a[2] - 2 * b[2] + c[2]) / 2);
      if (o.r[j] < WHEEL.lip && j * o.dt > 0.5) { low = Math.min(low, o.y[j] - WHEEL.top - WHEEL.ball); high = Math.max(high, o.y[j] - WHEEL.top - WHEEL.ball); }
    }
    const end = rouletteAt(o, ROULETTE_S.spin);
    const d = end.phi + (o.k + 0.5) * STEP; offMid = Math.max(offMid, Math.abs(Math.atan2(Math.sin(d), Math.cos(d))), Math.abs(end.r - WHEEL.rest), Math.abs(end.y - WHEEL.top - WHEEL.ball));
    phi = -(o.k + 0.5) * STEP;   // the next spin from where this one lay, as the room's do
  }
  const chi = counts.reduce((s, c) => s + (c - M / 37) ** 2 / (M / 37), 0);
  check(`roulette: the ball never jumps (a step at most ${(kink * 1000).toFixed(1)} mm off its neighbours' line: a bounce's, under 6)`, kink < 0.006);
  check(`…it lies still in the middle of its pocket by ${late.toFixed(2)} s, before the call at ${ROULETTE_S.spin}`, late > 0 && late <= ROULETTE_S.spin - 0.25 && offMid < 1e-4, `${offMid} off its middle`);
  check(`…on the wheel (once flicked out of the last pocket) it is never under the pockets' floor, nor over ${(high * 100).toFixed(1)} cm off it (under 5)`, low > -1e-6 && high < 0.05);
  check(`…each number as likely: χ² ${chi.toFixed(1)} over 37 pockets (under 70), each ${Math.min(...counts)} to ${Math.max(...counts)} of ${M}`, chi < 70);
  check("…and the number paid is the one it lies in", /R\.run = rouletteRun\(Math\.random, R\.ws, R\.beta - R\.w, R\.br\); R\.result = ROULETTE\[R\.run\.k\];/.test(html));
}

// ---- its history board: the numbers called, newest on top (as each is called); red, black and the zero as shares;
// the hot numbers (most often, the latest first among equals) and the cold (least often, the longest gone first)
{
  const RED_ = new Set(value(/^const RED = new Set\((\[[^\]]*\])\);/m)), [KEPT, SHOWN] = /^const ROULETTE_KEPT = (\d+), ROULETTE_SHOWN = (\d+);/m.exec(html).slice(1).map(Number);
  const rouletteStats = Function("ROULETTE", "RED", fn("rouletteStats") + "\nreturn rouletteStats;")(ROULETTE, RED_);
  const a = rouletteStats([7, 7, 0, 32, 15, 7, 32]), b = rouletteStats([...ROULETTE, 5]), same = (x, y) => JSON.stringify(x) === JSON.stringify(y);
  check("roulette's board: hot the most often (the latest first among equals), cold never yet (by number)", same(a.hot, [7, 32, 0, 15]) && same(a.cold, [1, 2, 3, 4]), JSON.stringify(a));
  check("…every number up: cold the longest gone", same(b.hot, [5, 0, 32, 15]) && same(b.cold, [26, 3, 35, 12]), JSON.stringify(b));
  check("…red, black and the zero as shares of the numbers called", Math.abs(a.red - 5 / 7) < 1e-9 && Math.abs(a.black - 1 / 7) < 1e-9 && Math.abs(a.zero - 1 / 7) < 1e-9);
  check(`…each number called goes on top of it (${SHOWN} shown, the last ${KEPT} counted)`, /R\.hist\.unshift\(n\); R\.hist\.length = Math\.min\(R\.hist\.length, ROULETTE_KEPT\); rouletteBoard\(R\);/.test(html) && SHOWN <= KEPT);
  const rows = /const h = i \? (\d+) : (\d+);/.exec(html), top = /let y = (\d+);\n  R\.hist\.slice\(0, ROULETTE_SHOWN\)/.exec(html), rule = /x\.fillRect\(16, (\d+), W - 32, 2\); x\.fillRect\(16, (\d+), W - 32, 2\);/.exec(html);
  check("…its rows fit between its rules", !!rows && !!top && !!rule && +top[1] + +rows[2] + (SHOWN - 1) * rows[1] <= +rule[2], rows && top && rule ? `${+top[1] + +rows[2] + (SHOWN - 1) * rows[1]} past ${rule[2]}` : "unread");
  check("…on its pole, which walkers go round", shell.includes("obstacles.push([TX + HX, TZ2 + HZ, 0.25]);"));
}

// ---- the games' rules, run
const RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"];
const rouletteWins = Function("RED", "return " + /^const rouletteWins = ([^\n]*);$/m.exec(html)[1])(RED);
let wrong = [];
for (let n = 0; n <= 36; n++) {
  const want = { "1-18": n >= 1 && n <= 18, "19-36": n >= 19, EVEN: n > 0 && n % 2 === 0, ODD: n % 2 === 1, RED: RED.has(n), BLACK: n > 0 && !RED.has(n) };
  for (const [name, w] of Object.entries(want)) if (rouletteWins({ name }, n) !== w) wrong.push(name + "@" + n);
  for (let k = 0; k <= 36; k++) if (rouletteWins({ n: k, name: String(k) }, n) !== (k === n)) wrong.push(k + "@" + n);
}
check("roulette: each spot wins on its numbers alone (the zero loses every outside bet)", !wrong.length, wrong.slice(0, 6).join(" "));
const rouletteSpot = Function("THREE", fn("rouletteSpot") + "\nreturn rouletteSpot;")();
const fakeR = { g: { worldToLocal: v => v } }, pt = (px, py) => ({ x: 0.5 + (px / 512 - 0.5) * 2.1, y: 0.9, z: 0.05 + (py / 256 - 0.5) * 1.05, clone() { return this; } });   // a point over the layout's canvas pixel (px, py)
wrong = [];
for (let c = 0; c < 12; c++) for (let r = 0; r < 3; r++) { const n = c * 3 + 3 - r, sp = rouletteSpot(fakeR, pt(48 + c * 36 + 18, 16 + r * 53 + 26)); if (!sp || sp.n !== n) wrong.push(n + "→" + (sp && sp.name)); }
["1-18", "EVEN", "RED", "BLACK", "ODD", "19-36"].forEach((name, i) => { const sp = rouletteSpot(fakeR, pt(48 + i * 72 + 36, 213)); if (!sp || sp.name !== name) wrong.push(name + "→" + (sp && sp.name)); });
const zero = rouletteSpot(fakeR, pt(28, 96)), off = rouletteSpot(fakeR, pt(500, 250));
check("roulette: a click on a cell of the layout is its number's, on a box along its edge that box's; the zero; off it, nothing", !wrong.length && zero && zero.n === 0 && off === null, wrong.join(" "));
check("…a number pays 36 for the chip (35 to 1), the rest 2 (evens)", /\(b\.spot\.n !== undefined \? 36 : 2\) \* CHIPS\.bet/.test(html));

// ---- the roulette table's own players: at most seven (six, with your two places kept free), each its own colour of chips; where their chips go; their bets
// within their wallets; a wallet's turnover (broke, or doubled up, and someone from the party for the place); their way
// on foot across the lot found, always, and clear
{
  const SEATS = value(/^const ROULETTE_SEATS = (\[[^\n]*\]);$/m), COLORS = value(/^const ROULETTE_CHIPS = (\[[^\]]*\]);/m), NPC = value(/^const ROULETTE_NPC = (\{[^}]*\});/m);
  const MAX = value(/^const ROULETTE_MAX = (\d+);/m), CHIPS = value(/^const CHIPS = (\{[^}]*\});/m), S = value(/^const ROULETTE_S = (\{[^}]*\});/m);
  const theirs = [0xffd23f, ...value(/^const chipColor = [^\n]*: (\[[^\]]*\])\[hash32/m)];   // yours, and a visitor's
  const near = (a, b) => Math.hypot((a >> 16) - (b >> 16), ((a >> 8) & 255) - ((b >> 8) & 255), (a & 255) - (b & 255)) < 60;
  check("roulette's own: six places (at most seven, the user's: the table has room for no more with yours free), one colour of chips each, none alike nor like yours or a visitor's",
        SEATS.length === 6 && COLORS.length === SEATS.length && COLORS.every((c, i) => !COLORS.some((d, j) => j !== i && near(c, d)) && !theirs.some(d => near(c, d))));
  check("…its places clear of the table (its top 3.2 by 1.5) and of each other", SEATS.every(([x, z]) => Math.abs(z) > 0.75 + 0.25 && Math.abs(x) < 1.6) && SEATS.every(([x, z], i) => SEATS.every(([x2, z2], j) => i === j || Math.hypot(x - x2, z - z2) >= 0.6)));
  const YOURS = value(/^const ROULETTE_YOURS = (\[\[[^\n]*\]\]);/m), DEALER = /R\.dealer = folk\("dealer", TX \+ WX, TZ2 - ([\d.]+), 0\);/.exec(html), WX = +/const WX = (-?[\d.]+), RW = ROULETTE_WHEEL/.exec(html)[1];
  check("…yours (the user's) always free: the near side's two places from the wheel, none of theirs within 0.55 of either; the places at the start all theirs",
        !!YOURS && YOURS.length === 2 && YOURS.every(([x, z]) => z > 0.75 && x >= -0.55 && x < 1.55 && SEATS.every(([sx, sz]) => Math.hypot(sx - x, sz - z) >= 0.55))
        && NPC.seated.every(i => i >= 0 && i < SEATS.length) && new Set(NPC.seated).size === NPC.seated.length);
  check("…and none crowding the croupier, behind the wheel", !!DEALER && SEATS.every(([x, z]) => Math.hypot(x - WX, z + +DEALER[1]) >= 0.65));
  const OUTSIDE = value(/^const ROULETTE_OUTSIDE = (\[[^\]]*\]);/m), named = Function("ROULETTE_OUTSIDE", fn("rouletteSpotNamed") + "\nreturn rouletteSpotNamed;")(OUTSIDE);
  const toPx = ([x, z]) => [((x - 0.5) / 2.1 + 0.5) * 512, ((z - 0.05) / 1.05 + 0.5) * 256];
  wrong = [];
  for (const name of [...Array(37).keys()].map(String).concat(OUTSIDE)) { const s = named(name), c = rouletteSpot(fakeR, pt(...toPx(s.at))); if (!c || c.name !== name || c.n !== s.n) wrong.push(name + "→" + (c && c.name)); }
  check("…a spot by its name (theirs) is where a click finds that spot (yours)", !wrong.length, wrong.join(" "));
  check("…each player's chips on a spot at its own place there (yours too), each a stack", /\[cx, cz\] = rouletteSlot\(R, doer, spot\)/.test(html) && /const k = B\.spots\.length, \[x, z\] = rouletteSlot\(R, B, spot\)/.test(html));
  // their bets: within the wallet, at most MAX chips, on the layout's spots, one chip in the air at a time, all before the ball
  const stats = Function("ROULETTE", "RED", fn("rouletteStats") + "\nreturn rouletteStats;")(ROULETTE, RED);
  const npcPlan = Function("ROULETTE_NPC", "CHIPS", "ROULETTE_MAX", "ROULETTE_S", "ROULETTE_OUTSIDE", "rouletteStats", "rouletteSpotNamed", fn("npcPlan") + "\nreturn npcPlan;")(NPC, CHIPS, MAX, S, OUTSIDE, stats, named);
  let seed = 7;
  const real = Math.random; Math.random = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
  wrong = [];
  for (let k = 0; k < 3000; k++) {
    const B = { chips: CHIPS.bet * (1 + Math.floor(Math.random() * 120)), style: k % 3, lucky: [3, 17, 32] }, from = 0.6 + Math.random() * 5;
    npcPlan({ hist: [5, 17, 17, 0, 32] }, B, from);
    const ts = B.plan.map(b => b.t);
    if (B.plan.length * CHIPS.bet > B.chips || B.plan.length > MAX || B.plan.some(b => !b.spot || !b.spot.at) || ts.some((t, i) => t < from || t > S.bets - 0.9 || (i && t - ts[i - 1] < 0.6 - 1e-9))) wrong.push(JSON.stringify([B.chips, ts]));
  }
  check("…a spin's bets within its wallet, at most " + MAX + " chips, each on a spot of the layout, one at a time before the ball", !wrong.length, wrong.slice(0, 2).join(" "));
  // a wallet's life: spins till broke or doubled up (as stepBettors leaves): long enough to watch, short enough that places come free
  const stays = [];
  for (let k = 0; k < 3000; k++) {
    const B = { buy: NPC.buy[0] + 50 * Math.floor(Math.random() * ((NPC.buy[1] - NPC.buy[0]) / 50 + 1)), style: k % 3, lucky: [3, 17, 32] };
    B.chips = B.buy;
    let n = 0;
    for (; n < 5000 && B.chips >= CHIPS.bet && B.chips < 2 * B.buy; n++) {
      npcPlan({ hist: [] }, B, 0.6);
      const won = Math.floor(Math.random() * 37);
      for (const b of B.plan) { B.chips -= CHIPS.bet; if (rouletteWins(b.spot, won)) B.chips += (b.spot.n !== undefined ? 36 : 2) * CHIPS.bet; }
    }
    stays.push(n);
  }
  Math.random = real;
  const mean = stays.reduce((s, n) => s + n, 0) / stays.length, spin = S.bets + S.spin + S.result;
  check(`…a player stays ${mean.toFixed(0)} spins on average (${(mean * spin / 60).toFixed(0)} min): a place comes free every few minutes`, mean > 8 && mean * spin / 60 / SEATS.length < 5 && Math.max(...stays) < 5000);
  check("…paid into its own wallet at the call (its chips back to its rail), the rest swept", /if \(b\.npc\) \{ b\.npc\.won \+= pay; continue; \}/.test(html) && /for \(const B of R\.bettors\) if \(B\.mode === "play" && B\.spots\.length\) npcCalled\(R, B\);/.test(html));
  check("…broke, or doubled up, it leaves for the party (cashing in on the way); a while after, someone from the party comes for its place, by the cashier",
        /if \(B\.chips < CHIPS\.bet\) \{ npcLeave\(cas, R, B, "broke"\); continue; \}/.test(html) && /if \(B\.chips >= 2 \* B\.buy\) \{ npcLeave\(cas, R, B, "up"\); continue; \}/.test(html) &&
        /npcGo\(cas, B, B\.cashIn \? "cashout" : "party"\)/.test(html) && /npcGo\(cas, npcNew\(R, seat, p, p\.ob\), "in"\);/.test(html) && /seat\.freeAt = cas\.t \+ between\(ROULETTE_NPC\.recruit\)/.test(html));
  check("…the party leaves one away at the casino be (no dancing, no mingling to it, no waiter)", /if \(p\.trip && p\.trip\.casino\) continue;/.test(html) && /club\.crowd\.filter\(p => p\.trip && !p\.trip\.casino\)\.length/.test(html));
  check("…its chips are the room's from the start (no meshes added as it plays), moving by themselves (live)", /m\.visible = false; m\.castShadow = true; m\.userData\.live = true; t\.add\(m\);/.test(html));
  // the way on foot: a lot with water, a wall with a gap in it, posts, and people standing about
  const lot = Function("LAND", "CAS", "ROOM_MID", "ROOM_R", [fn("lotGrid"), fn("lotStamp"), fn("lotPath")].join("\n") + "\nreturn { lotPath };")(LAND, CAS, 3, 19);
  const pool = (x, z) => Math.hypot(x - 5, z - 25) < 8, wall = (x, z, m) => Math.abs(z + 2) < 0.3 + m && !(x > 30 + m && x < 33 - m) && x > 22 - m;
  const posts = [...Array(30)].map((_, k) => [24 + (k * 7.3) % 20, -30 + (k * 11.7) % 60, 0.3, 0]), people = [...Array(12)].map((_, k) => ({ ob: [8 + k, 38 + (k % 3), 0.32, 0] }));
  const cas = { club: { inPool: pool, zF: 9, stage: { z: 45 }, crowd: people, dj: { ob: null } }, g: { userData: { blocked: wall } }, roulette: { bettors: [] }, obstacles: [...posts, ...people.map(p => p.ob)] };
  const bad = (x, z) => pool(x, z) || wall(x, z, 0) || posts.some(o => Math.hypot(x - o[0], z - o[1]) < o[2]) || (x > -13 && x < 19 && z > -8.5 && z < 9);   // (the room: its deck, -13 to 19 across, its back wall to its front, zF)
  seed = 11; Math.random = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
  wrong = [];
  let longest = 0;
  for (let k = 0; k < 60; k++) {
    const a = [9 + Math.random() * 6, 39 + Math.random() * 3], b = [24 + Math.random() * 20, -33 + Math.random() * 20];
    if (bad(...b)) continue;
    const t0 = Date.now(), w = lot.lotPath(cas, a, b, null), ms = Date.now() - t0;
    longest = Math.max(longest, ms);
    const pass = w.slice(1).every((q, i) => { const p = w[i], n = Math.ceil(Math.hypot(q[0] - p[0], q[1] - p[1]) / 0.1); for (let s = 0; s <= n; s++) if (bad(p[0] + (q[0] - p[0]) * s / n, p[1] + (q[1] - p[1]) * s / n)) return false; return true; });
    if (w[0] !== a || w[w.length - 1] !== b || !pass) wrong.push(JSON.stringify(w.map(p => p.map(v => +v.toFixed(1)))));
  }
  Math.random = real;
  check(`…its way on foot across the lot found, round the water, the wall (through its gap), the posts (the longest search ${longest} ms)`, !wrong.length && longest < 200, wrong.slice(0, 1).join(" "));
  check("…its search's costs kept as found (64-bit: rounded to 32, a cost no better looks better, and the search never ends)", /const cost = new Float64Array\(free\.length\)/.test(html) && /if \(pops > 8 \* free\.length\) return \[a, b\];/.test(html));
}
const cardValue = Function("return " + /^const cardValue = ([^\n]*);$/m.exec(html)[1])();
const handTotal = Function("cardValue", fn("handTotal") + "\nreturn handTotal;")(cardValue), hand = (...rs) => rs.map(r => ({ r }));
check("blackjack: an ace and a king 21; two aces and a 9 21; a king, a queen and a 5 25 (bust); a card face down not counted",
      handTotal(hand(1, 13)) === 21 && handTotal(hand(1, 1, 9)) === 21 && handTotal(hand(13, 12, 5)) === 25 && handTotal([{ r: 10 }, { r: 9, down: true }]) === 10 && handTotal(hand(1, 5, 1)) === 17);
const felt = /CylinderGeometry\(1\.3, 1\.3, ([\d.]+), 24, 1, false, -Math\.PI \/ 2, Math\.PI\), FELT, 0, ([\d.]+), 0, t\)/.exec(html), BJ_FELT = value(/^const BJ_FELT = ([\d.]+);/m);
check("blackjack: a card lies on the felt, not in it (BJ_FELT its top)", !!felt && BJ_FELT !== null && Math.abs(+felt[2] + felt[1] / 2 - BJ_FELT) < 1e-9 && /card\.to = \[[^\]]*BJ_FELT \+ 0\.004/.test(html),
      felt ? `the felt's top ${+felt[2] + felt[1] / 2}, BJ_FELT ${BJ_FELT}` : "the felt unread");
const settle = block("function bjSettle(", "\n}\n");
check("…paid: blackjack 2.5 the bet, a win 2, a push the bet back, a loss nothing", settle.includes("{ bj: 2.5, win: 2, push: 1, lose: 0 }[s.res] * s.bet"));
const calls = [], crRec = {};
const recInc = (r, k, n = 1) => { r[k] = (r[k] || 0) + n; }, recMax = (r, k, v) => { r[k] = Math.max(r[k] || 0, v); };
const crapsCall = Function("human", "playerName", "casSay", "chipsGive", "burst", "THREE", "actors", "record", "recInc", "recMax", fn("crapsCall") + "\nreturn crapsCall;")(
  d => d.human, d => d.name, (cas, who, text) => calls.push(text), (cas, d, n) => calls.push("+" + n), () => {}, { Vector3: function () {} }, null,
  (cas, d, f) => { if (d.human) f(crRec); }, recInc, recMax);
const K = { point: 0, bet: 10, shooter: { human: true, name: "duck", x: 0, z: 0 }, players: [], puck: { position: { set() {} } }, holder: null, npcShooter: 0 };
const roll = n => { K.roll = [Math.min(6, n - 1), n - Math.min(6, n - 1)]; crapsCall({}, K); };
roll(7); const comeOut7 = calls.splice(0).join(" | ");
K.bet = 10; roll(8); const point8 = K.point; roll(5); roll(8); const made = calls.splice(0).join(" | ");
K.bet = 10; roll(4); roll(7); const sevenOut = calls.splice(0).join(" | ");
check("craps: a 7 on the come-out wins (the bet back twice over)", comeOut7 === "duck: 7! winner | +20", comeOut7);
check("…an 8 is the point; a 5 nothing; the 8 again wins", point8 === 8 && made === "duck: 8: the point | duck: 5, the point is 8 | duck: 8! point made | +20", made);
check("…a 7 before the point: seven out, nothing paid", sevenOut === "duck: 4: the point | duck: seven out" && K.point === 0, sevenOut);
check("…and each throw into its shooter's record: the throws, a natural, the point made (a run of 1), the seven-out (the run over), what it won and lost",
      JSON.stringify(crRec) === JSON.stringify({ crRolls: 6, games: 4, crNaturals: 1, crWins: 2, best: 20, crPoints: 1, crRun: 0, crBestRun: 1, crSevenOuts: 1, crLosses: 1 }), JSON.stringify(crRec));

// ---- your record and achievements (record, ACH): what each game counts into a player's record, what earns each
// achievement, the bankroll's turns (chipsMove), and what the board keeps of yours (the fields as /api/casino takes them:
// server.py casino_owner), read back and added to (recMerge)
{
  const server = readFileSync(join(here, "server.py"), "utf8"), FIELD = new RegExp("^" + /^CASINO_FIELD = re\.compile\(r"([^"]+)"\)/m.exec(server)[1] + "$"), MAXF = +/^CASINO_FIELDS = (\d+)/m.exec(server)[1];
  const defs = html.slice(html.indexOf("const ACH_TIERS = "), html.indexOf("// What `doer` (you, or a visitor"));
  const RED_ = new Set(value(/^const RED = new Set\((\[[^\]]*\])\);/m)), wins = Function("RED", "return " + /^const rouletteWins = ([^\n]*);$/m.exec(html)[1])(RED_);
  const keptDefs = html.slice(html.indexOf("const keptNew = "), html.indexOf("function keptLoad() {")), said = [], posted = [];
  let fetchAnswer = { status: 200, ok: true };
  const E = Function("ROULETTE_MAX", "CHIPS", "rouletteWins", "human", "me", "playerName", "casSay", "handTotal", "chipsGive", "ROLE", "fetch",
    defs + "\n" + keptDefs + ["record", "achCheck", "chipsMove", "rouletteRecord", "bjSettle", "purse", "keptSave"].map(fn).join("\n")
    + "\nreturn { ACH, ACH_TIERS, ACH_RANKS, achProgress, record, achCheck, recMerge, chipsMove, rouletteRecord, bjSettle, purse, keptSave, keptTake, keptNew, KEPT };")(
    8, { start: 1000, slot: 5, bet: 10 }, wins, d => !!d && !d.npc, {}, d => d.name || "visitor", (c, w, t) => said.push(t), handTotal, () => {}, "host",
    (url, o) => { posted.push(JSON.parse(o.body)); return Promise.resolve(fetchAnswer); });
  const { ACH, ACH_RANKS, achProgress, recMerge, chipsMove, rouletteRecord, bjSettle, purse, keptSave, keptTake, keptNew, KEPT } = E, ids = ACH.map(a => a.id), total = ACH.reduce((s, a) => s + a.pts, 0);
  const CASINO = { purses: new Map() };
  check(`achievements: ${ACH.length}, ${total} points, each its own id, as the board keeps them (at most ${MAXF})`, new Set(ids).size === ids.length && ids.every(i => FIELD.test(i)) && ACH.length <= MAXF);
  check("…each of a tier, a goal, a glyph that fits its medal; some secret; none earned on a record of nothing",
        ACH.every(a => a.tier >= 0 && a.tier <= 3 && a.glyph.length <= 3 && achProgress(a, {})[1] > 0 && achProgress(a, {})[0] < achProgress(a, {})[1]) && ACH.some(a => a.secret));
  check("…the ranks in order, the last for every achievement earned", ACH_RANKS.every((r, i) => !i || r[0] > ACH_RANKS[i - 1][0]) && ACH_RANKS[ACH_RANKS.length - 1][0] === total);
  // every field the page writes into a record, and every one it reads (the achievements, Casino stats, the Players Club): one the board takes, and none read that is never written
  const wrote = new Set([...html.matchAll(/rec(?:Inc|Max|37)\((?:r|p\.rec), "(\w+)"/g), ...html.matchAll(/\br\.(\w+) (?:=|\|=)(?!=)/g)].map(m => m[1]));
  const cs = html.slice(html.indexOf("function csAchCard("), html.indexOf("if (ROLE !== \"host\") {\n  CS.btn.hidden = false;")), club = html.slice(html.indexOf("const { name, r, ach, chips } = show"), html.indexOf("P.tex.needsUpdate = true;")) + fn("clubLines");
  const read = new Set([...defs.matchAll(/\br\.(\w+)/g), ...cs.matchAll(/\br\.(\w+)/g), ...club.matchAll(/\br\.(\w+)/g)].map(m => m[1]));
  check(`…the record's ${wrote.size} fields, each a name the board takes (at most ${MAXF})`, wrote.size <= MAXF && [...wrote].every(k => FIELD.test(k)), [...wrote].filter(k => !FIELD.test(k)).join(", "));
  check("…and every one read (the achievements, Casino stats, the Players Club) is one written", [...read].every(k => wrote.has(k)), [...read].filter(k => !wrote.has(k)).join(", "));
  // blackjack: hands settled, into your record
  const VK = "a".repeat(64), you = { owner: true, name: "Duck" }, v = { visitor: "v1", vkey: VK, name: "Ana", office: "Ana's den" }, w = { visitor: "v2", name: "Guest" }, C = (...rs) => rs.map(r => ({ r }));
  KEPT.loaded = true;
  const deal = (mine, dealer, who = you) => bjSettle(CASINO, { dHand: C(...dealer), in: [{ hand: C(...mine), bet: 10, who }], dealer: {} });
  deal([1, 13], [13, 12]); deal([10, 9], [1, 13]); deal([10, 6, 9], [10, 7]); deal([2, 3, 4, 5, 7], [10, 8]); deal([10, 2], [10, 6, 9]); deal([10, 8], [10, 8]); deal([1, 12], [1, 13]);
  const K = KEPT.owner, r = K.rec, want = { bjHands: 7, bjNaturals: 2, bjWins: 3, bjLosses: 2, bjPushes: 2, bjDealerBJ: 1, bjBusts: 1, bjDealerBusts: 1, bjIce: 1, bjCharlie: 1, bj21: 1, bjBestRun: 2, bjRun: 2 };
  check("blackjack's record: hands, blackjacks (a push between two counts), wins, losses, pushes, lost to the dealer's blackjack, busts, the dealer's, a five-card 21, the best run",
        Object.entries(want).every(([k, n]) => r[k] === n), JSON.stringify(r));
  check("…its achievements earned (yours, the board's: KEPT.owner), Hot Hand not yet; your name kept with them", ["bj_first", "bj_natural", "bj_heart", "bj_charlie", "bj_ice"].every(i => K.ach[i] > 0) && !K.ach.bj_run && K.dirty && K.name === "Duck", Object.keys(K.ach).join(" "));
  for (let k = 0; k < 3; k++) deal([10, 9], [10, 7]);
  check("…five in a row: Hot Hand", r.bjBestRun === 5 && K.ach.bj_run > 0 && CASINO.purses.get("owner").achQ.map(a => a.id).join(" ") === "bj_first bj_natural bj_heart bj_charlie bj_ice bj_run");
  // roulette: a spin called
  const sp = k => typeof k === "number" ? { n: k, name: String(k) } : { name: k }, bet = (doer, k) => ({ doer, spot: sp(k) });
  rouletteRecord(CASINO, [bet(you, 17), bet(you, 17), bet(you, "BLACK"), bet(you, 0), bet(v, 5), bet(w, 5), { doer: { npc: true }, spot: sp(17), npc: {} }], 17);
  check("roulette's record: the spin, its chips, the numbers chosen, what came back (two on 17 and black: 740), the number hit once however many chips",
        r.rlSpins === 1 && r.rlChips === 4 && r.rlMost === 4 && r.rlBets[17] === 2 && r.rlBets[0] === 1 && r.rlWon === 740 && r.rlBest === 740 && r.rlUp === 1 && r.rlStraight === 1 && r.rlNums[17] === 1 && r.rlOutside === 1 && r.games === 3,
        JSON.stringify(r));
  const A = KEPT.visitors.get(VK);
  check("…Straight Up and Stacked earned; a visitor's spin in their own record, kept by their office's key with their name and office; the room's own in none",
        K.ach.rl_straight > 0 && K.ach.rl_stack > 0 && !!A && A.rec.rlSpins === 1 && !A.rec.rlWon && A.name === "Ana" && A.office === "Ana's den" && A.dirty && CASINO.purses.get("visit:v1").rec === A.rec && CASINO.purses.size === 3);
  check("…a visitor Visit gives no key: a record while they are here, kept by nobody", CASINO.purses.get("visit:v2").kept === null && CASINO.purses.get("visit:v2").rec.rlSpins === 1 && KEPT.visitors.size === 1);
  rouletteRecord(CASINO, [bet(you, 0)], 0); rouletteRecord(CASINO, [bet(you, 17)], 17);
  check("…a zero: Zero Hero; 17 again: Lucky Number", r.rlZero === 1 && K.ach.rl_zero > 0 && r.rlNums[17] === 2 && K.ach.rl_lucky > 0);
  // the bankroll: down under 100, broke, and back on winnings (Comeback Kid); the cashier's top-up clears the dip first
  const p = purse(CASINO, w);
  chipsMove(p, -950); const dip = p.rec.dip; chipsMove(p, -48); const broke = p.rec.broke; chipsMove(p, 998);
  check("the bankroll: under 100 noted, broke counted once, back to 1,000 on winnings a comeback", dip === 1 && broke === 1 && p.rec.comebacks === 1 && p.rec.dip === 0 && p.ach.bk_broke > 0 && p.ach.bk_comeback > 0 && p.rec.peak === 1000);
  check("…the cashier: the dip and the broke cleared before the chips, so a top-up is no comeback", /const r = p\.rec; r\.zero = r\.dip = 0; recInc\(r, "bought", n\); recInc\(r, "cashier"\); chipsMove\(p, n\);/.test(html));
  // sent: those that changed, at most every 2 s; a visitor with no name never (the board keeps them by it too); again if the board was down
  KEPT.visitors.set("b".repeat(64), Object.assign(keptNew(), { dirty: true }));
  keptSave(); const body = posted.pop() || {};
  check("sent to the board: yours (with your name) and each visitor's that changed, by their key (none without a name), then not again till they change",
        body.owner && body.owner.name === "Duck" && body.owner.chips === CASINO.purses.get("owner").chips && Object.keys(body.visitors || {}).join() === VK && body.visitors[VK].office === "Ana's den" && body.visitors[VK].rec.rlSpins === 1
        && !K.dirty && !A.dirty, JSON.stringify(Object.keys(body)));
  await new Promise(res => setTimeout(res, 0)); fetchAnswer = { status: 503, ok: false }; A.dirty = true; keptSave(); await new Promise(res => setTimeout(res, 0));
  check("…the board down: sent again", A.dirty && posted.length === 1);
  // yours before what the board kept is read back: no achievements (they may be kept already), the chips noted to send
  KEPT.loaded = false; K.dirty = false; const o = purse(CASINO, you), had = Object.keys(o.ach).length; o.rec.slPulls = 500; chipsMove(o, 5);
  check("…yours before the board's are read: nothing earned yet, the chips noted to send", Object.keys(o.ach).length === had && K.chips === o.chips && K.dirty);
  // what the board kept, read back: into a record that has some already, and the purse playing on it
  const e2 = keptNew(), live = { chips: 1100 }; e2.rec.bjHands = 1;
  keptTake(e2, { chips: 2000, rec: { bjHands: 5 }, ach: { bj_first: 7, nope: 1 }, name: "Bo", office: "Bo's", last: 99 }, [live]);
  check("read back (keptTake): the chips won since added to what was kept, the purse given them, the record added to, achievements known kept, the name",
        e2.chips === 2100 && live.chips === 2100 && e2.rec.bjHands === 6 && e2.ach.bj_first === 7 && !("nope" in e2.ach) && e2.name === "Bo" && e2.last === 99);
  const into = { bjHands: 2, peak: 1500, rlNums: Array(37).fill(0), games: 1, bjRun: 1 }; into.rlNums[3] = 1;
  const keptNums = Array(37).fill(0); keptNums[3] = 2;
  recMerge(into, { bjHands: 10, peak: 3000, rlNums: keptNums, games: 6, bjRun: 4, zero: 1, junk: "x" });
  check("…(recMerge): counts added, bests the best, the wheel's numbers added, the games played together, a run going kept",
        into.bjHands === 12 && into.peak === 3000 && into.rlNums[3] === 3 && into.games === 7 && into.bjRun === 1 && into.zero === 1 && !("junk" in into));
  // a visitor's key: Visit's office key (64 hex), never yours As a visitor
  const visitorKey = Function("window", "return " + /^const visitorKey = ([^\n]*);$/m.exec(html)[1])({ visit: { key: "c".repeat(64) } });
  check("a visitor known by their office's key (64 hex): none for yours (As a visitor), a bad one or none", visitorKey({ key: VK }) === VK && visitorKey({ key: "c".repeat(64) }) === null && visitorKey({ key: "x" }) === null && visitorKey(null) === null
        && html.includes("vkey: visitorKey(visitor), office: String((visitor && visitor.office) || \"\").slice(0, 60) };"));
  // the wiring
  check("all of them read back as the room's page starts (and again till they can be), sent at most every 2 s, never before they are read nor from your page",
        /if \(ROLE !== "user"\) \{ keptLoad\(\); setInterval\(keptSave, 2000\); \}/.test(html) && /function keptSave\(\) \{\n  if \(ROLE === "user" \|\| !KEPT\.loaded \|\| KEPT\.busy\) return;/.test(html)
        && /setTimeout\(keptLoad, 15000\)/.test(html) && fn("keptLoad").includes('fetch("/api/casino?all=1")'));
  check("…the engine's key reaches /api/casino", /\("GET", "\/api\/casino"\), \("POST", "\/api\/casino"\)\}/.test(server));
  check("each game counts: a pull, a slot win, a hand, a spin (before its bets are cleared), a throw, a new shooter's run",
        html.includes('record(cas, doer, r => { recInc(r, "slPulls"); r.games |= 8; });') && html.includes('record(cas, sp.by, r => { recInc(r, "slWins");') && /rouletteRecord\(cas, R\.bets, n\);\n      R\.bets = \[\];/.test(html)
        && html.includes('record(cas, doer, r => { r.crRun = 0; });') && block("function bjSettle(", "\n}\n").includes("record(cas, s.who, r => {"));
  check("an achievement earned over its player's head (all see it), one at a time; a visitor's whispered to them by Penny", html.includes("p.achS.material.map = achTex(a);") && html.includes("if (p.achT > 0) p.achT -= dt;")
        && html.includes('if (key.startsWith("visit:")) { const n = Object.keys(p.ach).length, pts = achScore(p.ach); clubSay(key.slice(6), `🏆 Achievement unlocked:'));
  check("Casino stats: read when opened, and every 2.5 s while open or you are at the casino; a new achievement pops up; whoever is picked read again as they play",
        /if \(!document\.hidden && \(CS\.open \|\| near\)\) csFetch\(\);/.test(html) && /if \(CS\.known\) for \(const id of ids\) if \(!CS\.known\.has\(id\)\)/.test(html)
        && /if \(!document\.hidden && CS\.open\) \{ csVisitor\(\); if \(\+\+CS\.ticks % 4 === 0\) csList\(\); \}/.test(html));
  check("…its picker: you, and every visitor the board keeps; a pick reads theirs", fn("csList").includes('fetch("/api/casino?visitors=1")') && fn("csVisitor").includes('fetch("/api/casino?visitor=" + encodeURIComponent(key))')
        && html.includes('CS.who = e.target.value; CS.vis = undefined;') && /\[\["", "You"\], \.\.\.CS\.list\.map\(/.test(html));
  // ---- the Players Club: where it stands, Penny, what she says and when, the kiosk's screen
  const CLUB = value(/^const CLUB = (\{[^}]*\});/m), [PX, PZ] = /const PX = ([\d.]+), PZ = (-?[\d.]+);/.exec(html).slice(1).map(Number), hotel = CAS.AISLE + 4.2 / 2 + 0.2;
  const corners = [[-0.82, -0.24], [0.82, -0.24], [-0.82, 0.42], [0.82, 0.42]].map(([dx, dz]) => [CLUB.x + dx, CLUB.z + dz]);
  check("the Players Club's kiosk on the casino's floor against the tower, between the HOTEL door and the prize's podium, clear of both; Penny beside it, clear of the door",
        html.includes("rects.push([CLUB.x - 0.82, CLUB.z - 0.24, CLUB.x + 0.82, CLUB.z + 0.42, 2.95, 0]);") && corners.every(([x, z]) => x > hotel && z > CAS.Z1 - 0.3 && z < CAS.Z1 + 1 && Math.hypot(x - PX, z - PZ) > 2.45) && CLUB.x - 1.2 - 0.32 > hotel
        && html.includes('const host = folk("dealer", CLUB.x - 1.2, CLUB.z + 0.45, 0);') && shell.indexOf("const host = folk(") > shell.indexOf("const folk = ("));   // (made after the casino's own are made by: a const not yet there throws, and the pool club with it)
  check("…the kiosk and Penny a click's (players: your card on its screen), as is her card's button for whoever pressed it",
        html.includes('k.userData.visitPoint = "players"') && html.includes('host.f.g.userData.visitPoint = "players"') && html.includes('host.f.g.name = "visit:npc:" + CLUB_NPC;')
        && html.includes("if (cas.pclub) { at({ players: cas }, cas.pclub.k); at({ players: cas }, cas.pclub.host.f.g); }") && html.includes("if (kind.players) return clubShow(kind.players, doer);")
        && html.includes("types.players = { cosmetic: true, label: VISITOR_TYPES.players, run: e => e.figure ? clubAct(e) : visitorTyped(e) };"));
  const figs = [];
  let casNow = { pclub: {} };
  const vsFigures = Function("ROLE", "window", "casinoOf", "TAROT_NPC", "TAROT_NAME", "CLUB_NPC", "CLUB_NAME", fn("vsFigures") + "\nreturn vsFigures;")("host", { visit: { figures: l => figs.push(l) } }, () => casNow, "vesna", "Madame Vesna", "penny", "Penny");
  vsFigures(); vsFigures(); casNow = null; vsFigures();
  check("…Penny one of the room's figures while the casino is there (her button: players; anyone may talk to her), the tarot reader always; sent again only as that changes",
        figs.length === 2 && figs[0].map(f => f.id).join() === "vesna,penny" && figs[0][1].actions.join() === "players" && figs[0][1].talk === "everyone" && figs[1].map(f => f.id).join() === "vesna"
        && JSON.stringify(figs[0]).length < 8192 && /markLand\(g\);   \/\/ the landscape \(Visit 0\.12\)\n  vsFigures\(\);/.test(html));
  const sent = [], clubQ = [];
  const say = Function("ROLE", "window", "visitAtLeast", "clubQ", "CLUB_NPC", "CLUB_NAME", "let clubQAt = 0;\n" + fn("clubSay") + "\n" + fn("clubSaid") + "\nreturn { clubSay, clubSaid };")(
    "host", { visit: { sayAs: (id, name, text, o) => sent.push([id, name, text, o.to]) } }, () => true, clubQ, "penny", "Penny");
  for (let k = 0; k < 15; k++) say.clubSay("v1", "x".repeat(250));
  say.clubSaid(3); say.clubSaid(4); say.clubSaid(5.3);
  check("…her lines whispered to one person each (sayAs to), cut to Visit's 200, one every 2.2 s (Visit: 5 every 10 s), at most 12 waiting",
        sent.length === 2 && sent.every(([id, name, text, to]) => id === "penny" && name === "Penny" && text.length === 200 && to === "v1") && clubQ.length === 10);
  const lines = Function("purse", "achScore", "ACH", "csRank", "achProgress", "CHIPS", "playerName", fn("clubLines") + "\nreturn clubLines;")(
    () => ({ chips: 123456789, ach: Object.fromEntries(ACH.slice(0, 20).map(a => [a.id, 1])), rec: { bought: 99999999, best: 1e9, bjWins: 99999, bjHands: 999999, bjNaturals: 9999, bjDealerBJ: 9999, rlSpins: 99999, rlStraight: 9999, crPoints: 9999, slPulls: 999999 } }),
    a => ACH.filter(x => a[x.id]).reduce((s, x) => s + x.pts, 0), ACH, Function("ACH_RANKS", fn("csRank") + "\nreturn csRank;")(ACH_RANKS), achProgress, { start: 1000 }, d => d.name)({}, { name: "Maximiliana Q" });
  check("…what she whispers you of yours: who you are at the tables, each game, the achievement nearest to hand, each line within Visit's 200", lines.length === 3 && lines.every(l => l.length <= 200), lines.map(l => l.length).join(" "));
  const hello = [], bodies = new Map([["v1", { name: "Ana", vkey: VK, x: 30, z: -20 }], ["v2", { name: "Cy", vkey: null, x: 30, z: -20 }], ["v3", { name: "Out", x: 0, z: 0 }], ["o", { name: "Duck", owner: true, x: 30, z: -20 }]]);
  const stepClub = Function("clubDraw", "clubSaid", "visitorBodies", "KEPT", "CAS", "CHIPS", "clubSay", fn("stepClub") + "\nreturn stepClub;")(() => {}, () => {}, bodies, { loaded: true, visitors: new Map([[VK, { chips: 2350, ach: { a: 1, b: 1 }, last: 5 }]]) }, CAS, { start: 1000 }, (to, t) => hello.push([to, t]));
  const P = { greeted: new Set() };
  stepClub({ pclub: P }, 10.2); stepClub({ pclub: P }, 11.5); stepClub({ pclub: P }, 12.5);
  check("…on the casino's floor, a visitor gets her hello once a visit: welcome back, with what's on their card; or what she's for (not you, nor one outside)",
        hello.length === 2 && hello[0][0] === "v1" && /Welcome back to the Lucky Token, Ana! 2,350 chips and 2 achievements/.test(hello[0][1]) && hello[1][0] === "v2" && /^Welcome to the Lucky Token, Cy!/.test(hello[1][1]) && !/next time/.test(hello[1][1]),
        JSON.stringify(hello));
  const addr = fn("clubAddressed");
  check("…a line meant for her answered to its sayer alone (a visit's id, or you), her own lines only; a whisper not put on the screen; a hello once a visit",
        /if \(e\.figure !== CLUB_NPC \|\| !e\.from \|\| !cas \|\| !cas\.pclub\) return;/.test(addr) && addr.includes('to = e.from.id || "owner"') && addr.includes("if (!e.whisper && Math.hypot(doer.x - CLUB.x, doer.z - CLUB.z) < 9) clubShow(cas, doer);")
        && fn("stepClub").includes("P.greeted.add(id);"));
}
// ---- the table's own players' wallets over their heads: from the buy-in, on their figures, changed at each call
{
  check("a player's wallet: on its figure, hidden till it has bought in, shown on the casino's floor", block("function npcTag(", "\n}\n").includes("B.f.g.add(tag, pop);")
        && /near = B\.worth !== null && B\.x > CAS\.X0 - 3/.test(html) && html.includes("worth: null, hue: ROULETTE_CHIPS[seat.i], tag: null };"));
  check("…its chips at the buy-in, at the start, at each call (with its + or −), each frame looked at; off its head at the party",
        html.includes("B.worth = B.chips = B.buy;") && html.includes("B.worth = B.chips = Math.round(") && html.includes("B.worth = B.chips; npcPop(B, won - staked);")
        && html.includes("npcTagStep(B, dt);") && html.includes("p.f.g.remove(T.tag, T.pop);"));
}

// ---- each name once
const names = [...html.matchAll(/^(?:async\s+)?function\s+([\w$]+)\s*\(|^(?:const|let|var)\s+([\w$]+)\s*=/gm)].map(m => m[1] || m[2]), seen = new Set(), twice = new Set();
for (const n of names) (seen.has(n) ? twice : seen).add(n);
check("the room's page declares each of its names once", !twice.size, [...twice].join(", "));

console.log(`\n${fails} failure(s)`);
process.exit(fails ? 1 : 0);
