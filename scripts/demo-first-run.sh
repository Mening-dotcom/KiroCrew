#!/usr/bin/env bash
# Demo Kiro Crew's one-chat first run. One command, from anywhere:
#
#   bash demo-first-run.sh              get or update the code, build it, start a fresh demo
#                                       (the cloud home is simulated: nothing is created in AWS)
#   bash demo-first-run.sh --real-aws   the same, but "Build my home" builds a REAL home
#                                       in your signed-in AWS account, billed by AWS
#   bash demo-first-run.sh --stop       stop the last demo and remove its files
#
# Everything it makes goes in one temporary folder, $TMPDIR/kirocrew-demo, which
# your system clears on its own; override with KIROCREW_DEMO_DIR. Run from inside a
# checkout, it uses that checkout and never changes its git state. Each demo gets
# a throwaway crew on a free port, so your own crew, its chats and its gateway are
# never touched.
#
# Needs: git, make, and kiro-cli (https://kiro.dev/cli/). Node and Python 3.12 are
# installed by the build when missing.
set -euo pipefail

REPO_URL="${KIROCREW_DEMO_REPO:-https://github.com/kirodotdev/KiroCrew.git}"
BRANCH="${KIROCREW_DEMO_BRANCH:-feat/one-chat-first-run}"
TMPROOT="${TMPDIR:-/tmp}"
TMPROOT="${TMPROOT%/}"
STATE="${KIROCREW_DEMO_DIR:-$TMPROOT/kirocrew-demo}"
LAST="$STATE/last-demo"

say() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

# What a long step is doing now, in plain words, read from its log.
phase_of() {
  awk '
    /ensure-node|mise.*node|Installing node/ { p = "checking Node" }
    /npm (ci|install)/                       { p = "installing the dashboard'"'"'s packages" }
    /tsc -p|vite/                            { p = "building the dashboard" }
    /website\/dist/                          { p = "copying the dashboard" }
    /ensure-python|mise.*python/             { p = "checking Python 3.12" }
    /-m venv/                                { p = "creating the Python environment" }
    /pip install|Collecting|Installing collected/ { p = "installing the backend" }
    /Receiving objects|Resolving deltas|Cloning into|remote:/ { p = "downloading" }
    END { print p }
  ' "$1" 2>/dev/null || true
}

# Run "$@" with its output in a log, showing a spinner, the time so far and the
# current phase; on failure, show the log's last lines and stop.
run_step() {
  local label="$1" log="$2"
  shift 2
  ("$@") >"$log" 2>&1 &
  local pid=$! start=$SECONDS i=0 phase
  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  if [ -t 1 ]; then
    while kill -0 "$pid" 2>/dev/null; do
      phase="$(phase_of "$log")"
      printf '\r    %s %s  %ss%s\033[K' "${frames[i % 10]}" "$label" "$((SECONDS - start))" "${phase:+  ·  $phase}"
      i=$((i + 1))
      sleep 0.2
    done
    printf '\r\033[K'
  else
    while kill -0 "$pid" 2>/dev/null; do
      sleep 30
      kill -0 "$pid" 2>/dev/null && note "$label: still working ($((SECONDS - start))s)"
    done
  fi
  if ! wait "$pid"; then
    printf '\n%s failed. The last lines of %s:\n\n' "$label" "$log" >&2
    tail -n 30 "$log" >&2
    die "$label did not finish (see above)."
  fi
  note "✓ $label ($((SECONDS - start))s)"
}

# ── Stop ───────────────────────────────────────────────────────────────────

stop_demo() {
  if [ ! -f "$LAST" ]; then
    echo "No demo to stop."
    return 0
  fi
  # shellcheck disable=SC1090
  . "$LAST"
  say "Stopping the demo on port $DEMO_PORT"
  DEMO_WORKSPACE="${DEMO_WORKSPACE:-}"
  # The stop only signals the gateway; it keeps writing to the crew home while it
  # shuts down, so wait for it to exit before the folders below are removed.
  _gw_pid="$(cat "$DEMO_HOME/run/gateway-$DEMO_PORT.pid" 2>/dev/null | tr -dc '0-9' || true)"
  KIROCREW_HOME="$DEMO_HOME" KIRO_HOME="$DEMO_HOME/kiro" "$DEMO_KIROCREW" stop --port "$DEMO_PORT" || true
  if [ -n "$_gw_pid" ]; then
    _waited=0
    while kill -0 "$_gw_pid" 2>/dev/null && [ "$_waited" -lt 150 ]; do
      sleep 0.2
      _waited=$((_waited + 1))
    done
    kill -0 "$_gw_pid" 2>/dev/null && note "The gateway (pid $_gw_pid) is still stopping; its files may reappear."
  fi
  if [ "${DEMO_REAL_AWS:-0}" = 1 ]; then
    say "This demo could have built a REAL home in AWS. Its cloud instances:"
    KIROCREW_HOME="$DEMO_HOME" KIRO_HOME="$DEMO_HOME/kiro" "$DEMO_KIROCREW" cloud list || true
    note "Destroy any listed above BEFORE removing the demo files, or they keep billing:"
    note "  KIROCREW_HOME='$DEMO_HOME' KIRO_HOME='$DEMO_HOME/kiro' '$DEMO_KIROCREW' cloud destroy <name>"
    note "Then remove the demo files: rm -rf '$DEMO_HOME' ${DEMO_WORKSPACE:+'$DEMO_WORKSPACE' }'$LAST'"
    return 0
  fi
  case "$DEMO_HOME" in
    "$STATE"/crews/crew.*) rm -rf "$DEMO_HOME" ;;
    *) note "Not removing $DEMO_HOME: it is not a demo home this script made." ;;
  esac
  case "$DEMO_WORKSPACE" in
    "") ;;
    "$STATE"/crews/workspace.*) rm -rf "$DEMO_WORKSPACE" ;;
    *) note "Not removing $DEMO_WORKSPACE: it is not a demo workspace this script made." ;;
  esac
  rm -f "$LAST"
  note "Done: the demo gateway is stopped and its files are removed."
}

