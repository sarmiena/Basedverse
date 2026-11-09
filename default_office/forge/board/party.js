// Your agents, as a party: the Agent Room's list of them and the kanban's, one making (the user, 2026-11-02: "retrofit
// this into the kanban board view"). Two things, each made by the page that wants it:
// - Figures(o): the room's seated voxel figure: its face the identicon, its halo of context, its clothes as the room's
//   environment has them (LOOKS), green about the gills with malaria. The room's scene makes its agents with it, and a
//   picture of one is made with it, so the room and every page's pictures are one making. The room gives its own
//   materials, textures and labels (its caches, which it prunes); a page with no room gets plain ones of its own.
// - Party.make(o): the list: a row each, the agent's head as the figure has it, what it is up to drawn on its picture
//   (a command running, dozing, its phone, a chat, an errand, out by the pool, working, its subagents, waiting on you),
//   its card beside it; over a row, a legend of what each thing on its picture means, its icon beside it, as a buff's
//   tooltip. A click on a row is the page's own (the room's: its ticket and its terminal; the kanban's the same).
// Loaded after common.js and three.js; nothing here runs till a page makes one. Never part of what a visitor sees: the
// room's server page makes figures with it, and a figure is all Visit sends.
(function () {
"use strict";
// What each of the room's environments dresses its agents in (room.html's ENVS says the same of each: test_party.mjs).
const LOOKS = { office: {}, sweatshop: { ragged: true }, cave: { ragged: true, militant: true }, pool: { pool: true } };
// Down with malaria (a mosquito's bite, in the sweatshop): the room keeps who, in this browser (room.html's `sick`).
const SICK_SKIN = "#93b743", SICK_FACE = 0xb4d47a;
// The Matrix's glyphs, on a desk's screen in the room and behind an agent's picture while a command of its runs.
const GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789Z:.=*+<>|";

// A page with no room: materials, boxes, faces and labels of its own (the room's, made the same way, are cached and
// pruned with its scene).
function plain() {
  const mats = new Map(), tex = new Map();
  const M = c => { const k = String(c); if (!mats.has(k)) mats.set(k, new THREE.MeshLambertMaterial({ color: c })); return mats.get(k); };
  const box = (w, h, d, mat, x, y, z, parent) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z); parent.add(m); return m; };
  const texMat = (t, opts) => new THREE.MeshLambertMaterial(Object.assign({ map: t }, opts));
  const cached = (k, make) => { if (!tex.has(k)) tex.set(k, make()); return tex.get(k); };
  const svgTex = (svg, px) => cached("svg:" + px + ":" + svg, () => {
    const c = document.createElement("canvas"); c.width = c.height = px;
    const t = new THREE.CanvasTexture(c); t.magFilter = t.minFilter = THREE.NearestFilter;
    const img = new Image();
    img.onload = () => { const g = c.getContext("2d"); g.imageSmoothingEnabled = false; g.drawImage(img, 0, 0, px, px); t.needsUpdate = true; };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" '));
    return t;
  });
  // One line on a rounded plate, as the room's textTex draws a halo's count.
  const label = (lines, o, scale, parent) => {
    o = Object.assign({ w: 640, h: 150, bg: "rgba(255,255,255,0.92)", fg: "#1c2128", font: "700 40px 'JetBrains Mono'", pad: 20 }, o);
    const t = cached("txt:" + JSON.stringify([lines, o]), () => {
      const c = document.createElement("canvas"); c.width = o.w; c.height = o.h; const g = c.getContext("2d"), t = new THREE.CanvasTexture(c);
      const draw = () => {
        g.clearRect(0, 0, o.w, o.h);
        if (o.bg) { g.fillStyle = o.bg; g.beginPath(); g.roundRect(0, 0, o.w, o.h, 18); g.fill(); }
        g.fillStyle = o.fg; g.textBaseline = "top"; g.font = o.font; g.fillText(String(lines[0] ?? ""), o.pad, o.pad); t.needsUpdate = true;
      };
      draw();
      if (document.fonts) document.fonts.load(o.font).then(draw, () => {});   // (drawn again in its own face once that is in)
      return t;
    });
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthWrite: false, transparent: true, fog: false }));
    s.scale.set(scale, scale * o.h / o.w, 1); parent.add(s); return s;
  };
  const dispose = g => g.traverse(x => {
    if (x.geometry) x.geometry.dispose();
    for (const m of [].concat(x.material || [])) if (![...mats.values()].includes(m)) m.dispose();
  });
  return { M, box, texMat, svgTex, label, dispose };
}

