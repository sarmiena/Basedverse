// Visit 4.0's acts in the Agent Room (room.html; basedverse#59, #60): what the room does to a visitor's body, said to
// Visit (actOut: visit.perform, and a copy of what is in its hand with visit.hold), and your own avatar's clips for
// them (meAvatar: sit, lie, drink, eat, throw, swat, wave, and its `hand`). room.html's own functions, run against a
// stand-in for Visit that writes down what it is told, and the room's own three.js. No browser, no network.
//
// Run: node board/test_acts.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "room.html"), "utf8");
const THREE = createRequire(import.meta.url)(join(here, "vendor/three.min.js"));
let fails = 0;
const check = (what, ok, detail = "") => { fails += !ok; console.log(`[${ok ? "ok" : "FAIL"}] ${what}${ok || !detail ? "" : ": " + detail}`); };

// A definition of room.html's, at the left margin, to the next thing there (as test_visitors.mjs reads them).
const lines = html.split("\n");
function def(name) {
  const at = lines.findIndex(l => new RegExp(`^(?:function\\s+${name}\\s*\\(|const\\s+${name}\\s*=)`).test(l));
  if (at < 0) throw new Error(`room.html has no ${name}`);
  let end = at + 1;
  while (end < lines.length && !/^\S/.test(lines[end])) end++;
  if (/^}/.test(lines[end] || "")) end++;
  return lines.slice(at, end).join("\n");
}

// ---------- actOut: what Visit is told, step by step ----------
const told = [];
const window = { visit: {
  perform: (id, clip, o) => { told.push(["perform", id, clip, o && o.loop]); return true; },
  hold: (id, obj) => { told.push(["hold", id, obj ? obj.name || "thing" : null, obj]); return true; },
} };
const actOut = new Function("window", def("VISIT_ACT") + "\n" + def("actOut") + "\nreturn actOut;")(window);
const said = () => told.splice(0).map(([k, , a, b]) => k === "perform" ? `perform ${a}${b === false ? " once" : ""}` : `hold ${a}`).join(", ");
const thing = name => { const g = new THREE.Group(); g.name = name; g.add(new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.1))); return g; };
const body = () => ({ act: null, throwing: null, swatting: null, cup: null, peel: null });

let b = body();
actOut(b, "v1", "theirs", null);
check("standing about: nothing said", said() === "");
b.act = { kind: "sit", t: 0.2, seat: {} };
actOut(b, "v1", "theirs", null);
check("on the way down onto a seat: nothing yet (its ease would move it 0.3 m)", said() === "");
b.act.t = 0.5; actOut(b, "v1", "theirs", null);
check("sat: sit, lasting", said() === "perform sit");
actOut(b, "v1", "theirs", { clip: "sit" });
check("still sat, and Visit has it: nothing more", said() === "");
actOut(b, "v1", "theirs", null);
check("Visit let the seat go (a float drifting): sit again", said() === "perform sit");
b.act = null; actOut(b, "v1", "theirs", { clip: "sit" });
check("walked off: none", said() === "perform null");
b.act = { kind: "sit", t: 1, seat: { lie: "lounger" } }; actOut(b, "v1", "theirs", null);
check("on a lounger: lie", said() === "perform lie");
b.throwing = { t: 0 }; actOut(b, "v1", "theirs", { clip: "lie" });
check("throwing from it: throw, once", said() === "perform throw once");
b.throwing = null; actOut(b, "v1", "theirs", null);
check("thrown: back to lying", said() === "perform lie");
b = body();

const cup = thing("cup"); cup.visible = false;
b.act = { kind: "drink", t: 0.1, held: cup }; actOut(b, "v2", "theirs", null);
check("at the cooler: drink, for as long as the room has it (looped); the cup not yet shown, not held", said() === "perform drink");
cup.visible = true; actOut(b, "v2", "theirs", { clip: "drink" });
const copy = told.find(t => t[0] === "hold") && told.find(t => t[0] === "hold")[3];
check("the cup in hand: a copy of it held", said() === "hold cup" && copy && copy !== cup && copy.children[0].geometry === cup.children[0].geometry, "the room's own cup stays on its figure's arm");
b.act = null; actOut(b, "v2", "theirs", { clip: "drink" });
check("drunk: none, and nothing held", said() === "perform null, hold null");
const tray = thing("cocktail"); tray.userData.visitStatic = true;
b.act = { kind: "cocktail", t: 1, held: tray }; actOut(b, "v2", "theirs", null);
const glass = told.find(t => t[0] === "hold")[3];
check("a cocktail: drink, its glass held, without the landscape's mark", said() === "perform drink, hold cocktail" && !glass.userData.visitStatic);
b.act = null; b.cup = { g: tray, sip: null }; actOut(b, "v2", "theirs", { clip: "drink" });
check("kept: the same glass, still held; no act between sips", said() === "perform null");
b.cup.sip = 0; actOut(b, "v2", "theirs", null);
check("a sip of it: drink", said() === "perform drink");
b.cup = null; actOut(b, "v2", "theirs", { clip: "drink" });
check("empty and gone: none, nothing held", said() === "perform null, hold null");
b = body();

