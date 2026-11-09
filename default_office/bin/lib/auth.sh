# Shared by bin/start and bin/auth: the board's sign-in, inside visit (basedverse 2.0: `visit auth …` in a
# terminal, its API served by the running visit on AUTH_PORT, 127.0.0.1). Sourced after env.sh and visit.sh
# (envval, setenv, visit_cmd), with ROOT and ENV set. Never run.

# The service token the board and the sign-in API share: new each bin/start, before the board starts (it reads it),
# readable by you only.
auth_token() { mkdir -p "$AUTH_DIR" && chmod 700 "$AUTH_DIR" && visit_token "$AUTH_DIR/token"; }

# `visit auth …`: on the running visit's database, or one started for the command.
auth_run() { visit_cmd auth "$@"; }

# The owner's account: none yet, made now from a terminal (the password typed by you, twice), else said.
auth_owner() {
  local out name
  out="$(auth_run user list 2>&1)" || { printf '⚠ %s\n' "$out"; return 0; }
  case "$out" in *"no users yet"*) ;; *) return 0 ;; esac
  if [ -t 0 ] && [ -t 1 ]; then
    printf '\nThe board has a sign-in now, and no account yet. Yours: its name (Enter: %s): ' "$(id -un)"
    read -r name || name=""
    auth_run user add "${name:-$(id -un)}" || echo "⚠ not made (above): bin/auth user add <name>, in a terminal"
  else
    echo "⚠ the board has no account yet, so nobody can sign in to it: run bin/auth user add <your name>, in a terminal"
  fi
}

# An API key, made once, into .env: auth_key <label> <ENV_KEY> <what it is for>. The agents' (forge.py sends it:
# REPO_BOARD_KEY) and bin/consoles' (it asks the board for the room's console calls with it: CONSOLES_KEY). (The
# engine's is visit's own, made new at each of its starts.)
auth_key() {
  local key
  [ -n "$(envval "$2")" ] && return 0
  auth_run key revoke "$1" >/dev/null 2>&1 || true   # one made before whose key is lost: a new one
  key="$(auth_run key add "$1" 2>/dev/null | tail -n 1 | tr -d '[:space:]')"
  case "$key" in
    bvk_*) setenv "$2" "$key"; chmod 600 "$ENV"; echo "  $3's key for the board: made ($2 in .env)" ;;
    *) echo "⚠ $3's key was not made: $3 cannot reach the board" ;;
  esac
}

# What the sign-in needs besides visit running: the owner's account, and the agents' and bin/consoles' keys.
auth_up() {
  auth_owner
  auth_key agents REPO_BOARD_KEY "the agents"
  auth_key consoles CONSOLES_KEY "bin/consoles"
}