function Figures(o) {
  o = Object.assign(plain(), o);
  const { M, box, texMat, svgTex, label } = o;
  const env = () => (o.env && o.env()) || {}, sickNow = s => !!(o.sick && o.sick(s));
  // A seated voxel figure, front +z, origin on the floor under the seat. Identicon face, its hue for the shirt.
  function figureLook(a) {
    const { hue } = identCells(a.session);
    const grey = a.fresh === "dead", dim = a.fresh === "stale", rag = !!env().ragged;
    // Ragged: the same hue, faded and grimy, so an agent is still known by its colour.
    const shirt = grey ? "#6e7681" : rag ? `hsl(${hue}, ${dim ? 8 : 20}%, 31%)` : `hsl(${hue}, ${dim ? 15 : 55}%, 38%)`;
    // Down with malaria (a mosquito's bite, in the sweatshop): green about the gills for a few minutes.
    const ill = !grey && sickNow(a.session), skinCss = grey ? "#9aa4ae" : ill ? SICK_SKIN : `hsl(${hue}, 45%, 88%)`;
    return { grey, dim, rag, ill, shirt: M(shirt), skin: M(skinCss), skinCss, dark: M(rag ? 0x3a3428 : 0x2b3138) };
  }
  // Tattered clothes: a hem torn to uneven strips, a sewn-on patch, holes front and back, a patched knee.
  // Seeded by the session, so an agent's rags are the same at every redraw and on every walk.
  function tatters(a, parent, hemY, kneeAt) {
    const R = rng(hash32(a.session)), shirt = figureLook(a).shirt, hole = M(0x15130f), patch = M([0x8a7550, 0x6f7f5a, 0x9a6a4a][hash32(a.session) % 3]);
    for (let i = 0; i < 5; i++) { const h = 0.05 + R() * 0.16; box(0.11, h, 0.33, shirt, -0.25 + i * 0.125 + (R() - 0.5) * 0.03, hemY - h / 2, 0, parent); }
    box(0.17, 0.15, 0.02, patch, -0.12 + R() * 0.22, hemY + 0.36 + R() * 0.12, 0.176, parent);
    box(0.15, 0.13, 0.02, patch, 0.12 - R() * 0.22, hemY + 0.42 + R() * 0.1, -0.176, parent);   // and one on the back, the side a seated agent shows
    box(0.07, 0.07, 0.02, hole, 0.16 - R() * 0.32, hemY + 0.18 + R() * 0.3, 0.176, parent);
    box(0.09, 0.06, 0.02, hole, 0.18 - R() * 0.36, hemY + 0.2 + R() * 0.3, -0.176, parent);
    if (kneeAt) box(0.21, 0.12, 0.04, patch, kneeAt[0], kneeAt[1], kneeAt[2], kneeAt[3] || parent);
  }
  // Armed, in the terrorist sweatshop (`militant`), all of it seeded by the session, so an agent's kit is the same at
  // every redraw and on every walk: over its face a balaclava (its eyes showing) or a bandana, or nothing; on its head a
  // helmet, a boonie hat, a beret or a beanie (not over a subagent's cap); across its chest a bandolier or a chest rig of
  // pouches; on a sling across its back a rifle, a carbine, a shotgun or a rocket launcher (a subagent: a stick or a
  // machete); grenades hanging from its belt. `parent` holds its torso (its middle y high), `h` is its head.
  function militantKit(a, parent, y, h) {
    const R = rng(hash32(a.session) ^ 0x7e44), pick = arr => arr[Math.floor(R() * arr.length)], intern = !!a.owner;
    const OLIVE = M(0x4b5320), DARK = M(0x23251d), KNIT = M(pick([0x1e2023, 0x2e3324, 0x3a3328])), b = (w, ht, d, m, x, yy, z, p = parent) => box(w, ht, d, m, x, yy, z, p);
    const mask = R() < (intern ? 0.4 : 0.7) ? (intern || R() < 0.5 ? "bandana" : "balaclava") : null;
    if (mask === "balaclava") { b(0.59, 0.2, 0.59, KNIT, 0, 0.19, 0, h); b(0.59, 0.25, 0.59, KNIT, 0, -0.155, 0, h); b(0.59, 0.12, 0.05, KNIT, 0, 0.03, -0.27, h); for (const sx of [-1, 1]) b(0.05, 0.12, 0.5, KNIT, sx * 0.27, 0.03, -0.02, h); }
    if (mask === "bandana") { const m = M(pick([0x8a2b22, 0x2f4a2a, 0xa38b5c, 0x1d1d1d, 0x2c3e66])); b(0.6, 0.26, 0.6, m, 0, -0.15, 0, h); b(0.12, 0.1, 0.12, m, 0, -0.08, -0.34, h); }
    if (!intern) {
      const hat = pick(["helmet", "boonie", "beret", "beanie", null]);
      if (hat === "helmet") { b(0.64, 0.2, 0.64, M(0x4f5530), 0, 0.33, 0, h); b(0.7, 0.04, 0.7, M(0x464b2b), 0, 0.24, 0, h); }
      if (hat === "boonie") { const m = M(pick([0x8a7b56, 0x5b6340])); b(0.6, 0.14, 0.6, m, 0, 0.35, 0, h); b(0.86, 0.03, 0.86, m, 0, 0.29, 0, h); }
      if (hat === "beret") b(0.62, 0.08, 0.62, M(pick([0x1d1d1d, 0x5a1d1d, 0x2c3a24])), 0.03, 0.32, 0, h).rotation.z = 0.15;
      if (hat === "beanie") b(0.6, 0.18, 0.6, KNIT, 0, 0.33, 0, h);
    }
    const chest = R();
    if (chest < 0.4) {   // a bandolier, over one shoulder, its rounds along it
      const band = new THREE.Group(); band.position.set(0, y + 0.02, 0.18); band.rotation.z = 0.8; parent.add(band);
      b(0.1, 0.95, 0.03, M(0x5a4030), 0, 0, 0, band); for (let k = 0; k < 6; k++) b(0.06, 0.04, 0.03, M(0xc9a24a), 0, -0.35 + k * 0.14, 0.02, band);
    } else if (chest < 0.75) {   // a chest rig, its pouches
      b(0.64, 0.36, 0.06, R() < 0.5 ? OLIVE : DARK, 0, y + 0.06, 0.18); for (const px of [-0.18, 0, 0.18]) b(0.15, 0.15, 0.07, DARK, px, y - 0.02, 0.23);
    }
    b(0.06, 0.95, 0.02, DARK, 0, y + 0.02, 0.176).rotation.z = -0.8;   // the sling across its front, its weapon on its back
    const w = new THREE.Group(); w.position.set(0, y + 0.05, -0.22); w.rotation.z = 0.9; parent.add(w);
    weaponModel(intern ? pick(["stick", "machete"]) : pick(["rifle", "rifle", "carbine", "shotgun", "launcher"]), w);
    b(0.62, 0.07, 0.36, DARK, 0, y - 0.33, 0);   // its belt, and grenades hanging from it
    for (let k = 0, n = intern ? (R() < 0.5 ? 1 : 0) : 1 + Math.floor(R() * 3); k < n; k++) {
      const gx = [-0.3, 0.3, -0.18][k], gr = new THREE.Mesh(new THREE.SphereGeometry(0.06, 8, 6), OLIVE);
      gr.scale.y = 1.25; gr.position.set(gx, y - 0.43, 0.2); gr.castShadow = true; parent.add(gr);
      b(0.02, 0.07, 0.015, DARK, gx + 0.04, y - 0.38, 0.23);
    }
  }
  // A weapon along its x (the muzzle, point or blade toward +x), its middle at the origin of `g`.
  function weaponModel(kind, g) {
    const WOOD = M(0x6b4528), METAL = M(0x2b2d30), b = (w, h, d, m, x, y = 0) => box(w, h, d, m, x, y, 0, g);
    const rod = (r, len, mat, x = 0) => { const c = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 8), mat); c.rotation.z = Math.PI / 2; c.position.x = x; c.castShadow = true; g.add(c); return c; };
    if (kind === "rifle" || kind === "carbine") {
      const k = kind === "rifle" ? 1 : 0.8;
      b(0.3 * k, 0.11, 0.05, WOOD, -0.44 * k, -0.03); b(0.36 * k, 0.09, 0.06, METAL, -0.12 * k); b(0.07, 0.2, 0.05, METAL, 0, -0.14).rotation.z = 0.3;
      b(0.24 * k, 0.075, 0.065, WOOD, 0.18 * k); b(0.3 * k, 0.03, 0.03, METAL, 0.44 * k, 0.01);
    } else if (kind === "shotgun") { b(0.32, 0.1, 0.05, WOOD, -0.42, -0.03); b(0.3, 0.08, 0.06, METAL, -0.1); b(0.5, 0.04, 0.04, METAL, 0.3, 0.02); b(0.3, 0.05, 0.06, WOOD, 0.2, -0.04); }
    else if (kind === "launcher") {   // a tube, its rocket's head out of the front, a grip under it
      rod(0.05, 1.0, M(0x4b5320)); const war = new THREE.Mesh(new THREE.ConeGeometry(0.09, 0.28, 8), M(0x3a4220)); war.rotation.z = -Math.PI / 2; war.position.x = 0.62; g.add(war);
      b(0.05, 0.14, 0.04, METAL, -0.05, -0.1);
    } else if (kind === "stick") rod(0.032, 1.0, M(0x6b4a2a));
    else if (kind === "machete") { b(0.18, 0.05, 0.035, M(0x1d1d18), -0.3); b(0.58, 0.09, 0.012, M(0xb8bcc0), 0.08, 0.01); }
    return g;
  }
  function head(a, grey, skin, ill) {
    const face = texMat(svgTex(avatar(a.session, 7), 70), grey ? { color: 0x9aa4ae } : ill ? { color: SICK_FACE } : {});
    const h = new THREE.Mesh(headGeo(), [skin, skin, skin, skin, face, skin]);
    h.castShadow = true;
    // A subagent reports no context of its own, so no halo; it wears a cap in its agent's colour instead.
    if (a.owner) h.add(cap(grey ? "#6e7681" : `hsl(${identCells(a.owner).hue}, 60%, 45%)`)); else h.add(halo(Number(a.ctx) || 0, grey));
    return h;
  }
  // A cap on the head (front +z): a crown and a brim.
  function cap(color) {
    const g = new THREE.Group(), m = M(color);
    box(0.6, 0.16, 0.6, m, 0, 0.35, 0, g); box(0.54, 0.05, 0.3, m, 0, 0.29, 0.42, g);
    g.userData.cap = true; return g;
  }
  // Context in use as a halo over the head, the kanban's ring laid flat: the lit arc is the share of
  // CTX_FULL in use, gold, turning red past 80%, with the token count just above. The head's child, so it
  // tilts with a slumped head and goes along on a walk. An agent that has reported no context yet (a
  // session started before its directory got the hooks) wears only the faint ring.
  function halo(n, grey) {
    const g = new THREE.Group(), ring = new THREE.Group(), f = Math.min(1, n / CTX_FULL);
    g.position.y = 0.5; ring.rotation.x = -Math.PI / 2; g.add(ring);
    const glow = c => new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: 0.95 });
    ring.add(new THREE.Mesh(new THREE.TorusGeometry(0.34, 0.022, 8, 48),
                            new THREE.MeshBasicMaterial({ color: 0xd0d7de, transparent: true, opacity: 0.3 })));
    if (f > 0) ring.add(new THREE.Mesh(new THREE.TorusGeometry(0.34, 0.045, 8, 48, Math.PI * 2 * f),
                                       glow(grey ? 0x9aa4ae : f >= 0.8 ? 0xf85149 : 0xffd866)));
    // h fits the font with its padding: textTex drops a line that would overflow the canvas.
    if (n) label([fmtTok(n)], { w: 220, h: 92, pad: 14, bg: "rgba(22,27,34,0.7)", fg: f >= 0.8 ? "#f85149" : "#ffd866", font: "700 56px 'JetBrains Mono'" }, 0.7, g)
      .position.y = 0.24;   // just above the ring, under the name card
    return g;
  }
  // (Seated as it sits at its desk, slumped when it has not acted lately; the room's figure() puts it in a pose: dozing,
  // stretching on waking, on its phone.)
  function figure(a) {
    const { grey, dim, ill, shirt, skin, dark } = figureLook(a);
    const g = new THREE.Group(), body = new THREE.Group(); g.add(body);
    for (const sx of [-0.14, 0.14]) { box(0.2, 0.18, 0.5, dark, sx, 0.7, 0.25, g); box(0.2, 0.62, 0.2, dark, sx, 0.31, 0.48, g); }
    box(0.6, 0.72, 0.34, shirt, 0, 1.04, 0, body);
    const arms = [-0.4, 0.4].map(sx => { const arm = box(0.16, 0.55, 0.18, shirt, sx, 1.02, 0.18, body); arm.rotation.x = -1.1; return arm; });
    const h = head(a, grey, skin, ill); h.position.y = 1.7; body.add(h);
    if (figureLook(a).rag) tatters(a, body, 0.68, [0.14, 0.7, 0.51, g]);
    if (env().militant) militantKit(a, body, 1.04, h);
    if (env().pool) poolKit(a, body, 1.04, h);
    if (ill) blanket(body, 1.1);
    // slumped: not acting lately (one in a turn but quiet a while sits up: it waits on a long command)
    if ((a.fresh === "idle" && !a.working) || dim || grey) { body.rotation.x = 0.18; h.rotation.x = 0.35; }
    g.userData = { head: h, arms, body, who: a };
    return g;
  }
  // At the pool club (`pool`): shades over the eyes, and a lei of flowers round the neck, its colours seeded by the session.
  // `parent` holds the torso (its middle y high), `h` is the head.
  function poolKit(a, parent, y, h) {
    const R = rng(hash32(a.session) ^ 0x1e1), FLOWERS = [0xff5fa2, 0xffd23f, 0xff8a3d, 0xf4f1ea, 0xb36bff, 0xff4d4d], DARK = M(0x151515);
    box(0.5, 0.13, 0.04, DARK, 0, 0.05, 0.29, h); for (const s of [-1, 1]) box(0.02, 0.04, 0.3, DARK, s * 0.285, 0.06, 0.13, h);
    const c1 = FLOWERS[Math.floor(R() * 6)], c2 = FLOWERS[Math.floor(R() * 6)];
    for (let k = 0; k < 10; k++) { const a = k / 10 * Math.PI * 2; box(0.11, 0.08, 0.11, M(k % 2 ? c1 : c2), Math.sin(a) * 0.27, y + 0.33 - (Math.cos(a) > 0.5 ? 0.07 : 0), Math.cos(a) * 0.19, parent); }
  }
  // A wool blanket round the shoulders, hanging down the back (the side the camera sees of someone at a desk), on
  // a torso whose middle is at y.
  function blanket(parent, y) {
    const g = new THREE.Group(), wool = M(0x9aa6b4), stripe = M(0x5f6e82); g.position.y = y; g.userData.blanket = true; parent.add(g);
    box(0.74, 0.66, 0.06, wool, 0, -0.04, -0.2, g); box(0.75, 0.08, 0.07, stripe, 0, -0.22, -0.2, g);
    for (const sx of [-1, 1]) { box(0.24, 0.07, 0.44, wool, sx * 0.26, 0.33, -0.01, g); box(0.07, 0.42, 0.3, wool, sx * 0.37, 0.1, -0.06, g); }
    return g;
  }
  return { figureLook, tatters, militantKit, weaponModel, head, cap, halo, poolKit, blanket, figure, M, box, dispose: o.dispose };
}