REAL_AWS=0
case "${1:-}" in
  "") ;;
  --real-aws) REAL_AWS=1 ;;
  --stop) stop_demo; exit 0 ;;
  -h|--help) usage; exit 0 ;;
  *) die "unknown argument '$1' (see --help)" ;;
esac

mkdir -p "$STATE"
say "Everything this demo makes goes in $STATE"
note "A temporary folder your system clears on its own; nothing to clean up by hand."
if [ -f "$LAST" ]; then
  say "An earlier demo is still running or recorded; stopping it first"
  stop_demo
  [ ! -f "$LAST" ] || die "clean up the real AWS demo above, then run this again."
fi

# ── Step 1: tools ──────────────────────────────────────────────────────────

say "Step 1/6: checking the tools this needs"
missing=""
for tool in git make; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
  note "Missing:$missing"
  if [ "$(uname)" = Darwin ]; then
    note "On macOS, install Apple's command line tools (they include git and make):"
    note "  xcode-select --install"
  fi
  die "install the missing tools, then run this again."
fi
note "git and make are here. Node and Python 3.12 are set up by the build if needed."

# ── Step 2: the code ───────────────────────────────────────────────────────

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$HERE/../src/kiro_crew" ] && [ -d "$HERE/../.git" ]; then
  REPO="$(cd "$HERE/.." && pwd)"
  say "Step 2/6: using the checkout this script is in"
  note "$REPO (branch $(git -C "$REPO" rev-parse --abbrev-ref HEAD)); its git state is left as it is."
else
  REPO="$STATE/KiroCrew"
  if [ -d "$REPO/.git" ]; then
    say "Step 2/6: updating the demo's copy of Kiro Crew to the latest '$BRANCH'"
    note "$REPO (this copy belongs to the demo; nothing of yours is in it)"
    run_step "Fetching the latest code" "$STATE/git.log" git -C "$REPO" fetch --progress --depth 1 origin "$BRANCH"
    git -C "$REPO" reset --quiet --hard FETCH_HEAD
  else
    say "Step 2/6: downloading Kiro Crew (branch '$BRANCH') into $REPO"
    note "From $REPO_URL. Only this branch's latest commit is fetched."
    run_step "Downloading the code" "$STATE/git.log" \
      git clone --progress --depth 1 --branch "$BRANCH" --single-branch "$REPO_URL" "$REPO"
  fi