const banana = thing("banana"), peel = thing("peel");
b.act = { kind: "eat", t: 1, held: banana }; actOut(b, "v3", "theirs", null);
check("a banana: eat, the banana held", said() === "perform eat, hold banana");
b.act = null; b.peel = peel; actOut(b, "v3", "theirs", { clip: "eat" });
check("eaten: the peel held", said() === "perform null, hold peel");
b.throwing = { t: 0 }; actOut(b, "v3", "theirs", null);
check("thrown: throw, once", said() === "perform throw once");
b.peel = null; actOut(b, "v3", "theirs", { clip: "throw" });
check("out of the hand: nothing held", said() === "hold null");
b.throwing = null; actOut(b, "v3", "theirs", null);
check("done throwing: none", said() === "perform null");
b.swatting = { t: 0 }; actOut(b, "v3", "theirs", null);
b.swatting = { t: 0 }; actOut(b, "v3", "theirs", { clip: "swat" });
check("two swats in a row: swat twice", said() === "perform swat once, perform swat once");
b = body();

b.peel = peel; actOut(b, "v4", "mine", null);
check("a visitor the room draws as its own figure: no copy held (its figure has the thing)", said() === "");
actOut(b, "v4", "theirs", null);
check("drawn as their own model: the peel held", said() === "hold peel");
actOut(b, "v4", "mine", null);
check("as the room's figure again: let go", said() === "hold null");

const none = new Function("window", def("VISIT_ACT") + "\n" + def("actOut") + "\nreturn actOut;")({});
let threw = null; try { none(body(), "v5", "theirs", null); } catch (e) { threw = e; }
check("no Visit: nothing, no error", !threw, String(threw));