// The "!" over an agent waiting on you: a quest-giver's mark, gold, a tapered bar and its dot, outlined dark, glowing
// (128 x 192: WAIT_ASPECT). The room hangs it over the agent's desk; a picture of the agent wears it too.
const WAIT_ASPECT = 1.5;
let waitC = null;
function waitCanvas() {
  if (!waitC) {
    const c = waitC = document.createElement("canvas"); c.width = 128; c.height = 192; const g = c.getContext("2d");
    const bar = () => { g.beginPath(); g.moveTo(34, 30); g.quadraticCurveTo(64, 2, 94, 30); g.lineTo(76, 124); g.quadraticCurveTo(64, 134, 52, 124); g.closePath(); };
    const dot = () => { g.beginPath(); g.arc(64, 160, 19, 0, 7); };
    const gold = g.createLinearGradient(0, 10, 0, 182); gold.addColorStop(0, "#fff6b0"); gold.addColorStop(0.45, "#ffd21f"); gold.addColorStop(1, "#d98e00");
    for (const f of [bar, dot]) {
      g.save(); g.shadowColor = "rgba(255,200,40,0.9)"; g.shadowBlur = 14; f(); g.lineJoin = "round"; g.lineWidth = 14; g.strokeStyle = "#3d2300"; g.stroke(); g.restore();
      f(); g.fillStyle = gold; g.fill();
    }
    g.fillStyle = "rgba(255,255,255,0.55)"; g.beginPath(); g.moveTo(44, 32); g.quadraticCurveTo(56, 18, 66, 18); g.lineTo(58, 100); g.closePath(); g.fill();   // its shine
    g.beginPath(); g.arc(58, 154, 6, 0, 7); g.fill();
  }
  return waitC;
}

