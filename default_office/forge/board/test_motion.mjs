// Cases for motion.js, the Agent Room's snapshot diff and per-agent queues.
// The rule under test: the room acts the difference out, and once a queue is empty the desk is the
// snapshot again; a fast claim-and-release never shows a card where it is not.
//
// Run: node board/test_motion.mjs
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { diff, Queues, along, pathLength } = require("./motion.js");

let fails = 0;
function caseOf(label, fn, want) {
  let got;
  try { got = fn(); } catch (e) { got = e.name + ": " + e.message; }
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log(`[${ok ? "ok " : "FAIL"}] ${label.padEnd(64)} ${JSON.stringify(got).slice(0, 90)}`);
}

const ag = (id, session, name, claims = []) => ({ id, session, name, claims });
const snap = (agents, o = {}) => Object.assign({ agents, gone: [], mail: [] }, o);
const kinds = evs => evs.map(e => e.kind + ":" + (e.ref || e.id));

// ---- diff
caseOf("no previous snapshot: nothing to act out", () => diff(null, snap([ag(1, "s1", "a", ["x#1"])])), []);
caseOf("same snapshot: no events", () => { const s = snap([ag(1, "s1", "a", ["x#1"])]); return diff(s, s); }, []);
caseOf("a new claim", () => diff(snap([ag(1, "s1", "a")]), snap([ag(1, "s1", "a", ["x#1"])])), [{ kind: "claim", who: "s1", ref: "x#1" }]);
caseOf("a dropped claim whose row is still open is a release",
  () => kinds(diff(snap([ag(1, "s1", "a", ["x#1"])]), snap([ag(1, "s1", "a")]))), ["release:x#1"]);
caseOf("a dropped claim whose row closed is a close",
  () => kinds(diff(snap([ag(1, "s1", "a", ["x#1"])]), snap([ag(1, "s1", "a")], { gone: [{ id: "x#1", why: "closed" }] }))), ["close:x#1"]);
caseOf("a row that left the board another way is a release, not a close",
  () => kinds(diff(snap([ag(1, "s1", "a", ["x#1"])]), snap([ag(1, "s1", "a")], { gone: [{ id: "x#1", why: "off-board" }] }))), ["release:x#1"]);
caseOf("release before claim when an agent swaps cards",
  () => kinds(diff(snap([ag(1, "s1", "a", ["x#1"])]), snap([ag(1, "s1", "a", ["x#2"])]))), ["release:x#1", "claim:x#2"]);
caseOf("agents in roster-id order, whatever order they are served in",
  () => diff(snap([ag(2, "s2", "b"), ag(1, "s1", "a")]), snap([ag(2, "s2", "b", ["x#2"]), ag(1, "s1", "a", ["x#1"])])).map(e => e.who), ["s1", "s2"]);
caseOf("an agent who just joined brings its claims with no walk",
  () => diff(snap([]), snap([ag(1, "s1", "a", ["x#1"])])), []);
caseOf("an agent who left takes its claims with no walk",
  () => diff(snap([ag(1, "s1", "a", ["x#1"])]), snap([])), []);
caseOf("new mail only, sender by handle, recipient by session",
  () => diff(snap([ag(1, "s1", "a"), ag(2, "s2", "b")], { mail: [{ id: 5, sender: "a", rcpt: "s2" }] }),
             snap([ag(1, "s1", "a"), ag(2, "s2", "b")], { mail: [{ id: 6, sender: "b", rcpt: "owner" }, { id: 5, sender: "a", rcpt: "s2" }] })),
  [{ kind: "mail", id: 6, from: "s2", to: null }]);

// ---- cards across the wall: a column change rides on the claim or release that came with it, a held card moved on is a
// move of its agent's, a close says where it was, and a card nobody holds just changes column
const rowsAt = cols => Object.entries(cols).map(([id, col]) => ({ id, col }));
caseOf("a claim that moved the card (Ready to In progress): one walk, from and to",
  () => diff(snap([ag(1, "s1", "a", [])], { rows: rowsAt({ "x#1": "ready" }) }), snap([ag(1, "s1", "a", ["x#1"])], { rows: rowsAt({ "x#1": "doing" }) })),
  [{ kind: "claim", who: "s1", ref: "x#1", from: "ready", to: "doing" }]);
caseOf("a claim that left it where it was: no from, no to",
  () => diff(snap([ag(1, "s1", "a", [])], { rows: rowsAt({ "x#1": "ready" }) }), snap([ag(1, "s1", "a", ["x#1"])], { rows: rowsAt({ "x#1": "ready" }) })),
  [{ kind: "claim", who: "s1", ref: "x#1" }]);
caseOf("a held card moved on (In progress to In review): a move of its holder's",
  () => diff(snap([ag(1, "s1", "a", ["x#1"]), ag(2, "s2", "b", [])], { rows: rowsAt({ "x#1": "doing" }) }), snap([ag(1, "s1", "a", ["x#1"]), ag(2, "s2", "b", [])], { rows: rowsAt({ "x#1": "review" }) })),
  [{ kind: "move", who: "s1", ref: "x#1", from: "doing", to: "review" }]);
