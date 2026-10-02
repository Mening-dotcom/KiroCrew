#!/bin/sh
# KiroCrew first-time setup script (public build)
# Usage: source setup.sh   (bash or zsh), or: bash setup.sh
#   or straight from GitHub, with no checkout:
#   curl -fsSL https://raw.githubusercontent.com/kirodotdev/KiroCrew/feat/one-chat-first-run/setup.sh | bash
#
# Sets up KiroCrew from source using only public tooling, asks nothing, and ends
# in the first-run chat, where every other choice is made:
#   0. From GitHub: fetch this branch's source into ~/.local/share/kirocrew/source
#      (KIROCREW_SOURCE_DIR), then run the setup.sh inside it
#   1. Python 3.12+, and whether Kiro CLI, the default agent engine, is installed
#      (the browser asks which engine to use at the end and guides its install
#      and sign-in; the build carries on either way)
#   2. Node.js (via ensure-node.sh) and optional tools (git-lfs, ffmpeg for voice)
#   3. Optional ACP adapter
#   4. Build frontend (npm/vite) + backend (pip)
#   5. PATH config
#   6. Agent config (kirocrew setup --agent-only)
#   7. `kirocrew start`: the gateway, and the first-run chat in your browser
#
# Options (after `| bash -s --` when piped):
#   --quick         skip optional tools; rebuild and reopen the existing crew
#   --no-start      stop after step 6
#   --with-extras   also install the optional tools (git-lfs, ffmpeg, the extra
#                   agent adapter); from a checkout they are installed by default
#   --branch NAME   the branch step 0 fetches (default: the one this file ships on)
#   --demo [...]    a throwaway demo instead (scripts/demo-first-run.sh; see its --help)

# Resolve script directory (works in bash and zsh, sourced or executed). Piped
# from curl there is no file, so no directory: the folder the command was typed
# in is never taken for a checkout, and step 0 fetches the source instead.
if [ -n "$BASH_SOURCE" ] && [ -f "$BASH_SOURCE" ]; then
    _kirocrew_dir="$(cd "$(dirname "$BASH_SOURCE")" && pwd)"
elif [ -n "$ZSH_VERSION" ]; then
    _kirocrew_dir="$(cd "$(dirname "${(%):-%x}")" && pwd)"
elif [ -f "$0" ] && [ "${0##*/}" = setup.sh ]; then
    _kirocrew_dir="$(cd "$(dirname "$0")" && pwd)"
else
    _kirocrew_dir=""
fi

_kc_start=1
_kc_extras=1
_kc_branch="${KIROCREW_BRANCH:-feat/one-chat-first-run}" # the branch this file ships on
_kc_demo=""
while [ $# -gt 0 ]; do
    case "$1" in
        --no-start) _kc_start=0 ;;
        --with-extras) _kc_extras=1 ;;
        --quick) _kc_extras=0 ;;
        --branch) _kc_branch="$2"; shift ;;
        --branch=*) _kc_branch="${1#*=}" ;;
        --demo) shift; _kc_demo=1; break ;;
        *) echo "setup.sh: unknown argument '$1'" >&2; return 2 2>/dev/null || exit 2 ;;
    esac
    shift
done

# Run "$@" with its output in a log, showing a spinner and the time so far;
# returns the command's status. Works sourced (bash or zsh) or executed.
_kc_spin() {
    _kc_label="$1"; _kc_log="$2"; shift 2
    _kc_mon=0
    case $- in *m*) _kc_mon=1; set +m ;; esac   # no job-control chatter when sourced
    ("$@") >"$_kc_log" 2>&1 &
    _kc_pid=$!
    _kc_t0=$SECONDS
    _kc_i=0
    if [ -t 1 ]; then
        while kill -0 "$_kc_pid" 2>/dev/null; do
            case $((_kc_i % 4)) in 0) _kc_f='|' ;; 1) _kc_f='/' ;; 2) _kc_f='-' ;; *) _kc_f='\' ;; esac
            printf '\r  %s %s  %ss\033[K' "$_kc_f" "$_kc_label" "$((SECONDS - _kc_t0))"
            _kc_i=$((_kc_i + 1))
            sleep 0.2
        done
        printf '\r\033[K'
    fi
    wait "$_kc_pid"
    _kc_rc=$?
    [ "$_kc_mon" = 1 ] && set -m
    if [ "$_kc_rc" = 0 ]; then
        echo "  ✅ $_kc_label ($((SECONDS - _kc_t0))s)"
    else
        echo "  ❌ $_kc_label failed; the last lines of $_kc_log:"
        tail -n 25 "$_kc_log"
    fi
    return "$_kc_rc"
}