// The list's look, the room's and the kanban's alike: first in the page's head, so a page's own rules go over it.
const CSS = `
.party .ag-row { display: flex; gap: 10px; align-items: stretch; padding: 5px; border-radius: 10px; cursor: pointer; }
.party .ag-row:hover { background: color-mix(in srgb, var(--line, #d0d7de) 45%, transparent); }
.party .ag-row.guest { cursor: default; opacity: .75; }
.party .ag-row.active, .party .ag-row.active:hover { background: color-mix(in srgb, #3b82d6 22%, transparent); box-shadow: inset 0 0 0 2px #3b82d6; }
.party .ag-row > canvas { flex: none; width: 80px; height: 80px; border-radius: 10px; background: radial-gradient(circle at 50% 35%, #46536a, #161b22); }
.party .ag-card { flex: 1; min-width: 0; background: #ffffff; color: #1c2128; border-radius: 10px; border-left: 7px solid var(--board, #d0d7de); padding: 6px 10px; font: 13px/1.4 var(--mono, ui-monospace, Consolas, monospace); }
.party .ag-card b { font-size: 15px; }
.party .ag-card .ag-l { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #57606a; }
.party .ag-card .ag-w { color: #8a4b00; font-weight: 700; white-space: normal; }
.party .ag-card .ag-act { color: #0e6b7a; }
.party .ag-card .ag-x { float: right; margin-left: 8px; }
.party .ag-card.dead { color: #6e7681; }
.party .ag-row.waiting .ag-card { background: #fff0d6; box-shadow: 0 0 0 2px #e8a33d; animation: ag-pulse 1.6s ease-in-out infinite; }
@keyframes ag-pulse { 50% { box-shadow: 0 0 0 4px rgba(232,163,61,.55); } }
@media (prefers-reduced-motion: reduce) { .party .ag-row.waiting .ag-card { animation: none; } }
.party .ag-none { color: var(--muted, #636c76); font: 12px var(--mono, ui-monospace, monospace); padding: 6px; }
.ag-tip { position: fixed; z-index: 20; pointer-events: none; width: max-content; max-width: 340px; box-sizing: border-box; background: rgba(13,17,23,.96); color: #e6edf3;
  border: 1px solid #c9a24a; border-radius: 8px; padding: 8px 10px 7px; font: 12.5px/1.35 var(--body, system-ui, sans-serif); box-shadow: 0 6px 22px rgba(0,0,0,.5); }
.ag-tip[hidden] { display: none; }
.ag-tip .tip-name { font: 700 14px var(--body, system-ui, sans-serif); color: #ffd866; margin-bottom: 2px; }
.ag-tip .tip-dir { font: 12px var(--mono, ui-monospace, monospace); color: #9cc8ff; margin-bottom: 4px; overflow-wrap: anywhere; }
.ag-tip .tip-host { color: #9aa4ae; }
.ag-tip .tip-row { display: flex; gap: 8px; align-items: center; margin: 5px 0; }
.ag-tip canvas { flex: none; width: 30px; height: 30px; border-radius: 6px; background: radial-gradient(circle at 50% 35%, #46536a, #161b22); }
.ag-tip .tip-row b { color: #ffffff; font-weight: 700; }
.ag-tip .tip-more { margin-top: 6px; padding-top: 5px; border-top: 1px solid #30363d; color: #9aa4ae; font-size: 12px; }
`;
let styled = false;

// What each thing an agent does between turns is called, on its card (ACT_SAID) and in its legend (ACT_LEGEND): the
// room's page plays them out (room.html actsNow) and tells the board (room.acts), so every page can say them.
const ACT_SAID = { doze: "💤 dozing", phone: "📱 on its phone", chat: "💬 chatting", cooler: "🥤 at the water cooler", coffee: "☕ getting a coffee",
  cocktail: "🍸 a cocktail at the bar", bananas: "🍌 off for a banana", pool: "🏝 out by the pool" };
const ACT_LEGEND = { doze: ["Dozing", "between turns"], phone: ["On its phone", "between turns"], chat: ["Chatting", "between turns"],
  cooler: ["At the water cooler", "between turns"], coffee: ["Getting a coffee", "between turns"], cocktail: ["A cocktail at the bar", "between turns"],
  bananas: ["Off for a banana", "between turns"], pool: ["Out by the pool", "its laptop and all"] };

