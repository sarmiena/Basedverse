# The stage, and your own room

**Gives your user:** on a page that draws no world: their own room, first person, and the room they visit.

**Your room needs it:** only on a page that draws no world of its own (engine/SPEC.md 7). A page with a world needs none.

**The reference:** `VisitUI.stage(v)`, on `v.homeView`.

On a page that draws no world of its own (engine/SPEC.md 7), the reference stage does two things:
- It puts your user's own room behind everything, first person, with a line under it (`v.homeView` draws it).
- While they visit a room, it shows that room full-window or in a corner, with how the link is going, **Back to my
  room** and **Leave**.

A page with its own world needs none of it: rooms are visited by walking onto their plots.

| What it does | Built on |
|---|---|
| Your own room, first person | `v.homeView(element)` → `{ element, shown, release() }` (it draws only while `shown`: not while visiting, nor once your page makes a world); `v.home` (`paused`: your room reloading) |
| The room visited | `v.visiting` ("change"), `v.view(element)` → `{ setActive(on), active, release() }`, `v.viewFocused` |
| How it is going, Leave | `v.stats` ("stats": `path`, `rtt`, `latency`, `fps`), `v.kick(v.visiting.id)` |

## If you build your own, it must

- draw your user's own room (`v.homeView`), and the room they visit (`v.view`) while they do;
- say how the visit's link is going, and let them leave.

## Check

A page without a world: your room first person; visiting, the other room full-window, Back to my room and Leave.

[All widgets](README.md) · [UI.md](../UI.md): why your page's UI is your own
