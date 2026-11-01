#!/usr/bin/env bash
# Set up, or update, the lobby on this server: any Linux with systemd and a public address (an EC2 instance, say),
# x86_64 or ARM64. No Docker: the lobby is one file, and Caddy (TLS, a free Let's Encrypt certificate) is another.
#
#   bin/deploy-lobby user@server      from your machine: copies the lobby and this directory here, and runs this
#   sudo LOBBY_DOMAIN=lobby.example.org ./setup.sh /path/to/lobby      on the server itself
#
# Nothing needs setting: each has a default. Any given is kept in /etc/basedverse-lobby/env (a later run keeps
# what it is not given again):
#   LOBBY_DOMAIN     the lobby's DNS name, pointing at this server (default basedverse.app): its certificate's, the
#                    name every hello signs, its relay's
#   LOBBY_IP         this server's public IPv4, served as well (a 6-day certificate, renewed by Caddy), for workrooms
#                    that still name the lobby by it (a lobby of before). Default: found (the EC2 instance's, else as
#                    checkip.amazonaws.com sees it); none: the name alone
#   LOBBY_V3         1 (the default, for now): Visit 0.5 to 2.x workrooms meet too, in a lobby of their own, while
#                    they upgrade; 0: Visit 3 only
#   LOBBY_TITLE, LOBBY_MOTD   its name (default: basedverse for basedverse.app, else the domain) and its message of
#                    the day (default: a welcome); LOBBY_ADMINS, operators' keys (default: none)
#   LOBBY_WORLD_SIZE the members a world takes (default: the lobby's own, 50)
#
# 1. Stops a lobby of before (Visit 2.x's: Python, Caddy and coturn in Docker), if there is one.
# 2. Installs the lobby (/usr/local/bin/basedverse-lobby) and Caddy (/usr/local/bin/basedverse-caddy, a pinned
#    release, its checksum checked), and a user they run as (basedverse-lobby).
# 3. Writes their settings and systemd units, starts Caddy, waits for its certificate, starts the lobby (with its
#    relay, which takes the same certificate for its address discovery), and prints the address workrooms join.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf '\n== %s\n' "$*"; }
die() { printf 'setup: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || exec sudo -E "$0" "$@"
bin="${1:-$here/../lobby}"
[ -x "$bin" ] || die "no lobby binary at $bin (bin/deploy-lobby copies one: or give its path)"
command -v systemctl >/dev/null 2>&1 || die "this needs systemd"

CADDY_VERSION=2.11.7
declare -A CADDY_SHA512=(
  [amd64]=a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7
  [arm64]=3db36ba90c7a6e8dda40ee3dd71fa08844c76b5fb08f61b31e5e78d2ed38e71c51dc7baed875e50d1ca1279196e84302967237386ae87c91ae9f2aaceada682e
)
case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) die "$(uname -m): x86_64 or ARM64 only" ;; esac
USER_NAME=basedverse-lobby STATE=/var/lib/basedverse-lobby ETC=/etc/basedverse-lobby

# -- the settings: what is given now, else what was kept, else the defaults
mkdir -p "$ETC"
declare -A was=()
if [ -f "$ETC/env" ]; then
  while IFS='=' read -r k v; do [[ "$k" =~ ^LOBBY_[A-Z_0-9]+$ ]] && was[$k]="$v"; done < "$ETC/env"