// The list, in `o.el` (given the class "party"). o: figures (a Figures), actOf(a, d) (what it is up to between turns:
// a kind, or "chat:<whom>"), sick(session), envKey() (the room's environment: LOOKS' key), boards(d), guest() (As a
// visitor: its rows dimmed), order ("waiting": those waiting on you first, the room's; "join": the roster's order, so
// nothing moves under the pointer, the kanban's), live() (the list is up: its pictures move only then), hint(a) (what a
// click does, the legend's last line), active() (the name of the agent the page has you at: its row lit; active()
// lights it again when that changes), and the page's own on each card: cardTop(a, d) (on its first line, at its right),
// cardMore(a, d) (under the rest) and sig(a, d) (what of those, changed, draws the row again).
function make(o) {
  if (!styled) { styled = true; const s = document.createElement("style"); s.textContent = CSS; document.head.prepend(s); }
  const F = o.figures, el = o.el;
  el.classList.add("party");
  const actOf = (a, d) => (a && o.actOf && o.actOf(a, d)) || "", sick = s => !!(o.sick && o.sick(s)), envKey = () => (o.envKey && o.envKey()) || "office";
  const S = { sig: "", r: null, sc: null, cam: null, heads: new Map(), rain: new Map(), byName: new Map(), raf: 0, drawn: 0, d: null, tipFor: null, tipAt: null, seen: true,
              still: !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) };
  const live = () => !el.hidden && S.seen && (!o.live || o.live());

  function render(d, force) {
    if (!d) return;
    if (force) S.sig = "";
    const list = (d.agents || []).slice().sort(o.order === "join" ? (a, b) => (a.id || 0) - (b.id || 0) : (a, b) => (!!b.blocked - !!a.blocked) || (a.id || 0) - (b.id || 0));
    const bs = (o.boards ? o.boards(d) : d.boards) || [], many = bs.length > 1, guest = !!(o.guest && o.guest());
    S.byName = new Map(list.map(a => [a.name, a])); S.d = d;   // (the pictures move by these: pic)
    const shown = list.map(a => ({ a, act: actSaid(a, d), key: [a.session, a.name, a.seat, a.fresh, a.blocked, a.doing, (a.subs || []).length, Math.round((Number(a.ctx) || 0) / 20000), a.board, sick(a.session), o.sig ? o.sig(a, d) : ""] }));
    const sig = JSON.stringify([shown.map(x => [x.key, x.act]), many, guest, envKey()]);
    if (sig === S.sig) return titles(d);
    S.sig = sig;
    el.innerHTML = list.length ? shown.map(({ a, act }) => {
      const n = (a.subs || []).length, band = many ? boardColor(bs, a.board) : "";
      return `<div class="ag-row${a.blocked ? " waiting" : ""}${guest ? " guest" : ""}${o.active && o.active() === a.name ? " active" : ""}" data-ag="${esc(a.name)}" data-seen="${esc(a.seen || "")}">
      <canvas width="160" height="160" role="img"></canvas>
      <div class="ag-card${a.fresh === "dead" ? " dead" : ""}"${band ? ` style="--board:${esc(band)}"` : ""}>
        <div>${o.cardTop ? `<span class="ag-x">${o.cardTop(a, d)}</span>` : ""}<b>${esc(a.name)}</b> · ${esc(a.seat || "?")}${a.fresh ? " · " + esc(a.fresh) : ""}${many ? ` · <span style="color:${esc(band)}">${esc((bs.find(b => b.key === a.board) || {}).name || "no board")}</span>` : ""}</div>
        ${a.blocked ? `<div class="ag-w">⏳ waiting on you${typeof a.blocked === "string" && a.blocked ? ": " + esc(a.blocked) : ""}</div>` : ""}
        <div class="ag-l">${esc(a.doing || "—")}</div>
        ${act ? `<div class="ag-l ag-act">${esc(act)}</div>` : ""}
        ${n ? `<div class="ag-l">⧗ waiting on ${n} subagent${n === 1 ? "" : "s"}</div>` : ""}${o.cardMore ? o.cardMore(a, d) : ""}</div></div>`;
    }).join("") : '<div class="ag-none">no agents on the roster</div>';
    pics(performance.now()); go();   // drawn at once (no blank between), then moving
    titles(d);
  }

  // Over its row (its picture or its card): what each thing on its picture means, as a buff's tooltip (tip: its own
  // icon beside each, drawn as the picture draws it), then what it is doing and what a click does; the same in words is
  // the picture's label, for a screen reader. Its times kept current (each render, every 2 s or so).
  function legend(a, d) {
    const act = actOf(a, d), kind = act.split(":")[0], whom = act.slice(kind.length + 1), subs = a.subs || [], ago = t => fmtAge(ageSecs(t)), ctx = Number(a.ctx) || 0;
    const h = headPic(a), crop = (x, y, w) => g => g.drawImage(h.cv, x, y, w, w, 0, 0, 60, 60);
    const at = (box, draw) => g => { const [x0, y0, x1, y1] = box, k = 60 / Math.max(x1 - x0, y1 - y0); g.setTransform(k, 0, 0, k, (60 - (x1 - x0) * k) / 2 - x0 * k, (60 - (y1 - y0) * k) / 2 - y0 * k); draw(g); };
    const t = performance.now() / 1000, out = [];
    if (a.blocked) out.push({ icon: g => g.drawImage(waitCanvas(), 15, 3, 30, 30 * WAIT_ASPECT), name: "Waiting on you", text: typeof a.blocked === "string" && a.blocked ? a.blocked : "" });
    if (a.shell) out.push({ icon: g => { const m = S.rain.get(a.name); if (m) g.drawImage(m.c, 0, 0, 60, 60); }, name: "A command running", text: "for " + ago(a.shell) + ": the Matrix on its screen" });
    if (a.working && !a.fresh && !a.shell && !a.blocked) out.push({ icon: at([104, 122, 158, 156], g => picTyping(g, t)), name: "Working", text: "in a turn, for " + ago(a.working) });
    if (ACT_LEGEND[kind]) {
      const [name, text] = ACT_LEGEND[kind];
      const icon = kind === "doze" ? at([98, 14, 160, 110], g => picZzz(g, 0.35)) : kind === "phone" ? (h.phone ? crop(h.phone[0] - 38, h.phone[1] - 38, 76) : crop(0, 0, 160))
        : kind === "chat" ? at([90, 40, 158, 94], g => picChat(g, t)) : at([98, 64, 158, 158], g => picErrand(g, kind, 1.2));
      out.push({ icon, name, text: kind === "chat" && whom ? "with " + whom + ", " + text : text });
    }
    if (subs.length) out.push({ icon: at([0, 118, 100, 158], g => picSubs(g, 0, subs.length)), name: subs.length === 1 ? "A subagent" : subs.length + " subagents",
      text: "working for it: " + subs.slice(0, 3).map(x => (x.type || "agent") + (x.what ? " (" + x.what + ")" : "")).join(", ") + (subs.length > 3 ? ", and " + (subs.length - 3) + " more" : "") });
    const ring = g => {   // the halo as its picture has it: the ring, filled by the share of CTX_FULL in use (red near full)
      const f = Math.min(1, ctx / CTX_FULL);
      g.lineWidth = 7; g.lineCap = "round"; g.strokeStyle = "rgba(208,215,222,0.35)"; g.beginPath(); g.ellipse(30, 32, 23, 11, 0, 0, 7); g.stroke();
      g.strokeStyle = f >= 0.8 ? "#f85149" : "#ffd866"; g.beginPath(); g.ellipse(30, 32, 23, 11, 0, Math.PI / 2, Math.PI / 2 + f * Math.PI * 2); g.stroke();
    };
    if (ctx) out.push({ icon: ring, name: "Its halo", text: fmtTok(ctx) + " tokens of context in use" + (ctx >= 0.8 * CTX_FULL ? ", nearly full (red)" : "") });
    if (a.fresh === "dead") out.push({ icon: crop(20, 30, 120), name: "Dead", text: "grey: nothing from it for " + ago(a.seen) });
    else if (a.fresh) out.push({ icon: crop(20, 30, 120), name: a.fresh === "stale" ? "Stale" : "Idle", text: "last acted " + ago(a.seen) + " ago" });
    if (!out.some(e => e.name !== "Its halo")) out.unshift({ icon: crop(20, 30, 120), name: "Between turns", text: a.seen ? "last acted " + ago(a.seen) + " ago" : "" });
    return { out, dir: a.dir || "", host: a.host || "", more: [a.doing ? "Doing: " + a.doing : "", o.hint ? o.hint(a) : ""].filter(Boolean) };
  }
  function status(a, d) {
    const { out, dir, host, more } = legend(a, d);
    return [a.name + (dir ? ", in " + dir : "") + (host ? ", on " + host : ""), ...out.map(e => e.name + (e.text ? ": " + e.text : "")), ...more].join("\n");
  }
  const tipEl = document.createElement("div");
  tipEl.className = "ag-tip"; tipEl.hidden = true; tipEl.setAttribute("aria-hidden", "true");
  document.body.appendChild(tipEl);
  const tipPlace = (x, y) => { const r = tipEl.getBoundingClientRect(); tipEl.style.left = Math.max(8, Math.min(x + 18, innerWidth - r.width - 8)) + "px"; tipEl.style.top = Math.max(8, Math.min(y + 14, innerHeight - r.height - 8)) + "px"; };
  function tip(name, x, y) {
    const a = name && S.byName.get(name);
    if (!a) { tipEl.hidden = true; S.tipFor = null; return; }
    S.tipFor = name;
    if (x !== undefined) S.tipAt = [x, y];
    const { out, dir, host, more } = legend(a, S.d);
    tipEl.innerHTML = `<div class="tip-name">${esc(a.name)}</div>` + (dir || host ? `<div class="tip-dir">📁 ${esc(dir || "?")}${host ? ` <span class="tip-host">on ${esc(host)}</span>` : ""}</div>` : "") + out.map(e => `<div class="tip-row"><canvas width="60" height="60"></canvas><div><b>${esc(e.name)}</b>${e.text ? " · " + esc(e.text) : ""}</div></div>`).join("")
      + (more.length ? `<div class="tip-more">${more.map(esc).join("<br>")}</div>` : "");
    tipEl.querySelectorAll("canvas").forEach((c, i) => { try { out[i].icon(c.getContext("2d")); } catch (e) { console.warn("a legend icon", e); } });
    tipEl.hidden = false;
    tipPlace(...(S.tipAt || [0, 0]));
  }
  el.addEventListener("mousemove", e => {
    const row = e.target.closest(".ag-row"), name = row && row.dataset.ag;   // its face or its card
    if (!name) { if (S.tipFor) tip(null); return; }
    if (name !== S.tipFor) tip(name, e.clientX, e.clientY);
    else { S.tipAt = [e.clientX, e.clientY]; tipPlace(e.clientX, e.clientY); }
  });
  el.addEventListener("mouseleave", () => tip(null));
  function titles(d) {
    el.querySelectorAll(".ag-row").forEach(row => {
      const a = S.byName.get(row.dataset.ag), cv = row.querySelector("canvas");
      if (!a || !cv) return;
      const said = status(a, d);
      if (cv.getAttribute("aria-label") !== said) cv.setAttribute("aria-label", said);
    });
    if (S.tipFor) tip(el.hidden ? null : S.tipFor);   // (up: its times current)
  }

  // An agent's head, drawn by a little renderer of the list's own (never the room's): its figure (Figures), framed head
  // and halo, into `cv` (headPic's picture of it, kept till the head itself changes). On its phone (`pose`), turned
  // aside, its head bowed over the phone held up before its face, its screen to it (holdPhone): where the phone is on
  // the picture comes back, for its glow (pic).
  function agentHead(a, cv, pose = null) {
    if (S.r === null) {
      try {
        S.r = new THREE.WebGLRenderer({ alpha: true, antialias: true }); S.r.setPixelRatio(1); S.r.setSize(160, 160, false);
        S.sc = new THREE.Scene(); S.sc.add(new THREE.HemisphereLight(0xffffff, 0x3a4252, 0.85));
        const dl = new THREE.DirectionalLight(0xffffff, 0.7); dl.position.set(1.4, 2.4, 2.2); S.sc.add(dl);
        S.cam = new THREE.PerspectiveCamera(30, 1, 0.1, 20); S.cam.position.set(0, 2.62, 2.2); S.cam.lookAt(0, 1.98, 0);   // the head, its halo and its count over it (to 2.6 m), from a little above: the ring a ring
      } catch (e) { S.r = false; console.warn("your agents' heads: no renderer", e); }
    }
    if (!S.r || !cv) return null;
    const fig = F.figure(a);
    if (pose === "phone") holdPhone(fig);
    S.sc.add(fig); S.r.render(S.sc, S.cam);
    const p = fig.userData.phone ? fig.userData.phone.getWorldPosition(new THREE.Vector3()).project(S.cam) : null;
    S.sc.remove(fig); F.dispose(fig);
    const g = cv.getContext("2d"); g.clearRect(0, 0, cv.width, cv.height); g.drawImage(S.r.domElement, 0, 0, cv.width, cv.height);
    return p ? [(p.x + 1) / 2 * cv.width, (1 - p.y) / 2 * cv.height] : null;
  }
  function holdPhone(fig) {
    const { head: h, body, who } = fig.userData;
    fig.rotation.y = 0.8; fig.position.x = -0.26; h.rotation.x = 0.22;   // seen from the side, looking down at it (aside, for it and its phone)
    const phone = new THREE.Group(); phone.position.set(0, 1.72, 0.56); body.add(phone);
    F.box(0.2, 0.36, 0.035, F.M(0xff7a59), 0, 0, 0, phone);   // its case, bright: its back is what we see
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.17, 0.32), new THREE.MeshBasicMaterial({ color: 0xbfe0ff })); screen.position.z = 0.019; phone.add(screen);
    F.box(0.16, 0.1, 0.09, F.figureLook(who).skin, 0, -0.16, -0.02, phone);   // its hand round the bottom of it
    fig.updateMatrixWorld(true);   // its screen to its face, turned a little our way (a glimpse of it)
    phone.lookAt(h.getWorldPosition(new THREE.Vector3()).lerp(S.cam.position, 0.3));
    fig.userData.phone = phone;
  }
  // Each picture moving: its head (headPic), and on it what it is up to (pic, about 15 times a second while the list is
  // up, seen and this tab shown; with reduced motion, once every 2 s and still):
  // - a command running (a.shell): the Matrix falling behind it, as on its desk's screen in the room;
  // - dozing: its head drooped and breathing, Z's rising; on its phone: turned aside over it, its screen's glow on its
  //   face; chatting: a bubble, dots going (and with whom on its card); an errand: what it went for (a coffee steaming,
  //   the cooler's cup, a cocktail, a banana); out by the pool: the sun (actOf: the room's page says which);
  // - at work (in a turn): typing dots; with subagents: their little heads; waiting on you: the "!" bobbing.
  function actSaid(a, d) {
    const act = actOf(a, d), kind = act.split(":")[0], whom = act.slice(kind.length + 1);
    return [ACT_SAID[kind] ? ACT_SAID[kind] + (whom ? " with " + whom : "") : "", a.shell ? "▶ a command running" : ""].filter(Boolean).join(" · ");
  }
  function headPic(a) {
    const pose = actOf(a, S.d).split(":")[0] === "phone" ? "phone" : null;
    const key = JSON.stringify([a.name, a.fresh, !!a.blocked, a.working, Math.round((Number(a.ctx) || 0) / 20000), sick(a.session), pose, envKey()]);
    let h = S.heads.get(a.name);
    if (!h) S.heads.set(a.name, h = { key: "", cv: Object.assign(document.createElement("canvas"), { width: 160, height: 160 }), phone: null });
    if (h.key !== key) {
      h.key = key; h.phone = agentHead(a, h.cv, pose);
      for (const ms of [400, 1500]) setTimeout(() => { if (h.key === key) h.phone = agentHead(a, h.cv, pose); }, ms);   // (again once its face's picture is in: svgTex draws one when its image comes)
    }
    return h;
  }
  function go() { if (!S.raf && live() && !document.hidden) S.raf = requestAnimationFrame(step); }
  function step(now) {
    S.raf = 0;
    if (!live() || document.hidden) return;
    S.raf = requestAnimationFrame(step);
    if (now - S.drawn >= (S.still ? 2000 : 66)) pics(now);
  }
  function pics(now) {
    S.drawn = now;
    el.querySelectorAll(".ag-row").forEach(row => { const a = S.byName.get(row.dataset.ag), cv = row.querySelector("canvas"); if (a && cv) pic(cv, a, S.still ? 1 : now / 1000); });
  }
  document.addEventListener("visibilitychange", go);
  if (window.IntersectionObserver) new IntersectionObserver(es => { S.seen = es[es.length - 1].isIntersecting; go(); }).observe(el);   // (scrolled away or folded: still)
  function pic(cv, a, t) {
    const g = cv.getContext("2d"), W = cv.width, kind = actOf(a, S.d).split(":")[0];
    g.clearRect(0, 0, W, W);
    if (a.shell) picRain(a.name, g, t);
    g.save();
    if (kind === "doze") { g.translate(80, 152); g.rotate(-0.24 + 0.03 * Math.sin(t * 1.3)); g.translate(-80, -152); }   // drooped, breathing
    const h = headPic(a);
    g.drawImage(h.cv, 0, 0);
    g.restore();
    if (kind === "doze") picZzz(g, t);
    else if (kind === "phone" && h.phone) picPhone(g, t, h.phone);
    else if (kind === "chat") picChat(g, t);
    else if (PIC_ERRANDS[kind]) picErrand(g, kind, t);
    if (a.working && !a.fresh && !a.shell && !a.blocked) picTyping(g, t);
    const n = (a.subs || []).length;
    if (n) picSubs(g, t, n);
    if (a.blocked) g.drawImage(waitCanvas(), W - 44, 4 + 3 * Math.sin(t * 2.4), 38, 38 * WAIT_ASPECT);
  }
  function picRain(name, g, t) {   // the Matrix, a picture of its own an agent, falling at 12 rows a second
    let m = S.rain.get(name);
    if (!m) {
      const c = Object.assign(document.createElement("canvas"), { width: 160, height: 160 });
      m = { c, q: c.getContext("2d"), cols: [...Array(10)].map(() => -Math.floor(Math.random() * 12)), at: -1 };
      m.q.fillStyle = "#020a04"; m.q.fillRect(0, 0, 160, 160); S.rain.set(name, m);
    }
    if (t - m.at >= 1 / 12 || t < m.at) {
      m.at = t;
      const q = m.q, glyph = () => GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
      q.fillStyle = "rgba(2,10,4,0.3)"; q.fillRect(0, 0, 160, 160);
      q.font = "700 15px 'JetBrains Mono', monospace"; q.textAlign = "center";
      m.cols = m.cols.map((y, i) => {
        if (y >= 0) { q.fillStyle = "#c8ffc0"; q.fillText(glyph(), i * 16 + 8, y * 16 + 14); if (y > 0) { q.fillStyle = "#22c55e"; q.fillText(glyph(), i * 16 + 8, (y - 1) * 16 + 14); } }
        return y > 10 && Math.random() < 0.25 ? -Math.floor(Math.random() * 8) : y + 1;
      });
    }
    g.drawImage(m.c, 0, 0);
  }
  // The row of the agent the page has you at (o.active(): its terminal open, say) lit, the others not: at once, with no
  // drawing again.
  function active() { const who = o.active ? o.active() : null; for (const r of el.querySelectorAll(".ag-row")) r.classList.toggle("active", !!who && r.dataset.ag === who); }
  return { render, tip, legend, status, headPic, pic, picRain, active, state: S };
}
function picZzz(g, t) {
  g.save(); g.textAlign = "center"; g.lineJoin = "round";
  for (let k = 0; k < 3; k++) {
    const p = (t * 0.45 + k / 3) % 1, x = 114 + p * 28 + 5 * Math.sin(t * 2 + k), y = 100 - p * 72;
    g.globalAlpha = Math.sin(p * Math.PI); g.font = `900 ${Math.round(16 + p * 20)}px system-ui, sans-serif`;
    g.lineWidth = 5; g.strokeStyle = "rgba(14,20,40,0.85)"; g.strokeText("Z", x, y); g.fillStyle = "#d6e6ff"; g.fillText("Z", x, y);
  }
  g.restore();
}
function picPhone(g, t, [x, y]) {   // its screen's light on its face, flickering as its feed goes by
  g.save(); g.globalCompositeOperation = "lighter";
  const k = 0.22 + 0.06 * Math.sin(t * 3.1) + 0.04 * Math.sin(t * 7.3), glow = g.createRadialGradient(x, y, 2, x, y, 46);
  glow.addColorStop(0, `rgba(140,190,255,${k + 0.12})`); glow.addColorStop(1, "rgba(140,190,255,0)");
  g.fillStyle = glow; g.fillRect(0, 0, 160, 160);
  g.restore();
}
function picChat(g, t) {
  g.save(); g.lineWidth = 3; g.strokeStyle = "#1c2128"; g.fillStyle = "#ffffff";
  g.beginPath(); g.moveTo(108, 74); g.lineTo(98, 90); g.lineTo(122, 76); g.closePath(); g.fill(); g.stroke();
  g.beginPath(); g.roundRect(94, 44, 60, 34, 14); g.fill(); g.stroke();
  g.beginPath(); g.moveTo(109, 76.5); g.lineTo(121, 76.5); g.strokeStyle = "#ffffff"; g.stroke();   // (where the tail joins it)
  for (let k = 0; k < 3; k++) { g.fillStyle = "#57606a"; g.beginPath(); g.arc(111 + k * 13, 61 - 5 * Math.max(0, Math.sin(t * 5 - k * 0.9)), 4.5, 0, 7); g.fill(); }
  g.restore();
}
function picTyping(g, t) {
  g.save(); g.beginPath(); g.roundRect(110, 128, 44, 24, 12); g.fillStyle = "rgba(22,27,34,0.85)"; g.fill();
  for (let k = 0; k < 3; k++) { g.globalAlpha = 0.35 + 0.65 * Math.max(0, Math.sin(t * 6 - k * 0.9)); g.fillStyle = "#e6edf3"; g.beginPath(); g.arc(121 + k * 11, 140, 4, 0, 7); g.fill(); }
  g.restore();
}
function picSubs(g, t, n) {   // its subagents: a little head each (three at most, then +N)
  g.save();
  for (let k = 0; k < Math.min(n, 3); k++) {
    const x = 4 + k * 32, y = 124 + 2 * Math.sin(t * 3 + k);
    g.fillStyle = "#e6edf3"; g.strokeStyle = "#1c2128"; g.lineWidth = 3.5; g.fillRect(x, y, 29, 29); g.strokeRect(x, y, 29, 29);
    g.fillStyle = "#1c2128"; g.fillRect(x + 6, y + 11, 5, 5); g.fillRect(x + 18, y + 11, 5, 5); g.fillRect(x + 9, y + 20, 11, 3);
  }
  if (n > 3) { g.font = "800 24px system-ui, sans-serif"; g.fillStyle = "#e6edf3"; g.lineWidth = 5; g.strokeStyle = "#1c2128"; g.strokeText("+" + (n - 3), 104, 148); g.fillText("+" + (n - 3), 104, 148); }
  g.restore();
}
function picErrand(g, kind, t) { g.save(); g.translate(-6, -8); g.translate(134, 140); g.scale(1.35, 1.35); g.translate(-134, -140); PIC_ERRANDS[kind](g, t); g.restore(); }   // (drawn small: a third bigger)
const PIC_ERRANDS = {
  coffee(g, t) {   // a mug, steaming
    g.save(); g.lineWidth = 3; g.strokeStyle = "#3b2414";
    g.beginPath(); g.arc(146, 138, 7, -1.3, 1.3); g.stroke();
    g.beginPath(); g.roundRect(118, 124, 28, 30, 5); g.fillStyle = "#f4ede2"; g.fill(); g.stroke();
    g.fillStyle = "#6b3f1f"; g.fillRect(121, 127, 22, 5);
    g.strokeStyle = "rgba(235,240,245,0.85)"; g.lineCap = "round";
    for (const x of [126, 137]) { g.beginPath(); for (let y = 0; y <= 24; y += 3) g.lineTo(x + 3 * Math.sin(y / 5 - t * 4 + x), 118 - y); g.stroke(); }
    g.restore();
  },
  cooler(g, t) {   // the cooler's paper cup, its water bubbling
    g.save(); g.lineWidth = 3; g.strokeStyle = "#1c2128";
    g.beginPath(); g.moveTo(118, 122); g.lineTo(150, 122); g.lineTo(144, 156); g.lineTo(124, 156); g.closePath(); g.fillStyle = "#ffffff"; g.fill(); g.stroke();
    g.beginPath(); g.moveTo(120, 130); g.lineTo(148, 130); g.lineTo(144, 154); g.lineTo(124, 154); g.closePath(); g.fillStyle = "#7cc4ff"; g.fill();
    for (let k = 0; k < 3; k++) { const p = (t * 0.8 + k / 3) % 1; g.globalAlpha = 1 - p; g.fillStyle = "#e8f6ff"; g.beginPath(); g.arc(128 + k * 7, 152 - p * 22, 2.5, 0, 7); g.fill(); }
    g.restore();
  },
  cocktail(g, t) {   // a glass, a straw and its little umbrella
    g.save(); g.lineWidth = 3; g.strokeStyle = "#1c2128";
    g.beginPath(); g.moveTo(116, 122); g.lineTo(152, 122); g.lineTo(134, 140); g.closePath(); g.fillStyle = "#ff8fb1"; g.fill(); g.stroke();
    g.beginPath(); g.moveTo(134, 140); g.lineTo(134, 154); g.moveTo(124, 155); g.lineTo(144, 155); g.stroke();
    g.save(); g.translate(146, 116); g.rotate(0.25 + 0.08 * Math.sin(t * 2));
    g.beginPath(); g.moveTo(0, 0); g.lineTo(0, 12); g.stroke();
    g.beginPath(); g.arc(0, 0, 11, Math.PI, 0); g.closePath(); g.fillStyle = "#ffd21f"; g.fill(); g.stroke();
    g.restore(); g.restore();
  },
  bananas(g, t) {   // a banana, rocking
    g.save(); g.translate(134, 140); g.rotate(0.15 * Math.sin(t * 2.2)); g.lineWidth = 3; g.strokeStyle = "#5a4300";
    g.beginPath(); g.arc(0, -22, 26, 0.55, 2.6); g.arc(0, -30, 24, 2.45, 0.7, true); g.closePath(); g.fillStyle = "#ffe14d"; g.fill(); g.stroke();
    g.fillStyle = "#5a4300"; g.fillRect(19, -10, 6, 6);
    g.restore();
  },
  pool(g, t) {   // out by the pool: the sun, turning
    g.save(); g.translate(138, 138); g.rotate(t * 0.6); g.strokeStyle = "#ffb000"; g.lineWidth = 3.5; g.lineCap = "round";
    for (let k = 0; k < 8; k++) { g.rotate(Math.PI / 4); g.beginPath(); g.moveTo(15, 0); g.lineTo(21, 0); g.stroke(); }
    g.beginPath(); g.arc(0, 0, 11, 0, 7); g.fillStyle = "#ffd21f"; g.fill(); g.lineWidth = 2.5; g.strokeStyle = "#c27c00"; g.stroke();
    g.restore();
  },
};

