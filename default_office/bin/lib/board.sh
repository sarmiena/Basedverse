# Shared by bin/start, bin/down and bin/board-restart: the board, and the LAN's HTTPS proxy (Caddy), on this machine
# as programs of their own: no Docker. Sourced after env.sh (envval), with ROOT and ENV set. Never run.
#
# The board is stdlib Python (forge/board/server.py, python3 on this machine: what bin/start already uses), on
# 127.0.0.1:3001 and its proxy listener 127.0.0.1:3002; it reads the project .env itself. Its data (the agents'
# messages, the room's state) is .board/. Caddy is its official release, downloaded once into .remote/ and checked
# against the checksum pinned here; its certificate authority in .remote/data/.

BOARD_DIR="$ROOT/.board"
REMOTE_DIR="$ROOT/.remote"
CADDY_VERSION=2.8.4
# sha512 of caddy_<version>_linux_<arch>.tar.gz, from the release's caddy_<version>_checksums.txt.
CADDY_SHA512_amd64=b8bec15d14fb033562af9f207850027bcbaa1f891edc9efe00d38bf39e1bf9944f8b6b8eba041ddd4c171cd70c905174c704d705be2f23bc678fe1eaf37a2485
CADDY_SHA512_arm64=5466234be3e988071cef937aedbdd94c15b6f75cf7307397e67c2641219ac9bfe2c2bb3b31fc05bc68d3b6398bbe50abfa16ccf3b127318ccac31115ad26507c

# A program of ours in the background: its pid in <dir>/<name>.pid, what it says in <dir>/<name>.log.
bg_pid() { cat "$1/$2.pid" 2>/dev/null || true; }
bg_running() { local p; p="$(bg_pid "$1" "$2")"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
bg_stop() {
  local p; p="$(bg_pid "$1" "$2")"
  if bg_running "$1" "$2"; then
    kill -TERM "$p"
    for _ in $(seq 20); do kill -0 "$p" 2>/dev/null || break; sleep 0.25; done
    kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null || true
  fi
  rm -f "$1/$2.pid"
}

board_running() { bg_running "$BOARD_DIR" board; }
board_down() { bg_stop "$BOARD_DIR" board; }

# The board, (re)started: up when it answers on 127.0.0.1:3001 (1 if it never does: its log's last lines said).
board_up() {
  local vport; vport="$(envval VISIT_PORT)"
  board_down
  mkdir -p "$BOARD_DIR" && chmod 700 "$BOARD_DIR"
  env -i PATH="$PATH" HOME="$HOME" PYTHONUNBUFFERED=1 REPO_ENV="$ENV" \
    BOARD_HOST=127.0.0.1 BOARD_PORT=3001 BOARD_PROXY_PORT=3002 BOARD_DB="$BOARD_DIR/board.db" \
    VISIT_HOST=127.0.0.1 VISIT_PORT="${vport:-7401}" \
    AUTH_HOST=127.0.0.1 AUTH_PORT=7403 AUTH_TOKEN_FILE="$ROOT/.auth/token" \
    setsid python3 "$ROOT/forge/board/server.py" >>"$BOARD_DIR/board.log" 2>&1 </dev/null &
  printf '%s' "$!" > "$BOARD_DIR/board.pid"
  for _ in $(seq 40); do
    curl -s -o /dev/null --max-time 1 http://127.0.0.1:3001/login && return 0
    board_running || break
    sleep 0.25
  done
  echo "⚠ the board did not start (its log, .board/board.log, last lines):"; tail -n 15 "$BOARD_DIR/board.log" | sed 's/^/  /'
  return 1
}

caddy_running() { bg_running "$REMOTE_DIR" caddy; }
caddy_down() { bg_stop "$REMOTE_DIR" caddy; }

# Caddy's release, once (.remote/caddy), checked against the pinned sha512.
caddy_get() {
  local arch bin="$REMOTE_DIR/caddy" want
  [ -x "$bin" ] && "$bin" version 2>/dev/null | grep -q "^v$CADDY_VERSION " && return 0
  case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) echo "⚠ no Caddy for $(uname -m)"; return 1 ;; esac
  [ "$(uname -s)" = Linux ] || { echo "⚠ Caddy is fetched for Linux only (here: $(uname -s))"; return 1; }
  want="$(eval echo "\$CADDY_SHA512_$arch")"
  mkdir -p "$REMOTE_DIR"
  echo "downloading Caddy $CADDY_VERSION (its official release, once)"
  python3 - "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/caddy_${CADDY_VERSION}_linux_$arch.tar.gz" "$want" "$bin" <<'PY' || return 1
import hashlib, io, os, sys, tarfile, urllib.request
url, want, dest = sys.argv[1:]
data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "default_office"}), timeout=300).read()
if hashlib.sha512(data).hexdigest() != want:
    sys.exit("Caddy: %s is not the release pinned in bin/lib/board.sh" % url)
body = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").extractfile("caddy").read()
open(dest + ".new", "wb").write(body)
os.chmod(dest + ".new", 0o755)
os.replace(dest + ".new", dest)
PY
}

# The board over HTTPS for other devices on this network: caddy_up <LAN address> <name> <port> [<tailnet address>].
# With a tailnet address (REMOTE has `tailscale`), at that too, and from your tailnet's addresses as well as private ones.
caddy_up() {
  local sites="https://$1:$3, https://$2.local:$3" from="private_ranges"
  if [ -n "${4:-}" ]; then sites="$sites, https://$4:$3"; from="$from 100.64.0.0/10 fd7a:115c:a1e0::/48"; fi   # Tailscale's addresses
  caddy_down
  caddy_get || return 1
  env -i PATH="$PATH" HOME="$HOME" XDG_DATA_HOME="$REMOTE_DIR/data" XDG_CONFIG_HOME="$REMOTE_DIR/config" \
    REMOTE_ADDRESS="$1" REMOTE_SITES="$sites" REMOTE_FROM="$from" \
    setsid "$REMOTE_DIR/caddy" run --config "$ROOT/forge/Caddyfile" --adapter caddyfile >>"$REMOTE_DIR/caddy.log" 2>&1 </dev/null &
  printf '%s' "$!" > "$REMOTE_DIR/caddy.pid"
  for _ in $(seq 40); do
    [ -f "$REMOTE_DIR/data/caddy/pki/authorities/local/root.crt" ] && caddy_running && return 0
    caddy_running || break
    sleep 0.25
  done
  echo "⚠ Caddy did not start (.remote/caddy.log, last lines):"; tail -n 10 "$REMOTE_DIR/caddy.log" | sed 's/^/  /'
  caddy_down; return 1
}

# The board on your tailnet (REMOTE has `tailscale`), over HTTPS: `tailscale serve` at this machine's name there
# (https://<name>.<tailnet>.ts.net:<ts_port>), with Tailscale's own certificate (one every device trusts), passed to the
# board's listener for a proxy; from your tailnet only (serve, never funnel). Tailscale lets only root or its operator
# change what it serves: once, `sudo tailscale set --operator=<you>` (bin/start offers it). ts_name: this machine's name
# on the tailnet, empty while Tailscale is not up here; ts_ip: its address there; ts_port: the HTTPS port it is served on
# there, REMOTE_TS_PORT, 9443 when blank. Never 443: tailscaled holds a port it serves on this machine's tailnet
# addresses, and a project's own dev server may need this machine's 443 (the user, 2026-11-02: move the board off it,
# not the project).
ts_name() {
  command -v tailscale >/dev/null 2>&1 || return 0
  tailscale status --json 2>/dev/null | python3 -c 'import json, sys; d = json.load(sys.stdin); print((d.get("Self") or {}).get("DNSName", "").rstrip(".") if d.get("BackendState") == "Running" else "")' 2>/dev/null || true
}
ts_ip() { tailscale ip -4 2>/dev/null | head -n 1 || true; }
ts_port() { local p; p="$(envval REMOTE_TS_PORT)"; printf '%s\n' "${p:-9443}"; }
# The HTTPS ports the tailnet serves the board's listener for a proxy on (ours), one a line: only ours is ever turned off.
ts_ports() {
  command -v tailscale >/dev/null 2>&1 || return 0
  tailscale serve status --json 2>/dev/null | python3 -c 'import json, sys
d = json.load(sys.stdin)
for k, w in (d.get("Web") or {}).items():
    if any(h.get("Proxy") == "http://127.0.0.1:3002" for h in (w.get("Handlers") or {}).values()): print(k.rsplit(":", 1)[1] if ":" in k else "443")' 2>/dev/null || true
}
ts_ours() { local ps; ps="$(ts_ports)"; [ -n "$ps" ] || return 1; [ -z "${1:-}" ] && return 0; printf '%s\n' "$ps" | grep -qx "$1"; }   # (on port $1, given)
ts_up() {   # 0: served; 2: Tailscale refused you (not its operator); 1: anything else (said)
  local out port p; port="$(ts_port)"
  if ! ts_ours "$port"; then
    out="$(timeout 30 tailscale serve --bg --https="$port" http://127.0.0.1:3002 2>&1)" || {
      case "$out" in *[Aa]ccess\ denied*|*operator*) return 2 ;; esac
      echo "⚠ tailscale serve did not take the board:"; printf '%s\n' "$out" | tail -n 5 | sed 's/^/  /'
      return 1
    }
  fi
  for p in $(ts_ports); do [ "$p" = "$port" ] || timeout 30 tailscale serve --https="$p" off >/dev/null 2>&1 || true; done   # ours on another port (443, before): off
  return 0
}
ts_down() { local p; for p in $(ts_ports); do timeout 30 tailscale serve --https="$p" off >/dev/null 2>&1 || true; done; }