# WSL can export a runtime directory whose user-bus socket is hidden by a
# mount. Probe the manager itself: an environment variable is not proof that
# systemd-run can reach it. Keep the fallback local even when setup is sourced.
_kc_start_gateway() (
    case "$(uname -s):$(uname -r)" in
        Linux:*[Mm]icrosoft*|Linux:*WSL*)
            if ! "$_py" - <<'PY'
import subprocess
import sys

try:
    result = subprocess.run(
        ["systemctl", "--user", "show", "--property=Version", "--value"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
except (OSError, subprocess.TimeoutExpired):
    sys.exit(1)
sys.exit(result.returncode)
PY
            then
                echo "  ⚠️  WSL's systemd user bus is unavailable."
                echo "     Continuing with filesystem sandboxing; cgroup memory and process limits"
                echo "     are unavailable for this gateway launch."
                echo "     Only this launch clears XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS."
                echo "     Repair the WSL user session, then verify with: systemctl --user show --property=Version"
                echo "     Until then, for manual starts: env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS kirocrew start"
                if [ "${_kc_facts#* }" = 1 ]; then
                    echo "     An installed service keeps its own environment; it still needs the WSL user bus repaired."
                fi
                unset XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
            fi
            ;;
    esac
    # A running gateway must release this crew's port before the new build
    # starts. An installed service keeps its own install and environment.
    if [ "${_kc_facts#* }" = 1 ]; then
        echo "→ A Kiro Crew service is installed on this machine; it is left as it is"
    elif [ -n "$_kc_port" ] && kirocrew stop --port "$_kc_port" >/dev/null 2>&1; then
        echo "→ Restarted the Kiro Crew running on port $_kc_port so it runs this build"
    fi
    kirocrew start
)

# ── 0. From GitHub: no checkout around this file ──
# Also for --demo from a checkout too old to carry the demo script.
if [ -z "$_kirocrew_dir" ] || [ ! -d "$_kirocrew_dir/src/kiro_crew" ] \
    || { [ -n "$_kc_demo" ] && [ ! -f "$_kirocrew_dir/scripts/demo-first-run.sh" ]; }; then
    _kc_src="${KIROCREW_SOURCE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/kirocrew/source}"
    echo "── Step 0: Source ──"
    if ! command -v git >/dev/null 2>&1; then
        echo "  ❌ git is needed. On macOS: xcode-select --install"
        return 1 2>/dev/null || exit 1
    fi
    mkdir -p "$(dirname "$_kc_src")"
    if [ -d "$_kc_src/.git" ]; then
        echo "→ Updating Kiro Crew's source to the latest '$_kc_branch' in $_kc_src"
        _kc_spin "Fetching the latest code" "$_kc_src.git.log" \
            git -C "$_kc_src" fetch --progress --depth 1 origin "$_kc_branch" \
            && git -C "$_kc_src" reset --quiet --hard FETCH_HEAD \
            || { return 1 2>/dev/null || exit 1; }
    else
        echo "→ Downloading Kiro Crew (branch '$_kc_branch') into $_kc_src"
        _kc_spin "Downloading the code" "$_kc_src.git.log" \
            git clone --progress --depth 1 --branch "$_kc_branch" --single-branch \
            "${KIROCREW_SOURCE_REPO:-https://github.com/kirodotdev/KiroCrew.git}" "$_kc_src" \
            || { return 1 2>/dev/null || exit 1; }
    fi
    echo "  Code at commit $(git -C "$_kc_src" rev-parse --short HEAD); continuing with its setup.sh"
    echo ""
    # From GitHub the optional tools are skipped: the first run needs none of
    # them, and each can be installed later (or with --with-extras).
    _kc_args="--quick"
    case " $* " in *" --with-extras "*) _kc_args="" ;; esac
    [ "$_kc_start" = 0 ] && _kc_args="$_kc_args --no-start"
    if [ -n "$_kc_demo" ]; then
        exec bash "$_kc_src/setup.sh" --demo "$@"
    fi
    # shellcheck disable=SC2086
    exec bash "$_kc_src/setup.sh" $_kc_args