fi
COMMIT="$(git -C "$REPO" rev-parse --short HEAD)"
note "Code at commit $COMMIT."

# ── Step 3: build ──────────────────────────────────────────────────────────

KIROCREW="$REPO/.venv/bin/kirocrew"
PY="$REPO/.venv/bin/python"
BUILT_MARK="$REPO/.venv/.demo-built-commit"
# The build records the Node and Python it picked in a data home; give it the
# demo's own, so your real crew's folder is not created or changed.
TOOLCHAIN_HOME="$STATE/toolchain"
mkdir -p "$TOOLCHAIN_HOME"
if [ -x "$KIROCREW" ] && [ "$(cat "$BUILT_MARK" 2>/dev/null || true)" = "$COMMIT" ]; then
  say "Step 3/6: already built for commit $COMMIT; skipping the build"
else
  say "Step 3/6: building Kiro Crew (the first time takes several minutes)"
  note "It installs Node and Python 3.12 if they are missing, the dashboard's packages,"
  note "builds the dashboard, and installs the backend into $REPO/.venv."
  note "Full output: $STATE/build.log"
  run_step "Building" "$STATE/build.log" env KIROCREW_HOME="$TOOLCHAIN_HOME" make -C "$REPO" build
  printf '%s\n' "$COMMIT" >"$BUILT_MARK"
fi

# ── Step 4: kiro-cli ───────────────────────────────────────────────────────

say "Step 4/6: checking kiro-cli, the agent Kiro Crew runs"
if command -v kiro-cli >/dev/null 2>&1 || [ -x "$HOME/.local/bin/kiro-cli" ] \
  || [ -x "/Applications/Kiro CLI.app/Contents/MacOS/kiro-cli" ]; then
  note "kiro-cli is installed. If it is signed out, its own sign-in opens in your browser next."
else
  note "kiro-cli is not installed. Install it from https://kiro.dev/cli/ , then run this again."
  die "kiro-cli is needed for the demo."
fi

# ── Step 5: a throwaway crew ───────────────────────────────────────────────

say "Step 5/6: setting up a throwaway demo crew"
mkdir -p "$STATE/crews"
DEMO_HOME="$(mktemp -d "$STATE/crews/crew.XXXXXX")"
# The chats' working folders, beside the crew home rather than in it, so none
# lands in your real workspace.
DEMO_WORKSPACE="$(mktemp -d "$STATE/crews/workspace.XXXXXX")"
DEMO_PORT="$("$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
cat >"$LAST" <<EOF
DEMO_HOME='$DEMO_HOME'
DEMO_PORT='$DEMO_PORT'
DEMO_REAL_AWS='$REAL_AWS'
DEMO_WORKSPACE='$DEMO_WORKSPACE'
DEMO_KIROCREW='$KIROCREW'
EOF
note "Crew home: $DEMO_HOME, on port $DEMO_PORT; chat folders in $DEMO_WORKSPACE."

export KIROCREW_HOME="$DEMO_HOME"
export KIROCREW_WORKSPACE="$DEMO_WORKSPACE"
export KIRO_HOME="$DEMO_HOME/kiro"
mkdir -p "$KIRO_HOME"
# A sample local agent to bring over, and nothing from your real ones.
export HERMES_HOME="$REPO/evals/crew-setup/fixtures/hermes"
mkdir -p "$DEMO_HOME/hidden/claude" "$DEMO_HOME/hidden/codex" "$DEMO_HOME/hidden/gemini"
export CLAUDE_CONFIG_DIR="$DEMO_HOME/hidden/claude"
export CODEX_HOME="$DEMO_HOME/hidden/codex"
export GEMINI_HOME="$DEMO_HOME/hidden/gemini"
note "It will find a sample Hermes agent (fake data) to import; your Claude Code and Codex history stay hidden."
if [ "$REAL_AWS" = 1 ]; then
  unset KIROCREW_CLOUD_SIMULATE
  note "REAL AWS: 'Build my home' creates an EC2 home in your signed-in AWS account, billed by AWS."