window.Figures = Figures;
// A head: a box 0.56 across, its face on its front (+z, a box's side 4) and its other five sides alike, drawn as two
// (a box's six sides are six draws, and the pool club has a hundred heads: a third of a frame's draws). Its materials
// stay six, by side as a box's are (the face at 4; a sick or a dead one's swapped by that index), but its sides 0 to 3
// and 5 are drawn together, with side 0's. One shape for every head (a plain geometry: it goes to visitors as itself).
let headBox = null;
function headGeo() {
  if (headBox) return headBox;
  const box = new THREE.BoxGeometry(0.56, 0.56, 0.56), idx = Array.from(box.index.array), g = new THREE.BufferGeometry().copy(box);
  g.setIndex([...idx.slice(0, 24), ...idx.slice(30, 36), ...idx.slice(24, 30)]);   // sides 0 to 3, 5; then 4
  g.clearGroups(); g.addGroup(0, 30, 0); g.addGroup(30, 6, 4);
  box.dispose();
  return headBox = g;
}
window.Party = { headGeo, make, LOOKS, SICK_SKIN, SICK_FACE, GLYPHS, WAIT_ASPECT, waitCanvas, ACT_SAID, ACT_LEGEND, picZzz, picPhone, picChat, picTyping, picSubs, picErrand, PIC_ERRANDS };
})();
