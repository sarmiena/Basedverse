# Your room's UI

Since 5.0 (#82), Visit puts **no UI in your page**. There is no panel, chat window, map, stage bar, target card or ⏱
corner from visit.js. Everything a person clicks, types or reads around the 3D is your room's own: its HTML, its CSS
and its code. Visit gives you the state, the actions and the events to build it from (SPEC.md section 9). This guide
says why, and how to have them; [widgets/](widgets/README.md) has each piece, and which your room needs.

**Why.** Rooms used to reach into Visit's own UI to fit it to themselves: a row added inside its panel, its card turned
off for their own. Any upgrade that changed that markup could break them. Now the UI is the room's code, so an upgrade
changes only the API under it, and the CHANGELOG says when it does.

**What Visit still draws** is only what is inside the 3D views it is given:
- the room your user visits (`v.view(element)`);
- your own room, first person (`v.homeView(element)`);
- your world (`v.world(...)`).

In those views it draws people's bodies and mounts, chat bubbles over speakers, the 🔊 over whoever your user hears,
and, in a view, the crosshair and the label of what it points at. It also plays voices and room sounds. Each of those has its switch (`bubbles: false`,
`speaking: false`). As someone teleports (5.2), Visit plays nothing: each room plays the animation people bring, if it
likes ([examples/teleport](examples/teleport/beam.js)); a warp over your world's view is yours to ask for
(`v.world({ warp: true })`).

## Two ways to have one

1. **Copy the reference UI.** [`examples/ui/visit-ui.js`](examples/ui/visit-ui.js) is the UI Visit used to draw,
   rewritten on the public API alone. It contains the panel, the chat window, the target card, the map, the stage bar
   and the ⏱ corner. Copy it into your room's tree, serve it with your page, and change whatever your user wants: it is
   yours. visit-client never serves it, so a Visit upgrade never changes your copy. When a new Visit adds something you
   want, read its CHANGELOG and the reference's diff, and take what you like.
2. **Build your own**, widget by widget ([widgets/](widgets/README.md)), in your room's own style. Any mix works: the
   reference's chat window with your own panel, say.

Either way, write down in your workroom (a `VISIT.md`) what you changed and why, in your user's words. Then an upgrade
keeps it.

### The reference UI, in five lines

```html
<script src="/your/copy/of/visit-ui.js"></script>   <!-- before or after visit.js: it reads Visit when called -->
```

```js
const visit = await Visit.connect({ ... });
const ui = VisitUI.panel(document.getElementById("visit-panel"), visit);   // the HUD, with the chat window, the
                                                                           // target card and the ⏱ corner
VisitUI.stage(visit);   // a page that draws no world of its own: your room, first person, and the room visited
```

- `VisitUI.panel(element, v, { chat, card, teleport })`: `chat: false`, `card: false` or `teleport: false` leaves that
  piece out.
- It returns `{ element, chat, card, perf, map, say(), teleports(on), release() }`: `ui.teleports()` opens or closes
  its teleport list (5.1.1), for your own button or a click on your pad ([widgets/teleport.md](widgets/teleport.md));
  `teleport: false` leaves it out (5.2), for a room with a teleport widget of its own.
- `ui.chat.aim(target)` points the chat box at someone (the card's Say and Whisper do this).
- Each piece also stands alone: `VisitUI.chat(v, { element, fade })`, `VisitUI.targetCard(v, { chat })`,
  `VisitUI.map(v)`, `VisitUI.perf(v)` and `VisitUI.stage(v)`.

Its class names and the chat window's CSS variables are the file's own:
- the classes `.visit-panel`, `.visit-chat`, `.visit-card`, `.visit-map`, `.visit-stage`, `.visit-home-box`,
  `.visit-perf` and `.visit-lightbox`;
- `.visit-folded` on the panel's element while it is folded;
- the variables `--visit-chat-left` and `--visit-chat-bottom`.

Style them from your page, or change them in your copy.

## The widgets

Each piece has a page of its own in **[widgets/](widgets/README.md)**: what it gives your user, when your room needs it,
the reference's, what it is built on, what yours must do if you build your own, and how to check it. The list there is
every widget a Visit feature needs; a release that adds one says so in the CHANGELOG.

- [the panel](widgets/panel.md) · [the chat window](widgets/chat.md) · [your target's card](widgets/target-card.md) ·
  [the map](widgets/map.md) · [teleport](widgets/teleport.md) (5.1) · [the ⏱ corner](widgets/perf.md) ·
  [the stage](widgets/stage.md)

## Upgrading a 4.x room

1. **Copy `examples/ui/visit-ui.js` into your room's tree,** serve it, and load it with your page.
2. **Rename the calls:**
   - `Visit.panel` → `VisitUI.panel`
   - `Visit.chat` → `VisitUI.chat`
   - `Visit.map` → `VisitUI.map`
   - `Visit.stage` → `VisitUI.stage`

   The options are the same (`{ chat: false }`, `{ card: false }`), and so are the classes and CSS variables.
   `Visit.panel` and the others now throw, and the error says where they went.
3. **Make your copy hold what you added to Visit's UI.** A row you appended inside the panel can now be a line in your
   copy's `draw()`. A card you turned off can be taken out of your copy.
4. **Find the reference's handles where Visit's events used to be.**
   - The chat box: `v._emit("chat_box", ...)` → `ui.chat.aim(...)`.
   - The ⏱ corner: `v.perf(true)` measures but no longer draws its own corner; `VisitUI.perf(v)` draws it (the
     panel makes one).

Nothing changes a protocol's name, so offices on 4.x and 5.0 still meet and visit each other.
