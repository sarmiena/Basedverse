# three.js, served by the board

The Agent Room's three.js, kept here and served by the board (`/vendor/three.min.js`, `/vendor/OrbitControls.js`) rather
than loaded from a CDN: room.html runs with the owner's session (which reaches the agents' consoles) and, in the engine,
with the engine's key, so no third party's server gets to put code into it.

- `three.min.js`: three.js r128 (npm `three@0.128.0`, `build/three.min.js`), 603445 bytes. MIT, Copyright 2010-2021
  Three.js Authors. Checked on 2026-11-01 against cdnjs's published SRI
  (`sha512-dLxUelApnYxpLt6K2iomGngnHO83iUvZytA3YjDUCjT0HDOHKXnVYdf3hU4JjM8uEhxf9nD1/ey98U3t2vZ0qQ==`) and jsdelivr's
  sha256 (`knS7zsjZYWhibHMrXTHHdaqM+36qBZm+wMF1kIosHOI=`).
- `OrbitControls.js`: `three@0.128.0/examples/js/controls/OrbitControls.js`, 26375 bytes, MIT. Checked against jsdelivr's
  sha256 (`ArtK3nEPPmBzKeN6IfCYvDrHDrbjPa+KZeefTbeF57I=`).

A newer three.js is a deliberate change here (and Visit's visitors load the same revision themselves: basedverse
checks it by its own SRI table).