// ---------- meAvatar: its clips, each on parts it has; its hand ----------
const stubs = `
const me = { look: { skin: "#c68642", shirt: "#3366cc", pants: "#222222", shoes: "#111111", hat: "none" }, name: "test" };
const M = c => new THREE.MeshLambertMaterial({ color: c });
const label = (t, o, s, parent) => { const g = new THREE.Object3D(); parent.add(g); return g; };
const meFace = () => null, meHair = () => {}, sickNow = () => false, sicken = () => {}, blanket = () => {};
const WHEEL_ARMS = [-1.15, 0.3];
let headBox = null;
${/^function headGeo\(\) \{[\s\S]*?\n\}/m.exec(readFileSync(join(here, "party.js"), "utf8"))[0]}
const Party = { headGeo };`;   // (a head's shape: party.js's own)
const meAvatar = new Function("THREE", stubs + "\n" + def("box") + "\n" + def("meTag") + "\n" + def("meModel") + "\n" + def("meAvatar") + "\nreturn meAvatar;")(THREE);
const av = meAvatar();
{   // a head draws as two (party.js headGeo), not a box's six: the five plain sides with side 0's material, the face (side 4) its own
  const head = av.getObjectByProperty("isMesh", true) && (() => { let h = null; av.traverse(o => { if (o.isMesh && Array.isArray(o.material) && o.material.length === 6) h = h || o; }); return h; })();
  const box = new THREE.BoxGeometry(0.56, 0.56, 0.56), g = head && head.geometry, gi = g && g.index && Array.from(g.index.array), bi = Array.from(box.index.array);
  check("a head draws as two: its five plain sides, then its face (a box's side 4)", !!g && g.type === "BufferGeometry" && JSON.stringify(g.groups.map(x => [x.start, x.count, x.materialIndex])) === "[[0,30,0],[30,6,4]]"
        && JSON.stringify(gi.slice(30)) === JSON.stringify(bi.slice(24, 30)) && JSON.stringify(gi.slice(0, 30).sort()) === JSON.stringify(bi.slice(0, 24).concat(bi.slice(30)).sort()), g ? JSON.stringify(g.groups) : "no head");
}
const names = av.animations.map(c => c.name);
for (const n of ["walk", "idle", "ride", "sit", "lie", "drink", "eat", "throw", "swat", "wave"]) check(`clip ${n}`, names.includes(n), names.join(", "));
const missing = av.animations.flatMap(c => c.tracks.map(t => THREE.PropertyBinding.parseTrackName(t.name).nodeName)).filter(n => !THREE.PropertyBinding.findNode(av, n));
check("every track moves a part the avatar has", missing.length === 0, missing.join(", "));
const hand = av.getObjectByName("hand");
check("a hand, on the right arm (where the room puts a cup or a peel)", !!hand && hand.parent && hand.parent.name === "arm1");
const at = clip => {
  const m = new THREE.AnimationMixer(av), a = m.clipAction(av.animations.find(c => c.name === clip));
  a.play(); m.update(clip === "lie" || clip === "sit" ? 0.5 : 1.0); av.updateMatrixWorld(true);
  const r = { head: av.getObjectByName("head").getWorldPosition(new THREE.Vector3()), hand: hand.getWorldPosition(new THREE.Vector3()) };
  a.stop(); m.uncacheRoot(av); av.updateMatrixWorld(true);
  return r;
};
const stand = { head: av.getObjectByName("head").getWorldPosition(new THREE.Vector3()), hand: hand.getWorldPosition(new THREE.Vector3()) };
const lie = at("lie"), drink = at("drink");
check("lie: back (the avatar faces -z: its head goes to +z) and lower", lie.head.z > stand.head.z + 0.8 && lie.head.y < stand.head.y - 0.5, JSON.stringify(lie.head));
check("drink: the hand up at the mouth", drink.hand.y > 1.3 && drink.hand.distanceTo(drink.head) < 0.75, JSON.stringify(drink.hand));
const after = av.getObjectByName("head").getWorldPosition(new THREE.Vector3());
check("and stood again after", after.distanceTo(stand.head) < 1e-6, JSON.stringify(after));

// ---------- actedOn: your dude on a friend's plot, as its room has you (Visit's frame: act, holding) ----------
const posed = new Function("THREE", stubs + `
const DOWN = new THREE.Vector3(0, -1, 0), MOUTH = [0, 1.62, 0.3], HOLD_AT = [0.1, 1.15, 0.42];
` + ["box", "meTag", "meModel", "pointing", "reach", "sitPose", "actedOn"].map(def).join("\n") + `
me.yaw = 0.3;
return { f: meModel(me.look, "you"), actedOn };`)(THREE);
const { f } = posed, step = (at, t) => { f.g.position.set(0, 0, 0); f.g.rotation.y = 0.3; posed.actedOn(f, at, t); };
const legX = () => f.legs[0].rotation.x, kneeX = () => f.legs[0].userData.knee.rotation.x;
step(null, 0);
check("off a plot: as you stand", legX() === 0 && kneeX() === 0 && f.g.rotation.x === 0);
step({ act: "sit", sit: true }, 1);
check("sat by its room: hips and knees bent", Math.abs(legX() + Math.PI / 2) < 1e-6 && Math.abs(kneeX() - Math.PI / 2) < 1e-6);
step({ act: null }, 2);
check("up again: straight", legX() === 0 && kneeX() === 0);
step({ act: "lie" }, 3);
check("laid back on a lounger: tipped, raised", f.g.rotation.x < -1 && f.g.position.y > 0.4);
step(null, 4);
check("off the plot: upright", f.g.rotation.x === 0);
step({ act: "drink" }, 5); step({ act: "drink" }, 6);
const toMouth = new THREE.Vector3(0, -0.6, 0).applyQuaternion(f.arms[1].quaternion).add(f.arms[1].position);
check("a drink: the right hand up toward the mouth", toMouth.y > 1.35, JSON.stringify(toMouth));
step({ act: null, holding: true }, 7);
const out = new THREE.Vector3(0, -0.6, 0).applyQuaternion(f.arms[1].quaternion).add(f.arms[1].position);
check("holding its thing: the hand out in front", out.z > 0.25, JSON.stringify(out));

console.log(`\n${fails} failure(s)`);
process.exit(fails ? 1 : 0);
