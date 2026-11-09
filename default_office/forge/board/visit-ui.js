// default_office's copy of the reference UI from basedverse v5.2.0 (visit/examples/ui/visit-ui.js, at tag v5.2.0). It is
// this room's own: Visit never changes it. What this copy changes from the reference is listed in VISIT.md
// ("Deviations from vanilla"); none so far. To take a later release's changes, diff the reference between the two
// tags and bring over what the user wants.
//
// visit-ui.js: a reference UI for Visit (basedverse: visit/examples/ui). Since 5.0 (#82) visit.js puts no UI in the
// page; this is the UI it used to put there, written on visit.js's public API alone. ../../UI.md says what each piece
// does and what it is built on.
//
// Copy this file into your room's own tree and change whatever you like: it is yours. visit-client never serves it,
// so an upgrade of Visit never changes it under you; only the API it uses can change, and the CHANGELOG says when.
//
//   <script src="visit-ui.js"></script>       (before or after visit.js: it reads Visit when it is called)
//   const ui = VisitUI.panel(element, v);    the HUD (in `element`, a box of your page's), with the chat window, your
//                                            target's card and the ⏱ corner; { chat: false, card: false } leaves
//                                            either out. ui.chat, ui.card, ui.perf, ui.map, ui.say(), ui.release();
//                                            ui.teleports(on): its ✦ list, from your own button or your pad (5.1.1);
//                                            { teleport: false }: none of it (5.2: a room with a teleport widget of its own)
//   VisitUI.stage(v);                        the room you visit, full-window or in a corner, your own room behind it
//   VisitUI.map(v).open();                   your world from above (the panel's Map)
//   VisitUI.chat(v), VisitUI.targetCard(v, { chat }), VisitUI.perf(v): each alone, for a page with its own panel
//
// Its classes (.visit-panel, .visit-folded on the panel's element, .visit-chat, .visit-card, .visit-map, .visit-stage,
// .visit-home-box, .visit-perf, .visit-lightbox) and the chat window's --visit-chat-left and --visit-chat-bottom are
// this file's own: style them, or change them here.
"use strict";
(function () {
  // Visit's limits (Visit.limits: what visit.js and visit-client keep to), read once Visit is loaded.
  let CHAT_LEN = 200, CHATS = 5, CHAT_WINDOW = 10000, CHAT_LOG = 100, MAX_PICKS = 8, MAX_IGNORED = 50, VOICE_SECS = 60;
  let VOICE_KINDS = ["audio/webm", "audio/ogg", "audio/mp4"], VERSION = "";
  // This UI's own.
  const CHAT_FADE = 20000;    // the chat window fades this long after its last line
  // Someone is looking at the chat window while its page is shown and focused, and they used a key, a click or the
  // pointer this recently (ms): the chat window tells Visit (v.attention), and what comes meanwhile is seen, not in the
  // mailbox (8.9).
  const LOOKING = 120000, STIRS = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"];
  const MOUNT_KEY = "KeyM";   // on your mount and off again (8.11), anywhere but a box of the page's own
  const TELEPORT_KEY = "KeyT";   // 5.1.1: the ✦ list, open or closed, the same way
  const HUDDLE_ASK_MS = 60000;   // a huddle's ask in the chat window's ring, this long

  // Each piece first: Visit loaded (5.0 or later), its limits read, and this the person's page (a room's own page,
  // role "host", has no UI of Visit's: engine/SPEC.md 7).
  function ready(v, what) {
    const V = window.Visit;
    if (!V || !V.limits) throw new Error(`VisitUI.${what}: load Visit 5.0 or later first (visit.js, from your visit-client)`);
    const L = V.limits;
    CHAT_LEN = L.chatLen; CHATS = L.chats; CHAT_WINDOW = L.chatWindowMs; CHAT_LOG = L.chatLog; MAX_PICKS = L.maxPicks; MAX_IGNORED = L.maxIgnored;
    VOICE_SECS = L.voiceSecs; VOICE_KINDS = L.voiceKinds.slice(); VERSION = V.version;
    if (v.role === "host") {
      const e = new Error(`VisitUI.${what}: the person's page's, and this page is the room's`);
      e.code = "wrong_role";
      throw e;
    }
  }

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isObj = x => !!x && typeof x === "object" && !Array.isArray(x);
  // A line's pictures and links, as "chat" gives them (checked again: what a page draws, it checks).
  const picturesFrom = l => (Array.isArray(l) ? l : []).filter(p => isObj(p) && /^[0-9a-f]{64}$/.test(p.id)).slice(0, 4).map(p => ({ id: p.id, mime: String(p.mime || ""), size: Number(p.size) || 0,
    ...(VOICE_KINDS.includes(String(p.mime)) ? { secs: Math.round(Math.min(VOICE_SECS, Math.max(0, Number(p.secs) || 0)) * 10) / 10 } : {}) }));
  const isVoice = p => VOICE_KINDS.includes(p.mime);
  const linksFrom = l => (Array.isArray(l) ? l : []).filter(x => isObj(x) && /^https?:\/\/\S+$/.test(String(x.url || ""))).slice(0, 2).map(x => ({ url: String(x.url),
    title: String(x.title || ""), description: String(x.description || ""), site: String(x.site || ""), image: picturesFrom(isObj(x.image) ? [x.image] : [])[0] || null }));
  // A point turned about the vertical by `a` (radians), as Visit turns plots; a plot's rectangle where `at` puts it
  // (world.plots: `size` unturned, in your room's units).
  const turnXZ = (x, z, a) => [x * Math.cos(a) + z * Math.sin(a), -x * Math.sin(a) + z * Math.cos(a)];
  const rectOf = (at, size) => {
    const odd = at.turn % 2 === 1, hw = (odd ? size[1] : size[0]) / 2, hd = (odd ? size[0] : size[1]) / 2;
    return [at.x - hw, at.z - hd, at.x + hw, at.z + hd];
  };

  // ---------- the ⏱ corner: how this page is doing, while v.perf measures ----------
  // A row a second ("perf"): frames, stalls, Visit's own work by part, the drawing of your room (and its draw calls),
  // pictures encoded, and how much there is. Copy report: v.perfReport(), the last two minutes. Off: v.perf(false).
  function perf(v, { parent = document.body } = {}) {
    ready(v, "perf");
    const el = document.createElement("div");
    el.className = "visit-perf"; el.hidden = true;
    el.style.cssText = "position:fixed;right:8px;bottom:8px;z-index:30;max-width:min(560px,calc(100vw - 16px));padding:6px 8px;border-radius:8px;" +
      "font:11px/1.45 ui-monospace,Consolas,monospace;color:#e6edf3;background:rgba(13,17,23,.88);border:1px solid #2d333b;white-space:pre-wrap";
    el.addEventListener("click", e => {
      const b = e.target.closest("button[data-p]");
      if (b && b.dataset.p === "copy") navigator.clipboard.writeText(JSON.stringify(v.perfReport(), null, 1)).then(() => { b.textContent = "copied"; }, () => { b.textContent = "could not copy"; });
      else if (b) v.perf(false);
    });
    parent.appendChild(el);
    const r1 = x => Math.round(x * 10) / 10, btn = "font:inherit;color:inherit;background:#161b22;border:1px solid #2d333b;border-radius:5px;padding:1px 6px;cursor:pointer";
    const onRow = row => {
      if (el.hidden) return;
      const parts = row.parts || {}, draws = row.draws, c = row.counts || {};
      const own = ["scan", "send", "whole", "landscape", "world", "voice"], all = own.reduce((n, k) => n + (parts[k] ? parts[k].ms : 0), 0);
      const line = k => parts[k] ? `${k} ${parts[k].ms}${parts[k].max >= 8 ? ` (slowest ${parts[k].max})` : ""}` : null;
      el.innerHTML = `<b>⏱ ${row.fps} fps</b> <span style="color:#8b949e">(Visit ${esc(row.visit)})</span> · slowest frame ${row.frame_max_ms ?? "–"} ms · ${row.long_tasks ? `<span style="color:#f85149">${row.long_tasks} stall${row.long_tasks > 1 ? "s" : ""} (${row.long_max_ms} ms)</span>` : "no stalls"}\n`
        + `Visit ${r1(all)} ms a second: ${own.map(line).filter(Boolean).join(" · ") || "idle"}`
        + (parts.draw ? `\ndrawing your room${c.plots ? " and its plots" : ""}: ${parts.draw.ms} ms a second (${r1(parts.draw.ms / Math.max(1, parts.draw.n))} a frame)` : "")
        + (draws ? `\ndraw calls a frame: ${draws.colour.toLocaleString("en")} + ${draws.shadow.toLocaleString("en")} for shadows` : "")
        + (parts.textures ? `\npictures sent: ${parts.textures.n} (encoded in the background, off the page: ${parts.textures.max} ms at most)` : "")
        + `\n${c.visitors} visitor${c.visitors === 1 ? "" : "s"} · ${c.watchers} watching · ${c.plots} plot${c.plots === 1 ? "" : "s"}${c.offline ? ` (${c.offline} offline)` : ""} · ${c.objects} objects`
        + `${c.landscape ? ` + ${c.landscape} landscape` : ""}${c.voices ? ` · ${c.voices} near` : ""}\n`
        + `<button data-p="copy" style="${btn}">copy report</button> <button data-p="off" style="${btn}">off</button>`;
    };
    const onChange = () => {
      if (v.perfOn && el.hidden) { el.hidden = false; el.textContent = "⏱ measuring…"; }
      else if (!v.perfOn) el.hidden = true;
    };
    v.on("perf", onRow); v.on("change", onChange);
    onChange();
    return { element: el, release() { v.off("perf", onRow); v.off("change", onChange); el.remove(); } };
  }

  // Your own room behind the stage (v.homeView: a page that draws no world of its own), and a line under it.
  function homeBox(v, parent) {
    const box = document.createElement("div");
    box.className = "visit-home-box"; box.hidden = true;
    box.style.cssText = "position:fixed;inset:0;z-index:4;background:#000";
    parent.appendChild(box);
    const hv = v.homeView(box);
    const cap = document.createElement("div");
    cap.style.cssText = "position:absolute;left:0;right:0;bottom:0;padding:6px 10px;font:13px/1.4 system-ui,sans-serif;color:#c9d1d9;background:rgba(13,17,23,.6);pointer-events:none";
    box.appendChild(cap);
    let failed = null;
    const draw = () => {
      box.hidden = !hv.shown;
      cap.textContent = failed || (!v.home ? "your room is not up" : v.home.paused ? "your room (reloading)…" : "your room · click to look around, WASD to walk, Shift to run, Esc frees the mouse");
    };
    const onError = e => { if (e && e.code === "home_view") { failed = e.message; draw(); } };
    v.on("change", draw); v.on("error", onError);
    draw();
    return { element: box, release() { v.off("change", draw); v.off("error", onError); hv.release(); box.remove(); } };
  }

  // ---------- the ready-made lobby panel ----------
  const PANEL_CSS = `
.visit-panel { font: 12px/1.4 ui-monospace, Consolas, monospace; color: #e6edf3; background: #161b22; border: 1px solid #2d333b; border-radius: 10px; overflow: auto; min-width: 260px; }
.visit-panel .row { display: flex; align-items: center; gap: 6px; padding: 6px 10px; border-top: 1px solid #2d333b; }
.visit-panel .row:first-child { border-top: 0; }
.visit-panel .hd { font-weight: 700; } .visit-panel .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.visit-panel .muted { color: #8b949e; } .visit-panel .dot { width: 8px; height: 8px; border-radius: 50%; background: #6e7681; flex: none; }
.visit-panel .dot.available { background: #3fb950; } .visit-panel .dot.busy { background: #e3b341; }
.visit-panel button, .visit-panel select, .visit-panel input { font: inherit; color: inherit; background: #0d1117; border: 1px solid #2d333b; border-radius: 6px; padding: 2px 8px; }
.visit-panel button { cursor: pointer; } .visit-panel button:disabled { opacity: .45; cursor: default; }
.visit-panel button.go { background: #2f9e4f; border-color: #2f9e4f; color: #fff; }
.visit-panel .row.tp { background: rgba(102, 221, 255, 0.07); }
.visit-panel .row.tp.sub { display: flex; }
.visit-panel .row.tp button.go { background: #1c7fa6; border-color: #1c7fa6; }
.visit-panel .knock { display: block; background: #fff0d6; color: #7a4a00; } .visit-panel .knock .btns { display: flex; gap: 6px; margin-top: 4px; }
.visit-panel button.wpick { padding: 0 4px; font-size: 11px; line-height: 16px; white-space: nowrap; }
.visit-panel button.wpick.on { color: #3fb950; border-color: #3fb950; }
.visit-panel .knock label { display: flex; align-items: center; gap: 4px; color: #7a4a00; }
.visit-panel .lnk { display: block; } .visit-panel .lnk .btns { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.visit-panel .speaking { color: #3fb950; } .visit-panel button.on { background: #9e2f2f; border-color: #9e2f2f; color: #fff; }
.visit-panel .knock input[type=checkbox] { flex: none; }
.visit-panel input { flex: 1; min-width: 0; }
.visit-panel .note { color: #e3b341; }
.visit-panel button.who { border: 0; background: none; padding: 0; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.visit-panel button.caret { border: 0; background: none; padding: 0 4px; color: #8b949e; }
.visit-panel .sub { display: block; padding-left: 30px; border-top: 0; }
.visit-panel .sub .btns { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.visit-panel .seg { display: inline-flex; } .visit-panel .seg button { border-radius: 0; } .visit-panel .seg button + button { border-left: 0; }
.visit-panel .seg button:first-child { border-radius: 6px 0 0 6px; } .visit-panel .seg button:last-child { border-radius: 0 6px 6px 0; }
.visit-panel .seg button.on2 { background: #1f6feb; border-color: #1f6feb; color: #fff; }
.visit-panel .hd button.caret { padding: 0 2px 0 0; } .visit-panel .ver { padding-top: 3px; padding-bottom: 3px; font-size: 11px; }
.visit-panel .qr { background: #fff; padding: 6px; border-radius: 6px; width: 232px; max-width: 100%; margin-bottom: 4px; } .visit-panel .qr img { display: block; width: 100%; height: auto; image-rendering: pixelated; }
.visit-panel .pics { letter-spacing: 2px; } .visit-panel .digits { font-size: 15px; letter-spacing: 2px; color: #e3b341; } .visit-panel .sub form { display: flex; gap: 6px; margin-top: 4px; }`;

  // Where a friend's world has put your room (8.4, `seen`), said plainly: which way from theirs (north is -z), and how
  // far apart the two plots' edges are.
  function seenText(v, name, at) {
    if (!at) return `${name} took your room out of their world`;
    const dx = Number(at.dx) || 0, dz = Number(at.dz) || 0, [W, D] = v.plotMetres, [mw, md] = (at.turn | 0) % 2 ? [D, W] : [W, D];
    const [tw, td] = Array.isArray(at.size) ? at.size.map(Number) : [NaN, NaN];
    const gap = Math.max(Math.abs(dx) - (tw + mw) / 2, Math.abs(dz) - (td + md) / 2);
    const dir = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][(Math.round(Math.atan2(dx, -dz) / (Math.PI / 4)) + 8) % 8];
    if (gap <= 0.5) return `${name} put your room right next to theirs, to the ${dir}`;
    return `${name} put your room ${dir} of theirs` + (Number.isFinite(gap) ? `, ${Math.round(gap)} m off` : "");
  }

  function panel(element, v, { chat: withChat = true, card: withCard = true, teleport: withTeleport = true } = {}) {
    ready(v, "panel");
    if (!document.getElementById("visit-panel-css")) {
      const st = document.createElement("style"); st.id = "visit-panel-css"; st.textContent = PANEL_CSS; document.head.appendChild(st);
    }
    const root = document.createElement("div");
    root.className = "visit-panel";
    element.appendChild(root);
    let drawn = "";
    // Folded (the caret, first in its first line): that line alone, and what asks you for an answer; remembered in this
    // browser. The element given gets visit-folded too, for a page's own rows beside the panel to fold with it.
    let folded = false;
    try { folded = localStorage.getItem("visit:panel") === "folded"; } catch (e) { /* not kept */ }
    const opened = new Set();   // the people (key, else office id) shown opened
    const groupsOpen = new Set();   // your worlds shown opened (lower-case names)
    let finding = false, making = false, describing = false;   // the lobby's worlds shown; a new world's form; your line's
    let joining = null, passwording = null;   // a locked world's password asked (to join it; yours, to change it)
    let linking = false, qr = null;   // linked browsers shown, with a code's box; a QR code shown for your phone ({ svg, until })
    let ignoredOpen = false;   // whom you ignore in chat, listed (3.3)
    let tpOpen = false, tpWhy = "";   // 5.1: the rooms that would take you by teleport, listed; why the last one did not
    const cw = withChat ? chat(v) : null;   // the chat window (8.9), bottom left (chat: false, none: your page has its own)
    const card = withCard ? targetCard(v, { chat: cw }) : null;   // your target's card (3.3, #48), top middle (card: false: your world draws its own)
    const pc = perf(v);   // the ⏱ corner, while v.perf measures
    let mapView = null;   // the map (8.4), once opened
    // M, anywhere but a box of the page's own: on your mount, or off (8.11).
    const mountChosen = () => v.mounts.some(m => m.id === v.mount) ? v.mount : v.mounts[0].id;
    const mountKey = e => {
      if (e.code !== MOUNT_KEY || e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || !v.mounts.length) return;
      const t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      v.ride(v.riding ? null : mountChosen());
    };
    addEventListener("keydown", mountKey);
    // 5.1.1: the ✦ list (where you may teleport), opened (asking the rooms again), closed, or turned over (no argument):
    // from the ✦, T anywhere but a box of the page's own, and the handle's teleports(), for the room's own button or a
    // click on its pad.
    const teleports = on => {
      if (!withTeleport || typeof v.findTeleports !== "function") return false;
      tpOpen = on === undefined ? !tpOpen : !!on; tpWhy = "";
      if (tpOpen) v.findTeleports();
      drawn = ""; draw();
      return tpOpen;
    };
    const teleportKey = e => {
      if (!withTeleport || e.code !== TELEPORT_KEY || e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || typeof v.findTeleports !== "function") return;
      const t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      teleports();
    };
    addEventListener("keydown", teleportKey);
    // Notes, a while each: where a friend's world put your room (once it settles: a friend dragging it about on their
    // Map tells you at every step; never again for the same place), and a call that ended other than hung up.
    let notes = [];
    const oops = (code, x) => { const message = String((x && x.message) || x); if (cw) cw.error({ code, message }); else note(message); };
    const note = text => { notes = notes.filter(n => n.until > Date.now()).concat([{ text, until: Date.now() + 10000 }]).slice(-3); drawn = ""; draw(); setTimeout(draw, 10100); };
    const seenSaid = new Map((v.seen || []).map(x => [x.key, JSON.stringify(x.at)])), seenWait = new Map();
    const onSeen = ({ key, name, at }) => {
      clearTimeout(seenWait.get(key));
      seenWait.set(key, setTimeout(() => {
        seenWait.delete(key);
        const sig = JSON.stringify(at || null);
        if (seenSaid.get(key) === sig || (!at && !seenSaid.has(key))) return;
        seenSaid.set(key, sig);
        note(seenText(v, name || "A friend", at));
      }, 1500));
    };
    const onCall = e => { if (e.state === "ended" && e.why && e.why !== "hung up") note(`${talks.has(e.key) ? "🎧" : "📞"} ${e.name}: ${e.why}`); if (e.state === "ended") talks.delete(e.key); else if (v.calls.some(c => c.key === e.key && c.voice)) talks.add(e.key); };
    const talks = new Set();   // (4.0: calls that were someone wanting to talk, said with 🎧)
    // Models (8.6): yours, as other rooms will read it, and how you look in the room you are in, once each.
    const modelSaid = new Set();
    const onModel = e => { if (e.problems.length && !modelSaid.has(e.what + e.problems[0])) { modelSaid.add(e.what + e.problems[0]); note(`${e.what}: ${e.problems[0]}${e.problems.length > 1 ? ` (and ${e.problems.length - 1} more: see the console)` : ""}`); } };
    const onBody = e => { if (e.problem && !modelSaid.has(e.channel + e.problem)) { modelSaid.add(e.channel + e.problem); note(`In ${e.name || "their"} room you look like their model: yours can't be shown (${e.problem})`); } };
    // A browser linked to you just now (by your 🌐 Lobby, 📱 Phone, or a code): said, so one you did not mean is seen.
    const browsersKnown = new Set(v.browsers.map(b => b.key));
    const onBrowsers = list => {
      for (const b of list) if (!browsersKnown.has(b.key)) {
        browsersKnown.add(b.key);
        if (Date.now() / 1000 - b.created < 120) note(`🌐 ${b.agent} ${b.pictures} is linked to you now: in the lobby as you (not yours? Link a browser: Unlink)`);
      }
    };
    v.on("seen", onSeen); v.on("call", onCall); v.on("model", onModel); v.on("body", onBody); v.on("browsers", onBrowsers);
    // 5.1 (SPEC 8.15): a teleport's end: there (the list folds away), or why not.
    const TP_WHY = { not_allowed: "that room said no", declined: "that room said no", timeout: "no answer in time", gone: "that room is gone",
      full: "that room is full", not_supported: "that room takes no teleports", no_world: "this page draws no world", offline: "you are offline",
      refused: "it did not let you in", no_pad: "your room has no pad", here: "you are there already", too_many: "your world watches all it can" };
    v.on("teleport", e => {
      if (e.state === "arrived") { tpOpen = false; tpWhy = ""; }
      if (e.state === "failed") tpWhy = `✦ ${e.name}: ${TP_WHY[e.reason] || e.reason}`;
      drawn = ""; draw();
    });
    function draw() {
      if (root.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;   // never under a typing hand
      const me = v.office, rows = [];
      root.classList.toggle("folded", folded); element.classList.toggle("visit-folded", folded);
      rows.push(`<div class="row hd"><button class="caret" data-v="fold" title="${folded ? "open the panel" : "fold the panel: its first line, and what asks you (a knock, a call)"} · Visit ${VERSION}">${folded ? "▸" : "▾"}</button><span class="dot ${v.state === "online" ? esc(v.status) : ""}"></span><span class="grow">Visit${me ? " · " + esc(me.name) : ""}</span><span class="muted">${esc(v.state)}</span>${withTeleport && typeof v.findTeleports === "function" ? `<button data-v="tp" class="${tpOpen || v.teleporting ? "go" : ""}" title="Teleport (T): the rooms that would take you now (each room says who it takes), and your own">✦</button>` : ""}<button data-v="perf" class="${v.perfOn ? "go" : ""}" title="how this page is doing: frames, stalls, and what Visit does (on or off)">⏱</button>${v.state === "online" ? `<button data-v="lobby" title="the lobby in a browser tab, as you: its chat, who is here, worlds (your door stays here)">🌐 Lobby</button>` : ""}${v.state === "online" ? `<button data-v="away" title="leave the lobby: nobody sees you or can link until you come back">Go offline</button>` : ""}</div>`);
      // 5.1 (SPEC 8.15): where you may teleport to, under the first line (folded or not, once you asked): each room that
      // said yes to you just now, your own first, but the one you are in.
      if (withTeleport && (tpOpen || v.teleporting)) {
        // (5.2: the room your user is in too: onto its pad, from wherever they are in it)
        const here = v.roomHere && v.roomHere.key, tp = v.teleporting, list = v.teleportTargets;
        if (tp) rows.push(`<div class="row tp"><span class="grow">✦ teleporting to <b>${esc(tp.name)}</b>…</span></div>`);
        else rows.push(`<div class="row tp"><span class="grow">✦ teleport to <span class="muted">· each room says whom it takes</span></span><button data-v="tp" title="close">✕</button></div>`);
        for (const t of list) rows.push(`<div class="row tp sub"><span class="grow">${t.home ? "⌂ <b>your room</b>" : `✦ <b>${esc(t.name)}</b>${t.user && t.user !== t.name ? ` <span class="muted">· ${esc(t.user)}</span>` : ""}`}${t.key === here ? ` <span class="muted">· here: to its pad</span>` : ""}</span><button class="go" data-v="tp_go" data-key="${esc(t.key)}"${tp ? " disabled" : ""}>Teleport</button></div>`);
        if (v.teleportAsking) rows.push(`<div class="row tp sub"><span class="grow muted">asking the rooms…</span></div>`);
        else if (!tp && !list.length) rows.push(`<div class="row tp sub"><span class="grow muted">no room would take you just now${v.state === "online" && !v.teleportLobby ? " (this lobby carries no teleports)" : ""}</span><button data-v="tp_again">Ask again</button></div>`);
        if (tpWhy && !tp) rows.push(`<div class="row tp sub note">${esc(tpWhy)}</div>`);
      }
      if (folded) {
        for (const c of v.calls) if (c.state === "ringing") rows.push(`<div class="row lnk ring"><div>${c.voice ? "🎧" : "📞"} <b>${esc(c.name)}</b> ${c.voice ? "wants to talk" : "is calling"}</div><div class="btns"><button class="go" data-v="pick_up" data-key="${esc(c.key)}">${c.voice ? "Answer" : "Pick up"}</button><button data-v="hang_up" data-key="${esc(c.key)}">Not now</button></div></div>`);
        return paint(rows);
      }
      if (v.state === "away") rows.push(`<div class="row"><span class="grow muted">out of the lobby: nobody sees you or can link</span><button class="go" data-v="back">Go online</button></div>`);
      else if (!v.signedIn) rows.push(`<div class="row"><span class="grow muted">out of the lobby: nobody is signed in on this workroom (its sign-in: auth/INTEGRATING.md)</span></div>`);
      else if (v.state === "online" && (v.lobbyInfo.title || v.lobbyInfo.motd)) rows.push(`<div class="row"><span class="grow muted">${esc(v.lobbyInfo.title || "the lobby")}${v.lobbyInfo.motd ? `: ${esc(v.lobbyInfo.motd)}` : ""}</span></div>`);
      for (const n of notes) if (n.until > Date.now()) rows.push(`<div class="row note">${esc(n.text)}</div>`);
      if (v.state === "online") {
        rows.push(`<div class="row"><span class="grow">you are</span><select data-v="status">${["available", "busy"].map(s => `<option${s === v.status ? " selected" : ""}>${s}</option>`).join("")}</select>
          <select data-v="max" title="how many visitors at once">${[0, 1, 2, 3, 4, 5, 6, 7, 8].map(n => `<option value="${n}"${n === v.maxVisitors ? " selected" : ""}>${n} visitors</option>`).join("")}</select></div>`);
        rows.push(`<div class="row"><span class="grow">your door</span><select data-v="door" title="open: everyone in a world with you may put your room in their world, and walks in without asking; closed: everyone asks, and your room leaves their worlds">
          <option value="open"${v.door !== "closed" ? " selected" : ""}>open to my worlds</option><option value="closed"${v.door === "closed" ? " selected" : ""}>closed: everyone asks</option></select></div>`);
        // Your line in the lobby's list of who is here (and on its public page): what you are, a few tags.
        if (describing) rows.push(`<form class="row" data-f="about"><input name="text" maxlength="200" placeholder="you, in a line" value="${esc(v.about ? v.about.text : "")}"><input name="tags" maxlength="110" placeholder="tags, comma-separated" value="${esc(v.about ? v.about.tags.join(", ") : "")}" style="max-width:9em"><button class="go">Save</button>${v.about ? `<button type="button" data-v="unlist" title="no line">Clear</button>` : ""}</form>`);
        else rows.push(`<div class="row"><span class="grow">${v.about ? `your line: <span class="muted">${esc(v.about.text || "(none)")}${v.about.tags.length ? " · " + v.about.tags.map(t => "#" + esc(t)).join(" ") : ""}</span>` : `<span class="muted">no line about you in the lobby</span>`}</span><button data-v="describe" title="your line in the lobby's list of who is here (and on its public page)">${v.about ? "Edit" : "Write one"}</button></div>`);
        // The lobby in a browser (3.1): on your phone (a QR code to scan), or a browser that shows a code (typed here,
        // allowed); the browsers linked, each to unlink.
        const on = v.browsers.filter(b => b.online).length;
        rows.push(`<div class="row"><span class="grow" title="the lobby's page in a browser, as you: its chat, who is here, your worlds">browsers <span class="muted">· ${v.browsers.length ? `${v.browsers.length} linked${on ? `, ${on} in` : ""}` : "the lobby, as you"}</span></span><button data-v="qr" class="${qr ? "go" : ""}" title="the lobby on your phone, as you: a QR code its camera opens (this links your phone; good once, for 2 minutes)">📱 On my phone</button><button data-v="browsers" class="${linking ? "go" : ""}" title="a browser at the lobby's page shows a code: type it here; and the browsers linked to you">Link a browser</button></div>`);
        // (An image of the SVG, never the SVG itself in the page: an image runs no script, whoever made it.)
        if (qr && qr.until > Date.now()) rows.push(`<div class="row sub"><div class="qr"><img alt="a QR code: the lobby, as you" src="data:image/svg+xml;charset=utf-8,${esc(encodeURIComponent(qr.svg))}"></div><div class="muted">to use the lobby on your phone: scan it with its camera, and your phone is in the lobby as you, linked (good once, for ${Math.max(1, Math.round((qr.until - Date.now()) / 60000))} more minutes)</div></div>`);
        // 3.2: a browser allowed, until it shows this: its camera scans the QR code (opening the lobby's page in it), or
        // its page takes the digits. Then it is linked ("browsers" says so).
        const cf = v.browserConfirm;
        if (cf && cf.until > Date.now()) rows.push(`<div class="row sub">${cf.qr ? `<div class="qr"><img alt="a QR code: the confirmation, for that browser's camera" src="data:image/svg+xml;charset=utf-8,${esc(encodeURIComponent(cf.qr))}"></div>` : ""}<div>Scan this with your phone to confirm <b>${esc(cf.agent)}</b> <span class="pics">${esc(cf.pictures)}</span> (your phone is not linked), or type <b class="digits">${esc(cf.confirm.slice(0, 3))} ${esc(cf.confirm.slice(3))}</b> on that browser's page. <span class="muted">It goes in by itself once you do (${Math.max(1, Math.round((cf.until - Date.now()) / 60000))} min).</span></div><div class="btns"><button data-v="b_cancel">Cancel</button></div></div>`);
        if (linking) {
          const p = v.browserPending;
          if (p) rows.push(`<div class="row knock"><b>${esc(p.agent)}</b> <span class="pics">${esc(p.pictures)}</span> waits with ${esc(p.code.slice(0, 4))}-${esc(p.code.slice(4))}: the same pictures on its screen? <div class="btns"><button class="go" data-v="b_allow" data-code="${esc(p.code)}">Link it</button><button data-v="b_not">Not that one</button></div></div>`);
          else rows.push(`<form class="row" data-f="code"><input name="code" maxlength="12" placeholder="the code it shows: ABCD-EFGH" autocomplete="off"><button class="go">Find it</button></form>`);
          for (const b of v.browsers) rows.push(`<div class="row sub"><span class="grow">${b.online ? "●" : "○"} ${esc(b.agent)} <span class="pics">${esc(b.pictures)}</span> <span class="muted">· ${b.online ? "in now" : `last in ${new Date(b.used * 1000).toLocaleDateString()}`}</span></span><button data-v="b_unlink" data-key="${esc(b.key)}" title="this browser no longer speaks for you">Unlink</button></div>`);
        }
        // Whom you ignore in chat (3.3): how many; opened, each to let go; Clear, all of them.
        if (v.ignored.length) {
          rows.push(`<div class="row"><span class="grow" title="their lines never reach you (click a name in chat, or /ignore name)">ignored <span class="muted">· ${v.ignored.length} of ${MAX_IGNORED}</span></span><button data-v="ignored" class="${ignoredOpen ? "go" : ""}">${ignoredOpen ? "Hide" : "Show"}</button><button data-v="ig_clear" title="hear everyone you ignore again">Clear</button></div>`);
          if (ignoredOpen) for (const i of v.ignored) rows.push(`<div class="row sub"><span class="grow">${esc(i.name || "someone")}${i.npc ? ` <span class="muted">· a figure in ${esc(v.nameOf(i.key) || "a room")}</span>` : ""}</span><button data-v="unignore" data-key="${esc(i.key)}" data-npc="${esc(i.npc || "")}">Unignore</button></div>`);
        }
        rows.push(`<div class="row"><span class="grow">visitors look like</span><select data-v="looks" title="in your room: the look each one brings (their model), or your own (our model); one can be set apart (click them below)">
          <option value="theirs"${v.avatars.default === "theirs" ? " selected" : ""}>their model</option><option value="mine"${v.avatars.default === "mine" ? " selected" : ""}>our model</option></select></div>`);
      }
      // 3.3: whether their room is in your world (your pick, at most MAX_PICKS; click to change): for someone in a world
      // with you (or picked before); nothing for anyone else, nor an office with no key yet.
      const pickBtn = (key, name) => {
        if (!key || key === v.key || !(v.isGuest(key) || v.isPicked(key))) return "";
        const on = v.isPicked(key), full = !on && v.picks.length >= MAX_PICKS;
        return `<button class="wpick${on ? " on" : ""}" data-v="pick" data-key="${esc(key)}" data-name="${esc(name)}"${full ? " disabled" : ""} title="${on
          ? `${esc(name)}'s room is in your world (click: take it out)` : full ? `your world shows ${MAX_PICKS} rooms already: take one out first`
          : `put ${esc(name)}'s room in your world, beside yours (${v.picks.length} of ${MAX_PICKS})`}">${on ? "🗺 ✓" : "🗺 +"}</button>`;
      };
      // Your phone (8.8): a call to make, to those linked to you whose rooms have phones.
      if (v.hasPhone && !v.calls.length && v.phones.length) rows.push(`<div class="row lnk"><div>📞 call</div><div class="btns">${v.phones.map(p => `<button data-v="call" data-key="${esc(p.key)}" title="ring their phone">${esc(p.name)}</button>`).join("")}</div></div>`);
      // Calls (8.8): ringing, calling, on.
      for (const c of v.calls) rows.push(`<div class="row lnk${c.state === "ringing" ? " ring" : ""}"><div>${c.voice ? "🎧" : "📞"} ${c.state === "ringing" ? `<b>${esc(c.name)}</b> ${c.voice ? "wants to talk" : "is calling"}` : c.state === "calling" ? `calling <b>${esc(c.name)}</b>…`
        : c.state === "answering" ? `<b>${esc(c.name)}</b>: connecting…` : `${c.voice ? "talking with" : "on a call with"} <b class="${c.speaking ? "speaking" : ""}">${esc(c.name)}</b>`}</div><div class="btns">${c.state === "ringing" ? `<button class="go" data-v="pick_up" data-key="${esc(c.key)}">${c.voice ? "Answer" : "Pick up"}</button>` : ""}${c.state === "on" && !c.voice
          ? `<button data-v="speaker" data-key="${esc(c.key)}" class="${c.speaker ? "go" : ""}" title="${c.speaker ? "on speaker: everyone near your phone hears and is heard (click: to your ear)" : "to your ear: only you (click: on speaker, for everyone near your phone)"}">${c.speaker ? "🔊 speaker" : "📞 handset"}</button>` : ""}<button data-v="hang_up" data-key="${esc(c.key)}">${c.state === "ringing" ? "Not now" : "Hang up"}</button></div></div>`);
      // Voice (8.7): those near you in the room you are in; your microphone on or off.
      if (v.state === "online") rows.push(`<div class="row"><span class="grow">${v.voices.length ? "near you: " + v.voices.map(x => `<b class="${x.speaking ? "speaking" : ""}">${x.speaking ? "🔊 " : ""}${esc(x.name)}</b>`).join(", ") : `voice <span class="muted">· nobody near you</span>`}</span>
        <button data-v="mic" class="${v.mic ? "go" : ""}" title="${v.mic ? "those near you hear you (click: microphone off)" : "your microphone: those near you hear you, the nearer the louder (headphones are best)"}">${v.mic ? "🎙 on" : "🎙 off"}</button><button data-v="sounds" class="${v.roomSounds ? "go" : ""}" title="${v.roomSounds ? "the room's own sounds, from where they are (click: off, in this browser)" : "the room's own sounds: off (click: on)"}">${v.roomSounds ? "🔈" : "🔇"}</button></div>`);
      // The room voice you are in (8.7): whose, who is in it; Leave.
      if (v.roomVoice) { const rv = v.roomVoice, inH = v.inHuddle; rows.push(`<div class="row"><span class="grow">🎧 ${inH ? `${inH.mine ? "your" : esc(`${rv.name}'s`)} huddle in ${esc(inH.group)}` : esc(rv.key === v.key ? "your room's voice" : `${rv.name}'s room's voice`)}${rv.joining ? " <span class=\"muted\">· joining…</span>"
        : `: ${rv.members.map(x => `<b class="${x.speaking ? "speaking" : ""}">${x.speaking ? "🔊 " : ""}${esc(x.key === v.key ? "you" : x.name)}</b>`).join(", ")}`}</span><button data-v="voice_leave" title="out of this voice">Leave</button></div>`); }
      // Your mount (8.11), once your room gives one: which one M gets you on, and on or off.
      if (v.mounts.length) rows.push(`<div class="row"><span class="grow">${v.riding ? "riding" : "your mount"}</span><select data-v="mount" title="the one M gets you on" style="max-width: 9em">${v.mounts.map(m =>
        `<option value="${esc(m.id)}"${m.id === v.mount ? " selected" : ""}>${esc(m.name)}${m.fly ? " (flies)" : ""}</option>`).join("")}</select><button data-v="ride" class="${v.riding ? "go" : ""}" title="M: on your mount, or off">${v.riding ? "Get off" : "Ride"}</button></div>`);
      const world = v.currentWorld, roundYours = world ? world.plots.filter(p => !p.home) : [];   // (a user page's own room is a plot too)
      if (world && v.state === "online") rows.push(`<div class="row"><span class="grow" title="the rooms you put in your world (🗺 + by someone in a world with you), at most ${MAX_PICKS}">your world: ${roundYours.length} ${roundYours.length === 1 ? "room" : "rooms"} round yours${(n => n ? ` (${n} offline)` : "")(roundYours.filter(p => p.offline).length)} <span class="muted">· ${v.picks.length} of ${MAX_PICKS} picked</span></span><button data-v="map" title="where your friends' rooms sit round yours, from above">Map</button></div>`);
      // Your worlds (LOBBY.md): each a row (how many of it are online, 🔒 a password); opened, its members, Leave, and for
      // its owner: take one out (✕) or keep them out (⛔), hand it on (♛), its password, public, End it; the lobby's
      // worlds to join (Find: a locked one asks its password), a new one (New: public, or with a password).
      if (v.state === "online") {
        const here = new Set(v.offices.map(o => o.key).filter(Boolean));
        rows.push(`<div class="row"><span class="grow">worlds <span class="muted">· ${v.groups.length ? `you are in ${v.groups.length}` : "their members walk into each other's rooms, and pick whose rooms sit round theirs"}</span></span><button data-v="find" class="${finding ? "go" : ""}" title="the lobby's worlds">Find</button><button data-v="new" class="${making ? "go" : ""}" title="a world of your own">New</button></div>`);
        if (making) rows.push(`<form class="row" data-f="group"><input name="name" maxlength="24" placeholder="its name"><input name="about" maxlength="200" placeholder="what it is" style="max-width:8em"><input name="password" maxlength="64" placeholder="password (none: public)" style="max-width:9em" autocomplete="new-password"><button class="go">Make</button></form>`);
        for (const g of v.groups) {
          const k = g.name.toLowerCase(), runs = v.isGroupOwner(g.name), online = g.members.filter(m => here.has(m.key) || m.key === v.key).length, open = groupsOpen.has(k);
          const how = g.locked ? " 🔒" : !g.open ? " · closed" : "";
          rows.push(`<div class="row"><button class="who grow" data-v="group" data-g="${esc(k)}"><b>⚑ ${esc(g.name)}</b>${how} <span class="muted">· ${online}/${g.members.length} online</span></button><button class="caret" data-v="group" data-g="${esc(k)}">${open ? "▾" : "▸"}</button></div>`);
          if (!open) continue;
          const who = m => `${here.has(m.key) || m.key === v.key ? "●" : "○"} ${esc(m.key === v.key ? "you" : (v.userOf(m.key) || m.name))}${m.key === g.owner ? " ♛" : ""}${m.key === v.key ? "" : " " + pickBtn(m.key, v.userOf(m.key) || m.name)}`;
          const subs = [];
          if (g.about) subs.push(`<div class="muted">${esc(g.about)}</div>`);
          subs.push(`<div>${g.members.map(m => runs && m.key !== g.owner
            ? `<span class="seg">${who(m)} <button data-v="g_out" data-g="${esc(g.name)}" data-key="${esc(m.key)}" title="out of ${esc(g.name)} (they may join again)">✕</button><button data-v="g_ban" data-g="${esc(g.name)}" data-key="${esc(m.key)}" title="out of ${esc(g.name)}, and kept out">⛔</button><button data-v="g_owner" data-g="${esc(g.name)}" data-key="${esc(m.key)}" data-name="${esc(m.name)}" title="hand ${esc(g.name)} to them: its owner">♛</button></span>`
            : who(m)).join(" · ")}</div>`);
          if (runs && g.bans.length) subs.push(`<div class="muted">kept out: ${g.bans.map(key => `<span class="seg">${esc(v.userOf(key) || (v.offices.find(o => o.key === key) || {}).name || key.slice(0, 8))} <button data-v="g_unban" data-g="${esc(g.name)}" data-key="${esc(key)}" title="let them join again">↺</button></span>`).join(" ")}</div>`);
          if (runs && passwording === k) subs.push(`<form data-f="g_pw" data-g="${esc(g.name)}"><input name="password" maxlength="64" placeholder="${g.locked ? "a new password (its members stay)" : "a password for it"}" autocomplete="new-password"><button class="go">Set</button></form>`);
          subs.push(`<div class="btns">${runs ? `<button data-v="g_pw" data-g="${esc(k)}" class="${passwording === k ? "go" : ""}" title="${g.locked ? "change its password (its members stay)" : "give it a password: only those who have it join"}">${g.locked ? "🔒 Password…" : "🔒 Lock…"}</button>${!g.open ? `<button data-v="g_public" data-g="${esc(g.name)}" title="anyone joins, no password">Make public</button>` : ""}` : ""}<button data-v="g_leave" data-g="${esc(g.name)}">Leave</button>${runs ? `<button data-v="g_end" data-g="${esc(g.name)}" title="ends it for everyone in it">End it</button>` : ""}</div>`);
          rows.push(`<div class="row sub">${subs.join("")}</div>`);
        }
        if (finding) {
          const mine = new Set(v.groups.map(g => g.name.toLowerCase())), list = v.groupList.filter(g => !mine.has(g.name.toLowerCase()));
          for (const g of list) {
            const k = g.name.toLowerCase();
            rows.push(`<div class="row"><span class="grow">⚑ ${esc(g.name)}${g.locked ? " 🔒" : ""} <span class="muted">${g.about ? `· ${esc(g.about)} ` : ""}· ${g.online}/${g.members} online</span></span>${g.open
              ? `<button class="go" data-v="g_join" data-g="${esc(g.name)}" title="public: you are in at once">Join</button>`
              : g.locked ? `<button data-v="g_ask" data-g="${esc(k)}" class="${joining === k ? "go" : ""}" title="it takes a password: its members have it">Join…</button>` : `<button disabled title="closed: its owner has not opened it">closed</button>`}</div>`);
            if (joining === k) rows.push(`<form class="row" data-f="g_join" data-g="${esc(g.name)}"><input name="password" maxlength="64" placeholder="${esc(g.name)}'s password" autocomplete="off"><button class="go">Join</button></form>`);
          }
          if (!list.length) rows.push(`<div class="row muted">no other worlds in this lobby yet</div>`);
        }
      }
      // People: one row each, whoever is in the lobby and whoever you are linked with, either way, linked first. A
      // linked one says where you two stand (in your room, in theirs, linked); click it for the rest: where each sees
      // the other, Go there or Put in my world, how they look in your room, Kick, Unlink. One not linked: Link.
      const people = [];
      const person = (who, add) => {
        if (!who) return;
        let p = people.find(x => (who.key && x.key === who.key) || (who.id && x.id === who.id));
        if (!p) people.push(p = { key: null, id: null, name: "", office: null, visit: null, watches: [], plot: null });
        p.key = p.key || who.key || null; p.id = p.id || who.id || null; p.name = p.name || String(who.name || "");
        add(p);
      };
      // 4.0 (#57): those you share a world with, those in your room, and those whose room you are in (or whose plot your
      // world shows). Who else is in the lobby is its page's (🌐 Lobby).
      for (const o of v.offices) if ((!me || o.id !== me.id) && o.key && v.groupsWith(o.key).length) person(o, p => { p.office = o; });
      for (const x of v.visitors) person(x.visitor, p => { p.visit = x; if (!p.office) p.office = v.offices.find(o => o.key && o.key === x.visitor.key) || null; });
      for (const x of v.watchers) if (!x.inside) person(x.visitor, p => p.watches.push(x));
      for (const w of v.watches) person(w.host, p => { p.plot = w; });   // (never your own room)
      const linked = p => !!(p.visit || p.watches.length || p.plot);
      const more = p => {   // a linked one, opened: where each sees the other (beyond its row's own word), what you can do
        const seen = [], btns = [];
        if (p.plot) seen.push(p.plot.inside ? "you are in their room" : "their room is in your world");
        for (const x of p.watches) seen.push("your room is in their world");
        // How each of you is drawn where the other is (8.6): their model here, and yours there.
        const here = p.visit && v.visitors.find(x => x.id === p.visit.id);
        if (here && here.problem) seen.push(`their model can't be shown here: ${here.problem}`);
        const inThere = v.watches.find(w => w.inside && (w.host.key === p.key || w.host.id === p.id));
        const there = inThere && inThere.body;
        if (there) seen.push(there.look === "theirs" ? "there, you look like your own model" : `there, you look like their model${there.problem ? ` (yours can't be shown: ${there.problem})` : ""}`);
        if (p.key) {
          const u = v.avatarUse(p.key);
          btns.push(`<span class="seg" title="how ${esc(p.name)} looks in your room">${[["theirs", "their model"], ["mine", "our model"]].map(([w, t]) =>
            `<button data-v="look" data-key="${esc(p.key)}" data-use="${w}" class="${u === w ? "on2" : ""}">${t}</button>`).join("")}</span>`);
        }
        // Your room's voice and chat (8.9): kept out of either, until you lift it; out of its voice now.
        if (v.voiceMembers.some(x => x.key === p.key)) seen.push("in your room's voice");
        const by = p.key && v.bannedBy.find(b => b.key === p.key);
        if (by) seen.push(`their room keeps you out of its ${by.voice && by.chat ? "voice and chat" : by.voice ? "voice" : "chat"}`);
        if (p.key) {
          const ban = v.banOf(p.key) || { voice: false, chat: false };
          btns.push(`<span class="seg" title="keep ${esc(p.name)} out of your room's voice, or its chat (until you lift it)">${["voice", "chat"].map(w =>
            `<button data-v="ban" data-key="${esc(p.key)}" data-what="${w}" class="${ban[w] ? "on2" : ""}">${ban[w] ? `${w} banned` : `ban ${w}`}</button>`).join("")}</span>`);
          if (v.voiceMembers.some(x => x.key === p.key)) btns.push(`<button data-v="voice_kick" data-key="${esc(p.key)}" title="out of your room's voice now (they may come back)">Kick from voice</button>`);
        }
        if (p.visit) btns.push(`<button data-v="kick" data-x="${esc(p.visit.id)}" title="out of your room">Kick</button>`);
        if (p.key) btns.push(`<button data-v="ignore" data-key="${esc(p.key)}" data-name="${esc(p.name)}" title="${v.isIgnored(p.key) ? "hear them in chat again" : "their lines in chat never reach you (nothing tells them)"}">${v.isIgnored(p.key) ? "Unignore" : "Ignore"}</button>`);
        btns.push(`<button data-v="cut" data-p="${esc(p.key || p.id)}" title="out of each other's rooms now${p.key && v.isPicked(p.key) ? ", and their room out of your world" : ""} (in a world together, either may walk in again)">Part</button>`);
        // (Plain text, each: a model's problem is the other room's word, or the other model's. 3.4.3: escaped.)
        return `<div class="row sub">${seen.length ? `<div class="muted">${[...new Set(seen)].map(esc).join(" · ")}</div>` : ""}<div class="btns">${btns.join("")}</div></div>`;
      };
      const talkBtn = (key, name) => key && v.canTalk(key) ? `<button data-v="talk" data-key="${esc(key)}" title="talk with ${esc(name)}, voice alone, wherever you both are: it rings for them">🎧</button>` : "";
      for (const p of [...people.filter(linked), ...people.filter(p => !linked(p))]) {
        const o = p.office, dot = `<span class="dot ${o ? esc(o.status) : ""}"></span>`;
        const who = (p.key && v.userOf(p.key)) || p.name, room = who !== p.name ? ` <span class="muted">(${esc(p.name)})</span>` : "";   // the name they go by, their office's after it
        if (linked(p)) {
          const k = p.key || p.id, open = opened.has(k);
          const state = p.visit ? "in your room" : p.plot && p.plot.inside ? "you are in their room" : p.plot ? "in your world" : "your room is in their world";
          rows.push(`<div class="row">${dot}<button class="who grow" data-v="more" data-p="${esc(k)}" title="${open ? "less" : "more: where you see each other, their look, Part"}"><b>${esc(who)}</b>${room} <span class="muted">· ${state}</span></button>${talkBtn(p.key, who)}${pickBtn(p.key, who)}<button class="caret" data-v="more" data-p="${esc(k)}">${open ? "▾" : "▸"}</button></div>`);
          if (open) rows.push(more(p));
          continue;
        }
        if (!o) continue;
        const kin = v.groupsWith(o.key);
        rows.push(`<div class="row">${dot}<span class="grow" title="${esc(o.name)} · in ${esc(kin.join(", "))} with you">${who !== o.name ? `<b>${esc(who)}</b> <span class="muted">(${esc(o.name)})</span>` : esc(o.name)} ⚑ <span class="muted">${esc(o.sim.name || "")} ${o.visitors}/${o.maxVisitors}</span>${o.about && o.about.text ? `<br><span class="muted">${esc(o.about.text)}${o.about.tags.length ? " · " + o.about.tags.map(t => "#" + esc(t)).join(" ") : ""}</span>` : ""}</span>
          ${talkBtn(o.key, who)}${pickBtn(o.key, who)}</div>`);
      }
      // Picked, and not in the lobby now: their room stays in your world (from its last landscape), to take out here.
      for (const x of v.picks) if (x.key !== v.key && !people.some(p => p.key === x.key)) {   // (4.3.0: never your own room: it is your world's middle)
        const name = v.userOf(x.key) || x.name || (v.guests.find(g => g.key === x.key) || {}).name || x.key.slice(0, 8);
        rows.push(`<div class="row"><span class="dot"></span><span class="grow">${esc(name)} <span class="muted">· away${v.isGuest(x.key) ? "" : " · in none of your worlds now"}</span></span>${pickBtn(x.key, name)}</div>`);
      }
      if (v.state === "online" && !people.length) rows.push(`<div class="row muted">${v.groups.length ? "nobody from your worlds is here now" : "meet people in a world: Find one, or make one (New); 🌐 Lobby shows who is here"}</div>`);
      if (!folded) rows.push(`<div class="row ver"><span class="grow"></span><span class="muted">Visit ${VERSION}</span></div>`);
      paint(rows);
    }
    function paint(rows) {
      const html = rows.join("");
      if (html === drawn) return;   // nothing new: an open menu stays open, a click lands
      drawn = html;
      root.innerHTML = html;
      // A knock's message, a world's name or password, your line: the box just opened takes the keys. Never a browser's
      // code box: it stays while the list of browsers under it changes, and the panel redraws only when no box is typed in.
      const input = root.querySelector("form[data-o] input, form[data-f]:not([data-f=code]) input");
      if (input && !root.contains(document.activeElement)) input.focus();
    }
    root.addEventListener("click", e => {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      const act = b.dataset.v;
      if (act === "fold") { folded = !folded; try { localStorage.setItem("visit:panel", folded ? "folded" : "open"); } catch (x) { /* not kept */ } draw(); }
      else if (act === "lobby") v.openLobby().catch(x => note(`The lobby's page: ${x.message || x}`));
      else if (act === "qr") {
        if (qr) { qr = null; return draw(); }
        v.lobbyQr().then(p => { if (!p.qr) throw new Error("no QR code"); qr = { svg: p.qr, until: p.expires * 1000 }; draw(); setTimeout(() => { if (qr && qr.until <= Date.now()) { qr = null; draw(); } }, Math.max(0, qr.until - Date.now()) + 500); },
          x => note(`📱 ${x.message || x}`));
      }
      else if (act === "browsers") { linking = !linking; v.browserPending = null; if (linking) v.listBrowsers(); draw(); }
      else if (act === "b_allow") v.allowBrowser(b.dataset.code).then(c => { draw(); setTimeout(draw, Math.max(0, c.until - Date.now()) + 500); }, x => note(`Link a browser: ${x.message || x}`));
      else if (act === "b_cancel") { v.browserConfirm = null; draw(); }   // (it lapses at the lobby in its time)
      else if (act === "b_not") { v.browserPending = null; draw(); }
      else if (act === "b_unlink") v.unlinkBrowser(b.dataset.key);
      else if (act === "describe") { describing = !describing; draw(); }
      else if (act === "unlist") { v.setAbout(null); describing = false; draw(); }
      else if (act === "find") { finding = !finding; joining = null; if (finding) v.findGroups(); draw(); }
      else if (act === "new") { making = !making; draw(); }
      else if (act === "group") { if (!groupsOpen.delete(b.dataset.g)) groupsOpen.add(b.dataset.g); draw(); }
      else if (act === "g_join") { v.joinGroup(b.dataset.g); setTimeout(() => v.findGroups(), 500); }
      else if (act === "g_ask") { joining = joining === b.dataset.g ? null : b.dataset.g; draw(); }
      else if (act === "g_leave") v.leaveGroup(b.dataset.g);
      else if (act === "g_end") { if (confirm(`End ${b.dataset.g} for everyone in it?`)) v.deleteGroup(b.dataset.g); }
      else if (act === "g_out") v.removeFromGroup(b.dataset.g, b.dataset.key);
      else if (act === "g_ban") v.removeFromGroup(b.dataset.g, b.dataset.key, { ban: true });
      else if (act === "g_unban") v.unbanFromGroup(b.dataset.g, b.dataset.key);
      else if (act === "g_owner") { if (confirm(`Hand ${b.dataset.g} to ${b.dataset.name}? They own it then, not you.`)) v.handGroup(b.dataset.g, b.dataset.key); }
      else if (act === "g_pw") { passwording = passwording === b.dataset.g ? null : b.dataset.g; draw(); }
      else if (act === "g_public") { if (confirm(`Make ${b.dataset.g} public? Anyone joins then, no password.`)) v.setGroup(b.dataset.g, { open: true }); }
      else if (act === "more") { if (!opened.delete(b.dataset.p)) opened.add(b.dataset.p); draw(); }
      else if (act === "cut") {   // every link between you two, ended both ways; out of your world (else its watch would come back)
        const k = b.dataset.p, is = who => !!who && (who.key === k || who.id === k), ends = new Set();
        if (v.isPicked(k)) v.unpick(k);
        for (const x of [...v.watchers, ...v.visitors]) if (is(x.visitor)) ends.add(x.id);
        for (const w of v.watches) if (is(w.host)) ends.add(w.id);
        for (const id of ends) v.kick(id);
        opened.delete(k);
      }
      else if (act === "talk") { if (!v.talk(b.dataset.key)) note("They cannot talk now"); }
      else if (act === "map") (mapView || (mapView = map(v))).open();
      else if (act === "tp") teleports();
      else if (act === "tp_again") { tpWhy = ""; v.findTeleports(); draw(); }
      else if (act === "tp_go") { tpWhy = ""; v.teleport(b.dataset.key); draw(); }
      else if (act === "perf") v.perf(!v.perfOn);
      else if (act === "ride") v.ride(v.riding ? null : mountChosen());
      else if (act === "mic") v.setMic(!v.mic).catch(x => oops("mic", x));
      else if (act === "call") { try { v.call(b.dataset.key); } catch (x) { note(String(x.message || x)); } }
      else if (act === "pick_up") v.pickUp(b.dataset.key).catch(x => oops("mic", x));
      else if (act === "hang_up") v.hangUp(b.dataset.key);
      else if (act === "speaker") v.speaker(b.dataset.key, !(v.calls.find(c => c.key === b.dataset.key) || {}).speaker);
      else if (act === "look") v.useAvatar(b.dataset.key, b.dataset.use === v.avatars.default ? null : b.dataset.use);
      else if (act === "kick") v.kick(b.dataset.x);
      else if (act === "ban") { const k = b.dataset.key, now = v.banOf(k) || { voice: false, chat: false }; v.ban(k, { ...now, [b.dataset.what]: !now[b.dataset.what] }); }
      else if (act === "voice_kick") v.voiceKick(b.dataset.key);
      else if (act === "voice_leave") v.joinVoice(null);
      else if (act === "away") v.leaveLobby();
      else if (act === "back") v.joinLobby();
      else if (act === "pick") v.isPicked(b.dataset.key) ? v.unpick(b.dataset.key) : v.pick(b.dataset.key, b.dataset.name);
      else if (act === "ignored") { ignoredOpen = !ignoredOpen; draw(); }
      else if (act === "sounds") v.setRoomSounds(!v.roomSounds);
      else if (act === "ig_clear") { if (confirm(`Hear everyone you ignore again (${v.ignored.length})?`)) v.clearIgnored(); }
      else if (act === "unignore") v.unignore(b.dataset.key, { npc: b.dataset.npc || null });
      else if (act === "ignore") v.isIgnored(b.dataset.key) ? v.unignore(b.dataset.key) : v.ignore(b.dataset.key, { name: b.dataset.name });
    });
    root.addEventListener("change", e => {
      if (e.target.dataset.v === "status") v.setStatus(e.target.value);
      if (e.target.dataset.v === "max") v.setMaxVisitors(Number(e.target.value));
      if (e.target.dataset.v === "door") v.setDoor(e.target.value === "open");
      if (e.target.dataset.v === "looks") v.useAvatars(e.target.value);
      if (e.target.dataset.v === "mount") v.setMount(e.target.value);
    });
    root.addEventListener("submit", e => {
      e.preventDefault();
      const f = e.target.closest("form");
      if (f.dataset.f === "group") {   // a world of your own: public, or with a password
        const name = f.elements.name.value.trim();
        if (name) v.createGroup(name, { about: f.elements.about.value.trim(), password: f.elements.password.value || null });
        making = false; document.activeElement.blur(); return draw();
      }
      if (f.dataset.f === "g_join") {   // a locked world, its password
        v.joinGroup(f.dataset.g, f.elements.password.value);
        joining = null; setTimeout(() => v.findGroups(), 800); document.activeElement.blur(); return draw();
      }
      if (f.dataset.f === "g_pw") {   // your world's password, new
        if (f.elements.password.value) v.setGroup(f.dataset.g, { password: f.elements.password.value });
        passwording = null; document.activeElement.blur(); return draw();
      }
      if (f.dataset.f === "code") {   // a browser's code, as it shows it: the browser waiting with it, to allow
        const code = f.elements.code.value.trim();
        document.activeElement.blur();
        if (code) v.linkBrowser(code).then(() => draw(), x => note(`Link a browser: ${x.message || x}`));
        return;
      }
      if (f.dataset.f === "about") {   // your room's line in the lobby's list
        const tags = f.elements.tags.value.split(/[,\s]+/).map(t => t.trim().toLowerCase().replace(/^#/, "")).filter(Boolean);
        v.setAbout(f.elements.text.value.trim(), tags);
        describing = false; document.activeElement.blur(); return draw();
      }
    });
    root.addEventListener("focusout", () => setTimeout(draw, 0));
    v.on("change", draw);
    const speaking = setInterval(() => { if (v.mic || v.voices.length || v.calls.length) draw(); }, 250);   // who is near, and speaking
    draw();
    return { element: root, chat: cw, card, perf: pc, get map() { return mapView; },
      release() { clearInterval(speaking); v.off("change", draw); v.off("seen", onSeen); v.off("call", onCall); v.off("model", onModel); v.off("body", onBody); v.off("browsers", onBrowsers); for (const t of seenWait.values()) clearTimeout(t); if (cw) cw.release(); if (card) card.release(); pc.release(); if (mapView) mapView.release(); removeEventListener("keydown", mountKey); removeEventListener("keydown", teleportKey); root.remove(); },
      say() { if (cw) cw.open(); },   // the chat window, open to type a line (as Enter does)
      teleports };   // 5.1.1: the ✦ list: (true) opened, (false) closed, () turned over; what it is now
  }

  // ---------- the chat window (8.9): bottom left, as in a game ----------
  // Enter to type a line, Enter to say it, Esc to stop; / opens it with the / typed, for a command. Where a line goes
  // is before the box: Say (everyone who can see you), Tell <name> (a whisper: them alone, wherever they are), To
  // your room / To <name>'s room (a room's chat: everyone in it, and linked with it), Lobby (everyone in it). Pick it
  // there, or start the line with /s, /w <name> (/t, /tell), /r (back to whoever last whispered to you, else whom
  // you last whispered), /room (yours) or /room <name>, /1 (/l) and a space; it stays for the next line. Tab
  // finishes a name, from those it could be, shown over the box; else it moves the box on to the next place (Shift:
  // back), in the choices' order: only where a line can go now, and one Tell, to whoever you last whispered with
  // (0.22.0); not while a command is in the box (that says where). 🎧 by the box (or /voice)
  // joins the voice of the room the box talks to (else your own room's), or leaves it: while you are in one, a line
  // over the box says whose and who is in it (🔊: speaking), and stays. The rest fades `fade` ms after the last line,
  // and comes back with the next one, or Enter.
  // `element`: where it goes (the page). Its place: --visit-chat-left, --visit-chat-bottom.
  // The colour of someone's name in chat, as WoW colours a name by its class: one of these, by their key (else
  // their name), so the same on every page. None is a scope's (a whisper's pink, a room's blue).
  const NAME_COLORS = ["#ff6b6b", "#ff9f43", "#d4a373", "#ffd93d", "#a3e048", "#2ee6a6", "#3dd5f3"];
  const nameColor = key => {
    let h = 2166136261;
    for (const c of String(key)) h = Math.imul(h ^ c.codePointAt(0), 16777619);
    return NAME_COLORS[(h >>> 0) % NAME_COLORS.length];
  };
  const CHAT_CSS = `
.visit-chat { position: fixed; left: var(--visit-chat-left, 16px); bottom: var(--visit-chat-bottom, 16px); z-index: 19; width: min(660px, calc(100vw - 32px)); font: 19.5px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif; color: #fff; transition: opacity .8s; }
.visit-chat.idle { pointer-events: none; }
.visit-chat > * { transition: opacity .8s; }
.visit-chat.idle > :not(.voice):not(.mail):not(.ring) { opacity: 0; }
.visit-chat .ring { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; pointer-events: auto; }
.visit-chat .ring:empty { display: none; }
.visit-chat .ring .ask { width: fit-content; max-width: 100%; padding: 5px 10px; font-size: 92%; color: #fff; background: rgba(13,17,23,.94); border: 2px solid #7ee787; border-radius: 8px; animation: visit-ring 1s ease-in-out infinite; }
.visit-chat .ring button { font: inherit; color: #c9d1d9; background: none; border: 1px solid #6e7681; border-radius: 4px; padding: 0 8px; margin-left: 8px; cursor: pointer; }
.visit-chat .ring button.go { color: #0d1117; background: #7ee787; border-color: #7ee787; font-weight: 600; }
@keyframes visit-ring { 0%, 100% { box-shadow: 0 0 0 0 rgba(126,231,135,.7); } 50% { box-shadow: 0 0 0 7px rgba(126,231,135,0); } }
.visit-chat button.headset.ring { opacity: 1; animation: visit-shake .5s ease-in-out infinite; }
@keyframes visit-shake { 0%, 100% { transform: rotate(0); } 25% { transform: rotate(-14deg); } 75% { transform: rotate(14deg); } }
@media (prefers-reduced-motion: reduce) { .visit-chat .ring .ask, .visit-chat button.headset.ring { animation: none; } }
.visit-chat .voice > div + div { margin-top: 2px; }
.visit-chat .voice { margin-top: 4px; padding: 2px 8px; width: fit-content; max-width: 100%; font-size: 85%; color: #7ee787; background: rgba(0,0,0,.45); border-radius: 6px; pointer-events: auto; }
.visit-chat .voice:empty { display: none; }
.visit-chat .voice .muted { color: #c9d1d9; opacity: .8; }
.visit-chat .voice button { font: inherit; color: #c9d1d9; background: none; border: 1px solid #6e7681; border-radius: 4px; padding: 0 5px; margin-left: 4px; cursor: pointer; }
.visit-chat .line img.pic { display: block; max-width: min(240px, 60vw); max-height: 180px; min-width: 40px; min-height: 30px; margin: 4px 0 2px; border-radius: 6px; cursor: zoom-in; background: rgba(255,255,255,.08); }
.visit-chat .card { display: flex; gap: 8px; align-items: flex-start; max-width: 440px; margin: 4px 0 2px; padding: 6px 8px; color: inherit; text-decoration: none; text-shadow: none; background: rgba(22,27,34,.88); border-left: 3px solid #aaaaff; border-radius: 6px; }
.visit-chat .card img { width: 64px; height: 64px; flex: none; object-fit: cover; border-radius: 4px; }
.visit-chat .card .site { font-size: 70%; opacity: .7; }
.visit-chat .card .title { font-size: 85%; font-weight: 600; }
.visit-chat .card .desc { font-size: 75%; opacity: .85; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.visit-chat .attach { display: none; flex-wrap: wrap; gap: 8px; align-items: flex-start; margin-top: 6px; }
.visit-chat.typing .attach:not(:empty) { display: flex; }
.visit-chat .chip { position: relative; }
.visit-chat .chip > img { display: block; width: 56px; height: 56px; object-fit: cover; border-radius: 4px; }
.visit-chat .chip.busy > img { opacity: .45; }
.visit-chat .chip .card { margin: 0; max-width: 360px; }
.visit-chat .chip button.drop { position: absolute; top: -6px; right: -6px; width: 18px; height: 18px; padding: 0; font: 11px/16px system-ui; color: #fff; background: #30363d; border: 1px solid #6e7681; border-radius: 50%; cursor: pointer; }
.visit-chat .shared { display: none; max-height: 16em; overflow-y: auto; margin-bottom: 4px; padding: 6px 8px; font-size: 80%; background: rgba(13,17,23,.94); border: 1px solid #30363d; border-radius: 6px; }
.visit-chat .shared.on { display: block; }
.visit-chat .shared .tabs { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }
.visit-chat .shared .tabs b { flex: 1; font-weight: 600; }
.visit-chat .shared button { font: inherit; color: #c9d1d9; background: none; border: 1px solid #30363d; border-radius: 4px; padding: 0 6px; cursor: pointer; }
.visit-chat .shared .tabs button.on { color: #fff; border-color: #aaaaff; }
.visit-chat .shared .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(72px, 1fr)); gap: 4px; margin-bottom: 4px; }
.visit-chat .shared .grid img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 4px; cursor: zoom-in; }
.visit-chat button.gallery { font: inherit; background: none; border: 0; padding: 0 2px; cursor: pointer; opacity: .8; }
.visit-lightbox { position: fixed; inset: 0; z-index: 60; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,.85); cursor: zoom-out; }
.visit-lightbox img { max-width: 95vw; max-height: 95vh; border-radius: 4px; }
.visit-chat button.headset { font: inherit; background: none; border: 0; padding: 0 2px; cursor: pointer; opacity: .55; }
.visit-chat button.headset.on { opacity: 1; text-shadow: 0 0 6px #7ee787; }
.visit-chat .log { max-height: 11em; overflow-y: auto; padding: 4px 8px; border-radius: 6px; text-shadow: 0 1px 2px #000, 0 0 4px #000; scrollbar-width: none; overflow-wrap: anywhere; }
.visit-chat .log:empty { display: none; }
.visit-chat:hover .log, .visit-chat.typing .log { background: rgba(0,0,0,.4); }
.visit-chat .line { padding-left: 1.25em; text-indent: -1.25em; }
.visit-chat .line + .line { margin-top: 3px; }
.visit-chat .line > img, .visit-chat .line > .card { text-indent: 0; }
.visit-chat .line.whisper, .visit-chat[data-scope=whisper] form { color: #ff80ff; }
.visit-chat .line.sim, .visit-chat[data-scope=sim] form { color: #aaaaff; }
.visit-chat .line.lobby, .visit-chat[data-scope=lobby] form { color: #ffc864; }
.visit-chat .line.group, .visit-chat[data-scope=group] form { color: #40ff40; }
.visit-chat .line.note { color: #ffff00; }
.visit-chat .line[data-time]::before { content: attr(data-time); margin-right: .45em; font-size: 78%; opacity: .6; font-variant-numeric: tabular-nums; }
.visit-chat .day { display: flex; align-items: center; gap: 8px; margin: 6px 0 3px; font-size: 72%; opacity: .7; }
.visit-chat .day::before, .visit-chat .day::after { content: ""; flex: 1; border-top: 1px solid rgba(255,255,255,.35); }
.visit-chat .names { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; padding: 0 8px; font-size: 85%; }
.visit-chat .names:empty { display: none; }
.visit-chat .who.act { cursor: pointer; pointer-events: auto; } .visit-chat .who.act:hover { text-decoration: underline; }
.visit-chat .namemenu { position: absolute; z-index: 2; display: flex; flex-direction: column; gap: 3px; min-width: 9em; padding: 6px 8px; font-size: 80%; background: rgba(13,17,23,.96); border: 1px solid #30363d; border-radius: 6px; pointer-events: auto; }
.visit-chat .namemenu[hidden] { display: none; }
.visit-chat .namemenu .hd { font-weight: 600; } .visit-chat .namemenu .muted { color: #8b949e; font-weight: 400; }
.visit-chat .namemenu button { font: inherit; text-align: left; color: #c9d1d9; background: none; border: 1px solid #30363d; border-radius: 4px; padding: 1px 6px; cursor: pointer; }
.visit-chat .namemenu button:hover { background: #1f2a44; }
.visit-chat .names button { font: inherit; color: #c9d1d9; background: rgba(22,27,34,.92); border: 1px solid #30363d; border-radius: 4px; padding: 0 6px; cursor: pointer; }
.visit-chat .names button.on { color: #fff; background: #1f2a44; border-color: #aaaaff; }
.visit-chat .line.mention { background: rgba(255,200,100,.16); border-left: 3px solid #ffc864; padding-left: calc(1.25em + 5px); border-radius: 3px; }
.visit-chat form { display: none; align-items: center; gap: 4px; margin-top: 4px; padding: 3px 6px; background: rgba(0,0,0,.7); border: 1px solid #6e7681; border-radius: 6px; }
.visit-chat.typing form { display: flex; }
.visit-chat select { font: inherit; color: inherit; background: none; border: 0; padding: 0; max-width: 45%; cursor: pointer; }
.visit-chat option { color: #e6edf3; background: #161b22; }
.visit-chat input { flex: 1; min-width: 0; font: inherit; color: inherit; background: none; border: 0; outline: none; }
.visit-chat button.sound { font: inherit; color: inherit; background: none; border: 0; padding: 0 2px; cursor: pointer; opacity: .8; }
.visit-chat button.mail { display: none; margin-top: 4px; padding: 1px 8px; font: inherit; font-size: 85%; color: #ffc864; background: rgba(0,0,0,.55); border: 1px solid #ffc864; border-radius: 6px; cursor: pointer; pointer-events: auto; }
.visit-chat button.mail.on { display: inline-block; }
.visit-chat .mailbox { display: none; max-height: 16em; overflow-y: auto; margin-bottom: 4px; padding: 6px 8px; font-size: 80%; background: rgba(13,17,23,.94); border: 1px solid #30363d; border-radius: 6px; }
.visit-chat .mailbox.on { display: block; }
.visit-chat .mailbox .tabs { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.visit-chat .mailbox .tabs b { flex: 1; font-weight: 600; }
.visit-chat .mailbox button { font: inherit; color: #c9d1d9; background: none; border: 1px solid #30363d; border-radius: 4px; padding: 0 6px; cursor: pointer; }
.visit-chat .mailbox .line { cursor: pointer; border-radius: 3px; }
.visit-chat .mailbox .line:hover { background: rgba(255,255,255,.08); }
.visit-chat .mailbox .when { opacity: .6; font-size: 85%; }
.visit-chat .mailbox .held { display: flex; gap: 6px; align-items: baseline; color: #ff80ff; }
.visit-chat .mailbox .held span { flex: 1; }
.visit-chat .line.held { opacity: .75; font-style: italic; }
.visit-chat button.play { font: inherit; font-size: 90%; color: inherit; background: rgba(255,255,255,.08); border: 1px solid currentColor; border-radius: 12px; padding: 0 8px; margin: 2px 0 0 4px; text-indent: 0; cursor: pointer; }
.visit-chat button.rec { font: inherit; color: inherit; background: none; border: 0; padding: 0 2px; cursor: pointer; opacity: .8; }
.visit-chat button.rec.on { opacity: 1; color: #ff6b6b; }
.visit-chat .chip .clip { display: block; padding: 4px 8px; border: 1px solid #6e7681; border-radius: 12px; }
.visit-chat .chip.busy > .clip { opacity: .45; }`;

  // A picture full size, over everything, until a click or Esc.
  function lightbox(url) {
    const d = document.createElement("div"), img = document.createElement("img");
    d.className = "visit-lightbox"; img.src = url; d.appendChild(img);
    const shut = () => { d.remove(); removeEventListener("keydown", esc, true); };
    const esc = e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); shut(); } };
    d.addEventListener("click", shut);
    addEventListener("keydown", esc, true);
    document.body.appendChild(d);
  }

  // A soft sound for a line that comes (not your own): a whisper rises, a lobby line is lower, the rest one note. Made
  // with Web Audio (a page's sound waits for a key or a click: browsers allow none before).
  let chatAudio = null;
  function chatSound(scope) {
    try {
      const ctx = chatAudio || (chatAudio = new (window.AudioContext || window.webkitAudioContext)());
      if (ctx.state === "suspended") ctx.resume();
      const notes = scope === "whisper" ? [660, 880] : scope === "lobby" ? [392] : scope === "group" ? [440, 587] : [523];
      notes.forEach((f, i) => {
        const o = ctx.createOscillator(), g = ctx.createGain(), at = ctx.currentTime + i * 0.09;
        o.type = "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(0.08, at + 0.015); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.22);
        o.connect(g).connect(ctx.destination); o.start(at); o.stop(at + 0.25);
      });
    } catch (e) { /* no sound here */ }
  }

  // 4.0 (#58): a phone's ring for someone who wants to talk (two bursts of a two-tone trill), a huddle's rising chime.
  // Not silenced by 🔕: it asks for an answer, as a phone does.
  function ringSound(kind) {
    try {
      const ctx = chatAudio || (chatAudio = new (window.AudioContext || window.webkitAudioContext)());
      if (ctx.state === "suspended") ctx.resume();
      const tones = kind === "talk" ? [0, 0.4].flatMap(b => Array.from({ length: 8 }, (_, i) => [b + i * 0.045, i % 2 ? 480 : 620, 0.045]))
        : [[0, 523, 0.16], [0.14, 659, 0.16], [0.28, 784, 0.3]];
      for (const [t, f, d] of tones) {
        const o = ctx.createOscillator(), g = ctx.createGain(), at = ctx.currentTime + t;
        o.type = kind === "talk" ? "square" : "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(kind === "talk" ? 0.035 : 0.09, at + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, at + d);
        o.connect(g).connect(ctx.destination); o.start(at); o.stop(at + d + 0.02);
      }
    } catch (e) { /* no sound here */ }
  }

  // 3.3 (#48): Visit's own card for your user's target (Visit.panel's; card: false, none: your world draws its own), top
  // middle: its name; a figure's title, line and actions (the room's interaction types: each runs by the room's own
  // rules); Say (a line aloud, meant for it) and Whisper (a figure: to it alone; a person: through the lobby); a person's
  // Link or Knock; ✕, no target.
  const CARD_CSS = `
.visit-card { position: fixed; top: 12px; left: 50%; transform: translateX(-50%); z-index: 18; min-width: 220px; max-width: min(420px, calc(100vw - 32px)); padding: 8px 10px; font: 13px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; color: #e6edf3; background: rgba(13,17,23,.92); border: 1px solid #30363d; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,.4); }
.visit-card[hidden] { display: none; }
.visit-card .hd { display: flex; align-items: baseline; gap: 6px; } .visit-card .hd b { font-size: 15px; } .visit-card .ti { color: #8b949e; flex: 1; }
.visit-card .ab { margin-top: 4px; color: #c9d1d9; } .visit-card .ab.no { color: #8b949e; font-style: italic; }
.visit-card .bt { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.visit-card button { font: inherit; color: #c9d1d9; background: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 2px 8px; cursor: pointer; }
.visit-card button:hover { background: #30363d; } .visit-card button.x { background: none; border: 0; padding: 0 2px; color: #8b949e; }`;
  function targetCard(v, { chat: cw = null } = {}) {
    ready(v, "targetCard");
    const withChat = !!cw;
    if (!document.getElementById("visit-card-css")) {
      const st = document.createElement("style"); st.id = "visit-card-css"; st.textContent = CARD_CSS; document.head.appendChild(st);
    }
    const root = document.createElement("div");
    root.className = "visit-card"; root.hidden = true;
    document.body.appendChild(root);
    let drawn = "";
    const draw = () => {
      const t = v.target;
      if (!t) { root.hidden = true; drawn = ""; return; }
      const o = t.kind === "person" ? v.offices.find(x => x.key === t.id) : null;
      const acts = v.actionsOf(t);
      const talks = t.kind !== "figure" || v.mayTalk(t);
      const html = `<div class="hd"><b>${esc(t.name || "someone")}</b><span class="ti">${esc(t.kind === "figure" ? t.title || "" : o ? (v.isGuest(o.key) ? "in a world with you" : "") : "")}</span><button type="button" class="x" data-c="clear" title="no target">✕</button></div>`
        + (t.about ? `<div class="ab">${esc(t.about)}</div>` : "")
        + (talks ? "" : `<div class="ab no">${t.talk === "owner" ? "only its room's owner may talk to it" : "it does not answer"}</div>`)
        + `<div class="bt">${acts.map(a => `<button type="button" data-c="act" data-a="${esc(a.type)}">${esc(a.label)}</button>`).join("")}`
        + (withChat && talks ? `<button type="button" data-c="say" title="a line aloud, meant for ${esc(t.name)}">Say</button>${t.kind === "figure" || (o && o.whisper) ? `<button type="button" data-c="whisper" title="${t.kind === "figure" ? "to it alone" : "to them alone"}">Whisper</button>` : ""}` : "")
        + (o && v.canTalk(o.key) ? `<button type="button" data-c="talk" title="talk with ${esc(t.name)}, voice alone: it rings for them">🎧 Talk</button>` : "") + `</div>`;
      if (html !== drawn) { drawn = html; root.innerHTML = html; }
      root.hidden = false;
    };
    root.addEventListener("click", e => {
      const b = e.target.closest("button[data-c]"), t = v.target;
      if (!b || !t) return;
      const c = b.dataset.c, o = t.kind === "person" ? v.offices.find(x => x.key === t.id) : null;
      if (c === "clear") v.setTarget(null);
      else if (c === "act") v.act(t.id, b.dataset.a);
      else if (c === "say" && cw) cw.aim({ scope: "aim", kind: t.kind, id: t.id, name: t.name, room: t.room });
      else if (c === "whisper" && t.kind === "figure" && cw) cw.aim({ scope: "figure", id: t.id, name: t.name, room: t.room });
      else if (c === "whisper" && o && cw) cw.aim({ scope: "whisper", id: o.id, key: o.key, name: o.name });
      else if (c === "talk" && o) v.talk(o.key);
    });
    v.on("target", draw); v.on("change", draw);
    draw();
    return { element: root, release() { v.off("target", draw); v.off("change", draw); root.remove(); } };
  }
  function chat(v, { element = document.body, fade = CHAT_FADE, sound = true } = {}) {
    ready(v, "chat");
    if (!document.getElementById("visit-chat-css")) {
      const st = document.createElement("style"); st.id = "visit-chat-css"; st.textContent = CHAT_CSS; document.head.appendChild(st);
    }
    const root = document.createElement("div");
    root.className = "visit-chat";
    root.dataset.scope = "say";
    root.innerHTML = `<div class="shared"></div><div class="mailbox"></div><div class="log" aria-live="polite"></div><div class="names"></div><div class="ring"></div><div class="voice"></div><button type="button" class="mail"></button><div class="attach"></div><form><select title="where your line goes"></select><input maxlength="${CHAT_LEN}" autocomplete="off" spellcheck="false"><button type="button" class="rec" title="a voice message (click again: done; at most a minute)">🎙</button><button type="button" class="gallery" title="pictures, voice messages and links shared here">🖼</button><button type="button" class="headset">🎧</button><button type="button" class="sound"></button></form>`;
    element.appendChild(root);
    const shared = root.querySelector(".shared"), attach = root.querySelector(".attach"), gallery = root.querySelector("button.gallery");
    const mailbox = root.querySelector(".mailbox"), mail = root.querySelector("button.mail");
    const log = root.querySelector(".log"), names = root.querySelector(".names"), voice = root.querySelector(".voice"), ring = root.querySelector(".ring"), headset = root.querySelector("button.headset"), form = root.querySelector("form"), pick = form.querySelector("select"), input = form.querySelector("input"), bell = form.querySelector("button.sound"), recBtn = form.querySelector("button.rec");
    // A sound with each line that comes, unless this browser was told not to (the 🔔 by the box).
    let quiet = !sound;
    try { if (localStorage.getItem("visit:chat-quiet") === "1") quiet = true; } catch (e) { /* not kept */ }
    const bellSay = () => { bell.textContent = quiet ? "🔕" : "🔔"; bell.title = quiet ? "no sound with a line (click: a sound)" : "a sound with each line (click: none)"; };
    bellSay();
    bell.addEventListener("click", () => { quiet = !quiet; try { localStorage.setItem("visit:chat-quiet", quiet ? "1" : "0"); } catch (e) { /* not kept */ } bellSay(); input.focus(); });
    let to = { scope: "say" }, choices = [], typing = false, last = null, told = null, tell = null, fadeTimer = null, intro = null;
    try {   // where lines went before a reload (this tab)
      const t = JSON.parse(sessionStorage.getItem("visit:chat-to") || "null");
      if (isObj(t) && ["say", "whisper", "sim", "lobby", "group"].includes(t.scope)) to = { scope: t.scope, ...["key", "id", "name"].reduce((o, k) => (typeof t[k] === "string" ? { ...o, [k]: t[k] } : o), {}) };
    } catch (e) { /* not kept */ }
    const yours = () => ({ scope: "sim", key: v.key || "", name: v.office ? v.office.name : "" });   // your room's chat
    const room = t => t.key === v.key ? "your room" : `${t.name || "a"}'s room`;
    const whom = t => v.whisperTargets.find(o => (t.key && o.key === t.key) || o.id === t.id);   // a whisper's person, now
    const label = t => t.scope === "aim" ? `Say to ${t.name}` : t.scope === "figure" ? `Tell ${t.name}` : t.scope === "lobby" ? "Lobby" : t.scope === "group" ? `World ${t.name}` : t.scope === "whisper" ? `Tell ${t.name}${whom(t) ? "" : " (away)"}` : t.scope === "sim" ? `To ${room(t)}` : "Say";
    const groupTo = g => ({ scope: "group", key: g.name.toLowerCase(), name: g.name });   // one of your world groups' chat
    const same = (a, b) => a.scope === b.scope && (a.key || a.id || null) === (b.key || b.id || null);
    // A line's text, in parts: a name ({ name, key }), in its own colour, among the rest.
    const parts = l => {
      const who = { name: `[${l.name || "someone"}]`, key: l.key || l.name, k: l.key || "", npc: l.npc || null, plain: l.name || "" };
      if (l.scope === "note") return [l.text];
      if (l.scope === "lobby") return ["[Lobby] ", who, `: ${l.text}`];
      if (l.scope === "group") return [`[${l.group}] `, who, `: ${l.text}`];
      if (l.scope === "whisper") { const t = l.to || {}; return l.mine ? ["To ", { name: `[${t.name || ""}]`, key: t.key || t.name, k: t.key || "", npc: t.npc || null, plain: t.name || "" }, `: ${l.text}`] : [who, ` whispers: ${l.text}`]; }
      if (l.scope === "sim") { const r = room(l.to || {}); return [`[${r[0].toUpperCase() + r.slice(1)}] `, who, `: ${l.text}`]; }
      return [who, ` says: ${l.text}`];
    };
    const wake = () => {
      root.classList.remove("idle");
      clearTimeout(fadeTimer);
      if (fade > 0) fadeTimer = setTimeout(function sleep() { if (typing || root.matches(":hover") || shared.classList.contains("on") || mailbox.classList.contains("on")) fadeTimer = setTimeout(sleep, 1000); else root.classList.add("idle"); }, fade);
    };
    // A picture by its id, shown once the visit-client has it (a click: full size); a link, as its sender's
    // preview has it (a click: the link, in a new tab).
    const picture = (id, cls, big = true) => {
      const img = document.createElement("img");
      if (cls) img.className = cls;
      img.alt = "a picture";
      v.media(id).then(url => {
        if (!url) { img.alt = "(a picture that cannot be had)"; return; }
        const end = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
        img.addEventListener("load", () => { if (end && log.contains(img)) log.scrollTop = log.scrollHeight; }, { once: true });
        img.src = url;
      });
      if (big) img.addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); if (img.src) lightbox(img.src); });
      return img;
    };
    const card = l => {
      const a = document.createElement("a");
      a.className = "card";
      if (/^https?:\/\//.test(l.url)) { a.href = l.url; a.target = "_blank"; a.rel = "noopener noreferrer"; }
      if (l.image && l.image.id) a.appendChild(picture(l.image.id, "", false));
      const t = document.createElement("div");
      for (const [cls, text] of [["site", l.site], ["title", l.title || l.url], ["desc", l.description]]) if (text) { const x = document.createElement("div"); x.className = cls; x.textContent = text; t.appendChild(x); }
      a.appendChild(t);
      return a;
    };
    // A voice message: ▶ and how long it is; a click plays it (again: stops it).
    const clock = secs => { const n = Math.round(secs || 0); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`; };
    const voiceEl = p => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "play"; b.title = "a voice message";
      const idle = () => { b.textContent = p.secs ? `▶ ${clock(p.secs)}` : "▶"; };
      idle();
      let audio = null;
      b.addEventListener("click", async e => {
        e.preventDefault(); e.stopPropagation();
        if (audio && !audio.paused) { audio.pause(); audio.currentTime = 0; return idle(); }
        if (!audio) {
          const url = await v.media(p.id);
          if (!url) { b.textContent = "(a voice message that cannot be had)"; b.disabled = true; return; }
          audio = new Audio(url);
          audio.addEventListener("ended", idle);
          audio.addEventListener("timeupdate", () => { if (!audio.paused) b.textContent = `■ ${clock(audio.currentTime)}${p.secs ? " / " + clock(p.secs) : ""}`; });
        }
        audio.play().catch(() => idle());
        b.textContent = "■";
      });
      return b;
    };
    // When a line came, as WoW's chat timestamps have it: its time (this browser's clock and way of writing one), dim,
    // before it; and over a day's first line, its day, when the log holds more than today.
    const hhmm = at => new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const dayOf = at => new Date(at).toDateString();
    const dayName = at => { const d = new Date(at); return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", ...(d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}) }); };
    const days = () => {   // the log's days: one over each line whose day is not the one before it's (today, for the first)
      let prev = dayOf(Date.now());
      for (const d of log.querySelectorAll(".line[data-at]")) {
        const at = Number(d.dataset.at), k = dayOf(at), was = d.previousElementSibling, has = was && was.classList.contains("day") ? was : null;
        if (k === prev) { if (has) has.remove(); continue; }
        prev = k;
        if (has) { if (has.textContent !== dayName(at)) has.textContent = dayName(at); continue; }
        const x = document.createElement("div");
        x.className = "day"; x.textContent = dayName(at);
        d.before(x);
      }
    };
    const lineEl = l => {   // a line: its text, then its pictures (or voice message) and its links' previews
      const d = document.createElement("div");
      d.className = "line " + l.scope + (l.mention ? " mention" : "") + (l.held ? " held" : "");
      if (l.held) d.title = "kept by your visit-client: it goes once they and you are both in the lobby";
      for (const p of parts(l)) {
        if (typeof p === "string") { d.appendChild(document.createTextNode(p)); continue; }
        const n = document.createElement("span");
        n.className = "who"; n.textContent = p.name; n.style.color = nameColor(p.key || "");
        if (p.k && !(p.k === v.key && !p.npc)) {   // 3.3: a click, for Whisper and Ignore (nameMenu)
          n.classList.add("act"); n.dataset.k = p.k; n.dataset.name = p.plain || "";
          if (p.npc) n.dataset.n = p.npc;
        }
        d.appendChild(n);
      }
      if (!l.mine && l.key && l.scope !== "note") { d.dataset.k = l.key; if (l.npc) d.dataset.n = l.npc; }   // (whose: to take out, ignored)
      if (l.at) { d.dataset.at = String(l.at); d.dataset.time = hhmm(l.at); }
      for (const p of l.media || []) d.appendChild(isVoice(p) ? voiceEl(p) : picture(p.id, "pic"));
      for (const c of l.links || []) d.appendChild(card(c));
      return d;
    };
    // A line from before this page, among the others from before it (above the window's first note), where its time
    // puts it: one can come after newer ones (a line kept for you, one from another day), and the days over the log
    // follow the lines' order. Over the day of the line it goes before, if that one has its day over it.
    const placeOld = d => {
      const at = Number(d.dataset.at), stop = intro && intro.parentNode === log ? intro : null;
      if (Number.isFinite(at)) for (let x = log.firstElementChild; x && x !== stop; x = x.nextElementSibling) {
        if (!x.classList.contains("line") || !(Number(x.dataset.at) > at)) continue;
        const m = x.previousElementSibling;
        return log.insertBefore(d, m && m.classList.contains("day") ? m : x);
      }
      return stop ? log.insertBefore(d, stop) : log.appendChild(d);
    };
    const add = l => {   // a line at the end; one from before this page (`old`), where its time puts it among those (placeOld)
      const end = log.scrollTop + log.clientHeight >= log.scrollHeight - 4, d = lineEl(l);
      if (l.old) placeOld(d); else log.appendChild(d);
      while (log.childElementCount > CHAT_LOG * 4) { log.firstChild.remove(); older = true; }
      days();
      if (end) log.scrollTop = log.scrollHeight;
      wake();
      return d;
    };
    // The chat's history (8.12), up the log: older lines, from the visit-client's database, as the log scrolls to
    // its top.
    let older = true, loadingOlder = false;
    log.addEventListener("scroll", () => {
      if (log.scrollTop > 24 || loadingOlder || !older) return;
      const first = log.querySelector(".line[data-at]"), before = first ? Number(first.dataset.at) : Date.now(), h = log.scrollHeight;
      loadingOlder = true;
      v.history({ before, limit: 40 }).then(r => {
        older = r.more;
        const top = log.firstChild;
        for (const l of r.lines) log.insertBefore(lineEl(l), top);
        days();
        log.scrollTop += log.scrollHeight - h;
      }, () => { older = false; }).finally(() => { loadingOlder = false; });
    });
    const note = text => add({ scope: "note", text });
    const fill = () => {   // the choices before the box: Say, a whisper to each, your room (anyone linked), each linked room, the lobby
      const linked = v.chatTargets;
      choices = [{ scope: "say" }, ...v.whisperTargets.map(o => ({ scope: "whisper", id: o.id, key: o.key, name: o.name })),
        ...(linked.length ? [yours()] : []), ...linked.map(o => ({ scope: "sim", key: o.key, name: o.name })), ...(v.lobbyChat ? [{ scope: "lobby" }] : []),
        ...(v.state === "online" ? v.groups.map(groupTo) : [])];
      if (!choices.some(c => same(c, to))) choices.splice(1, 0, to);   // (gone, or no lobby chat: said so if used)
      pick.innerHTML = choices.map((c, i) => `<option value="${i}">${esc(label(c))}:</option>`).join("");
      pick.value = String(choices.findIndex(c => same(c, to)));
      root.dataset.scope = to.scope;
      if (to.scope === "whisper") tell = to;
      headsetSay();
      try { sessionStorage.setItem("visit:chat-to", JSON.stringify(to)); } catch (e) { /* not kept */ }
    };
    // Tab: the box on to the next place a line can go now (back: the one before), in the choices' order, with one Tell:
    // whoever you last whispered with (the box's, else either way), else none. From a place gone: on from its kind's.
    const KINDS = ["say", "whisper", "sim", "lobby", "group"];
    const step = back => {
      const p = [to.scope === "whisper" && to, tell, last, told].map(t => t && whom(t)).find(Boolean), linked = v.chatTargets;
      const stops = [{ scope: "say" }, ...(p && v.lobbyChat ? [{ scope: "whisper", id: p.id, key: p.key, name: p.name }] : []),
        ...(linked.length ? [yours()] : []), ...linked.map(o => ({ scope: "sim", key: o.key, name: o.name })), ...(v.lobbyChat ? [{ scope: "lobby" }] : []),
        ...(v.state === "online" ? v.groups.map(groupTo) : [])];
      const n = stops.length, i = stops.findIndex(c => same(c, to)), k = KINDS.indexOf(to.scope);
      let j = i >= 0 ? (i + (back ? n - 1 : 1)) % n : -1;
      if (i < 0) for (let x = 0; x < n; x++) if (back ? KINDS.indexOf(stops[x].scope) < k : KINDS.indexOf(stops[x].scope) > k) { j = x; if (!back) break; }
      to = stops[j >= 0 ? j : back ? n - 1 : 0];
      fill();
    };
    const open = (start = "") => {
      if (document.pointerLockElement) document.exitPointerLock();
      typing = true; root.classList.add("typing"); fill(); wake();
      input.value = start; input.focus();
    };
    const close = () => { recStop(false); typing = false; root.classList.remove("typing"); input.value = ""; picker = null; picked = null; drawPicker(); pending = []; linkCard = null; linkSkip.clear(); drawAttach(); if (root.contains(document.activeElement)) document.activeElement.blur(); wake(); };
    // The one of `list` whose name starts `rest`, then a space (the longest), or, on Enter (`done`), is all of it or
    // the one name it starts.
    const named = (list, rest, done) => {
      const low = rest.toLowerCase(), all = done && low.trim();
      const o = list.filter(o => o.name && ((all && all === o.name.toLowerCase()) || low.startsWith(o.name.toLowerCase() + " "))).sort((a, b) => b.name.length - a.name.length)[0];
      if (o || !all) return o;
      const start = list.filter(o => o.name && o.name.toLowerCase().startsWith(all));
      return start.length === 1 ? start[0] : undefined;
    };
    // 3.3, ignoring (as WoW's): whom a name may be, those who spoke here lately first (a room's figures too), then
    // whoever is about; and a click on a name in the log, for Whisper and Ignore.
    const ignorable = () => {
      const by = new Map();
      const put = (key, npc, name) => { if (key && name && !(key === v.key && !npc) && !by.has(name.toLowerCase())) by.set(name.toLowerCase(), { key, npc: npc || null, name }); };
      for (const l of [...v.chatLog].reverse()) if (!l.mine && l.scope !== "note") put(l.key, l.npc, l.name);
      for (const o of [...v.whisperTargets, ...v.awayTargets, ...v.guests, ...v.chatTargets]) put(o.key, null, o.name);
      return [...by.values()];
    };
    const ignoreSay = who => {
      if (v.ignored.length >= MAX_IGNORED && !v.isIgnored(who.key, who.npc)) return note(`You ignore ${MAX_IGNORED} already: let one go first (/unignore name, or the panel)`);
      v.ignore(who.key, { npc: who.npc, name: who.name });
      note(`Ignoring ${who.name}: ${who.npc ? "its" : "their"} lines no longer reach you (/unignore ${who.name})`);
    };
    const nameMenu = document.createElement("div");
    nameMenu.className = "namemenu"; nameMenu.hidden = true;
    root.appendChild(nameMenu);
    log.addEventListener("click", e => {
      const n = e.target.closest(".who[data-k]");
      if (!n) return;
      e.stopPropagation();
      const key = n.dataset.k, npc = n.dataset.n || null, name = n.dataset.name || "";
      const w = !npc && [...v.whisperTargets, ...v.awayTargets].find(o => o.key === key);
      nameMenu.innerHTML = `<div class="hd">${esc(name || "someone")}${npc ? ` <span class="muted">· a room's figure</span>` : ""}</div>${w ? `<button type="button" data-m="w">Whisper</button>` : ""}<button type="button" data-m="ig">${v.isIgnored(key, npc) ? "Unignore" : "Ignore"}</button>`;
      Object.assign(nameMenu.dataset, { k: key, n: npc || "", name });
      const r = n.getBoundingClientRect(), rr = root.getBoundingClientRect();
      nameMenu.style.left = `${Math.max(0, r.left - rr.left)}px`; nameMenu.style.bottom = `${rr.bottom - r.top + 2}px`;
      nameMenu.hidden = false; wake();
    });
    nameMenu.addEventListener("click", e => {
      const b = e.target.closest("button[data-m]");
      if (!b) return;
      const key = nameMenu.dataset.k, npc = nameMenu.dataset.n || null, name = nameMenu.dataset.name;
      nameMenu.hidden = true;
      if (b.dataset.m === "w") {
        const o = [...v.whisperTargets, ...v.awayTargets].find(x => x.key === key);
        if (o) { to = { scope: "whisper", ...(o.id ? { id: o.id } : {}), key: o.key, name: o.name }; open(); }
      } else if (v.isIgnored(key, npc)) { v.unignore(key, { npc }); note(`${name}: no longer ignored`); }
      else ignoreSay({ key, npc, name });
    });
    addEventListener("pointerdown", e => { if (!nameMenu.hidden && !nameMenu.contains(e.target)) nameMenu.hidden = true; }, true);
    // Ignored now: their lines out of the log (they never come again).
    const onIgnored = () => {
      for (const d of log.querySelectorAll(".line[data-k]")) if (v.isIgnored(d.dataset.k, d.dataset.n || null)) d.remove();
      days();
    };
    // A name being typed after /w (/t, /tell, /whisper) or /room (/sim): the command, and the names it could be.
    const naming = () => {
      const m = /^\/(w|t|whisper|tell|room|sim)\s([\s\S]*)$/i.exec(input.value);
      if (!m) return null;
      const stem = m[2].toLowerCase(), rooms = /^(room|sim)$/i.test(m[1]);
      return { cmd: m[1], stem, names: (rooms ? v.chatTargets : [...v.whisperTargets, ...v.awayTargets]).map(o => o.name).filter(n => n && n.toLowerCase().startsWith(stem)) };
    };
    // Whom a line may mention (@): those linked with you, your visitors, those in the lobby; by the names they go by.
    const people = () => {
      const by = new Map(), put = (key, name) => { if (key && key !== v.key && name && !by.has(key)) by.set(key, v.userOf(key) || name); };
      for (const o of v.chatTargets) put(o.key, o.name);
      for (const x of v.visitors) put(x.visitor.key, x.visitor.name);
      for (const o of v.offices) if (!v.office || o.id !== v.office.id) put(o.key, o.name);
      for (const f of v.figuresHere) if (v.mayTalk({ ...f, room: v.roomHere.key })) put("figure:" + f.id, f.name);   // 3.3 (#48): the room's figures you may talk to
      return [...new Set(by.values())].sort((a, b) => a.localeCompare(b));
    };
    // 3.3 (#48): the figure of the room you are in that a line mentions (@ and its name, as a word of its own), if any:
    // the line is meant for it.
    const mentionedFigure = text => v.figuresHere.filter(f => v.mayTalk({ ...f, room: v.roomHere.key })).find(f => new RegExp("(^|[\\s([{\"'])@" + f.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![\\p{L}\\p{N}_])", "iu").test(text));
    // The names the box could take where it is typed (over it, to pick: up and down, Tab or Enter, or a click): after
    // /w or /room, the name it is to; after @, someone the line mentions. Picked, it stays shut until the box changes.
    let picker = null, picked = null;
    const pickerFor = () => {
      if (input.value === picked) return null;
      const n = naming();
      if (n) return n.names.length ? { items: n.names.slice(0, 8), take: name => { input.value = `/${n.cmd} ${name}`; } } : null;
      const caret = input.selectionStart == null ? input.value.length : input.selectionStart, m = /(^|\s)@([^@\n]{0,24})$/.exec(input.value.slice(0, caret));
      if (!m) return null;
      const items = people().filter(p => p.toLowerCase().startsWith(m[2].toLowerCase())).slice(0, 8);
      return items.length ? { items, take: name => {
        const from = caret - m[2].length, at = from + name.length + 1;
        input.value = input.value.slice(0, from) + name + " " + input.value.slice(caret);
        input.setSelectionRange(at, at);
      } } : null;
    };
    const drawPicker = () => { names.innerHTML = picker ? picker.items.map((n, i) => `<button type="button" data-i="${i}" class="${i === picker.at ? "on" : ""}">${esc(n)}</button>`).join("") : ""; };
    const hint = () => {
      const p = pickerFor(), same = p && picker && p.items.join("\n") === picker.items.join("\n");
      picker = p ? Object.assign(p, { at: same ? picker.at : 0 }) : null;
      drawPicker();
    };
    const take = i => {
      if (!picker || !picker.items[i]) return;
      picker.take(picker.items[i]);
      picked = input.value; picker = null;
      drawPicker();
    };
    names.addEventListener("mousedown", e => { const b = e.target.closest("button[data-i]"); if (b) { e.preventDefault(); take(Number(b.dataset.i)); input.focus(); } });
    const help = "/s say (everyone who can see you) · /w name (a whisper) · /r reply · /room (your room's chat) · /room name (a linked room's) · /1 the lobby · /g world (a world's chat; /2, /3… yours in order) · /voice (🎧: with whom the box talks to: a person, a world's huddle, a room) · /ignore name, /unignore name (or click a name) · @ a name: mention them";
    // Pictures for the next line (pasted, or dropped on the window: each kept by the visit-client at once, `media`
    // once kept), and a link's preview (made as the link is typed; ✕: none for it).
    let pending = [], linkCard = null, linkTimer = null;
    const linkSkip = new Set();
    const chip = (inside, drop) => {
      const c = document.createElement("div");
      c.className = "chip";
      c.appendChild(inside);
      const x = document.createElement("button");
      x.type = "button"; x.className = "drop"; x.textContent = "✕"; x.title = "not this";
      x.addEventListener("click", () => { drop(); drawAttach(); input.focus(); });
      c.appendChild(x);
      return c;
    };
    const drawAttach = () => {
      attach.textContent = "";
      for (const p of pending) {
        const el = document.createElement(p.voice ? "span" : "img");
        if (p.voice) { el.className = "clip"; el.textContent = `🎙 ${clock(p.secs)}`; } else el.src = p.url;
        const c = chip(el, () => { pending = pending.filter(q => q !== p); });
        if (!p.media) c.classList.add("busy");
        attach.appendChild(c);
      }
      if (linkCard && !linkCard.loading) attach.appendChild(chip(card(linkCard), () => { linkSkip.add(linkCard.url); linkCard = null; }));
    };
    const addPictures = files => {
      if (!typing) open();
      for (const f of files) {
        if (pending.length >= 4) { note("At most 4 pictures in a line"); break; }
        const p = { url: URL.createObjectURL(f), media: null };
        p.ready = v.share(f).then(m => { p.media = m; drawAttach(); return m; },
          e => { pending = pending.filter(q => q !== p); drawAttach(); note(`That picture cannot be shared: ${e.message || e}`); return null; });
        pending.push(p);
      }
      drawAttach();
    };
    input.addEventListener("paste", e => {
      const files = [...((e.clipboardData && e.clipboardData.items) || [])].filter(i => i.kind === "file" && /^image\//.test(i.type)).map(i => i.getAsFile()).filter(Boolean);
      if (!files.length) return;
      if (!(e.clipboardData.getData("text/plain") || "").trim()) e.preventDefault();
      addPictures(files);
    });
    root.addEventListener("dragover", e => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
    root.addEventListener("drop", e => {
      const files = [...((e.dataTransfer && e.dataTransfer.files) || [])].filter(f => /^image\//.test(f.type));
      if (files.length) { e.preventDefault(); addPictures(files); }
    });
    const linkLook = () => {   // the first link in the box: its preview, made by the visit-client
      const m = /https?:\/\/[^\s<>"']+/.exec(input.value), url = m ? m[0].replace(/[.,;:!?)\]]+$/, "") : null;
      if (!url || linkSkip.has(url)) { if (linkCard) { linkCard = null; drawAttach(); } return; }
      if (linkCard && linkCard.url === url) return;
      linkCard = { url, loading: true };
      drawAttach();
      v.preview(url).then(p => {
        if (!linkCard || linkCard.url !== url) return;
        linkCard = p ? { ...p, url } : null;
        if (!p) linkSkip.add(url);
        drawAttach();
      }, () => { if (linkCard && linkCard.url === url) { linkCard = null; drawAttach(); } });
    };
    // What was shared (🖼): the pictures, or the links, in the conversation the box talks to, newest first.
    let sharedKind = "media";
    const conversation = () => to.scope === "sim" ? "room:" + to.key : to.scope === "whisper" ? "person:" + (to.key || "id:" + to.id) : to.scope === "lobby" ? "lobby" : to.scope === "group" ? "group:" + to.key : v.roomHere.conversation;
    const sharedDraw = async more => {
      const c = conversation(), title = c === "lobby" ? "the lobby" : c.startsWith("person:") ? `you and ${to.name || "them"}`
        : c === "room:" + v.key ? "your room" : `${v.nameOf(c.slice(5)) || "their"}'s room`;
      if (!more) shared.innerHTML = `<div class="tabs"><b>Shared in ${esc(title)}</b><button data-k="media" class="${sharedKind === "media" ? "on" : ""}">Pictures</button><button data-k="voice" class="${sharedKind === "voice" ? "on" : ""}">Voice</button><button data-k="links" class="${sharedKind === "links" ? "on" : ""}">Links</button><button data-k="close" title="close">✕</button></div><div class="items"></div>`;
      const items = shared.querySelector(".items");
      const moreButton = items.querySelector("button.more");
      if (moreButton) moreButton.remove();
      try {
        const r = await v.shared(c, sharedKind, { before: more ? Number(items.dataset.before) : undefined });
        let box = items.querySelector(".grid");
        if (sharedKind === "media" && !box && r.items.length) { box = document.createElement("div"); box.className = "grid"; items.appendChild(box); }
        const voiceRow = i => { const d = document.createElement("div"); d.textContent = `${i.mine ? "you" : i.name || "someone"}: `; d.appendChild(voiceEl({ id: i.id, secs: 0 })); return d; };
        for (const i of r.items) (sharedKind === "media" ? box : items).appendChild(sharedKind === "media" ? picture(i.id, "") : sharedKind === "voice" ? voiceRow(i) : card(linksFrom([i])[0] || { url: i.url }));
        if (!r.items.length && !more) items.textContent = sharedKind === "media" ? "No pictures shared here yet" : sharedKind === "voice" ? "No voice messages here yet" : "No links shared here yet";
        if (r.items.length) items.dataset.before = String(r.items[r.items.length - 1].at);
        if (r.more) { const b = document.createElement("button"); b.className = "more"; b.textContent = "More"; b.dataset.k = "more"; items.appendChild(b); }
      } catch (e) { items.textContent = `Not available: ${e.message || e}`; }
    };
    gallery.addEventListener("click", () => { shared.classList.toggle("on"); if (shared.classList.contains("on")) sharedDraw(false); wake(); input.focus(); });
    shared.addEventListener("click", e => {
      const b = e.target.closest("button[data-k]");
      if (!b) return;
      if (b.dataset.k === "close") shared.classList.remove("on");
      else if (b.dataset.k === "more") sharedDraw(true);
      else { sharedKind = b.dataset.k; sharedDraw(false); }
    });
    // 🎧 follows the box (4.0, #58): a whisper's person, a voice with them alone (it rings for them); a world, its huddle
    // (start one: it rings for everyone in it; or join the one going on); a room, its voice (as before). Say and the
    // lobby have none: who is near you hears you (🎙).
    const voiceAim = () => {
      if (to.scope === "whisper") { const p = whom(to); return { kind: "talk", key: (p && p.key) || to.key || "", name: (p && p.name) || to.name || "them" }; }
      if (to.scope === "group") { const g = v.groups.find(x => x.name.toLowerCase() === String(to.key || to.name).toLowerCase()); return g ? { kind: "huddle", group: g.name } : null; }
      if (to.scope === "sim" && to.key) return { kind: "room", key: to.key, name: room(to) };
      return null;
    };
    const huddleOf = g => v.huddles.find(h => h.group.toLowerCase() === String(g).toLowerCase());
    const asks = new Map(), talked = new Set();   // huddles asking you in (a ring above the box); talks seen (their end said)
    let starting = null;   // the world whose huddle this page just started (said as a huddle, not as your room's voice)
    const headsetSay = () => {
      const a = voiceAim(), ringing = v.calls.some(c => c.voice && c.state === "ringing") || asks.size > 0;
      headset.hidden = !a && !ringing;
      headset.classList.toggle("ring", ringing);
      let on = false, title = "";
      if (!a) title = "someone wants to talk: 🎧 Answer above";
      else if (a.kind === "talk") {
        const c = v.calls.find(x => x.key === a.key);
        on = !!c && c.state !== "ringing";
        title = !c ? (v.canTalk(a.key) ? `talk with ${a.name}, voice alone, wherever you both are: it rings for them` : `${a.name} cannot talk now (offline, or an older Visit)`)
          : c.state === "ringing" ? `${a.name} wants to talk (click: answer)` : c.state === "on" ? `talking with ${a.name} (click: hang up)` : `calling ${a.name}… (click: give up)`;
      } else if (a.kind === "huddle") {
        const h = huddleOf(a.group), inH = v.inHuddle, here = !!inH && inH.group.toLowerCase() === a.group.toLowerCase();
        on = here;
        title = here ? (inH.mine ? `your huddle in ${a.group} (click: end it)` : `in the huddle in ${a.group} (click: leave it)`)
          : h && !h.mine ? `${h.from.name}'s huddle in ${a.group}: click to join` : `start a huddle in ${a.group}: it rings for everyone in it who is online`;
      } else {
        on = !!v.roomVoice && v.roomVoice.key === a.key;
        title = on ? `in ${a.name}'s voice (click: leave it)` : `join ${a.name}'s voice: everyone in it hears everyone else, wherever they are`;
      }
      headset.classList.toggle("on", on);
      headset.title = title;
    };
    const voiceToggle = () => {
      const a = voiceAim();
      if (!a) { const c = v.calls.find(x => x.voice && x.state === "ringing"); if (c) answer(c.key); else { const h = [...asks.values()].pop(); if (h) joinAsk(h.group); } return; }
      if (a.kind === "talk") {
        const c = v.calls.find(x => x.key === a.key);
        if (c && c.state === "ringing") return answer(c.key);
        if (c) return v.hangUp(c.key);
        if (!v.talk(a.key)) note(!v.lobbyVoice ? "This lobby carries no voice yet (an older lobby)" : `${a.name} cannot talk now: offline, or on a Visit before 4.0`);
        return;
      }
      if (a.kind === "huddle") {
        const h = huddleOf(a.group), inH = v.inHuddle;
        if (inH && inH.group.toLowerCase() === a.group.toLowerCase()) { if (inH.mine) v.huddle(a.group, false); else v.joinVoice(null); return; }
        if (h && !h.mine) return joinAsk(h.group);
        starting = a.group;
        if (!v.huddle(a.group, true)) { starting = null; note(!v.lobbyVoice ? "This lobby carries no voice yet (an older lobby)" : `No huddle in ${a.group} now`); }
        return;
      }
      if (v.roomVoice && v.roomVoice.key === a.key) return void v.joinVoice(null);
      if (!v.joinVoice(a.key)) note(v.bannedBy.some(b => b.key === a.key && b.voice) ? `${a.name[0].toUpperCase() + a.name.slice(1)} keeps you out of its voice` : `${a.name}: you are not linked with them now`);
    };
    const answer = key => v.pickUp(key).catch(e => note(`The microphone: ${e.message || e}`));
    // Huddles started in your worlds lately ask you in (the ring above the box) until joined, dismissed or over.
    const joinAsk = group => { asks.delete(group.toLowerCase()); if (!v.joinHuddle(group)) note(`The huddle in ${group} is over`); voiceLine(); };
    // What rings above the box: someone who wants to talk, a huddle asking you in. It stays until you answer.
    const ringLine = () => {
      for (const [k, h] of asks) if (Date.now() - h.at > HUDDLE_ASK_MS) asks.delete(k);
      const calls = v.calls.filter(c => c.voice && c.state === "ringing");
      const html = calls.map(c => `<div class="ask">🎧 <b>${esc(c.name)}</b> wants to talk<button type="button" class="go" data-v="answer" data-key="${esc(c.key)}">🎧 Answer</button><button type="button" data-v="decline" data-key="${esc(c.key)}">Not now</button></div>`)
        .concat([...asks.values()].map(h => `<div class="ask">🎧 <b>${esc(h.from.name)}</b> started a huddle in <b>${esc(h.group)}</b><button type="button" class="go" data-v="join" data-g="${esc(h.group)}">🎧 Join</button><button type="button" data-v="dismiss" data-g="${esc(h.group)}">✕</button></div>`)).join("");
      if (html !== ringDrawn) { ringDrawn = html; ring.innerHTML = html; }
      if (html) wake();
      ringTone(calls.length > 0 ? "talk" : asks.size > 0 ? "huddle" : null);
    };
    let ringDrawn = "", toneTimer = 0, toneKind = null, toneAt = 0;
    const ringTone = kind => {   // a phone's ring while someone wants to talk (every 2 s); a huddle's, twice
      if (kind === toneKind) return;
      toneKind = kind; clearInterval(toneTimer); toneTimer = 0;
      if (!kind) return;
      const once = () => { if (kind === "huddle" && Date.now() - toneAt > 4500) { clearInterval(toneTimer); return; } ringSound(kind); };
      toneAt = Date.now(); once();
      toneTimer = setInterval(once, kind === "talk" ? 2000 : 2200);
    };
    ring.addEventListener("click", e => {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      if (b.dataset.v === "answer") answer(b.dataset.key);
      else if (b.dataset.v === "decline") v.hangUp(b.dataset.key);
      else if (b.dataset.v === "join") joinAsk(b.dataset.g);
      else if (b.dataset.v === "dismiss") { asks.delete(b.dataset.g.toLowerCase()); voiceLine(); }
    });
    let voiceDrawn = "", voiceIn = null;
    const voiceLine = () => {   // what you are in: talking with someone (or calling them), a huddle, a room's voice
      const rv = v.roomVoice, inH = v.inHuddle;
      const who = rv && !rv.joining ? rv.members.map(x => `${x.speaking ? "🔊 " : ""}${esc(x.key === v.key ? "you" : x.name)}`).join(" · ") : `<span class="muted">joining…</span>`;
      const mic = v.mic ? "" : ` <span class="muted">(your microphone is off)</span>`;
      const talks = v.calls.filter(c => c.voice && c.state !== "ringing").map(c => `<div>🎧 ${c.state === "on" ? `Talking with <b>${c.speaking ? "🔊 " : ""}${esc(c.name)}</b>` : c.state === "answering" ? `<b>${esc(c.name)}</b>: connecting…` : `Calling <b>${esc(c.name)}</b>…`}<button type="button" data-v="hangup" data-key="${esc(c.key)}">${c.state === "calling" ? "Give up" : "Hang up"}</button></div>`);
      const room = !rv ? "" : inH ? `<div>🎧 Huddle in <b>${esc(inH.group)}</b>: ${who}<button type="button" data-v="leave" title="${inH.mine ? "end it: everyone out" : "out of it"}">${inH.mine ? "End" : "Leave"}</button></div>`
        : `<div>🎧 ${esc(rv.key === v.key ? "Your room" : `${rv.name}'s room`)}: ${who}${mic}<button type="button" data-v="leave" title="out of this voice">Leave</button></div>`;
      const html = talks.join("") + room;
      if (html !== voiceDrawn) { voiceDrawn = html; voice.innerHTML = html; }
      ringLine();
      headsetSay();
    };
    voice.addEventListener("click", e => {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      if (b.dataset.v === "hangup") v.hangUp(b.dataset.key);
      else if (b.dataset.v === "leave") { const inH = v.inHuddle; if (inH && inH.mine) v.huddle(inH.group, false); else v.joinVoice(null); }
    });
    headset.addEventListener("click", () => { voiceToggle(); input.focus(); });
    const onCall = c => {   // a talk that ended: said why, once (a phone's calls are the room's)
      if (c.state === "ended" && !["hung up"].includes(c.why) && v.calls.every(x => x.key !== c.key)) {
        const why = { declined: "not now, they said", "no answer": "no answer", missed: "you missed it", "gave up": "they gave up" }[c.why] || c.why;
        if (talked.has(c.key)) note(`🎧 ${c.name}: ${why}`);
      }
      if (c.state === "ended") talked.delete(c.key); else if (isObj(c) && v.calls.some(x => x.key === c.key && x.voice)) talked.add(c.key);
      voiceLine();
    };
    const onHuddle = h => {
      const k = String(h.group || "").toLowerCase();
      if (h.joined || h.left) return voiceLine();
      if (h.on && h.mine) { if (starting && starting.toLowerCase() === k) note(`🎧 Your huddle in ${h.group}: it rang for everyone in it who is online; whoever joins hears you, and you them`); starting = null; }
      else if (h.on && h.fresh) { asks.set(k, h); toneKind = null; note(`🎧 ${h.from.name} started a huddle in ${h.group}: 🎧 Join, by the box (or /g ${h.group} and 🎧)`); }
      else if (!h.on) { if (asks.delete(k) || (v.inHuddle && v.inHuddle.group.toLowerCase() === k)) note(`🎧 The huddle in ${h.group} is over`); }
      voiceLine();
    };
    // A command at the start of the box, once a space follows it (or on Enter): where lines go from now. True: it was
    // one, and what follows it is the line.
    const command = done => {
      const m = /^\/(\S+)(?:\s([\s\S]*))?$/.exec(input.value);
      if (!m) { if (done) input.value = ""; return false; }
      if (m[2] === undefined && !done) return false;
      const cmd = m[1].toLowerCase();
      let rest = m[2] || "", next = null;
      if (cmd === "voice") { if (!done && m[2] === undefined) return false; voiceToggle(); input.value = rest; return !!rest.trim(); }
      if (cmd === "s" || cmd === "say") next = { scope: "say" };
      else if (cmd === "1" || cmd === "l" || cmd === "lobby") next = v.lobbyChat ? { scope: "lobby" } : (done && note("No lobby chat: this lobby does not carry it, or you are not in it"), null);
      else if (cmd === "r" || cmd === "reply") next = last || told || (done && note("Nobody has whispered to you yet"), null);
      else if (/^[2-9]$/.test(cmd)) { const g = v.groups[Number(cmd) - 2]; next = g ? groupTo(g) : (done && note(`/${cmd}: your ${["second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"][Number(cmd) - 2]} world's chat, and you are in ${v.groups.length}`), null); }
      else if (cmd === "g" || cmd === "group") {
        const g = named(v.groups, rest, done) || (done && !rest.trim() && v.groups.length === 1 ? v.groups[0] : null);
        if (g) { next = groupTo(g); rest = rest.slice(g.name.length + 1); }
        else if (!done) return false;
        else note(v.groups.length ? `/${cmd} and one of your worlds: ${v.groups.map(x => x.name).join(", ")}` : "You are in no world: the panel finds them");
      }
      else if (cmd === "ignore" || cmd === "unignore") {   // 3.3: by name; alone, whom you ignore
        if (!done) return false;
        const off = cmd === "unignore", who = rest.trim() ? named(off ? v.ignored : ignorable(), rest, true) : null;
        if (!rest.trim()) note(v.ignored.length ? `You ignore: ${v.ignored.map(i => i.name || "someone").join(", ")} (/unignore name; the panel clears them)` : "You ignore nobody (/ignore name, or click a name)");
        else if (!who) note(off ? `You do not ignore anyone called ${rest.trim()}` : `Nobody called ${rest.trim()} has said anything here, or is in the lobby`);
        else if (off) { v.unignore(who.key, { npc: who.npc }); note(`${who.name}: no longer ignored`); }
        else ignoreSay(who);
      }
      else if (["w", "t", "whisper", "tell", "room", "sim"].includes(cmd)) {
        const rooms = cmd === "room" || cmd === "sim", o = named(rooms ? v.chatTargets : v.whisperTargets, rest, done) || (!rooms && named(v.awayTargets, rest, done));
        if (o) { next = rooms ? { scope: "sim", key: o.key, name: o.name } : { scope: "whisper", id: o.id, key: o.key, name: o.name }; rest = rest.slice(o.name.length + 1); }
        else if (!done) return false;   // a name still being typed
        else if (rooms && !rest.trim()) next = yours();
        else note(!rest.trim() ? `/${cmd} and a name` : rooms ? `You are not linked with anyone called ${rest.trim()}` : `Nobody called ${rest.trim()} is in the lobby (or their Visit takes no whispers)`);
      } else if (done) note(`Unknown: /${cmd}. ${help}`);
      if (next) { to = next; fill(); input.value = rest; return true; }
      if (done) input.value = "";
      return false;
    };
    input.addEventListener("input", () => { command(false); hint(); clearTimeout(linkTimer); linkTimer = setTimeout(linkLook, 500); });
    input.addEventListener("click", hint);
    input.addEventListener("keyup", e => { if (/^(ArrowLeft|ArrowRight|Home|End)$/.test(e.key)) hint(); });
    input.addEventListener("keydown", e => {
      e.stopPropagation();   // the page's own keys (walking) are not for this box
      if (e.key === "Escape" && recording) { e.preventDefault(); recStop(false); }
      else if (e.key === "Escape") { e.preventDefault(); if (picker) { picked = input.value; picker = null; drawPicker(); } else close(); }
      else if (picker && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        picker.at = (picker.at + (e.key === "ArrowDown" ? 1 : picker.items.length - 1)) % picker.items.length;
        drawPicker();
      } else if (picker && (e.key === "Tab" || e.key === "Enter") && !e.shiftKey) { e.preventDefault(); take(picker.at); }
      else if (e.key === "Tab" && !e.ctrlKey && !e.altKey && !e.metaKey && !e.isComposing) { e.preventDefault(); if (!input.value.startsWith("/")) step(e.shiftKey); }
    });
    pick.addEventListener("change", () => { to = choices[Number(pick.value)] || { scope: "say" }; fill(); input.focus(); });
    // A line said where the box talks to: false (and why, said) if it could not be.
    const send = (t, media, links) => {
      if (to.scope === "figure" || to.scope === "aim") {   // 3.3 (#48): to a figure alone, or aloud, meant for your target
        if (to.room && to.room !== v.roomHere.key) { note(`${to.name} is not in the room you are in`); to = { scope: "say" }; fill(); return false; }
        if (to.scope === "figure" && (media.length || links.length)) { note("A whisper to a figure is text only"); return false; }
        if (v.say(t, to.scope === "figure" ? { whisper: { kind: "figure", id: to.id, name: to.name } } : { target: { kind: to.kind, id: to.id } }, { media, links })) return true;
        note(`Too fast: at most ${CHATS} lines every ${CHAT_WINDOW / 1000} s`);
        return false;
      }
      if (to.scope === "say") {   // @ a figure of the room you are in: meant for it
        const f = mentionedFigure(t);
        if (f) { if (v.say(t, { target: { kind: "figure", id: f.id } }, { media, links })) return true; note(`Too fast: at most ${CHATS} lines every ${CHAT_WINDOW / 1000} s`); return false; }
      }
      if (to.scope === "group") {
        if (media.length || links.length) { note("Pictures, voice messages and links go to rooms, and to those you are linked with: not through the lobby"); return false; }
        if (!v.groups.some(g => g.name.toLowerCase() === to.key)) { note(`You are no longer in ${to.name}`); to = { scope: "say" }; fill(); return false; }
        if (v.say(t, { group: to.name })) return true;
        note(v.state !== "online" ? "Not in the lobby now" : `Too fast: at most ${CHATS} lines every ${CHAT_WINDOW / 1000} s`);
        return false;
      }
      const p = to.scope === "whisper" && whom(to), away = to.scope === "whisper" && !p && /^[0-9a-f]{64}$/.test(to.key || "");   // (away: kept for them)
      if (v.say(t, to.scope === "say" ? undefined : to.scope === "lobby" ? "lobby" : to.scope === "whisper" ? (away ? { whisper: to.key, name: to.name } : { whisper: p ? p.id : "" })
        : to.key === v.key ? "room" : { key: to.key }, { media, links })) {
        if (away) note(`${to.name || "They"}: not in the lobby now. Your visit-client keeps it, and passes it on once you are both in it`);
        if (v.unseen.conversations.some(c => c.conversation === conversation())) v.read(conversation());   // answered: what came there, read
        return true;
      }
      if (away) note(media.length || links.length ? `${to.name} is away: a whisper kept for them is text only (pictures and voice messages go to those you are linked with)`
        : `Too fast: at most ${CHATS} lines every ${CHAT_WINDOW / 1000} s`);
      else if ((media.length || links.length) && (to.scope === "lobby" || (to.scope === "whisper" && p && !v.chatTargets.some(o => o.key === p.key)))) note("Pictures, voice messages and links go to rooms, and to those you are linked with: not through the lobby");
      else if (to.scope === "whisper" && !p) { note(`${to.name} is not in the lobby now`); to = { scope: "say" }; fill(); }
      else if (to.scope === "sim" && to.key !== v.key && !v.chatTargets.some(o => o.key === to.key)) { note(`${to.name}'s room: you are not linked with them now`); to = { scope: "say" }; fill(); }
      else if ((to.scope === "lobby" || to.scope === "whisper") && !v.lobbyChat) { note("No lobby chat: this lobby does not carry it, or you are not in it"); to = { scope: "say" }; fill(); }
      else note(`Too fast: at most ${CHATS} lines every ${CHAT_WINDOW / 1000} s`);
      return false;
    };
    form.addEventListener("submit", e => {
      e.preventDefault();
      if (recording) { recording.send = true; return recStop(true); }   // the voice message first: then the line, with it
      const cmd = input.value.startsWith("/");
      if (cmd && !command(true)) return;   // not a line (said why)
      const t = input.value.trim(), pics = pending.slice(), links = linkCard && !linkCard.loading && t.includes(linkCard.url) ? [linkCard] : [];
      if (!t && !pics.length) { if (!cmd) close(); return; }   // (a command alone: where the next line goes, the box still open)
      if (!pics.length) { if (send(t, [], links)) close(); return; }
      close();   // its pictures: said once each is kept
      Promise.all(pics.map(p => p.ready)).then(ms => { const media = ms.filter(Boolean); if (media.length || t) send(t, media, links); });
    });
    root.addEventListener("focusout", () => setTimeout(() => { if (typing && !root.contains(document.activeElement)) { typing = false; root.classList.remove("typing"); wake(); } }, 0));
    // The mailbox (8.9): 📬 and how many lines meant for you came while nobody looked (whispered, said in your room's
    // chat, mentioning you), shown even when the window has faded; ⏳, whispers you left for someone away, still kept.
    // A click: those lines (they are read then; a click on one: the box talks there) and those whispers (✕: taken
    // back).
    let mailLines = [], mailMore = 0;
    const when = at => dayOf(at) === dayOf(Date.now()) ? hhmm(at) : new Date(at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    const replyTo = l => l.scope === "whisper" ? { scope: "whisper", id: l.id, key: l.key, name: l.name }
      : l.scope === "sim" ? (l.to && l.to.key && l.to.key !== v.key ? { scope: "sim", key: l.to.key, name: l.to.name } : yours()) : l.scope === "lobby" ? { scope: "lobby" }
      : l.scope === "group" && l.group ? { scope: "group", key: l.group.toLowerCase(), name: l.group } : { scope: "say" };
    const mailDraw = () => {
      mailbox.innerHTML = `<div class="tabs"><b>📬 For you</b><button type="button" data-m="close" title="close">✕</button></div>`;
      if (!mailLines.length && !v.held.length) mailbox.insertAdjacentHTML("beforeend", `<div class="when">Nothing new</div>`);
      mailLines.forEach((l, i) => {
        const d = lineEl(l);
        d.dataset.i = String(i); d.title = "click: answer there";
        if (l.at) d.dataset.time = when(l.at);   // (its day too, when not today)
        mailbox.appendChild(d);
      });
      if (mailMore > 0) mailbox.insertAdjacentHTML("beforeend", `<div class="when">and ${mailMore} more, older: up the chat</div>`);
      if (v.held.length) mailbox.insertAdjacentHTML("beforeend", `<div class="tabs" style="margin-top:6px"><b>⏳ Kept for someone away: it goes once you are both in the lobby</b></div>`
        + v.held.map(h => `<div class="held"><span>To [${esc(h.name || "them")}]: ${esc(h.text)}</span><button type="button" data-h="${esc(h.id)}" title="take it back">✕</button></div>`).join(""));
    };
    const mailSay = () => {
      const n = v.unseen.count, h = v.held.length;
      mail.classList.toggle("on", n > 0 || h > 0);
      mail.textContent = [n ? `📬 ${n}` : "", h ? `⏳ ${h}` : ""].filter(Boolean).join(" · ");
      mail.title = [n ? `${n} line${n === 1 ? "" : "s"} for you, not read yet` : "", h ? `${h} whisper${h === 1 ? "" : "s"} kept for someone away` : ""].filter(Boolean).join("; ") + " (click: see)";
      if (mailbox.classList.contains("on")) mailDraw();
    };
    mail.addEventListener("click", () => {
      if (mailbox.classList.toggle("on")) {
        mailLines = v.unseen.lines.slice(); mailMore = Math.max(0, v.unseen.count - mailLines.length);
        shared.classList.remove("on");
        mailDraw();
        if (v.unseen.count) v.read();
      }
      wake();
    });
    mailbox.addEventListener("click", e => {
      const b = e.target.closest("button[data-m], button[data-h]");
      if (b) { if (b.dataset.h) v.unhold(b.dataset.h); else mailbox.classList.remove("on"); return; }
      const d = e.target.closest(".line[data-i]");
      if (!d || e.target.closest("button.play, img, a")) return;   // (its voice message, a picture, a link: as in the chat)
      mailbox.classList.remove("on");
      to = replyTo(mailLines[Number(d.dataset.i)]);
      open();
    });
    const onHeldSent = h => note(`Passed on to ${h.name || "them"}, now in the lobby: ${h.text}`);
    // Whether someone is looking at this chat: the page shown and focused, and a key, a click or the pointer within
    // LOOKING. Your visit-client is told: a line meant for you that comes meanwhile is seen, else it is in the mailbox.
    let stirred = Date.now();
    const look = () => v.attention(document.visibilityState === "visible" && document.hasFocus() && Date.now() - stirred < LOOKING);
    const stir = () => { const was = Date.now() - stirred >= LOOKING; stirred = Date.now(); if (was) look(); };
    const lookTimer = setInterval(look, 10000);
    addEventListener("focus", look); addEventListener("blur", look); document.addEventListener("visibilitychange", look);
    for (const t of STIRS) addEventListener(t, stir, { capture: true, passive: true });
    // A voice message (🎙): recorded here, at most VOICE_SECS (🎙 again, or Enter: done; Esc: none), then by the box as a
    // picture is, and said with the line.
    let recording = null;
    const recSay = () => { recBtn.classList.toggle("on", !!recording); recBtn.textContent = recording ? `⏺ ${clock((Date.now() - recording.at) / 1000)}` : "🎙"; };
    const recStop = keep => {
      const r = recording;
      if (!r) return;
      recording = null; clearInterval(r.timer);
      Object.assign(r, { keep, secs: Math.min(VOICE_SECS, (Date.now() - r.at) / 1000) });
      if (r.rec.state !== "inactive") r.rec.stop(); else r.done();
      recSay();
    };
    const addVoice = (blob, secs) => {
      if (!typing) open();
      pending = pending.filter(p => !p.voice);   // one a line
      if (pending.length >= 4) return note("At most 4 pictures in a line");
      const p = { voice: true, secs, url: null, media: null };
      p.ready = v.share(blob).then(m => { p.media = { ...m, secs }; drawAttach(); return p.media; },
        e => { pending = pending.filter(q => q !== p); drawAttach(); note(`That voice message cannot be shared: ${e.message || e}`); return null; });
      pending.push(p);
      drawAttach();
    };
    const recStart = async () => {
      if (!window.MediaRecorder || !navigator.mediaDevices) return note("This browser cannot record a voice message");
      const kind = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find(k => MediaRecorder.isTypeSupported(k));
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
      catch (e) { return note(`No microphone for a voice message: ${e.message || e}`); }
      if (recording) { for (const t of stream.getTracks()) t.stop(); return; }
      const rec = new MediaRecorder(stream, { ...(kind ? { mimeType: kind } : {}), audioBitsPerSecond: 32000 }), chunks = [];
      const r = recording = { rec, at: Date.now(), keep: false, secs: 0, send: false, over: false };
      r.timer = setInterval(() => { recSay(); if (Date.now() - r.at >= VOICE_SECS * 1000) recStop(true); }, 250);
      r.done = () => {
        if (r.over) return;
        r.over = true;
        for (const t of stream.getTracks()) t.stop();
        if (!r.keep || !chunks.length) return;
        addVoice(new Blob(chunks, { type: (rec.mimeType || kind || "audio/webm").split(";")[0] }), r.secs);
        if (r.send) form.requestSubmit();
      };
      rec.addEventListener("dataavailable", e => { if (e.data && e.data.size) chunks.push(e.data); });
      rec.addEventListener("stop", () => r.done());
      rec.start(1000);
      recSay();
    };
    recBtn.addEventListener("click", () => { if (recording) recStop(true); else recStart(); input.focus(); });
    const key = e => {   // Enter (or /), anywhere but a box of the page's own: type a line
      if (e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.isComposing || typing) return;
      const t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(t.tagName))) return;
      if (e.key === "Enter") { e.preventDefault(); open(); }
      else if (e.key === "/") { e.preventDefault(); open("/"); }
    };
    const onChat = l => {
      add(l);
      if (!quiet && !l.mine && !l.old && l.scope !== "note") chatSound(l.mention ? "whisper" : l.scope);   // a mention chimes as a whisper does
      heard(l);
    };
    const heard = l => {   // whom /r answers: who last whispered to you, else whom you last whispered; Tab's Tell: either
      if (l.scope !== "whisper") return;
      if (!l.mine && l.id) tell = last = { scope: "whisper", id: l.id, key: l.key || null, name: l.name };
      else if (l.mine && l.to && l.to.id) tell = told = { scope: "whisper", id: l.to.id, key: l.to.key || null, name: l.to.name };
    };
    const onError = e => { if (e && (["say", "whisper", "chat", "voice_join", "groups", "admin", "about", "browser_allow", "browser_revoke"].includes(e.ref) || String(e.ref || "").startsWith("group_"))) note(String(e.message || e.code)); };
    // The lobby (LOBBY.md): its greeting as you join it; the sign-in it takes; your worlds, as you join and leave them.
    const onOnline = info => note(`In ${info.title || "the lobby"}${info.motd ? `: ${info.motd}` : ""}${v.groups.length ? ` · your worlds: ${v.groups.map(g => g.name).join(", ")} (/g)` : ""}`);
    const onSigned = on => note(on ? "Signed in on this workroom: joining the lobby" : "Nobody is signed in on this workroom: out of the lobby until someone is");
    const known = new Set(v.groups.map(g => g.name.toLowerCase()));
    const onGroup = g => { const k = g.name.toLowerCase(); if (!known.has(k)) { known.add(k); note(`You are in ${g.name} now: its members walk into your room, and you theirs; 🗺 + puts one's room in your world (/g to talk)`); } };
    const onLeft = l => { known.delete(String(l.group).toLowerCase()); if (l.reason !== "left") note(l.reason === "removed" ? `Taken out of ${l.group}` : l.reason === "banned" ? `Taken out of ${l.group}, and kept out` : `${l.group} has ended`); };
    const onVoice = rv => {   // into a room voice (said once), or out of one (and why)
      const o = rv && rv.out;
      if (o) note(o.why === "kicked" ? `${o.name} took you out of their room's voice` : o.why === "banned" ? `${o.name}'s room keeps you out of its voice` : `Out of ${o.name}'s room's voice: you are no longer linked`);
      else if (rv && !rv.joining && voiceIn !== rv.key && !starting) { const inH = v.inHuddle;
        note(inH ? `🎧 In ${inH.mine ? "your" : `${rv.name}'s`} huddle in ${inH.group}: everyone in it hears everyone else`
          : `In ${rv.key === v.key ? "your room's" : `${rv.name}'s room's`} voice: everyone in it hears everyone else${v.mic ? "" : ", and you once your microphone is on (🎙)"}`); }
      voiceIn = rv && !rv.joining ? rv.key : rv ? voiceIn : null;
      voiceLine();
    };
    const onBanned = b => note(b.voice || b.chat ? `${b.name}'s room keeps you out of its ${b.voice && b.chat ? "voice and chat" : b.voice ? "voice" : "chat"}` : `${b.name}'s room lets you back into its voice and chat`);
    addEventListener("keydown", key);
    v.on("chat", onChat); v.on("error", onError); v.on("room_voice", onVoice); v.on("banned", onBanned); v.on("change", voiceLine); v.on("voice", voiceLine);
    v.on("call", onCall); v.on("huddle", onHuddle);
    v.on("unseen", mailSay); v.on("held", mailSay); v.on("held_sent", onHeldSent);
    v.on("online", onOnline); v.on("signed_in", onSigned); v.on("group", onGroup); v.on("group_left", onLeft); v.on("ignored", onIgnored);
    mailSay(); look();
    voiceIn = v.roomVoice && !v.roomVoice.joining ? v.roomVoice.key : null;
    voiceLine();
    for (const l of v.chatLog) { add(l); heard(l); }
    intro = note("Enter to chat · Tab where it goes · @ mention · paste a picture · 🎙 voice message · 🖼 shared · 🎧 talk (a person, a world's huddle, a room) · /s say · /w whisper · /r reply · /room yours, /room name · /1 lobby · /g world · click a name: whisper, ignore");
    return {
      element: root, open, close,
      // 3.3 (#48): the target card's Say and Whisper: the box talks there ({ scope: "aim" | "figure" | "whisper", ... }).
      aim(t) { if (!isObj(t)) return; to = t; fill(); open(); },
      // A failure of the page's own (its 🎙, say), told as Visit's "error" lines are.
      error(e) { onError(e); },
      release() {
        removeEventListener("keydown", key);
        v.off("chat", onChat); v.off("error", onError); v.off("room_voice", onVoice); v.off("banned", onBanned); v.off("change", voiceLine); v.off("voice", voiceLine);
        v.off("call", onCall); v.off("huddle", onHuddle); ringTone(null);
        v.off("unseen", mailSay); v.off("held", mailSay); v.off("held_sent", onHeldSent);
        v.off("online", onOnline); v.off("signed_in", onSigned); v.off("group", onGroup); v.off("group_left", onLeft);
        recStop(false); clearInterval(lookTimer); v.attention(false);
        removeEventListener("focus", look); removeEventListener("blur", look); document.removeEventListener("visibilitychange", look);
        for (const t of STIRS) removeEventListener(t, stir, { capture: true });
        clearTimeout(fadeTimer); root.remove();
      },
    };
  }

  // ---------- your world's map: where friends' rooms sit round yours, seen from above ----------
  const MAP_CSS = `
.visit-map { position: fixed; inset: 0; z-index: 20; display: flex; align-items: center; justify-content: center; background: rgba(1,4,9,.55); font: 12px/1.4 ui-monospace, Consolas, monospace; color: #e6edf3; }
.visit-map[hidden] { display: none; }
.visit-map .box { width: min(1000px, 94vw); height: min(680px, 88vh); display: flex; flex-direction: column; background: #0d1117; border: 1px solid #2d333b; border-radius: 10px; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,.5); }
.visit-map .hd { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #2d333b; }
.visit-map .ft { padding: 6px 12px; border-top: 1px solid #2d333b; color: #8b949e; }
.visit-map .grow { flex: 1; min-width: 0; } .visit-map .muted { color: #8b949e; }
.visit-map .main { flex: 1; display: flex; min-height: 0; }
.visit-map canvas { flex: 1; min-width: 0; display: block; cursor: grab; background: #161b22; touch-action: none; outline: none; }
.visit-map .side { width: 250px; flex: none; border-left: 1px solid #2d333b; padding: 10px 12px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.visit-map button, .visit-map input { font: inherit; color: inherit; background: #161b22; border: 1px solid #2d333b; border-radius: 6px; padding: 3px 8px; }
.visit-map button { cursor: pointer; } .visit-map button.go { background: #2f9e4f; border-color: #2f9e4f; color: #fff; }
.visit-map button.pick { text-align: left; } .visit-map input { width: 76px; }
.visit-map .row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.visit-map svg { display: block; width: 100%; height: 150px; background: #161b22; border-radius: 6px; }`;

  // A friend's view of the two of you (8.4, `v.seen`): their plot, and yours where they have put it in their
  // world; in metres, north (-z) up. Nothing of their other friends: they never say. HTML, for the map's side.
  const COMPASS = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"];
  function theirView(s, mine) {
    const a = s.at, odd = a.turn % 2 === 1, [tw, td] = a.size, mw = odd ? mine[1] : mine[0], md = odd ? mine[0] : mine[1], r = x => Math.round(x * 100) / 100;
    const x0 = Math.min(-tw / 2, a.dx - mw / 2), z0 = Math.min(-td / 2, a.dz - md / 2), x1 = Math.max(tw / 2, a.dx + mw / 2), z1 = Math.max(td / 2, a.dz + md / 2);
    const span = Math.max(x1 - x0, z1 - z0), pad = span * 0.08, fs = span / 14;
    const box = (cx, cz, w, d, fill, stroke, text) => `<rect x="${r(cx - w / 2)}" y="${r(cz - d / 2)}" width="${r(w)}" height="${r(d)}" fill="${fill}" stroke="${stroke}" stroke-width="${r(fs / 6)}"/>`
      + `<text x="${r(cx)}" y="${r(cz + fs / 3)}" font-size="${r(fs)}" text-anchor="middle" fill="#e6edf3">${text}</text>`;
    // your room's own north, as they have it turned: an arrow against that side, pointing out of it
    const [nx, nz] = turnXZ(0, -1, a.turn * Math.PI / 2), k = fs * 0.45, cx = a.dx + nx * (mw / 2 - k), cz = a.dz + nz * (md / 2 - k);
    const arrow = [[cx + nx * k, cz + nz * k], [cx - nx * k / 2 - nz * k * 0.8, cz - nz * k / 2 + nx * k * 0.8], [cx - nx * k / 2 + nz * k * 0.8, cz - nz * k / 2 - nx * k * 0.8]];
    const dist = Math.hypot(a.dx, a.dz), dir = COMPASS[(Math.round(Math.atan2(-a.dz, a.dx) / (Math.PI / 4)) % 8 + 8) % 8];
    return `<div class="muted">in their world:</div><svg viewBox="${r(x0 - pad)} ${r(z0 - pad)} ${r(x1 - x0 + 2 * pad)} ${r(z1 - z0 + 2 * pad)}">`
      + box(0, 0, tw, td, "rgba(88,166,255,.16)", "#58a6ff", esc(s.name || "theirs")) + box(a.dx, a.dz, mw, md, "rgba(63,185,80,.18)", "#3fb950", "you")
      + `<polygon points="${arrow.map(p => p.map(r).join(",")).join(" ")}" fill="#8b949e"/></svg>`
      + `<div class="muted">${dist < 0.5 ? "your room on theirs" : `your room ${dir} of theirs, ${Math.round(dist)} m middle to middle`}${a.turn ? ", " + ["", "turned a quarter left", "turned half round", "turned a quarter right"][a.turn] : ""}</div>`;
  }

  // Your world from above (v.world first): your room's plot, and each friend's where your layout puts it. Drag one
  // to put it there (it snaps to half units, and flush against what is near), turn it, or type where its middle
  // goes. Your layout is yours alone: nobody agrees to it, and each friend in it sees only where their own room
  // sits. Pick a friend to see their view of the two of you (`v.seen`). What you place is kept by visit-client
  // (with VISIT_STATE_FILE, across reloads and restarts). { element, open(), close(), release() }
  function map(v, { parent = document.body } = {}) {
    ready(v, "map");
    if (!document.getElementById("visit-map-css")) {
      const st = document.createElement("style"); st.id = "visit-map-css"; st.textContent = MAP_CSS; document.head.appendChild(st);
    }
    const root = document.createElement("div");
    root.className = "visit-map"; root.hidden = true;
    root.innerHTML = `<div class="box"><div class="hd"><b>Your world</b><span class="grow muted">where your friends' rooms sit round yours: your layout, yours alone</span>`
      + `<button type="button" data-m="fit">Fit</button><button type="button" data-m="close">Close</button></div>`
      + `<div class="main"><canvas tabindex="0"></canvas><div class="side"></div></div>`
      + `<div class="ft">drag a room to move it · R turns it right (Shift+R left) · Delete: back to the ring · drag the ground to look about, the wheel to zoom</div></div>`;
    parent.appendChild(root);
    const canvas = root.querySelector("canvas"), side = root.querySelector(".side"), ctx = canvas.getContext("2d");
    const pending = new Map();   // key -> where you just put it, until the layout says so
    let view = null, sel = null, drag = null, pan = null, timer = 0, sideSig = "";

    // Your plot, and each friend's as the world lays it out now (what you just moved where you put it).
    function plots() {
      const w = v.currentWorld;
      if (!w) return null;
      const laid = w.layout();   // laid out anew, not the frame's: a placement kept a moment ago is in it
      for (const [key, p] of pending) {
        const l = v.layout.find(q => q.key === key);
        if (l && Math.abs(l.x - p.x) < 1e-6 && Math.abs(l.z - p.z) < 1e-6 && (l.turn | 0) === p.turn) pending.delete(key);
      }
      const mp = v.plot, mine = [mp.center[0] - mp.size[0] / 2, mp.center[1] - mp.size[1] / 2, mp.center[0] + mp.size[0] / 2, mp.center[1] + mp.size[1] / 2];
      const list = laid.filter(x => !x.home).map(x => {   // (a user page's own room is in the world's plots too: "my room" here)
        const at = drag && drag.key === x.key ? drag.at : pending.get(x.key) || x.at;
        return { key: x.key, name: x.name, at, rect: rectOf(at, x.size), size: x.size, offline: x.offline,
          placed: pending.has(x.key) || v.layout.some(p => p.key === x.key) };
      });
      return { mp, mine, list };
    }

    const dpr = () => window.devicePixelRatio || 1;
    const toPx = (x, z) => [canvas.clientWidth / 2 + (x - view.ox) * view.s, canvas.clientHeight / 2 + (z - view.oz) * view.s];
    const toWorld = (px, py) => [view.ox + (px - canvas.clientWidth / 2) / view.s, view.oz + (py - canvas.clientHeight / 2) / view.s];
    function fit() {
      const d = plots();
      if (!d || !canvas.clientWidth) return;
      let [x0, z0, x1, z1] = d.mine;
      for (const p of d.list) { x0 = Math.min(x0, p.rect[0]); z0 = Math.min(z0, p.rect[1]); x1 = Math.max(x1, p.rect[2]); z1 = Math.max(z1, p.rect[3]); }
      const pad = Math.max(x1 - x0, z1 - z0) * 0.12 + 2;
      view = { s: Math.min(canvas.clientWidth / (x1 - x0 + 2 * pad), canvas.clientHeight / (z1 - z0 + 2 * pad)), ox: (x0 + x1) / 2, oz: (z0 + z1) / 2 };
    }

    // Where a room being dragged goes: on half units, then flush against a side near it (within 8 pixels).
    function snapAt(p, at, d) {
      const x0 = Math.round(at.x * 2) / 2, z0 = Math.round(at.z * 2) / 2, r = rectOf({ x: x0, z: z0, turn: at.turn }, p.size);
      const hw = (r[2] - r[0]) / 2, hd = (r[3] - r[1]) / 2, tol = 8 / view.s, others = [d.mine, ...d.list.filter(q => q.key !== p.key).map(q => q.rect)];
      let bx = null, bz = null;
      for (const o of others) {
        for (const [a, b] of [[x0 - hw, o[2]], [x0 + hw, o[0]], [x0 - hw, o[0]], [x0 + hw, o[2]]]) if (Math.abs(b - a) < tol && (bx === null || Math.abs(b - a) < Math.abs(bx))) bx = b - a;
        for (const [a, b] of [[z0 - hd, o[3]], [z0 + hd, o[1]], [z0 - hd, o[1]], [z0 + hd, o[3]]]) if (Math.abs(b - a) < tol && (bz === null || Math.abs(b - a) < Math.abs(bz))) bz = b - a;
      }
      return { x: x0 + (bx || 0), z: z0 + (bz || 0), turn: at.turn };
    }
    const put = (p, at) => { pending.set(p.key, at); v.place(p.key, at); };

    function draw() {
      if (root.hidden) return;
      const d = plots(), W = canvas.clientWidth, H = canvas.clientHeight, k = dpr();
      if (canvas.width !== Math.round(W * k) || canvas.height !== Math.round(H * k)) { canvas.width = Math.round(W * k); canvas.height = Math.round(H * k); }
      ctx.setTransform(k, 0, 0, k, 0, 0);
      ctx.fillStyle = "#161b22"; ctx.fillRect(0, 0, W, H);
      if (!d) { ctx.fillStyle = "#8b949e"; ctx.font = "13px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.fillText("this room has no world (v.world)", W / 2, H / 2); return side.innerHTML = ""; }
      if (!view) fit();
      if (!view) return;
      // A grid of whole units (or tens), north (-z) up.
      const step = [0.5, 1, 2, 5, 10, 20, 50, 100, 200].find(s => s * view.s >= 24) || 500, [wx0, wz0] = toWorld(0, 0), [wx1, wz1] = toWorld(W, H);
      ctx.strokeStyle = "#21262d"; ctx.lineWidth = 1; ctx.beginPath();
      for (let x = Math.ceil(wx0 / step) * step; x <= wx1; x += step) { const [px] = toPx(x, 0); ctx.moveTo(Math.round(px) + 0.5, 0); ctx.lineTo(Math.round(px) + 0.5, H); }
      for (let z = Math.ceil(wz0 / step) * step; z <= wz1; z += step) { const [, py] = toPx(0, z); ctx.moveTo(0, Math.round(py) + 0.5); ctx.lineTo(W, Math.round(py) + 0.5); }
      ctx.stroke();
      ctx.fillStyle = "#8b949e"; ctx.font = "11px ui-monospace, monospace"; ctx.textAlign = "left"; ctx.fillText(`N ↑   grid ${step} ${step === 1 ? "unit" : "units"}`, 8, 16);
      const rect = (r, fill, stroke, dash, lw) => {
        const [a, b] = toPx(r[0], r[1]), [c, e] = toPx(r[2], r[3]);
        if (fill) { ctx.fillStyle = fill; ctx.fillRect(a, b, c - a, e - b); }
        ctx.setLineDash(dash || []); ctx.strokeStyle = stroke; ctx.lineWidth = lw || 1.5; ctx.strokeRect(a, b, c - a, e - b); ctx.setLineDash([]);
      };
      const label = (r, text, sub) => {
        const [cx, cz] = toPx((r[0] + r[2]) / 2, (r[1] + r[3]) / 2);
        ctx.textAlign = "center"; ctx.fillStyle = "#e6edf3"; ctx.font = "bold 12px system-ui, sans-serif"; ctx.fillText(text, cx, cz);
        if (sub) { ctx.fillStyle = "#8b949e"; ctx.font = "11px system-ui, sans-serif"; ctx.fillText(sub, cx, cz + 14); }
      };
      rect(d.mine, "rgba(63,185,80,.18)", "#3fb950");
      label(d.mine, "your room", v.office ? v.office.name : "");
      for (const p of d.list) {
        const on = sel === p.key;
        rect(p.rect, on ? "rgba(88,166,255,.32)" : "rgba(88,166,255,.16)", on ? "#e6edf3" : "#58a6ff", null, on ? 2.5 : 1.5);
        // its own north: an arrow near that side, pointing out of it (how it is turned)
        const [nx, nz] = turnXZ(0, -1, p.at.turn * Math.PI / 2), hw = (p.rect[2] - p.rect[0]) / 2, hd = (p.rect[3] - p.rect[1]) / 2;
        const [ax, az] = toPx(p.at.x + nx * hw * 0.75, p.at.z + nz * hd * 0.75);
        ctx.fillStyle = "#8b949e"; ctx.beginPath(); ctx.moveTo(ax + nx * 6, az + nz * 6); ctx.lineTo(ax - nx * 3 - nz * 5, az - nz * 3 + nx * 5);
        ctx.lineTo(ax - nx * 3 + nz * 5, az - nz * 3 - nx * 5); ctx.closePath(); ctx.fill();
        label(p.rect, p.name || "?", (p.offline ? "offline · " : "") + (p.placed ? "placed" : "in the ring"));
      }
      drawSide(d);
    }

    function drawSide(d) {
      const p = d.list.find(q => q.key === sel), theirs = v.seen.find(q => q.key === sel), others = v.seen.filter(q => !d.list.some(p => p.key === q.key));
      const sig = JSON.stringify(p ? [p.key, p.at, p.placed, theirs, v.isPicked(p.key)] : theirs ? ["only", theirs] : ["list", d.list.map(q => [q.key, q.name]), others]);
      if (sig === sideSig || (side.contains(document.activeElement) && document.activeElement.tagName === "INPUT")) return;
      sideSig = sig;
      const pick = (q, sub) => `<button type="button" class="pick" data-m="pick" data-k="${esc(q.key)}">${esc(q.name || "?")}${sub}</button>`;
      if (!p && !theirs) {
        side.innerHTML = `<div class="muted">your room: ${d.mp.size.map(x => Math.round(x * 100) / 100).join(" × ")} units</div>`
          + (d.list.length ? d.list.map(q => pick(q, "")).join("")
            : `<div class="muted">no friends' rooms in your world yet: 🗺 + someone in a world with you (in the panel), or put a link here</div>`)
          + (others.length ? `<div class="muted">in their worlds, not in yours:</div>` + others.map(q => pick(q, "")).join("") : "");
        return;
      }
      const r2 = x => Math.round(x * 100) / 100, name = (p || theirs).name;
      side.innerHTML = `<div><b>${esc(name || "?")}</b></div>`
        + (p ? `<div class="row">x <input data-f="x" value="${r2(p.at.x)}"> z <input data-f="z" value="${r2(p.at.z)}"></div>`
          + `<div class="row">${["not turned", "turned a quarter left", "turned half round", "turned a quarter right"][p.at.turn]} <button type="button" data-m="ccw" title="a quarter turn left">⟲</button><button type="button" data-m="cw" title="a quarter turn right">⟳</button></div>`
          + `<div class="row"><button type="button" data-m="auto" title="where the world puts it by itself">Back to the ring</button></div>`
          + (v.isPicked(p.key) ? `<div class="row"><button type="button" data-m="unpick" title="out of your world: its plot, where you put it, and what was kept of it while they are away all go (in the lobby or not)">Take out of my world</button></div>` : "")
          : `<div class="muted">not in your world</div>`)
        + (theirs ? theirView(theirs, v.plotMetres) : `<div class="muted">you are not in their world (or they have not said)</div>`)
        + `<button type="button" data-m="unsel">All rooms</button>`;
    }

    function hit(px, py) {
      const d = plots();
      if (!d || !view) return null;
      const [x, z] = toWorld(px, py);
      return [...d.list].reverse().find(p => x >= p.rect[0] && x <= p.rect[2] && z >= p.rect[1] && z <= p.rect[3]) || null;
    }
    const pos = e => { const b = canvas.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
    canvas.addEventListener("pointerdown", e => {
      canvas.focus();
      const [px, py] = pos(e), p = hit(px, py);
      canvas.setPointerCapture(e.pointerId);
      if (p) {
        sel = p.key;
        const [x, z] = toWorld(px, py);
        drag = { key: p.key, dx: x - p.at.x, dz: z - p.at.z, at: { ...p.at }, moved: false };
      } else pan = { px, py, ox: view.ox, oz: view.oz };
      draw();
    });
    canvas.addEventListener("pointermove", e => {
      const [px, py] = pos(e);
      if (drag) {
        const d = plots(), p = d && d.list.find(q => q.key === drag.key);
        if (!p) return;
        const [x, z] = toWorld(px, py);
        drag.at = snapAt(p, { x: x - drag.dx, z: z - drag.dz, turn: drag.at.turn }, d); drag.moved = true;
        draw();
      } else if (pan) { view.ox = pan.ox - (px - pan.px) / view.s; view.oz = pan.oz - (py - pan.py) / view.s; draw(); }
      else canvas.style.cursor = hit(px, py) ? "move" : "grab";
    });
    const up = () => {
      if (drag && drag.moved) { const d = plots(), p = d && d.list.find(q => q.key === drag.key); if (p) put(p, drag.at); }
      drag = null; pan = null; draw();
    };
    canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("wheel", e => {
      e.preventDefault();
      const [px, py] = pos(e), [x, z] = toWorld(px, py), f = Math.exp(-e.deltaY * 0.0015);
      view.s = Math.max(0.2, Math.min(400, view.s * f));
      const [x2, z2] = toWorld(px, py); view.ox += x - x2; view.oz += z - z2;
      draw();
    }, { passive: false });
    const turnSel = by => { const d = plots(), p = d && d.list.find(q => q.key === sel); if (p) put(p, { x: p.at.x, z: p.at.z, turn: (p.at.turn + by + 4) % 4 }); draw(); };
    canvas.addEventListener("keydown", e => {
      if (e.key === "r" || e.key === "R") { e.preventDefault(); turnSel(e.shiftKey ? 1 : -1); }   // R right, Shift+R left
      else if ((e.key === "Delete" || e.key === "Backspace") && sel) { e.preventDefault(); pending.delete(sel); v.unplace(sel); }
      else if (e.key === "Escape") close();
    });
    root.addEventListener("click", e => {
      if (e.target === root) return close();   // outside the box
      const b = e.target.closest("button[data-m]");
      if (!b) return;
      const m = b.dataset.m, d = plots(), p = d && d.list.find(q => q.key === sel);
      if (m === "close") close();
      else if (m === "fit") { fit(); draw(); }
      else if (m === "pick") { sel = b.dataset.k; draw(); }
      else if (m === "unsel") { sel = null; draw(); }
      else if (m === "cw" || m === "ccw") turnSel(m === "cw" ? -1 : 1);   // a turn is a quarter turn left
      else if (m === "auto" && p) { pending.delete(p.key); v.unplace(p.key); }
      else if (m === "unpick" && p) { pending.delete(p.key); v.unpick(p.key); sel = null; sideSig = ""; draw(); }   // away or not
    });
    side.addEventListener("change", e => {
      const f = e.target.dataset.f, d = plots(), p = d && d.list.find(q => q.key === sel), n = Number(e.target.value);
      if (!p || !f || !Number.isFinite(n) || Math.abs(n) > 1e5) return draw();
      put(p, { x: f === "x" ? n : p.at.x, z: f === "z" ? n : p.at.z, turn: p.at.turn });
      e.target.blur(); sideSig = ""; draw();
    });
    function open() { root.hidden = false; view = null; sideSig = ""; draw(); canvas.focus(); clearInterval(timer); timer = setInterval(draw, 200); }
    function close() { root.hidden = true; clearInterval(timer); drag = pan = null; }
    v.on("change", draw);
    return { element: root, open, close, get shown() { return !root.hidden; },
      // for tests: where a room's middle is on screen (clientX, clientY)
      _px(key) { const d = plots(), p = d && view && d.list.find(q => q.key === key); if (!p) return null; const b = canvas.getBoundingClientRect(), [x, y] = toPx(p.at.x, p.at.z); return [b.left + x, b.top + y]; },
      release() { close(); v.off("change", draw); root.remove(); } };
  }

  // ---------- the ready-made stage: the room you are visiting, full-window or in a corner ----------
  const STAGE_CSS = `
.visit-stage { position: fixed; z-index: 5; background: #000; overflow: hidden; }
.visit-stage.big { inset: 0; }
.visit-stage.small { right: 16px; bottom: 16px; width: 320px; height: 180px; border: 1px solid #2d333b; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.4); cursor: pointer; }
.visit-stage .screen { position: absolute; inset: 0; }
.visit-stage .bar { position: absolute; left: 0; right: 0; bottom: 0; display: flex; gap: 8px; align-items: center; padding: 6px 10px; font: 12px/1.3 ui-monospace, Consolas, monospace; color: #e6edf3; background: rgba(13,17,23,.78); }
.visit-stage .bar .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.visit-stage .bar .stats { color: #8b949e; white-space: nowrap; }
.visit-stage .bar button { font: inherit; color: inherit; background: #161b22; border: 1px solid #2d333b; border-radius: 6px; padding: 2px 10px; cursor: pointer; }
.visit-stage.small .bar button, .visit-stage.small .bar .stats { display: none; }
.visit-stage .menu { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; background: rgba(13,17,23,.72); font: 12px/1.3 ui-monospace, Consolas, monospace; color: #e6edf3; }
.visit-stage .menu[hidden] { display: none; }
.visit-stage .menu button { font: inherit; color: inherit; background: #161b22; border: 1px solid #2d333b; border-radius: 6px; padding: 3px 12px; cursor: pointer; min-width: 150px; }
.visit-stage .menu button.go { background: #2f9e4f; border-color: #2f9e4f; color: #fff; }`;

  // While you visit: the room you are in fills the window, with how it is going (the link's path and round
  // trip, input to screen, frames a second), "Back to my room" and "Leave". Back in your room, the visit
  // shrinks to a corner (and takes no keys: v.viewFocused is false, so the page's own are its own); a click
  // on it brings it back to the full window. A link (8.5) starts in the corner, seen from outside: a click on it
  // offers "Go there" (the full window, in their room: "Back to my room" puts it in the corner again), "Put in my
  // world" (with v.world) and "Unlink". Gone when the visit or link ends. { element, big, shrink(), grow(),
  // release() }

  function stage(v, { parent = document.body } = {}) {
    ready(v, "stage");
    const home = v.role === "user" && !v.currentWorld ? homeBox(v, parent) : null;   // the person's own room, first person, behind the stage
    if (!document.getElementById("visit-stage-css")) {
      const st = document.createElement("style"); st.id = "visit-stage-css"; st.textContent = STAGE_CSS; document.head.appendChild(st);
    }
    const root = document.createElement("div");
    root.className = "visit-stage big"; root.hidden = true;
    root.innerHTML = `<div class="screen"></div><div class="bar"><span class="grow"></span><span class="stats"></span>`
      + `<button type="button" data-s="small" title="your own room, with this one in the corner">Back to my room</button><button type="button" data-s="leave">Leave</button></div>`
      + `<div class="menu" hidden></div>`;
    parent.appendChild(root);
    const screen = root.querySelector(".screen"), cap = root.querySelector(".grow"), nums = root.querySelector(".stats"), menu = root.querySelector(".menu");

    let view = null, big = true, shown = null;
    function set(on) { big = on; root.className = "visit-stage " + (on ? "big" : "small"); if (view) view.setActive(on); menu.hidden = true; draw(); stats(); }
    function draw() {
      if (v.visiting && !view) { root.hidden = false; view = v.view(screen); big = true; root.className = "visit-stage big"; }
      if (!v.visiting && view) { view.release(); view = null; root.hidden = true; menu.hidden = true; shown = null; }
      if (!v.visiting) return;
      const vi = v.visiting, name = vi.host.name || "Their";
      if (shown !== vi.id) { shown = vi.id; if (!big) set(true); }
      cap.textContent = big ? `${name}'s room${vi.paused ? " (reloading)" : ""} · click to look around, WASD to walk, Shift to run, Esc frees the mouse` : `${name}'s room · click to go back in`;
    }
    function stats() {
      const x = v.stats;
      nums.textContent = !v.visiting ? "" : [x.path, x.rtt != null && `link ${Math.round(x.rtt)} ms`,
        x.latency != null && `input to screen ${Math.round(x.latency)} ms`, x.fps ? `${x.fps} fps` : ""].filter(Boolean).join(" · ");
    }
    root.addEventListener("click", e => {
      const b = e.target.closest("button[data-s]"), s = b && b.dataset.s;
      if (b) {
        e.stopPropagation();
        if (s === "leave") return v.visiting && v.kick(v.visiting.id);
        return set(false);   // Back to my room
      }
      if (!big) set(true);
    });
    v.on("change", draw); v.on("stats", stats);
    draw();
    return {
      element: root, home: home && home.element, get big() { return big; }, shrink: () => set(false), grow: () => set(true),
      release() { v.off("change", draw); v.off("stats", stats); if (view) view.release(); if (home) home.release(); root.remove(); },
    };
  }

  window.VisitUI = { panel, chat, targetCard, map, stage, perf, lightbox, seenText, nameColor };
})();