fi
cd "$_kirocrew_dir" || return 1

if [ -n "$_kc_demo" ]; then
    # A throwaway crew in temporary folders; the demo script explains each step.
    bash "$_kirocrew_dir/scripts/demo-first-run.sh" "$@"
    _kc_rc=$?
    cd - > /dev/null 2>&1
    return "$_kc_rc" 2>/dev/null || exit "$_kc_rc"
fi

ACP_NPM_PKG="@agentclientprotocol/claude-agent-acp"

echo "👻 KiroCrew Setup"
echo ""

# ── Ensure PATH includes common install locations ──
_KC_ORIG_PATH="$PATH"
[ -d "$HOME/.local/bin" ] && export PATH="$HOME/.local/bin:$PATH"
if [ -s "$HOME/.nvm/nvm.sh" ]; then
    export NVM_DIR="$HOME/.nvm"
    # shellcheck disable=SC1091
    . "$NVM_DIR/nvm.sh"
fi

# ── Helper ──

_check() {
    command -v "$1" >/dev/null 2>&1
}

# ── 1. Python and Kiro CLI ──

echo "── Step 1: Python and Kiro CLI ──"
_py=""
for _candidate in python3.12 python3.13 python3; do
    if _check "$_candidate" && "$_candidate" -c "import sys; assert sys.version_info >= (3,12)" 2>/dev/null; then
        _py="$_candidate"
        break
    fi
done
# See cloud-install.sh's _has_provisioner: the automatic fallback may use a
# version manager that is already installed, and may never install one, because
# ensure-python.sh would do that by piping a remote script into sh.
_has_provisioner() {
    command -v mise >/dev/null 2>&1 || [ -x "$HOME/.local/bin/mise" ]
}
if [ -z "$_py" ] && [ -f "$_kirocrew_dir/ensure-python.sh" ] && _has_provisioner; then
    # Runs from a clone, so the repo's own bootstrap is available: provision
    # 3.12 rather than stopping on a distro whose archive cannot supply it.
    echo "  → No system Python 3.12+; provisioning one via ensure-python.sh…"
    bash "$_kirocrew_dir/ensure-python.sh" >/dev/null 2>&1 || true
    _recorded="$(cat "${KIROCREW_HOME:-$HOME/.kiro/crew}/python-bin" 2>/dev/null || true)"
    if [ -n "$_recorded" ] && [ -x "$_recorded" ] \
        && "$_recorded" -c "import sys; assert sys.version_info >= (3,12)" 2>/dev/null; then
        _py="$_recorded"
    fi
fi
if [ -z "$_py" ]; then
    echo "  ❌ Python 3.12+ required and could not be provisioned."
    echo "     Install Python 3.12+ (https://www.python.org/downloads/), or install mise"
    echo "     (https://mise.jdx.dev/installing-mise.html) and re-run — this script will"
    echo "     then provision Python 3.12 through it."
    cd - > /dev/null 2>&1
    return 1 2>/dev/null || exit 1
fi
echo "  ✅ $($_py --version 2>&1) ($(which "$_py"))"
# Kiro CLI now, before the long build, so it is known up front whether the default
# agent engine is here. This script installs no engine and waits on none: the
# build carries on, and the first-run chat asks which engine to use and walks
# through installing and signing in to it.
if _check kiro-cli; then
    echo "  ✅ kiro-cli, the default agent engine ($(which kiro-cli))"
else
    echo "  ⚠️  Kiro CLI, the default agent engine, isn't installed yet. Setup carries on."
fi
if [ "$_kc_start" = 1 ]; then
    echo "     At the end your browser asks which agent engine to use (Kiro is the"
    echo "     default) and walks you through installing and signing in to it."
else
    echo "     'kirocrew start' then opens your browser, which asks which agent engine to"
    echo "     use (Kiro is the default) and walks you through installing and signing in."
fi
echo ""

# ── 2. Node.js + optional tools ──

echo "── Step 2: Dependencies ──"

