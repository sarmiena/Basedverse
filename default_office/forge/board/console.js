// An agent's console: its herdr pane, the screen as herdr shows it (refreshed every second), with a box to reply in, keys
// for Claude Code's menus, and pictures to send it. bin/consoles serves it on the board's machine only, with a token of its
// own: the board relays a page's calls to it (/api/console/...), adding the token, so a page works from another device
// too and never holds it; without bin/consoles, the panel says why. Opened from the Agent Room (an agent's name card,
// room.html) and the kanban (an agent, index.html), one per page: AgentConsole.make(opts) gives { openPane(who, pend),
// close(), attach(seq, who), failed(seq, why), call(path, body), get who }. Each page opens it only from a click of its
// owner's own; it is a page's, over it, never part of the room's scene (a visitor never sees it).
//   opts.beside(): the page's panel it sits beside when open (the room's ticket, the kanban's quick view), or null;
//   opts.side: "after" (to its right, the room's) or "before" (to its left, the kanban's);
//   opts.onClose(): when it closes (the room gives its canvas the keys back); opts.rectKey: where this browser keeps
//   its size and place for that page.
// Uses common.js's squeezed. Its stylesheet is its own, put on the page the first time one is made.
(function () {
const CSS = `/* An agent's console: its herdr pane (bin/consoles), opened from its name card. A terminal, so dark in either theme. */
.console { position: fixed; top: var(--top-h, 64px); left: 50%; transform: translateX(-50%); width: min(940px, calc(100% - 32px)); max-height: calc(100% - var(--top-h, 64px) - 48px); box-sizing: border-box; z-index: var(--con-z, 9); display: flex; flex-direction: column; background: #0c0d0f; color: #d4d4d4; border: 1px solid #d29922; border-radius: 8px; box-shadow: 0 10px 30px rgba(0,0,0,.45); font: 12px var(--mono, 'JetBrains Mono', ui-monospace, Consolas, monospace); }
.console[hidden] { display: none; }
.console .con-head { display: flex; align-items: baseline; gap: 10px; padding: 7px 10px; border-bottom: 1px solid #2d333b; cursor: move; touch-action: none; user-select: none; }
.console .con-grip { position: absolute; width: 18px; height: 18px; z-index: 1; touch-action: none; }
.console .con-grip.nw { left: -4px; top: -4px; cursor: nwse-resize; } .console .con-grip.ne { right: -4px; top: -4px; cursor: nesw-resize; }
.console .con-grip.sw { left: -4px; bottom: -4px; cursor: nesw-resize; } .console .con-grip.se { right: -4px; bottom: -4px; cursor: nwse-resize; }
.console .con-grip.se::after { content: ""; position: absolute; right: 7px; bottom: 7px; width: 8px; height: 8px; border-right: 2px solid #d29922; border-bottom: 2px solid #d29922; border-bottom-right-radius: 3px; opacity: .7; }
.console .con-head b { font: 700 13px var(--mono, 'JetBrains Mono', ui-monospace, Consolas, monospace); color: #e6edf3; }
.console .con-st { color: #8b949e; }
.console .con-st.blocked { color: #ffb84d; }
.console .con-x { margin-left: auto; font: 700 16px/1 var(--body, system-ui, sans-serif); color: #8b949e; background: none; border: 0; cursor: pointer; padding: 0 4px; }
.console pre { margin: 0; padding: 8px 10px; overflow: auto; white-space: pre; min-height: 120px; flex: 1 1 auto; line-height: 1.35; font-size: var(--con-font, 12px); }
.console .con-since { color: #6e7681; }
/* The scrollback in blocks of CON_CHUNK lines: a change lays out its own block alone (a new status line, the screen's
   alone), and a block out of view is not laid out or painted at all; at least as wide as its longest line, so that
   none is cut off (content-visibility's paint containment) and the pane still scrolls sideways to it. */
.console .con-since, .console .con-hist, .console .con-live, .console .con-hist > span { display: block; }
.console .con-hist > span { content-visibility: auto; contain-intrinsic-block-size: auto 270em; min-width: 100%; width: max-content; }
.console .con-err { color: #f85149; padding: 0 10px; }
.console .con-err:empty { display: none; }
.console .con-keys { display: flex; flex-wrap: wrap; gap: 4px; padding: 6px 10px 0; border-top: 1px solid #2d333b; }
.console .con-keys button, .console .con-reply button { font: 700 12px var(--mono, 'JetBrains Mono', ui-monospace, Consolas, monospace); color: #e6edf3; background: #161b22; border: 1px solid #2d333b; border-radius: 5px; padding: 3px 8px; cursor: pointer; }
.console .con-reply { display: flex; gap: 6px; padding: 6px 10px 10px; }
.console textarea { flex: 1; resize: vertical; min-height: 34px; box-sizing: border-box; font: 12px var(--mono, 'JetBrains Mono', ui-monospace, Consolas, monospace); color: #e6edf3; background: #161b22; border: 1px solid #2d333b; border-radius: 6px; padding: 6px 8px; }
.console .con-reply button { background: #2f9e4f; border-color: #2f9e4f; color: #fff; }
.console .con-reply .con-pic { background: #161b22; border-color: #2d333b; font: 15px/1 var(--body, system-ui, sans-serif); padding: 0 8px; }
.console .con-pics { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 10px 0; }
.console .con-pics[hidden] { display: none; }
.console .con-thumb { position: relative; width: 60px; height: 44px; border: 1px solid #2d333b; border-radius: 5px; overflow: hidden; background: #161b22; }
.console .con-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.console .con-thumb.up img { opacity: .45; }
.console .con-thumb.bad { border-color: #f85149; } .console .con-thumb.bad img { opacity: .3; }
.console .con-thumb span { position: absolute; inset: 0; display: grid; place-items: center; font: 700 15px var(--mono, 'JetBrains Mono', ui-monospace, Consolas, monospace); color: #e6edf3; pointer-events: none; }
.console .con-thumb button { position: absolute; top: 2px; right: 2px; width: 17px; height: 17px; padding: 0; font: 700 12px/1 var(--body, system-ui, sans-serif); color: #e6edf3; background: rgba(0,0,0,.7); border: 0; border-radius: 50%; cursor: pointer; }
.console.drop { border-color: #3fb950; box-shadow: 0 0 0 2px rgba(63,185,80,.55), 0 10px 30px rgba(0,0,0,.45); }`;
function make(opts = {}) {
  if (!document.getElementById("agent-console-css")) { const s = document.createElement("style"); s.id = "agent-console-css"; s.textContent = CSS; document.head.appendChild(s); }
  const CONSOLE_POLL_MS = 1000, CONSOLE_KEYS = [["esc", "Esc"], ["up", "↑"], ["down", "↓"], ["enter", "Enter"], ["1", "1"], ["2", "2"], ["3", "3"]];
  const con = { el: null, who: "", timer: 0, last: null, gen: 0, pollErr: false, rect: null, hist: [], histBase: 0, watched: null, pending: null, queue: [], flushing: false, pics: [] };
  const CON_HIST_MAX = 6000;   // scrollback lines kept on the page (bin/consoles keeps about 5000)
  const CON_CHUNK = 200;   // scrollback lines to a block (the stylesheet's .con-hist > span: its 270em, 200 lines of 1.35)
  // Its size and place: a corner drags it bigger or smaller, its title bar moves it, and it opens next time as it was
  // left (this browser's, best-effort). herdr's pane has its own columns and rows, so a bigger window shows the same
  // screen, its text grown with the window's width (from CON_FONT at CON_BASE_W wide, up to CON_FONT_MAX).
  const CON_MIN_W = 420, CON_MIN_H = 260, CON_BASE_W = 940, CON_FONT = 12, CON_FONT_MAX = 22, CON_RECT = opts.rectKey || "console.rect";
  try { const r = JSON.parse(localStorage.getItem(CON_RECT) || "null"); if (r && [r.x, r.y, r.w, r.h].every(Number.isFinite)) con.rect = r; } catch (e) {}
  function placeConsole(r) {
    const el = con.el, vw = innerWidth, vh = innerHeight;
    if (!el) return;
    if (!r) {   // the stylesheet's: centred, at its default width; beside the page's panel (opts.beside), if the window has room
      el.style.cssText = "";
      const p = opts.beside && opts.beside(), t = p && p.getBoundingClientRect();
      if (t && opts.side === "before") {   // to its left (the kanban's quick view, on the right)
        const w = Math.min(CON_BASE_W, t.left - 28);
        if (w >= CON_MIN_W) Object.assign(el.style, { left: Math.max(16, t.left - 12 - w) + "px", width: w + "px", transform: "none" });
      } else if (t) {   // to its right (the room's ticket, on the left)
        const left = t.right + 12;
        if (vw - left - 16 >= CON_MIN_W) Object.assign(el.style, { left: left + "px", width: Math.min(CON_BASE_W, vw - left - 16) + "px", transform: "none" });
      }
      return;
    }
    const w = Math.min(Math.max(r.w, Math.min(CON_MIN_W, vw)), vw), h = Math.min(Math.max(r.h, Math.min(CON_MIN_H, vh)), vh);
    const x = Math.min(Math.max(r.x, 0), vw - w), y = Math.min(Math.max(r.y, 0), vh - h);
    Object.assign(el.style, { left: x + "px", top: y + "px", width: w + "px", height: h + "px", maxHeight: "none", transform: "none" });
    el.style.setProperty("--con-font", Math.round(Math.min(CON_FONT_MAX, Math.max(CON_FONT, CON_FONT * w / CON_BASE_W)) * 2) / 2 + "px");
  }
  // A drag from a corner ("nw", "ne", "sw", "se": that corner follows the pointer, the opposite one stays) or the
  // title bar ("move"), kept inside the window.
  function dragConsole(e, how) {
    if (e.button !== 0) return;
    e.preventDefault();
    const b = con.el.getBoundingClientRect(), x0 = e.clientX, y0 = e.clientY, t = e.currentTarget;
    t.setPointerCapture(e.pointerId);
    const move = ev => {
      const dx = ev.clientX - x0, dy = ev.clientY - y0, vw = innerWidth, vh = innerHeight, mw = Math.min(CON_MIN_W, vw), mh = Math.min(CON_MIN_H, vh);
      let L = b.left, T = b.top, R = b.right, B = b.bottom;
      if (how === "move") {
        const sx = Math.min(Math.max(dx, -L), vw - R), sy = Math.min(Math.max(dy, -T), vh - B);
        L += sx; R += sx; T += sy; B += sy;
      } else {
        if (how.includes("w")) L = Math.min(Math.max(L + dx, 0), R - mw);
        if (how.includes("e")) R = Math.max(Math.min(R + dx, vw), L + mw);
        if (how.includes("n")) T = Math.min(Math.max(T + dy, 0), B - mh);
        if (how.includes("s")) B = Math.max(Math.min(B + dy, vh), T + mh);
      }
      placeConsole(con.rect = { x: L, y: T, w: R - L, h: B - T });
    };
    const up = () => {
      t.removeEventListener("pointermove", move); t.removeEventListener("pointerup", up); t.removeEventListener("pointercancel", up);
      try { if (con.rect) localStorage.setItem(CON_RECT, JSON.stringify(con.rect)); } catch (err) {}
    };
    t.addEventListener("pointermove", move); t.addEventListener("pointerup", up); t.addEventListener("pointercancel", up);
  }
  addEventListener("resize", () => { if (con.el && !con.el.hidden && con.rect) placeConsole(con.rect); });
  async function consoleCall(path, body) {   // through the board, which adds bin/consoles' token: this page never holds it
    let r;
    try {
      r = await fetch("/api/console" + path, body
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
        : { cache: "no-store" });
    } catch (e) { throw new Error("the board did not answer"); }
    const b = await r.json().catch(() => ({}));
    if (!r.ok) { const err = new Error(b.error || "bin/consoles: " + r.status); err.code = b.code; throw err; }
    return b;
  }
  // The pane's screen, its colours and weights as herdr read them (SGR only; any other escape is dropped).
  const ANSI16 = ["#1c1c1c", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5", "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff"];
  function ansi256(n) {
    if (n < 16) return ANSI16[n];
    if (n >= 232) { const v = 8 + (n - 232) * 10; return `rgb(${v},${v},${v})`; }
    const c = x => x ? 55 + x * 40 : 0; n -= 16;
    return `rgb(${c(Math.floor(n / 36))},${c(Math.floor(n / 6) % 6)},${c(n % 6)})`;
  }
  function ansiHtml(text) {
    const st = {}, esc = t => t.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
    const color = (a, k) => a[k] === 5 ? [ansi256(a[k + 1] & 255), k + 2] : a[k] === 2 ? [`rgb(${a[k + 1] & 255},${a[k + 2] & 255},${a[k + 3] & 255})`, k + 4] : [null, k + 1];
    function sgr(p) {
      const a = (p || "0").split(";").map(x => +x || 0);
      for (let k = 0; k < a.length; k++) {
        const c = a[k];
        if (c === 0) for (const key of Object.keys(st)) delete st[key];
        else if (c === 1) st.b = 1; else if (c === 2) st.d = 1; else if (c === 3) st.i = 1; else if (c === 4) st.u = 1; else if (c === 7) st.inv = 1;
        else if (c === 22) st.b = st.d = 0; else if (c === 23) st.i = 0; else if (c === 24) st.u = 0; else if (c === 27) st.inv = 0;
        else if (c >= 30 && c <= 37) st.fg = ANSI16[c - 30]; else if (c >= 90 && c <= 97) st.fg = ANSI16[c - 82]; else if (c === 39) st.fg = null;
        else if (c >= 40 && c <= 47) st.bg = ANSI16[c - 40]; else if (c >= 100 && c <= 107) st.bg = ANSI16[c - 92]; else if (c === 49) st.bg = null;
        else if (c === 38 || c === 48) { const [v, next] = color(a, k + 1); st[c === 38 ? "fg" : "bg"] = v; k = next - 1; }
      }
    }
    let out = "", i = 0, m;
    const put = t => {
      if (!t) return;
      let fg = st.fg, bg = st.bg; if (st.inv) [fg, bg] = [bg || "#0c0d0f", fg || "#d4d4d4"];
      const css = [fg && "color:" + fg, bg && "background:" + bg, st.b && "font-weight:700", st.d && "opacity:.6", st.i && "font-style:italic", st.u && "text-decoration:underline"].filter(Boolean);
      out += css.length ? `<span style="${css.join(";")}">${esc(t)}</span>` : esc(t);
    };
    const re = /\x1b\[([0-?]*)[ -\/]*([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]?/g;
    text = text.replace(/\r/g, "");
    while ((m = re.exec(text))) { put(text.slice(i, m.index)); i = re.lastIndex; if (m[2] === "m" && /^[0-9;]*$/.test(m[1])) sgr(m[1]); }
    put(text.slice(i));
    return out;
  }
  function consoleEl() {
    if (con.el) return con.el;
    const el = document.createElement("div");
    el.className = "console"; el.hidden = true; el.setAttribute("role", "dialog");
    el.innerHTML = `${["nw", "ne", "sw", "se"].map(c => `<div class="con-grip ${c}" data-c="${c}" title="Drag to resize"></div>`).join("")}
      <div class="con-head" title="Drag to move"><b></b><span class="con-st"></span><button class="con-x" type="button" aria-label="Close">×</button></div>
      <pre><span class="con-since"></span><span class="con-hist"></span><span class="con-live"></span></pre><div class="con-err"></div>
      <div class="con-keys">${CONSOLE_KEYS.map(([k, t]) => `<button type="button" data-k="${k}" title="Send ${t} to the agent">${t}</button>`).join("")}</div>
      <div class="con-pics" hidden></div>
      <form class="con-reply"><button type="button" class="con-pic" title="Attach a picture (or paste one into the reply, or drop it on the console)" aria-label="Attach a picture">📎</button><input type="file" accept="image/*" multiple hidden><textarea rows="2"></textarea><button type="submit">Send</button></form>`;
    document.body.appendChild(el);
    const ta = el.querySelector("textarea"), say = t => { el.querySelector(".con-err").textContent = t; };
    el.querySelector(".con-x").onclick = closeConsole;
    for (const g of el.querySelectorAll(".con-grip")) g.addEventListener("pointerdown", e => dragConsole(e, g.dataset.c));
    el.querySelector(".con-head").addEventListener("pointerdown", e => { if (!e.target.closest("button")) dragConsole(e, "move"); });
    el.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); closeConsole(); } });
    el.querySelector(".con-keys").addEventListener("click", async e => {
      const k = e.target.closest("button[data-k]"); if (!k) return;
      if (con.pending) return say("it is still starting");
      try { await consoleCall("/keys", { agent: con.who, keys: [k.dataset.k] }); say(""); con.pollErr = false; pollConsole(); } catch (err) { say(err.message); con.pollErr = false; }
    });
    el.querySelector("form").addEventListener("submit", async e => {
      e.preventDefault();
      if (con.pics.some(p => p.state === "up")) return say("a picture is still on its way: a moment");
      const pics = con.pics.filter(p => p.state === "ok" && p.who === con.who), text = [ta.value, ...pics.map(p => p.path)].filter(t => t.trim()).join("\n");   // each picture's path on a line of its own (addPic)
      if (!text.trim()) return;
      if (con.pending || con.queue.length) { con.queue.push(text); ta.value = ""; clearPics(); showQueue(); if (!con.pending) flushQueue(); return; }   // a new agent, not up yet: kept for it
      try { await consoleCall("/send", { agent: con.who, text }); ta.value = ""; clearPics(); say(""); con.pollErr = false; pollConsole(); }
      catch (err) { con.pollErr = false; say(err.code === "agent_blocked" ? con.who + " is at a question: answer it with the keys above (1, 2, 3, or ↑ ↓ and Enter)" : err.message); }
    });
    ta.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); el.querySelector("form").requestSubmit(); } });
    const pics = list => { const fs = [...(list || [])].filter(f => f && f.type.startsWith("image/")); for (const f of fs) addPic(f); return fs.length; };
    el.addEventListener("paste", e => { const d = e.clipboardData; if (d && pics([...d.items].filter(i => i.kind === "file").map(i => i.getAsFile()))) e.preventDefault(); });   // a picture pasted: attached (text pastes as ever)
    el.addEventListener("dragover", e => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); el.classList.add("drop"); } });
    el.addEventListener("dragleave", e => { if (!el.contains(e.relatedTarget)) el.classList.remove("drop"); });
    el.addEventListener("drop", e => { el.classList.remove("drop"); if (pics(e.dataTransfer.files)) e.preventDefault(); });
    const pick = el.querySelector("input[type=file]");
    el.querySelector(".con-pic").onclick = () => pick.click();
    pick.onchange = () => { pics(pick.files); pick.value = ""; ta.focus(); };
    return con.el = el;
  }
  // Pictures for the agent (pasted into the console, dropped on it, or picked with 📎): each sent as it comes, through
  // the board (/api/console/image) to bin/consoles, which keeps it on the agent's machine; its path then goes with the
  // reply, on a line of its own, and Claude Code takes a pasted line that is a picture's path as the picture itself (as a
  // file dropped on it): [Image #1] in its prompt. PNG, JPEG, GIF and WebP as they are; any other picture the browser
  // can open, as a PNG. A thumbnail of each, till the reply goes (× takes one back); none for a new agent not up yet.
  const PIC_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"], PIC_MAX = 10 << 20;
  async function picBlob(file) {
    if (PIC_TYPES.includes(file.type)) return file;
    const bmp = await createImageBitmap(file), c = document.createElement("canvas");
    c.width = bmp.width; c.height = bmp.height; c.getContext("2d").drawImage(bmp, 0, 0);
    return await new Promise((ok, no) => c.toBlob(b => b ? ok(b) : no(new Error("it could not be made a PNG")), "image/png"));
  }
  async function addPic(file) {
    const err = con.el && con.el.querySelector(".con-err");
    if (!con.who || con.pending) { if (err) err.textContent = "it is still starting: paste the picture once it is up"; return; }
    const pic = { url: URL.createObjectURL(file), state: "up", path: null, why: "", who: con.who };
    con.pics.push(pic); showPics();
    try {
      const blob = await picBlob(file);
      if (blob.size > PIC_MAX) throw new Error("it is over " + (PIC_MAX >> 20) + " MB");
      let r;
      try { r = await fetch("/api/console/image?agent=" + encodeURIComponent(pic.who), { method: "POST", headers: { "Content-Type": blob.type }, body: blob }); }
      catch (e) { throw new Error("the board did not answer"); }
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(b.error || "bin/consoles: " + r.status);
      pic.path = b.path; pic.state = "ok";
    } catch (e) { pic.state = "bad"; pic.why = e.message; if (err) err.textContent = "a picture was not sent: " + e.message; }
    showPics();
  }
  function dropPic(pic) { URL.revokeObjectURL(pic.url); con.pics.splice(con.pics.indexOf(pic), 1); showPics(); }
  function clearPics() { for (const p of con.pics) URL.revokeObjectURL(p.url); con.pics = []; showPics(); }
  function showPics() {
    const box = con.el && con.el.querySelector(".con-pics"); if (!box) return;
    box.hidden = !con.pics.length;
    box.replaceChildren(...con.pics.map((p, i) => {
      const d = document.createElement("div");
      d.className = "con-thumb " + p.state; d.title = p.state === "bad" ? "not sent: " + p.why : p.state === "up" ? "on its way…" : "picture " + (i + 1) + ": it goes with your reply";
      d.innerHTML = `<img alt=""><span>${p.state === "up" ? "…" : p.state === "bad" ? "!" : ""}</span><button type="button" aria-label="Take it back">×</button>`;
      d.querySelector("img").src = p.url; d.querySelector("button").onclick = () => dropPic(p);
      return d;
    }));
  }
  // Open on agent `who` (or, `pend`, a new agent starting, till it has a name: attach). The page decides who may: each page
  // opens it only from a click of its owner's own (room.html's openConsole, index.html's).
  function openPane(who, pend = null) {
    const el = consoleEl();
    if (con.who !== who || pend || con.pending) {
      clearPics();   // the pictures were another agent's
      Object.assign(con, { last: null, hist: [], histBase: 0, watched: null, queue: [] });
      for (const c of ["since", "hist", "live", "err", "st"]) el.querySelector(".con-" + c).textContent = "";
    }
    con.who = who; con.pending = pend; el.hidden = false; placeConsole(con.rect);   // pend: a new agent's (ticketAct), till it has a name (consoleAttach)
    if (pend) { showQueue(); el.querySelector(".con-st").textContent = "· starting…"; }
    consoleNamed(pend ? "new agent · " + pend.label : who);
    el.querySelector("textarea").focus();
    if (!pend) pollConsole();
  }
  function consoleNamed(name) {
    const el = con.el;
    el.setAttribute("aria-label", name + "'s terminal");
    el.querySelector(".con-head b").textContent = name;
    el.querySelector("textarea").placeholder = con.pending ? "A note for it, sent as soon as it is up (Enter queues it)" : "Reply to " + name + " (Enter sends; Shift+Enter, a new line; paste or drop a picture to send it too)";
  }
  // While a new agent starts: what it is for, and what you have queued for it.
  function showQueue() {
    const p = con.pending; if (!p || !con.el) return;
    con.el.querySelector(".con-live").textContent = `starting a new agent in ${p.label}${p.ref ? " for " + p.ref : ""}: a new herdr tab, Claude, then /go${p.ref ? " " + p.ref : ""}…\n`
      + (con.queue.length ? "\nqueued for it, sent once it is up:\n" + con.queue.map(t => "  • " + t).join("\n") : "\n(type here meanwhile: it waits for it)");
  }
  // The new agent your click started has a name: its console, from here on, what you queued sent to it in order.
  function consoleAttach(seq, who) {
    if (!con.pending || con.pending.seq !== seq || !con.el || con.el.hidden) return;
    con.pending = null; con.who = who; consoleNamed(who);
    con.el.querySelector(".con-live").textContent = ""; con.el.querySelector(".con-st").textContent = "";
    pollConsole();
  }
  function consoleFailed(seq, why) {
    if (!con.pending || con.pending.seq !== seq || !con.el) return;
    con.el.querySelector(".con-st").textContent = "· not started";
    con.el.querySelector(".con-err").textContent = "no new agent: " + why + (con.queue.length ? " (your notes are still here: copy them)" : "");
  }
  // What was queued for a new agent, to it now, in order (not while it is at a question: those wait, and go after).
  async function flushQueue(status) {
    if (!con.queue.length || con.flushing || status === "blocked" || !con.who) return;
    con.flushing = true;
    try {
      while (con.queue.length) { await consoleCall("/send", { agent: con.who, text: con.queue[0] }); con.queue.shift(); }
    } catch (err) { con.el.querySelector(".con-err").textContent = "a queued note waits: " + err.message; }
    finally { con.flushing = false; }
  }
  function closeConsole() {
    if (!con.el) return;
    clearPics(); con.el.hidden = true; con.who = ""; con.pending = null; con.gen++; clearTimeout(con.timer);
    if (opts.onClose) opts.onClose();
  }
  // One read now, and the next in a second (in three when it failed; none while the tab is hidden or it is closed).
  // Each brings the screen, and the scrollback lines above it from the last one this page has on (bin/consoles keeps
  // them, each at its place: a line that came back onto the screen is handed back, so the page drops it).
  async function pollConsole() {
    clearTimeout(con.timer);
    const who = con.who, gen = ++con.gen; if (!who) return;
    let wait = CONSOLE_POLL_MS;
    try {
      if (!document.hidden) {
        const have = con.histBase + con.hist.length;
        const r = await consoleCall("/screen?agent=" + encodeURIComponent(who) + "&since=" + (con.watched === null ? 0 : have));
        if (gen !== con.gen) return;   // closed, or another read started meanwhile
        const pre = con.el.querySelector("pre"), st = con.el.querySelector(".con-st"), histEl = pre.querySelector(".con-hist");
        st.textContent = r.status === "blocked" ? "· waiting on you" : r.status ? "· " + r.status : "";
        flushQueue(r.status);   // anything queued for it as it started
        st.className = "con-st" + (r.status === "blocked" ? " blocked" : "");
        if (con.watched !== null && r.watched !== con.watched) {   // bin/consoles restarted: its scrollback from the start
          Object.assign(con, { hist: [], histBase: 0, watched: null }); histEl.textContent = ""; wait = 0;
        } else {
          const atEnd = con.last === null || pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
          let dirty = Infinity;   // the first line (in the agent's output) no longer as the page shows it
          con.watched = r.watched;
          if (r.from < con.histBase || r.from > have) { con.hist = r.lines.slice(); con.histBase = r.from; dirty = -Infinity; }
          else {
            if (r.from < have) { con.hist.length = r.from - con.histBase; dirty = r.from; }   // lines back on the screen
            con.hist.push(...r.lines);
          }
          if (con.hist.length > CON_HIST_MAX) { const cut = con.hist.length - CON_HIST_MAX + 1000; con.hist.splice(0, cut); con.histBase += cut; }
          showHist(histEl, dirty);
          const at = new Date(r.watched * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
          pre.querySelector(".con-since").textContent = con.histBase === 0
            ? `  ── scrollback from ${at}, when bin/consoles began watching ──\n` : "  ── earlier lines are no longer kept ──\n";
          if (r.screen !== con.last) { pre.querySelector(".con-live").innerHTML = ansiHtml(squeezed(r.screen).replace(/\s+$/, "")); con.last = r.screen; }
          if (atEnd) pre.scrollTop = pre.scrollHeight;
        }
        if (con.pollErr) { con.el.querySelector(".con-err").textContent = ""; con.pollErr = false; }   // the read's own trouble, gone
      }
    } catch (err) {
      if (gen !== con.gen) return;
      con.el.querySelector(".con-err").textContent = err.message; con.pollErr = true; wait = 3000;
    }
    if (gen === con.gen && con.who) con.timer = setTimeout(pollConsole, wait);
  }
  // The scrollback shown as con.hist has it (its lines from con.histBase on), in blocks of CON_CHUNK lines by their place
  // in the agent's output (block k: lines k·CON_CHUNK on), each of them knowing the lines it shows (_from, _to). Those
  // from line `dirty` on have changed: a block with any of them is drawn again; one short of its lines (the last), given
  // the rest; one off the top (trimmed) or past the end, dropped. So a poll touches a block or two, not the scrollback
  // (all of it drawn again, then laid out, was a stall of a quarter of a second on a long one).
  function showHist(histEl, dirty) {
    const base = con.histBase, end = base + con.hist.length;
    const html = (a, b) => ansiHtml(con.hist.slice(a - base, b - base).join("\n")) + "\n";
    let next = Math.floor(base / CON_CHUNK);
    for (const b of [...histEl.children]) {
      const from = Math.max(b._k * CON_CHUNK, base), to = Math.min((b._k + 1) * CON_CHUNK, end);
      if (to <= from || !(b._k >= next)) { b.remove(); continue; }
      if (b._from !== from || b._to > to || b._to > dirty) b.innerHTML = html(from, to);
      else if (b._to < to) b.insertAdjacentHTML("beforeend", html(b._to, to));
      b._from = from; b._to = to; next = b._k + 1;
    }
    for (let k = next; k * CON_CHUNK < end; k++) {
      const b = document.createElement("span"), from = Math.max(k * CON_CHUNK, base), to = Math.min((k + 1) * CON_CHUNK, end);
      b._k = k; b._from = from; b._to = to; b.innerHTML = html(from, to); histEl.appendChild(b);
    }
  }
  return { openPane, close: closeConsole, attach: consoleAttach, failed: consoleFailed, call: consoleCall, get who() { return con.who; } };
}
window.AgentConsole = { make };
})();