fi
pick() { local k="$1" d="${2:-}"; local v="${!k:-}"; [ -n "$v" ] || v="${was[$k]:-}"; [ -n "$v" ] || v="$d"; printf '%s' "$v"; }
domain="$(pick LOBBY_DOMAIN basedverse.app)"
[[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "LOBBY_DOMAIN $domain is not a DNS name"
[ "$domain" = basedverse.app ] && name=basedverse || name="$domain"
title="$(pick LOBBY_TITLE "$name")" admins="$(pick LOBBY_ADMINS)" v3="$(pick LOBBY_V3 1)" ip="$(pick LOBBY_IP auto)"
motd="$(pick LOBBY_MOTD "Welcome. Find a world in your panel (Find), or make one of your own (New): the rooms of its members sit round yours.")"
world="$(pick LOBBY_WORLD_SIZE)"
[ -z "$world" ] || { [[ "$world" =~ ^[0-9]{1,4}$ ]] && [ "$world" -ge 2 ] && [ "$world" -le 1000 ]; } || die "LOBBY_WORLD_SIZE $world: 2 to 1000"
for x in "$title" "$motd" "$admins" "$v3"; do case "$x" in *$'\n'*) die "a setting is one line" ;; esac; done
if [ "$ip" = auto ]; then   # this server's public IPv4: the EC2 instance's own word, else what the internet sees
  tok=$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)
  ip=$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $tok" http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)
  [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || ip=$(curl -fsS -m 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)
  [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "setup: this server's public address could not be found: its name alone" >&2; ip=none; }
fi
[ "$ip" = none ] && ip=""
[ -z "$ip" ] || [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "LOBBY_IP $ip is not an IPv4 address (or none)"

# -- 1. a lobby of before (Docker: the Python lobby, Caddy, coturn): stopped, its files gone
old="$(dirname "$here")"
if [ -f "$here/compose.yml" ] && command -v docker >/dev/null 2>&1; then
  say "stopping the lobby of before (Docker: the Python lobby, Caddy, coturn)"
  (cd "$here" && docker compose down) || true
fi
rm -rf "$here/compose.yml" "$here/.env" "$old/server"

# -- 2. the programs
say "installing the lobby and Caddy $CADDY_VERSION"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin "$USER_NAME"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE" "$STATE/data" "$STATE/caddy"
install -m 0755 "$bin" /usr/local/bin/basedverse-lobby.new && mv /usr/local/bin/basedverse-lobby.new /usr/local/bin/basedverse-lobby
if ! /usr/local/bin/basedverse-caddy version 2>/dev/null | grep -q "^v$CADDY_VERSION "; then
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/caddy.tar.gz" "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/caddy_${CADDY_VERSION}_linux_$arch.tar.gz"
  echo "${CADDY_SHA512[$arch]}  $tmp/caddy.tar.gz" | sha512sum -c --quiet - || die "Caddy's download is not the one pinned here"
  tar -xzf "$tmp/caddy.tar.gz" -C "$tmp" caddy
  install -m 0755 "$tmp/caddy" /usr/local/bin/basedverse-caddy
  rm -rf "$tmp"
fi
/usr/local/bin/basedverse-lobby --version

# -- 3. settings, units, start
say "settings: $ETC/env (lobby $domain)"
cert_dir="$STATE/caddy/certificates/acme-v02.api.letsencrypt.org-directory/$domain"
umask 027
cat > "$ETC/env" <<EOF
# Written by lobby/deploy/setup.sh (run again to change: what it is given replaces what is here). lobby --help says
# what each is.
LOBBY_DOMAIN=$domain
LOBBY_TITLE=$title
LOBBY_MOTD=$motd
LOBBY_ADMINS=$admins
LOBBY_V3=$v3
LOBBY_IP=${ip:-none}
LOBBY_HOSTS=$domain${ip:+,$ip}
LOBBY_LISTEN=127.0.0.1
LOBBY_PORT=8765
LOBBY_TRUST_PROXY=1
LOBBY_DATA_DIR=$STATE/data
LOBBY_RELAY_PORT=3340
LOBBY_RELAY_URL=https://$domain/
LOBBY_RELAY_CERT=$cert_dir/$domain.crt
LOBBY_RELAY_KEY=$cert_dir/$domain.key
EOF
[ -z "$world" ] || echo "LOBBY_WORLD_SIZE=$world" >> "$ETC/env"   # (none: the lobby's own, 50; a blank one would not parse)
chgrp "$USER_NAME" "$ETC/env"
umask 022
# Caddy: TLS for the lobby's name (a free Let's Encrypt certificate, renewed by Caddy, kept where the relay reads it
# for its QUIC address discovery), and for its IP address too when LOBBY_IP says (Let's Encrypt's 6-day profile; a
# client naming an IP sends no server name, hence default_sni).
{
  printf '{\n\tadmin off\n'
  [ -z "$ip" ] || printf '\tdefault_sni %s\n' "$ip"
  printf '}\n\n'
  cat <<CADDY
$domain {
	tls {
		issuer acme
	}
	# The lobby's relay: iroh's paths. Everything else is the lobby (its WebSocket, its page, /health).
	@relay path /relay /relay/* /ping /generate_204
	reverse_proxy @relay 127.0.0.1:3340
	reverse_proxy 127.0.0.1:8765
}
CADDY
  [ -z "$ip" ] || cat <<CADDY

$ip {
	tls {
		issuer acme {
			profile shortlived
		}
	}
	reverse_proxy 127.0.0.1:8765
}
CADDY
} > "$ETC/Caddyfile"
chmod 0644 "$ETC/Caddyfile"
cat > /etc/systemd/system/basedverse-caddy.service <<EOF
[Unit]
Description=Caddy for the basedverse lobby: TLS for $domain
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
EnvironmentFile=$ETC/env
Environment=XDG_DATA_HOME=$STATE XDG_CONFIG_HOME=$STATE
ExecStart=/usr/local/bin/basedverse-caddy run --config $ETC/Caddyfile --adapter caddyfile
ExecReload=/usr/local/bin/basedverse-caddy reload --config $ETC/Caddyfile --adapter caddyfile
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$STATE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/basedverse-lobby.service <<EOF
[Unit]
Description=The basedverse lobby ($domain)
After=network-online.target basedverse-caddy.service
Wants=network-online.target

[Service]
User=$USER_NAME
EnvironmentFile=$ETC/env
ExecStart=/usr/local/bin/basedverse-lobby
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$STATE
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable basedverse-caddy basedverse-lobby >/dev/null 2>&1
systemctl restart basedverse-caddy

say "waiting for $domain's certificate (Let's Encrypt; $domain must point here, ports 80 and 443 open)"
for _ in $(seq 1 $(( ${LOBBY_WAIT_S:-180} / 3 + 1 ))); do [ -s "$cert_dir/$domain.key" ] && break; sleep 3; done
[ -s "$cert_dir/$domain.key" ] || { journalctl -u basedverse-caddy -n 30 --no-pager; die "no certificate for $domain yet: Caddy's log above"; }
systemctl restart basedverse-lobby
for _ in $(seq 1 20); do
  if out=$(curl -fsS -m 5 "https://$domain/health" 2>/dev/null); then
    printf 'up: %s' "$out"
    say "done. The lobby's page: https://$domain/ · each workroom's .env:"
    printf '  LOBBY_URL=wss://%s/\n' "$domain"
    exit 0
  fi
  sleep 3
done
journalctl -u basedverse-lobby -n 30 --no-pager
die "https://$domain/health did not answer: the lobby's log above"