# Node.js (>= 22 required for the website build; 24 LTS recommended)
if [ -x "$_kirocrew_dir/ensure-node.sh" ]; then
    bash "$_kirocrew_dir/ensure-node.sh"
    # Re-source managers so newly-installed node lands on PATH
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
    if [ -f "$HOME/.local/bin/mise" ]; then
        eval "$("$HOME/.local/bin/mise" activate bash 2>/dev/null)" 2>/dev/null || true
    elif command -v mise >/dev/null 2>&1; then
        eval "$(mise activate bash 2>/dev/null)" 2>/dev/null || true
    fi
    # On a host where no version manager can serve a supported Node -- Amazon
    # Linux 2, whose glibc 2.26 cannot load the official builds -- ensure-node.sh
    # installs a private toolchain that neither nvm nor mise owns, so the
    # re-sourcing above cannot surface it. It records the directory it settled on;
    # consume that marker the way the Makefile and kiro_crew.env.node_bin_dirs()
    # already do. Without this the checks below miss the node just installed and
    # the frontend build plus the agent-backend install are skipped.
    _nbd="$(cat "${KIROCREW_HOME:-$HOME/.kiro/crew}/node-bin-dir" 2>/dev/null || true)"
    if [ -n "$_nbd" ] && [ -x "$_nbd/node" ]; then
        export PATH="$_nbd:$PATH"
    fi
    unset _nbd
    if _check node; then
        echo "  ✅ node ($(which node))"
    else
        echo "  ⚠️  node not found after ensure-node.sh — run: bash ensure-node.sh"
    fi
elif _check node; then
    echo "  ✅ node ($(which node))"
else
    echo "  ⚠️  node not found — run: bash ensure-node.sh"
fi

if [ "$_kc_extras" = 0 ]; then
    echo "  ⏭  Optional tools skipped (git-lfs; ffmpeg, for voice). Later: bash setup.sh --with-extras"
fi
# Git LFS: optional, used if any LFS-tracked assets are added
if [ "$_kc_extras" = 0 ]; then
    :
elif ! git lfs version >/dev/null 2>&1; then
    if [ "$(uname)" = "Darwin" ] && _check brew; then
        echo "  → Installing git-lfs..."
        brew install git-lfs >/dev/null 2>&1 || true
        if git lfs version >/dev/null 2>&1; then
            git lfs install >/dev/null 2>&1
            echo "  ✅ git-lfs installed and initialized"
        else
            echo "  ⚠️  git-lfs install failed — run manually: brew install git-lfs && git lfs install"
        fi
    else
        echo "  ⚠️  git-lfs not found (optional) — install via your package manager if needed"
    fi
else
    if ! git config --global filter.lfs.smudge >/dev/null 2>&1; then
        git lfs install >/dev/null 2>&1 && echo "  ✅ git-lfs ($(git lfs version 2>/dev/null | head -1))" \
            || echo "  ⚠️  git-lfs filter init failed — run: git lfs install"
    else
        echo "  ✅ git-lfs ($(git lfs version 2>/dev/null | head -1))"
    fi
fi

# ffmpeg: needed for voice input (whisper) and MP3 stitching
if [ "$_kc_extras" = 1 ] && ! _check ffmpeg; then
    if [ "$(uname)" = "Darwin" ] && _check brew; then
        echo "  → Installing ffmpeg..."
        brew install ffmpeg >/dev/null 2>&1 || true
        _check ffmpeg && echo "  ✅ ffmpeg installed" \
            || echo "  ⚠️  ffmpeg install failed — run manually: brew install ffmpeg"
    else
        echo "  ⚠️  ffmpeg not found (optional, for voice) — install via your package manager"
    fi
fi
echo ""

# ── 3. Optional ACP adapter ──

echo "── Step 3: Agent Backends ──"
if _check claude-agent-acp; then
    echo "  ✅ claude-agent-acp ($(which claude-agent-acp))"
elif [ "$_kc_extras" = 0 ]; then
    echo "  ⏭  The optional Claude agent adapter is skipped. Later: npm i -g $ACP_NPM_PKG"
elif _check npm; then
    echo "  → Installing $ACP_NPM_PKG via npm..."
    if npm install -g "$ACP_NPM_PKG" >/dev/null 2>&1; then
        echo "  ✅ claude-agent-acp installed"
    else
        echo "  ⚠️  npm i -g $ACP_NPM_PKG failed — install it manually before selecting this backend"
    fi
else
    echo "  ⚠️  npm not found — install the optional agent backend later:"
    echo "       npm i -g $ACP_NPM_PKG"
