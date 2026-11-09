# Shared by bin/start and bin/deploy-lobby: reading and writing the project .env ($ENV), asking a lobby
# whether it is up, and where basedverse is. Sourced, never run.

# KEY -> its value in .env ('' when blank or missing): the last such line, quotes and trailing space off.
envval() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV" | tail -n 1 | sed 's/[[:space:]]*$//' | tr -d '"'"'"; }

# KEY VALUE: rewrite KEY's line in .env (or add one), every other line as it was.
setenv() {
  python3 - "$ENV" "$1" "$2" <<'PY'
import re, sys
path, key, val = sys.argv[1:]
lines = open(path, encoding="utf-8").read().splitlines(True)
hit = [i for i, l in enumerate(lines) if re.match(r"\s*%s\s*=" % key, l)]
if hit:
    lines[hit[-1]] = "%s=%s\n" % (key, val)
else:
    lines.append(("" if not lines or lines[-1].endswith("\n") else "\n") + "%s=%s\n" % (key, val))
open(path, "w", encoding="utf-8").writelines(lines)
PY
}

# A lobby URL (ws:// or wss://) -> its /health answer ("ok 2 online"), or nothing.
lobby_health() {
  python3 - "$1" <<'PY' 2>/dev/null || true
import sys, urllib.request
u = sys.argv[1].replace("wss://", "https://", 1).replace("ws://", "http://", 1).rstrip("/") + "/health"
print(urllib.request.urlopen(u, timeout=5).read().decode().strip())
PY
}

# Where basedverse (the lobby and Visit, its own repository) is: BASEDVERSE in .env, relative to this
# repository or absolute, else ../basedverse beside it. Printed absolute, whether it is there or not.
basedverse() {
  local d; d="$(envval BASEDVERSE)"; d="${d:-../basedverse}"; d="${d/#\~/$HOME}"
  case "$d" in /*) ;; *) d="$ROOT/$d" ;; esac
  (cd "$d" 2>/dev/null && pwd) || printf '%s\n' "$d"
}