caseOf("a close: from where it was (to Closed lately)",
  () => diff(snap([ag(1, "s1", "a", ["x#1"])], { rows: rowsAt({ "x#1": "review" }) }), snap([ag(1, "s1", "a", [])], { gone: [{ id: "x#1", why: "closed" }] })),
  [{ kind: "close", who: "s1", ref: "x#1", from: "review" }]);
caseOf("a release that put it back in Ready: from and to",
  () => diff(snap([ag(1, "s1", "a", ["x#1"])], { rows: rowsAt({ "x#1": "doing" }) }), snap([ag(1, "s1", "a", [])], { rows: rowsAt({ "x#1": "ready" }) })),
  [{ kind: "release", who: "s1", ref: "x#1", from: "doing", to: "ready" }]);
caseOf("a card nobody holds that changed column: no walk",
  () => diff(snap([ag(1, "s1", "a", [])], { rows: rowsAt({ "x#1": "ready" }) }), snap([ag(1, "s1", "a", [])], { rows: rowsAt({ "x#1": "parked" }) })), []);
{
  const q = new Queues();
  q.push([{ kind: "move", who: "s1", ref: "x#1", from: "doing", to: "review" }, { kind: "move", who: "s1", ref: "x#1", from: "review", to: "doing" }]);
  caseOf("moves are queued, each walked (never undone by the next)", () => q.q.get("s1").map(e => e.to), ["review", "doing"]);
  q.start("s1");
  caseOf("a move under way: its notes stay on the desk, the card in its hands", () => q.view("s1", ["x#1"]), { desk: ["x#1"], hand: "x#1" });
}