fi
echo ""

# ── 4. Build (npm/vite frontend + pip backend) ──

echo "── Step 4: Build ──"

# Frontend: vite emits to website/dist; stage into src/kiro_crew/static/dist
if _check node && [ -d "$_kirocrew_dir/website" ]; then
    echo "→ Building frontend (website/); the first time takes a few minutes..."
    _kc_build_frontend() {
        cd "$_kirocrew_dir/website" \
            && { [ -f package-lock.json ] && npm ci --no-audit --no-fund --loglevel=error \
                 || npm install --no-audit --no-fund --loglevel=error; } \
            && npm run build
    }
    if _kc_spin "Frontend build" "${TMPDIR:-/tmp}/kirocrew-setup-frontend.log" _kc_build_frontend; then
        _dist_src="$_kirocrew_dir/website/dist"
        _dist_dst="$_kirocrew_dir/src/kiro_crew/static/dist"
        if [ -d "$_dist_src" ]; then
            rm -rf "$_dist_dst"
            mkdir -p "$(dirname "$_dist_dst")"
            cp -R "$_dist_src" "$_dist_dst"
            echo "  ✅ Frontend built and staged → src/kiro_crew/static/dist"
        else
            echo "  ⚠️  website/dist not found after build — dashboard will use legacy fallback"
        fi
    else
        echo "  ⚠️  Frontend build failed — dashboard will use legacy fallback"
    fi
else
    echo "  ⚠️  Skipping frontend build (node or website/ not available)"
fi

# Backend: venv + pip install -e .
_venv="$_kirocrew_dir/.venv"
# Build the venv under a umask that masks group/other WRITE so bin/kirocrew
# and its dirs are born non-group-writable -- `kirocrew service install`
# refuses to attach its AppArmor profile to a group/world-writable launcher
# (see the matching block in cli.sh for the full rationale). OR-ing with 022
# only ADDS write-mask bits, so a stricter caller umask is preserved.
_KC_PREV_UMASK="$(umask)"
umask "$(printf '%03o' "$(( $(umask) | 022 ))")"
# A reused venv keeps the perms it was born with: one built by an older installer
# under a permissive umask still has a group/world-writable root or bin/, so the
# AppArmor profile would keep refusing. Rebuild it under the tightened umask.
if [ -d "$_venv" ] && [ -n "$(find "$_venv" "$_venv/bin" -prune \( -perm -g+w -o -perm -o+w \) -print 2>/dev/null)" ]; then
    echo "→ Recreating virtual environment (existing one is group/world-writable)..."
    rm -rf "$_venv"
fi
# Same requires-python reuse rule as install.sh: a pre-3.12 venv cannot host the
# package, so rebuild rather than pip-install into it and hit a hard refusal.
if [ ! -d "$_venv" ] || [ ! -x "$_venv/bin/python" ] \
    || ! "$_venv/bin/python" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
    if [ -d "$_venv" ]; then
        echo "→ Recreating virtual environment (existing interpreter < 3.12)..."
        rm -rf "$_venv"
    else
        echo "→ Creating virtual environment..."
    fi
    "$_py" -m venv "$_venv" || {
        echo "  ❌ Failed to create venv"
        umask "$_KC_PREV_UMASK"
        cd - > /dev/null 2>&1
        return 1 2>/dev/null || exit 1
    }
fi
echo "→ Installing kirocrew (pip)..."
"$_venv/bin/pip" install --upgrade pip setuptools wheel -q 2>/dev/null || true
# --prefer-binary, as `make backend` does: on an old-glibc host (Amazon Linux 2)
# the newest release of a compiled dependency may ship only a manylinux_2_28
# wheel and an sdist, and pip would try to compile the sdist; this takes an
# older prebuilt wheel instead, and changes nothing where wheels exist (macOS).
if _kc_spin "Backend install (pip)" "${TMPDIR:-/tmp}/kirocrew-setup-backend.log" \
    env KIROCREW_SKIP_FRONTEND=1 "$_venv/bin/pip" install --prefer-binary -e "$_kirocrew_dir" -q; then
    # macOS: re-sign the wheels' native libraries ad hoc, as `make backend`
    # does, so the kernel's code-signing checks do not kill the interpreter.
    bash "$_kirocrew_dir/packaging/resign-macos-libs.sh" "$_venv/bin/python" >/dev/null 2>&1 || true
    echo "  ✅ Build succeeded"
    # Record install method for tooling that branches on it
    echo "pip" > "$_kirocrew_dir/.install-method"
    umask "$_KC_PREV_UMASK"