else
  export KIROCREW_CLOUD_SIMULATE=1
  note "The cloud home is SIMULATED: nothing is created in AWS."
fi

# Your MCP servers are merged into every crew; declare each one disabled here so
# the demo cannot read your mail, calendar or documents on screen.
"$PY" - "$DEMO_HOME/mcp.json" <<'PY'
import json, os, sys
path = os.path.expanduser("~/.kiro/settings/mcp.json")
try:
    names = sorted((json.load(open(path)).get("mcpServers") or {}).keys())
except (OSError, ValueError):
    names = []
json.dump(
    {"mcpServers": {n: {"command": "true", "args": [], "disabled": True} for n in names}},
    open(sys.argv[1], "w"),
    indent=1,
)
print("    Your MCP servers, switched off for the demo:", ", ".join(names) or "none")
PY

# A small repo with a few commits, for "set up a morning dev brief on it".
SAMPLE="$DEMO_HOME/acme-api"
mkdir -p "$SAMPLE"
(
  cd "$SAMPLE"
  git init -q
  git config user.name "Demo Dev"
  git config user.email "demo@example.com"
  printf '# acme-api\nA tiny sample service.\n' >README.md
  git add README.md && git commit -qm "chore: start acme-api"
  printf 'def health():\n    return {"ok": True}\n' >app.py
  git add app.py && git commit -qm "feat: add a health endpoint"
  printf 'def health():\n    return {"ok": True, "version": "0.2.0"}\n' >app.py
  git commit -qam "feat: report the version from /health"
  printf 'TODO: rate-limit /login\n' >NOTES.md
)
note "Sample repo for the job preview: $SAMPLE"

TOKEN="ghp_$("$PY" -c 'import secrets, string; print("".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36)))')"
cat <<EOF

What to show:
  1. The terminal asks nothing; the browser opens on the first-run chat (nav collapsed),
     on the welcome; the composer stays locked until the agent can answer.
  2. The setup steps, each a hint above the composer -> Review:
     agent engine (Kiro) -> Continue; Set up Kiro CLI -> Continue (it checks the
     sign-in); Privacy -> Continue; How would you like to start? -> Set me up.
  3. The hello found a Hermes agent -> Bring it over (memories, a skill, a persona;
     its jobs arrive switched off).
  4. Connect GitHub -> declining is fine; the agent moves on.
  5. Say: "Set up a weekday 8am dev brief on $SAMPLE and preview it"
     -> Run a preview now -> Allow once on the card -> Keep it.
     The chat becomes your main chat.
  6. Right after Keep: "Where should your crew live?" -> In the cloud -> Continue
     -> pick a size ("What's the difference?" explains them) -> Build my home
     (simulated: it builds in the background).
  7. Say: "What's going on?"   Then paste: "my token is $TOKEN"
     (a fake token: it is moved to the vault before the model sees it).
  8. When the home card says the home is ready -> Move in.

When you are done: bash $0 --stop
EOF

# ── Step 6: start ──────────────────────────────────────────────────────────

say "Step 6/6: starting the demo crew and opening the first-run chat"
"$KIROCREW" start --port "$DEMO_PORT"

# The demo must not reach your real accounts: check the agent spec it wrote.
"$PY" - "$KIRO_HOME/agents/kirocrew.json" <<'PY' || { "$KIROCREW" stop --port "$DEMO_PORT" || true; die "a non-crew MCP server is live in the demo; stopped it."; }
import json, sys
try:
    spec = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(0)  # not written yet; the gateway writes it before the first turn
live = [k for k, v in (spec.get("mcpServers") or {}).items()
        if not k.startswith("kirocrew") and not (v or {}).get("disabled")]
if live:
    print("live non-crew MCP servers:", ", ".join(live), file=sys.stderr)
    sys.exit(1)
PY
say "The demo is running. Stop it with: bash $0 --stop"
note "(The 'kirocrew stop' hint above is for a normal crew; for the demo use --stop.)"