// ---- queues
caseOf("a claim queued: the card stays off the desk until it is walked", () => {
  const q = new Queues(); q.push([{ kind: "claim", who: "s1", ref: "x#1" }]);
  const before = q.view("s1", ["x#1"]); q.start("s1"); const walking = q.view("s1", ["x#1"]); q.done("s1");
  return [before, walking, q.view("s1", ["x#1"])];
}, [{ desk: [], hand: null }, { desk: [], hand: "x#1" }, { desk: ["x#1"], hand: null }]);
caseOf("a release queued: the card stays on the desk until it is walked", () => {
  const q = new Queues(); q.push([{ kind: "release", who: "s1", ref: "x#1" }]);
  const before = q.view("s1", []); q.start("s1"); const walking = q.view("s1", []); q.done("s1");
  return [before, walking, q.view("s1", [])];
}, [{ desk: ["x#1"], hand: null }, { desk: [], hand: "x#1" }, { desk: [], hand: null }]);
caseOf("claim then release before the walk starts: both cancelled, nothing shown", () => {
  const q = new Queues(); q.push([{ kind: "claim", who: "s1", ref: "x#1" }]); q.push([{ kind: "close", who: "s1", ref: "x#1" }]);
  return [q.start("s1"), q.view("s1", [])];
}, [null, { desk: [], hand: null }]);
caseOf("a release that comes while its claim is being walked waits its turn", () => {
  const q = new Queues(); q.push([{ kind: "claim", who: "s1", ref: "x#1" }]); q.start("s1");
  q.push([{ kind: "release", who: "s1", ref: "x#1" }]);
  const mid = q.view("s1", []); q.done("s1");
  return [mid, q.start("s1").kind];
}, [{ desk: [], hand: "x#1" }, "release"]);
caseOf("one agent walks at a time per desk, others in parallel", () => {
  const q = new Queues(); q.push([{ kind: "claim", who: "s1", ref: "x#1" }, { kind: "claim", who: "s1", ref: "x#2" }, { kind: "claim", who: "s2", ref: "x#3" }]);
  return [q.start("s1").ref, q.start("s1"), q.start("s2").ref, q.waiting()];
}, ["x#1", null, "x#3", []]);
caseOf("a card passed between agents is claimed only after the giver has walked it back", () => {
  const q = new Queues(); q.push(diff(snap([ag(1, "s1", "a", ["x#1"]), ag(2, "s2", "b")]), snap([ag(1, "s1", "a"), ag(2, "s2", "b", ["x#1"])])));
  const first = [q.start("s2"), q.start("s1").kind, q.start("s2")]; q.done("s1");
  return [...first, q.start("s2").kind];
}, [null, "release", null, "claim"]);
// Plays the queues for up to 20 frames, one agent at a time in the order given (each agent's ended
// walk, then its next start), and records any frame where a card is in two places and what is
// left unplayed. The room ends every walk before starting any in a frame; this order is the harder
// one, since a claimer listed first tries to start before the giver's release has been marked done.
function play(q, who, claims) {
  const log = [], twice = [];
  for (let f = 0; f < 20; f++) {
    for (const w of who) { if (q.busy(w)) { log.push(w + ":" + q.active.get(w).kind + ":" + q.active.get(w).ref); q.done(w); } q.start(w); }
    const at = {};
    for (const w of who) { const v = q.view(w, claims[w]); for (const r of v.desk.concat(v.hand || [])) (at[r] = at[r] || []).push(w); }
    for (const [r, ws] of Object.entries(at)) if (ws.length > 1) twice.push(f + ":" + r);
  }
  return { log, twice, left: who.filter(w => q.busy(w) || (q.q.get(w) || []).length) };
}
caseOf("a card handed over behind another release is never in two places", () => {
  const q = new Queues(), before = snap([ag(1, "B", "b"), ag(2, "A", "a", ["w", "x"])]), after = snap([ag(1, "B", "b", ["x"]), ag(2, "A", "a")]);
  q.push(diff(before, after));
  return play(q, ["B", "A"], { A: [], B: ["x"] });
}, { log: ["A:release:w", "A:release:x", "B:claim:x"], twice: [], left: [] });
caseOf("two agents swapping cards never wait on each other", () => {
  const q = new Queues(), before = snap([ag(1, "A", "a", ["x"]), ag(2, "B", "b", ["y"])]), after = snap([ag(1, "A", "a", ["y"]), ag(2, "B", "b", ["x"])]);
  q.push(diff(before, after));
  const r = play(q, ["A", "B"], { A: ["y"], B: ["x"] });
  return [r.log.length, r.twice, r.left];
}, [4, [], []]);
caseOf("a held-back claim lets its own agent's release go first", () => {
  const q = new Queues(); q.push([{ kind: "release", who: "A", ref: "x" }, { kind: "claim", who: "B", ref: "x" }, { kind: "release", who: "B", ref: "z" }]);
  q.start("A"); return q.start("B");
}, { kind: "release", who: "B", ref: "z" });
caseOf("too far behind: every queue is dropped, every desk is the snapshot", () => {
  const q = new Queues(2); q.push([{ kind: "release", who: "s2", ref: "x" }]); q.start("s2");
  q.push(["a", "b", "x"].map(r => ({ kind: "claim", who: "s1", ref: r })));
  return [q.view("s1", ["a", "b", "x"]), q.view("s2", []), q.busy("s2")];
}, [{ desk: ["a", "b", "x"], hand: null }, { desk: [], hand: null }, false]);
caseOf("an overflow mid-snapshot drops the rest of that snapshot too: no card in two places", () => {
  const q = new Queues(4);
  q.push([{ kind: "claim", who: "A", ref: "y" }, ...["a", "b", "c", "d", "e"].map(r => ({ kind: "claim", who: "B", ref: r })), { kind: "release", who: "C", ref: "y" }]);
  return play(q, ["A", "B", "C"], { A: ["y"], B: ["a", "b", "c", "d", "e"], C: [] });
}, { log: [], twice: [], left: [] });
caseOf("a held claim does not hold up the agent's other claims", () => {
  const q = new Queues(); q.push([{ kind: "release", who: "A", ref: "x" }, { kind: "claim", who: "B", ref: "x" }, { kind: "claim", who: "B", ref: "y" }]);
  q.start("A"); return q.start("B");
}, { kind: "claim", who: "B", ref: "y" });
caseOf("mail is not queued on a desk", () => { const q = new Queues(); q.push([{ kind: "mail", id: 1, from: "s1", to: "s2" }]); return q.waiting(); }, []);
caseOf("an agent leaving the roster drops its queue", () => {
  const q = new Queues(); q.push([{ kind: "claim", who: "s1", ref: "x#1" }, { kind: "claim", who: "s2", ref: "x#2" }]); q.start("s2");
  q.prune(["s1"]); return [q.waiting(), q.busy("s2")];
}, [["s1"], false]);
caseOf("flush: every desk is the snapshot", () => {
  const q = new Queues(); q.push([{ kind: "claim", who: "s1", ref: "x#1" }]); q.start("s1"); q.flush();
  return [q.view("s1", ["x#1"]), q.busy("s1")];
}, [{ desk: ["x#1"], hand: null }, false]);

// ---- paths
const P = [[0, 0], [3, 0], [3, 4]];
caseOf("path length", () => pathLength(P), 7);
caseOf("along the first leg, heading +x", () => { const p = along(P, 1.5); return [p.x, p.z, +p.heading.toFixed(3)]; }, [1.5, 0, 1.571]);
caseOf("along the second leg, heading +z", () => { const p = along(P, 5); return [p.x, p.z, p.heading]; }, [3, 2, 0]);
caseOf("past the end stays at the end", () => { const p = along(P, 99); return [p.x, p.z]; }, [3, 4]);

console.log(`\n${fails} failure(s)`);
process.exit(fails ? 1 : 0);
