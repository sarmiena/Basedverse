# Widgets: the UI your room gives its user

A widget is one piece of your room's UI around the 3D: the panel, the chat window, the list your user teleports from.
Since 5.0, Visit draws none of them in your page ([UI.md](../UI.md) says why). Each one is your room's own code, built on
Visit's API (SPEC.md section 9). **Nothing here is forced on anyone**: your room has each widget the way it likes, or not
at all, and its `VISIT.md` says which.

What this folder promises is the list: **every widget a Visit feature needs has a page here**, and a release that adds
one, or changes what one must do, says so in the CHANGELOG. So an integration, or an upgrade, goes down this list and
makes sure the room has what its features need. A room without a widget a feature needs has the feature in name only:
it works under the hood, and its user cannot reach it.

| widget | gives your user | your room needs it | the reference |
|---|---|---|---|
| [panel](panel.md) | who they are in the lobby, worlds, people, voice, calls, the door; the way to the other widgets | always | `VisitUI.panel` |
| [chat](chat.md) | lines, whispers, rooms' and worlds' chats, the lobby's; pictures, voice messages; the mailbox; 🎧 | always | `VisitUI.chat` (the panel's) |
| [target card](target-card.md) | the person or figure they picked, a figure's actions, Say, Whisper | rooms with figures (3.3) | `VisitUI.targetCard` (the panel's) |
| [map](map.md) | their world from above: placing friends' rooms; a friend's view of them | a page with a world (`v.world`) | `VisitUI.map` (the panel's Map) |
| [teleport](teleport.md) | the rooms that would take them now, and going there | every room from 5.1 | the panel's ✦, T, `ui.teleports(on)` |
| [⏱ corner](perf.md) | how their page is doing, a row a second | optional (troubleshooting) | `VisitUI.perf` (the panel's ⏱) |
| [stage](stage.md) | their own room first person, and the room they visit | a page that draws no world | `VisitUI.stage` |

## Two ways to have each

1. **The reference's.** [`examples/ui/visit-ui.js`](../examples/ui/visit-ui.js) has every widget above, built on the
   public API alone. Copy it into your room's tree once, and it is yours: change whatever your user wants. visit-client
   never serves it, so an upgrade never changes your copy: when a release adds or changes a widget, bring over that part
   of the reference's diff between the two tags (the CHANGELOG says which).
2. **Your own**, in your room's style: each widget's page says what it gives your user, what it is built on, and what
   yours **must** do. Mix them as you like: the reference's chat window with your own panel, say.

## Each widget's page

- **Gives your user**, and **your room needs it**: when it is needed, and when it is not.
- **The reference**: which call makes it, and where it sits.
- **What it does / built on**: each thing it shows or does, and the state, actions and events it uses.
- **If you build your own, it must**: the list to check yours against.
- **Check**: how to see it work, with INTEGRATING.md step 8's two rooms.

## Keeping track

In your `VISIT.md`, a line per widget: the reference's (as it came, or changed: how), your own (where), or none (and
why, in your user's words). The `/basedverse:visit` skill goes down this list on every upgrade, and asks about any
widget a feature needs that the room does not have.
