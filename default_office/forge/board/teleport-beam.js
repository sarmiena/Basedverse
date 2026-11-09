// default_office's copy of basedverse's example teleport animation (visit/examples/teleport/beam.js, v5.2.0). It is this
// room's own: your teleport animation (your page gives it Visit, as it gives your lowrider), and how the room plays
// anyone's (its page, the engine's: what each visitor brought, and yours). What it changes from the example is in
// VISIT.md; none so far.
//
// beam.js: a teleport animation, as example code (basedverse: visit/examples/teleport). Since 5.2 Visit plays no
// animation of its own as someone teleports (SPEC.md 8.15): each person's room gives Visit theirs, as it gives their
// mounts (a possession with `teleport: true`), and Visit carries it to whatever room they are in; that room plays it,
// if it likes, when it hears them charging, going or coming ("teleported"). Copy this file into your room's tree and
// change whatever you like: it is yours, and visit-client never serves it.
//
//   <script src="beam.js"></script>          (it uses the THREE you give it)
//
//   Your user's, to bring along (their own look in every room that plays it):
//     v.possessions([...yours, { id: "teleport", name: "Beam", object: TeleportBeam.make(THREE, { color: 0x66ddff }),
//                                unit: 1, teleport: true }]);
//     Its "out" clip is 2 s: their teleport waits that long, held where they stand, then goes.
//
//   Your room, playing what visitors brought (on the room's page, the host's):
//     v.on("teleported", e => { if (e.animation) TeleportBeam.play(THREE, scene, e.animation, e.way === "in" ? "in" : "out", e.at); });
//
// What it looks like: going ("out", 2 s), a glow spreading at their feet, rings running up faster and faster, sparks
// gathering, a column of light forming round them, and, as the 2 s end, a flash as it collapses into a line and is gone;
// coming ("in", 1.6 s), the column coming down onto them, rings running down, and fading. In metres, additive (it
// lights what is behind it). Everything that moves is keyframed (AnimationClips on named parts: positions, scales,
// opacities), so it travels as data and plays in any room's three.js.
"use strict";
(function () {
  const FPS = 30, OUT = 2, IN = 1.6;
  const ease = x => x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x), bump = (t, a, b) => t <= a || t >= b ? 0 : Math.sin(Math.PI * (t - a) / (b - a));

  // The parts, by name (an animation finds them by it), and what each does at time t of a clip.
  function parts(THREE, color) {
    const g = new THREE.Group();
    g.name = "teleport-beam";
    const mat = c => new THREE.MeshBasicMaterial({ color: c, opacity: 0, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide, fog: false, toneMapped: false });
    // each part in a group of its own, moved and scaled by it (a primitive's own translate does not travel: SPEC 8.1)
    const part = (name, geo, m, y = 0, flat = false) => {
      const p = new THREE.Group(), o = new THREE.Mesh(geo, m);
      p.name = name + "-g"; o.name = name; p.position.y = y;
      if (flat) o.rotation.x = -Math.PI / 2;
      p.add(o); g.add(p);
      return { p, o };
    };
    const P = {
      beam: part("beam", new THREE.CylinderGeometry(0.55, 0.55, 3.2, 28, 1, true), mat(color), 1.6),
      core: part("core", new THREE.CylinderGeometry(0.1, 0.16, 3.4, 12, 1, true), mat(0xffffff), 1.7),
      disc: part("disc", new THREE.CircleGeometry(0.95, 36), mat(color), 0.03, true),
      rings: [0, 1, 2].map(i => part("ring" + i, new THREE.TorusGeometry(0.62, 0.035, 8, 44), mat(color), 0, true)),
      sparks: Array.from({ length: 14 }, (_, i) => Object.assign(part("spark" + i, new THREE.OctahedronGeometry(0.055), mat(0xffffff)),
        { a: i / 14 * Math.PI * 2, r: 0.32 + (i % 3) * 0.13, s: 0.8 + (i % 4) * 0.15, d: (i % 5) * 0.07 })),
    };
    return { g, P };
  }

  // Going: OUT seconds, the flash and collapse its last quarter second.
  function outAt(t, P, put) {
    const J = OUT - 0.25, build = Math.min(1, t / J), gone = ease((t - J) / 0.25);
    put(P.disc, { opacity: (0.15 + 0.6 * build) * (1 - gone), scale: 0.4 + 0.9 * ease(build) });
    const wide = Math.max(0.02, (0.15 + 0.85 * ease((t - 0.7) / (J - 0.8))) * (1 - 0.97 * gone));
    put(P.beam, { opacity: 0.75 * ease((t - 0.5) / (J - 0.6)) * (1 - 0.6 * gone), sx: wide, sy: 1 + 0.4 * gone, y: 1.6 + 0.6 * gone });
    put(P.core, { opacity: bump(t, J - 0.45, OUT + 0.01) });
    P.rings.forEach((r, i) => {
      const ph = (0.5 * t + 0.8 * t * t / J + i / 3) % 1;   // a cycle up each, faster and faster
      put(r, { y: 0.1 + 2.9 * ph, scale: 1 - 0.35 * ph, opacity: 0.9 * Math.sin(Math.PI * ph) * Math.min(1, t / 0.4) * (1 - gone) });
    });
    for (const sp of P.sparks) {
      const k = ((t * (0.6 + 0.9 * build) + sp.d * 3) % 1.2) / 1.2, a = sp.a + t * 4 * sp.s * (0.5 + build);
      put(sp, { x: Math.cos(a) * sp.r * (1.3 - 0.6 * k), y: 0.15 + 2.6 * k * sp.s, z: Math.sin(a) * sp.r * (1.3 - 0.6 * k),
        opacity: Math.sin(Math.PI * k) * Math.min(1, t / 0.3) * (1 - gone) });
    }
  }

  // Coming: IN seconds.
  function inAt(t, P, put) {
    const on = ease(t / 0.15), off = ease((t - 0.75) / 0.85), level = on * (1 - off), down = ease(t / 0.25) || 0.01;
    put(P.beam, { opacity: 0.75 * level, sx: Math.max(0.05, 1.3 - 1.05 * ease(t / 0.3) + 0.6 * off), sy: down, y: 3.2 - 1.6 * down });
    put(P.core, { opacity: bump(t, 0, 0.9) });
    put(P.disc, { opacity: 0.7 * level, scale: 0.5 + 0.8 * ease(t / 0.4) });
    P.rings.forEach((r, i) => {
      const k = ease((t - 0.08 - 0.14 * i) / 0.8);
      put(r, { y: 3.0 - 2.9 * k, scale: 1 - 0.35 * k, opacity: 0.9 * bump(t, 0.08 + 0.14 * i, 0.88 + 0.14 * i) });
    });
    for (const sp of P.sparks) {
      const k = Math.max(0, (t - sp.d) / 1.2), a = sp.a + k * 5 * sp.s;
      put(sp, { x: Math.cos(a) * sp.r * (1 + 0.4 * k), y: 3.0 - 2.7 * k * sp.s, z: Math.sin(a) * sp.r * (1 + 0.4 * k), opacity: bump(t, sp.d, sp.d + 1.2) });
    }
  }

  // A clip of `secs`, sampled FPS times a second from `at`: each part's place, scale and opacity, keyframed.
  function clip(THREE, name, secs, at, P) {
    const tracks = new Map(), times = [];
    const key = (path, Track, n) => tracks.get(path) || (tracks.set(path, { Track, n, values: [] }), tracks.get(path));
    for (let i = 0, n = Math.round(secs * FPS); i <= n; i++) {
      const t = i / FPS;
      times.push(t);
      at(t, P, (part, v) => {
        const g = part.p.name, o = part.o.name, p = part.p.position, sc = part.p.scale;
        key(g + ".position", THREE.VectorKeyframeTrack, 3).values.push(v.x ?? p.x, v.y ?? p.y, v.z ?? p.z);
        const sx = v.scale ?? v.sx ?? 1, sy = v.scale ?? v.sy ?? 1;
        key(g + ".scale", THREE.VectorKeyframeTrack, 3).values.push(sx, sy, v.scale ?? v.sx ?? sc.z);
        key(o + ".material.opacity", THREE.NumberKeyframeTrack, 1).values.push(v.opacity ?? 0);
      });
    }
    const out = [];
    for (const [path, { Track, n, values }] of tracks) if (values.length === times.length * n) out.push(new Track(path, times, values));
    return new THREE.AnimationClip(name, secs, out);
  }

  // The animation: an Object3D, its clips "out" and "in" on it (`animations`), its parts unseen until a clip plays.
  function make(THREE, { color = 0x66ddff } = {}) {
    const { g, P } = parts(THREE, color);
    g.animations = [clip(THREE, "out", OUT, outAt, P), clip(THREE, "in", IN, inAt, P)];
    return g;
  }

  // Plays one of an animation's clips (one a visitor brought: "teleported"'s `animation`, a fresh copy) once, in `root`
  // at `at` (their feet, in your room's terms), then takes it out and lets it go. Steps itself; { done, stop() }.
  function play(THREE, root, animation, name, at) {
    const c = (animation.animations || []).find(x => x.name.toLowerCase() === String(name).toLowerCase());
    if (!c) return { done: Promise.resolve(), stop() {} };
    animation.position.set(Number(at.x) || 0, Number(at.y) || 0, Number(at.z) || 0);
    root.add(animation);
    const mixer = new THREE.AnimationMixer(animation), act = mixer.clipAction(c);
    act.setLoop(THREE.LoopOnce, 1); act.clampWhenFinished = true; act.play();
    let last = performance.now(), timer = 0, finish;
    const done = new Promise(r => { finish = r; });
    const stop = () => {
      clearInterval(timer); mixer.stopAllAction();
      if (animation.parent) animation.parent.remove(animation);
      animation.traverse(o => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose(); } });
      finish();
    };
    mixer.update(0);
    timer = setInterval(() => {   // (a timer, not a frame: a room's page in the engine draws no frames)
      const now = performance.now();
      mixer.update((now - last) / 1000); last = now;
      if (act.time >= c.duration) stop();
    }, 1000 / 60);
    return { done, stop };
  }

  window.TeleportBeam = { make, play, OUT, IN };
})();
