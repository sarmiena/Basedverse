// The Agent Room's motion: what happened between two /api/board snapshots, and one queue per
// agent that acts it out. The board stays the truth; the room only plays the difference, and once an
// agent's queue is empty its desk is exactly the snapshot. Pure functions and one class: no DOM, no
// three.js. test_motion.mjs pins them.
"use strict";

// The events from `prev` to `next`, in a fixed order: per agent (roster id order) its releases and
// closes, then its claims, then its moves; then new mail by id. An agent on only one
// side (joined or left) has no events: its desk appears or goes with the snapshot. A claim that
// disappears is a `close` when its row closed (gone[].why), else a `release`. A card's column change
// rides on the claim or release that came with it (`from`, `to`: the agent takes it there), and a close
// says the column it leaves (`from`: to Closed lately); a card an agent keeps holding that changed
// column is a `move` of that agent's (it walks it across the wall). A card nobody holds just changes column.
function diff(prev, next) {
  if (!prev || !next) return [];
  const out = [];
  const before = new Map((prev.agents || []).filter(a => a.session).map(a => [a.session, a]));
  const closed = new Set((next.gone || []).filter(g => g.why === "closed").map(g => g.id));
  const was = new Map((prev.rows || []).map(r => [r.id, r.col])), now = new Map((next.rows || []).map(r => [r.id, r.col]));
  const moved = ref => { const from = was.get(ref), to = now.get(ref); return from && to && from !== to ? { from, to } : null; };
  for (const a of (next.agents || []).slice().sort((x, y) => x.id - y.id)) {
    const p = a.session && before.get(a.session);
    if (!p) continue;
    const had = new Set(p.claims || []), has = new Set(a.claims || []);
    for (const ref of had) if (!has.has(ref)) {
      if (closed.has(ref)) out.push(Object.assign({ kind: "close", who: a.session, ref }, was.get(ref) ? { from: was.get(ref) } : {}));
      else out.push(Object.assign({ kind: "release", who: a.session, ref }, moved(ref) || {}));
    }
    for (const ref of has) if (!had.has(ref)) out.push(Object.assign({ kind: "claim", who: a.session, ref }, moved(ref) || {}));
    for (const ref of has) if (had.has(ref) && moved(ref)) out.push(Object.assign({ kind: "move", who: a.session, ref }, moved(ref)));
  }
  // Mail names its sender by handle and its recipient by session (or `owner`, `all`).
  const seen = new Set((prev.mail || []).map(m => m.id)), agents = next.agents || [];
  const bySession = s => agents.some(a => a.session === s) ? s : null;
  const byName = n => (agents.find(a => a.name === n) || {}).session || null;
  for (const m of (next.mail || []).slice().sort((x, y) => x.id - y.id)) if (!seen.has(m.id)) out.push({ kind: "mail", id: m.id, from: byName(m.sender), to: bySession(m.rcpt) });
  return out;
}

// What undoes what: a claim and a release (or close) of the same ref. A queued event that its opposite
// undoes before it starts is cancelled with it, so a fast claim-and-release never shows a card going
// to a desk it is not on.
const UNDO = { claim: ["release", "close"], release: ["claim"], close: ["claim"], move: [] };   // (a move is never undone: each is walked)

class Queues {
  constructor(max = 4) { this.max = max; this.q = new Map(); this.active = new Map(); }
  // Queue events per agent (`who`). Mail and anything without an agent is not queued here.
  push(events) {
    for (const e of events) {
      if (!e.who || !UNDO[e.kind]) continue;
      const q = this.q.get(e.who) || [];
      const i = q.findIndex(o => UNDO[e.kind].includes(o.kind) && o.ref === e.ref);
      if (i >= 0) { q.splice(i, 1); this.q.set(e.who, q); continue; }
      q.push(e);
      this.q.set(e.who, q);
      // Too far behind the truth: every queue is dropped and the room shows the snapshot. Dropping
      // only this agent's oldest could drop a claim still waiting on another agent's release, and
      // put that card on two desks.
      if (q.length > this.max) { this.flush(); return; }   // and the rest of this snapshot's events with it
    }
  }
  // The agent's next event, now in progress; null if it is busy or has none startable. A card passed
  // between agents is claimed only once no other agent still has its giving back (release or close)
  // queued or under way, so it is never in two places. Only a claim is ever held back: the agent runs
  // its first event that is not a held claim, so a giving back always runs and no two agents can
  // wait on each other.
  start(who) {
    if (this.active.has(who)) return null;
    const q = this.q.get(who);
    if (!q || !q.length) return null;
    const i = q.findIndex(e => !this.held(e, who));
    if (i < 0) return null;
    const [e] = q.splice(i, 1); this.active.set(who, e); return e;
  }
  held(e, who) {
    if (e.kind !== "claim") return false;
    const gives = o => o && UNDO[e.kind].includes(o.kind) && o.ref === e.ref;
    for (const [w, q] of this.q) if (w !== who && q.some(gives)) return true;
    for (const [w, o] of this.active) if (w !== who && gives(o)) return true;
    return false;
  }
  done(who) { this.active.delete(who); }
  busy(who) { return this.active.has(who); }
  waiting() { return [...this.q].filter(([w, q]) => q.length && !this.active.has(w)).map(([w]) => w); }
  // Forget agents no longer on the roster, and (flush) everything: the room then shows the snapshot.
  prune(sessions) { const keep = new Set(sessions); for (const m of [this.q, this.active]) for (const w of [...m.keys()]) if (!keep.has(w)) m.delete(w); }
  flush() { this.q.clear(); this.active.clear(); }
  // Where the agent's claimed cards are while its queue plays: `desk` (on the desk), `hand` (carried
  // by the walking figure). A claim still to be walked is on the wall, not the desk; a release still
  // to be walked is still on the desk; a move keeps it on the desk (the agent's notes) as it carries the
  // card across the wall. With an empty queue, desk == claims.
  view(who, claims) {
    const desk = (claims || []).slice(), q = this.q.get(who) || [], a = this.active.get(who);
    const drop = ref => { const i = desk.indexOf(ref); if (i >= 0) desk.splice(i, 1); };
    for (const e of q) if (e.kind === "claim") drop(e.ref); else if ((e.kind === "release" || e.kind === "close") && !desk.includes(e.ref)) desk.push(e.ref);
    let hand = null;
    if (a && a.ref !== undefined) { if (a.kind !== "move") drop(a.ref); hand = a.ref; }   // (a move: its notes stay on the desk, the card in its hands)
    return { desk, hand };
  }
}

// A point `dist` along a polyline of [x, z] points, with the heading (radians about +y, 0 = +z) of
// the leg it is on. Past the end it stays at the last point.
function along(pts, dist) {
  for (let i = 1; i < pts.length; i++) {
    const [x0, z0] = pts[i - 1], [x1, z1] = pts[i], len = Math.hypot(x1 - x0, z1 - z0);
    if (dist <= len || i === pts.length - 1) {
      const f = len ? Math.min(1, Math.max(0, dist / len)) : 1;
      return { x: x0 + (x1 - x0) * f, z: z0 + (z1 - z0) * f, heading: Math.atan2(x1 - x0, z1 - z0) };
    }
    dist -= len;
  }
  return { x: pts[0][0], z: pts[0][1], heading: 0 };
}
const pathLength = pts => pts.slice(1).reduce((s, p, i) => s + Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]), 0);

if (typeof window === "undefined" && typeof module !== "undefined") module.exports = { diff, Queues, along, pathLength };
