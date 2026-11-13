# Shared by bin/start, bin/down and bin/auth: this office's visit (basedverse 2.0's one program: visit-client,
# the board's sign-in and the engine, with their database inside it). Sourced after env.sh (envval, setenv), with
# ROOT and ENV set. Never run.
#
# visit runs on this machine as one process, on 127.0.0.1 only: the board (on this machine too, bin/lib/board.sh)
# relays the owner's page to it (/visit/ws), asks it for sign-ins (AUTH_PORT), and its engine loads the room's
# server page from the board. Nothing of it is in Docker.

# The tokens (bin/start's, new each run), its log and pid, the state file (where the office keeps its key and
# whether it went offline) and friends' landscapes.
VISIT_DIR="$ROOT/.visit"
AUTH_DIR="$ROOT/.auth"

# The visit program: VISIT_PATH in .env (one of your own: a build of a branch, say), else basedverse's release
# VISIT_VERSION names (v5.3.0 when blank), downloaded once into .visit/bin/ and checked against the release's
# SHA256SUMS: no basedverse checkout, no build. A new VISIT_VERSION is an upgrade.
VISIT_RELEASES="https://github.com/sarmiena/Basedverse/releases/download"

visit_bin() {
  local p; p="$(envval VISIT_PATH)"
  if [ -n "$p" ]; then printf '%s\n' "${p/#\~/$HOME}"; return; fi
  printf '%s\n' "$VISIT_DIR/bin/visit"
}

visit_get() {
  local version bin target
  bin="$(visit_bin)"
  if [ -n "$(envval VISIT_PATH)" ]; then
    [ -x "$bin" ] || { echo "⚠ VISIT_PATH $bin is not a program"; return 1; }
    return 0
  fi
  version="$(envval VISIT_VERSION)"; version="${version:-v5.3.0}"
  if [ -x "$bin" ] && "$bin" --version 2>/dev/null | grep -q "^visit ${version#v} "; then return 0; fi
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) target=x86_64-unknown-linux-musl ;;
    Linux-aarch64) target=aarch64-unknown-linux-musl ;;
    Darwin-arm64) target=aarch64-apple-darwin ;;
    Darwin-x86_64) target=x86_64-apple-darwin ;;
    *) echo "⚠ no visit release for $(uname -s) $(uname -m): set VISIT_PATH to one you built"; return 1 ;;
  esac
  echo "downloading visit $version ($target; Postgres and Chrome are inside it: about 150 MB, once)"
  mkdir -p "$(dirname "$bin")"
  python3 - "$VISIT_RELEASES/$version" "visit-$target.tar.gz" "$bin" <<'PY' || { echo "⚠ visit $version was not downloaded (above)"; return 1; }
import hashlib, io, os, sys, tarfile, urllib.request
base, name, dest = sys.argv[1:]
get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "default_office"}), timeout=900).read()
data = get(base + "/" + name)
sums = dict((l.split()[1].lstrip("*"), l.split()[0]) for l in get(base + "/SHA256SUMS").decode().splitlines() if len(l.split()) == 2)
if sums.get(name) != hashlib.sha256(data).hexdigest():
    sys.exit("visit: %s does not match the release's SHA256SUMS" % name)
body = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").extractfile("visit").read()
open(dest + ".new", "wb").write(body)
os.chmod(dest + ".new", 0o755)
os.replace(dest + ".new", dest)
PY
}

# Where this workroom keeps its data (its database): VISIT_DATA_DIR in .env, written once, so it stays put.
visit_data() {
  local d base; d="$(envval VISIT_DATA_DIR)"
  if [ -z "$d" ]; then
    base="${XDG_DATA_HOME:-$HOME/.local/share}/basedverse"
    d="$base/$(printf '%s' "$(envval LOBBY_NAME)" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9]\{1,\}/-/g; s/^-//; s/-$//')"
    [ "$d" = "$base/" ] && d="$base/default_office"
    setenv VISIT_DATA_DIR "$d"
  fi
  printf '%s\n' "${d/#\~/$HOME}"
}

visit_pid() { cat "$VISIT_DIR/visit.pid" 2>/dev/null || true; }
visit_running() { local p; p="$(visit_pid)"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }

# Stop visit, if it runs (SIGTERM: its engine, then its Postgres, stop with it), and remove the tokens and the
# engine's mark: the board's /visit/mode then says there is none, and its sign-in refuses everyone.
visit_down() {
  local p; p="$(visit_pid)"
  if visit_running; then
    kill -TERM "$p"
    for _ in $(seq 60); do kill -0 "$p" 2>/dev/null || break; sleep 0.5; done
    if kill -0 "$p" 2>/dev/null; then echo "⚠ visit did not stop in 30 s: killed"; kill -KILL "$p" 2>/dev/null || true; fi
  fi
  rm -f "$VISIT_DIR/visit.pid" "$VISIT_DIR/token" "$VISIT_DIR/user-token" "$VISIT_DIR/visit.json" "$VISIT_DIR/engine.json"
}