else
    echo "  ❌ pip install failed"
    umask "$_KC_PREV_UMASK"
    cd - > /dev/null 2>&1
    return 1 2>/dev/null || exit 1
fi
# Symlink CLI so `kirocrew` works on PATH
mkdir -p "$HOME/.local/bin"
ln -sf "$_venv/bin/kirocrew" "$HOME/.local/bin/kirocrew"
if _check kirocrew; then
    echo "  ✅ kirocrew command available ($(which kirocrew))"
else
    echo "  ✅ kirocrew symlinked → ~/.local/bin/kirocrew (restart shell or fix PATH in Step 5)"
fi
echo ""

# ── 5. PATH ──

echo "── Step 5: PATH ──"
export PATH="$HOME/.local/bin:$PATH"
echo "→ ~/.local/bin added to PATH"

_path_line="export PATH=\"\$HOME/.local/bin:\$PATH\""

# No question: add the line once, to the rc of the shell in use, only when
# ~/.local/bin is not already on the PATH that shell starts with; nothing else
# in the file is touched. Remove the two "# KiroCrew" lines to undo it.
case "${SHELL:-}" in
    */zsh) _kc_rc="$HOME/.zshrc" ;;
    */bash) _kc_rc="$HOME/.bashrc" ;;
    *) _kc_rc="" ;;
esac
if [ -n "$_kc_rc" ]; then
    if grep -qF "$_path_line" "$_kc_rc" 2>/dev/null; then
        echo "  ✅ Already in $_kc_rc"
    elif case ":${_KC_ORIG_PATH:-}:" in *":$HOME/.local/bin:"*) true ;; *) false ;; esac; then
        echo "  ✅ ~/.local/bin is already on your PATH"
    else
        { echo ""; echo "# KiroCrew"; echo "$_path_line"; } >> "$_kc_rc"
        echo "  ✅ Added ~/.local/bin to PATH in $_kc_rc (remove the '# KiroCrew' lines to undo)"
    fi
fi
echo ""

# ── 6. Install agent config ──

echo "── Step 6: Agent Config ──"
echo "→ Installing agent config..."
KIROCREW_PROJECT_DIR="$_kirocrew_dir" kirocrew setup --agent-only \
    || echo "  ⚠️  kirocrew setup --agent-only failed (run manually later)"

echo ""
echo "👻 Setup complete!"
echo ""
if [ "$_kc_start" = 1 ]; then
    # ── 7. Start: the gateway, and the first-run chat in the browser ──
    echo "── Step 7: Start ──"
    if [ -n "${KIROCREW_HOME:-}" ]; then
        echo "  ⚠️  KIROCREW_HOME is set, so the crew lives in $KIROCREW_HOME"
        echo "     (unset KIROCREW_HOME to use your normal crew in ~/.kiro/crew)"
    else
        echo "→ Your crew lives in $HOME/.kiro/crew"
    fi
    # A gateway already running would be reused as it is, on the code it started
    # with, so a plain one on this crew's port is stopped first. An installed
    # service is never touched: it runs the install it was set up with, and
    # `kirocrew stop` without a port would stop it whatever crew it serves.
    _kc_facts="$("$_venv/bin/python" -c 'from kiro_crew import cli_server
from kiro_crew.service import controller
print(cli_server.resolve_client_port(None), 1 if controller.installed_unit_path() else 0)' 2>/dev/null || true)"
    _kc_port="${_kc_facts% *}"
    echo "→ Starting Kiro Crew; your browser opens on the chat, where setup continues."
    # Under `curl | bash` stdin is this script; give kirocrew the terminal, so
    # kiro-cli's own sign-in can run there when it is needed.
    if (exec </dev/tty) 2>/dev/null; then
        _kc_start_gateway </dev/tty
    else
        _kc_start_gateway
    fi
    _kc_result=$?
else
    echo "  kirocrew start      # start the gateway and open the chat"
    echo "  kirocrew doctor     # verify everything"
    _kc_result=0
fi

# Cleanup
cd - > /dev/null 2>&1
return "$_kc_result" 2>/dev/null || exit "$_kc_result"