# A token, new: 64 hex characters into a file only you can read.
visit_token() { (umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$1"); }

# Where the board's pages come from, for --origins (visit refuses a page from any other, exactly: its page.rs), comma-
# separated: the board's own (the engine's page too); with `lan` in REMOTE, the HTTPS proxy's (Caddy at REMOTE_PORT: this
# machine's LAN address and <name>.local, and its tailnet address too when REMOTE has `tailscale` as well); with
# `tailscale`, the tailnet's `tailscale serve` (https://<its name>:<ts_port>, board.sh's, when Tailscale is up here). All
# of bin/start's addresses: before, only REMOTE=lan exactly counted, so with `lan,tailscale` a page from either was
# refused, and from the tailnet always.
visit_origins() {
  local origins="http://localhost:3001,http://127.0.0.1:3001" remote rport addr name tsn="" tsip=""
  remote=",$(envval REMOTE | tr -d '[:space:]'),"; rport="$(envval REMOTE_PORT)"; rport="${rport:-3443}"
  case "$remote" in *,tailscale,*) if type ts_name >/dev/null 2>&1; then tsn="$(ts_name)"; [ -n "$tsn" ] && tsip="$(ts_ip)"; fi ;; esac
  case "$remote" in *,lan,*)
    addr="$(python3 -c 'import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("192.0.2.1", 9)); print(s.getsockname()[0])' 2>/dev/null || true)"
    name="$(hostname -s 2>/dev/null || hostname)"
    [ -n "$addr" ] && origins="$origins,https://$addr:$rport,https://$name.local:$rport"
    [ -n "$tsip" ] && origins="$origins,https://$tsip:$rport" ;;
  esac
  [ -n "$tsn" ] && origins="$origins,https://$tsn:$(ts_port)"
  printf '%s\n' "$origins"
}

# Start visit (stopping one that runs), with new tokens (.visit/token: the room's, its engine's; .visit/user-token:
# the person's, which the board relays the owner's page with) and the board's for the sign-in API (.auth/token), the
# lobby when the .env names one, and the engine hosting the room's server page from the board. Waits for its ready
# line; then marks engine.json (the board's: the room's page is the engine's, the owner's a user). 1 when it does
# not start: the board then refuses everyone (the sign-in is in it) and has no Visit.
# `visit_up debug` (bin/start --debug): the engine's debug hook on too (ENGINE_DEBUG: /debug/eval runs code in the
# room's page, for whoever reaches 127.0.0.1:7405, with no password). Off unless asked, each start.
visit_up() {
  local debug="" bin data origins line=""
  [ "${1:-}" = debug ] && debug=1
  visit_down
  visit_get || return 1
  bin="$(visit_bin)"; data="$(visit_data)"
  mkdir -p "$VISIT_DIR" "$AUTH_DIR" && chmod 700 "$VISIT_DIR" "$AUTH_DIR"
  visit_token "$VISIT_DIR/token"; visit_token "$VISIT_DIR/user-token"
  [ -s "$AUTH_DIR/token" ] || visit_token "$AUTH_DIR/token"   # (bin/start makes it new, before the board starts)
  origins="$(visit_origins)"
  echo "starting visit (its database, the board's sign-in and the engine are inside it; its log: .visit/visit.log)"
  : > "$VISIT_DIR/visit.out"
  # Only what it needs (never the .env's GitHub token): env -i, then its settings; ENGINE_DEBUG only when asked.
  [ -n "$debug" ] && echo "⚠ the engine's debug hook is on (bin/start --debug): any program on this machine can run code in the room's page; bin/start without it turns it off"
  env -i PATH="$PATH" HOME="$HOME" ${XDG_DATA_HOME:+XDG_DATA_HOME="$XDG_DATA_HOME"} ${VISIT_LOG:+RUST_LOG="$VISIT_LOG"} \
    VISIT_LOBBY="$(envval LOBBY_URL)" VISIT_NAME="$(envval LOBBY_NAME)" VISIT_SIM=default_office/2 \
    VISIT_PORT="$(envval VISIT_PORT)" VISIT_MAX_VISITORS="$(envval VISIT_MAX_VISITORS)" VISIT_ORIGINS="$origins" \
    VISIT_TOKEN_FILE="$VISIT_DIR/token" VISIT_USER_TOKEN_FILE="$VISIT_DIR/user-token" VISIT_DATA_DIR="$data" \
    VISIT_STATE_FILE="$VISIT_DIR/state.json" VISIT_CACHE_DIR="$VISIT_DIR/landscapes" \
    AUTH_TOKEN_FILE="$AUTH_DIR/token" AUTH_PORT=7403 \
    ENGINE_ROOM_URL="http://127.0.0.1:3001/room?role=host" ${debug:+ENGINE_DEBUG=1} \
    setsid "$bin" >"$VISIT_DIR/visit.out" 2>"$VISIT_DIR/visit.log" </dev/null &
  printf '%s' "$!" > "$VISIT_DIR/visit.pid"
  for _ in $(seq 120); do
    line="$(head -n 1 "$VISIT_DIR/visit.out" 2>/dev/null || true)"
    case "$line" in *'"ready":true'*) break ;; esac
    visit_running || break
    sleep 0.5
  done
  case "$line" in
    *'"ready":true'*)
      printf '{"since": %s}\n' "$(date +%s)" > "$VISIT_DIR/engine.json"
      return 0 ;;
  esac
  echo "⚠ visit did not start (its log, last lines):"; tail -n 15 "$VISIT_DIR/visit.log" | sed 's/^/  /'
  visit_down
  return 1
}

# visit's own commands, on this workroom's data (its running database, or one started for the command).
visit_cmd() { VISIT_DATA_DIR="$(visit_data)" "$(visit_bin)" "$@"; }
