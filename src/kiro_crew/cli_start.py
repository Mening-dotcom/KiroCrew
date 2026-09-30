"""``kirocrew start``: from an installed CLI to an open chat in one command.

The installer half of the one-chat first run
(``docs/request-for-change/rfc-one-chat-first-run.md`` §5.1): ``start.sh``
installs the wheel through ``cli.sh`` and then execs this command, and a user
who already has the CLI runs it directly. It takes four steps, in order, and
each one that cannot proceed stops with the command that fixes it:

1. **Harness.** The configured agent harness (``agent.acp_backend``) must be
   installed. For kiro-cli that is the resolved binary plus its sign-in; for any
   other harness it is the machine-local install probe. On a terminal, a
   harness missing only kiro-cli is left to the dashboard's prerequisite gate,
   which walks the user through installing it and signing in, so the command
   carries on to the browser. Any other missing harness, or any run that cannot
   ask (``--no-input``, no terminal), is reported with its official install
   guidance and exits :data:`EXIT_HARNESS_MISSING`. Kiro Crew never downloads a
   harness itself. A signed-out kiro-cli is offered its OWN sign-in command in
   this terminal, and otherwise only named, because the gate covers sign-in as
   well.
2. **Gateway.** A gateway already answering ``/api/ready`` is reused. An
   installed service is left to its service manager (started without sudo on
   macOS; otherwise its start command is printed). Otherwise a gateway is
   spawned, detached by default or as a child of this process with
   ``--foreground``, always with ``--no-open`` so the only browser tab is the
   one this command opens.
3. **Landing URL.** A session is minted exactly as ``kirocrew token`` mints it
   (the per-port local secret, ``GET /api/token/local``), and the URL lands on
   the first-run chat (``/chat?sid=<slot>``) when the gateway recorded one.
4. **Browser.** The URL is opened when this host can open one. Otherwise every
   origin ``kirocrew token`` would print is printed, with a terminal QR code of
   the first origin a phone could reach, or an ``ssh -L`` hint when the
   dashboard is loopback-only and a QR code of it would lead nowhere.

The minted token is a bearer session. It is printed and handed to the browser,
which is the point of the command, but it is never passed to a logger.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import ipaddress
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

from kiro_crew import cli_server, platform_compat
from kiro_crew.config import KiroCrewConfig
from kiro_crew.config.loader import config_dir, read_local_secret
from kiro_crew.dashboard.origin import resolve_dashboard_host
from kiro_crew.gateway_lock import LockProbeError, lock_holder
from kiro_crew.gateway_shutdown_budget import TOTAL_SHUTDOWN_BUDGET_SECS
from kiro_crew.instances import run_marker
from kiro_crew.loopback_http import loopback_urlopen
from kiro_crew.service import controller as service_controller
from kiro_crew.service.common import Platform, current_platform, restart_command_hint

logger = logging.getLogger(__name__)

#: The gateway is serving and the landing URL was opened or printed.
EXIT_OK = 0
#: No gateway could be started or reached on the resolved port.
EXIT_GATEWAY_FAILED = 1
#: The configured agent harness is not installed, and either the run cannot ask
#: (``--no-input``, no terminal) or the dashboard does not guide that harness's
#: install (and ``--skip-harness-check`` was not passed). Distinct from a
#: gateway failure so ``start.sh`` and scripts can tell "install kiro-cli" from
#: "read the gateway log".
EXIT_HARNESS_MISSING = 3
#: Interrupted by the user before the gateway was up (128 + SIGINT).
EXIT_INTERRUPTED = 130

#: How long a freshly spawned gateway gets to answer ``/api/ready``. Longer than
#: a restart's budget because a first start on a new install also creates the
#: data home and installs the agent specs before it binds.
_START_READY_TIMEOUT_SECS = 60.0
#: How long an installed service that is running, or was just started, gets to
#: answer before the command reports it and stops.
_SERVICE_READY_TIMEOUT_SECS = 30.0
#: How long to wait for the gateway to record the first-run session. The
#: gateway writes it during its first start, which may finish just after
#: readiness; an install that cannot get one (onboarded, or the privacy
#: notice already acknowledged) does not wait at all.
_FIRST_RUN_SLOT_WAIT_SECS = 10.0
_FIRST_RUN_SLOT_POLL_SECS = 0.25
#: How long to keep asking for the local secret a just-started gateway writes.
_TOKEN_MINT_WAIT_SECS = 5.0
#: The session the landing URL carries lasts as long as one ``kirocrew token``
#: and ``kirocrew restart`` mint by default.
_LANDING_TOKEN_TTL = cli_server._RESTART_TOKEN_TTL
#: A slot key goes into the landing URL, and the first-run state file it comes
#: from is agent-writable, so only the shape a real slot key has is accepted.
_SLOT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
#: A home's name as the summary prints it. The card store it is read from is
#: agent-writable, so any other name (a terminal escape, a second line) is replaced
#: by the name the home's tag gives it.
_HOME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()'-]{0,79}$")

# How the gateway this command ended up with is running, for the summary.
_HOW_REUSED = "reused"
_HOW_SERVICE = "service"
_HOW_DETACHED = "detached"
_HOW_FOREGROUND = "foreground"


@dataclass(frozen=True)
class _Gateway:
    """The gateway this command will land the browser on."""

    how: str
    pid: int | None = None


def run_start(args: argparse.Namespace) -> int:
    """Entry point for ``kirocrew start``; returns the process exit code."""
    try:
        return _run(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED


def _run(args: argparse.Namespace) -> int:
    port = cli_server.resolve_client_port(args.port)
    refusal = _check_harness(args)
    if refusal is not None:
        return refusal
    _choose_home(args)

    found = _existing_gateway(port)
    if isinstance(found, int):
        return found
    if found is None:
        if args.foreground:
            return _run_foreground(args, port)
        spawned = _spawn_background(port)
        if isinstance(spawned, int):
            return spawned
        found = spawned
    elif args.foreground:
        print("   (--foreground has nothing to run: a gateway is already serving.)")

    _land(args, port)
    _print_summary(port, found)
    return EXIT_OK


# ── Step 0: where the crew lives ──


def _choose_home(args: argparse.Namespace) -> None:
    """Record ``--home`` for the first run, when a script passed it. Asks nothing.

    Where the crew lives is a question for the first-run chat, not the terminal:
    it asks once the first job is kept, and the home card states the cost and
    takes the owner's click. ``--home`` only lets a script answer ahead of time;
    a cloud answer makes the chat open with the home card on screen, and any
    answer means the chat does not ask again.
    """
    from kiro_crew import first_run
    from kiro_crew.cloud.local_signin import configured_region

    choice = getattr(args, "home", None)
    if choice is None:
        return
    if not _first_run_possible():
        # A later home is one "move me to the cloud" from any chat.
        print("   --home applies to a first run; ask the chat to move you to the cloud.")
        return
    profile = getattr(args, "aws_profile", "") or ""
    region = ""
    if choice == "cloud":
        # No fallback region: a new AWS account works in its own region only, and
        # the home card finds it once AWS answers (``local_signin.resolve_home_region``).
        region = getattr(args, "aws_region", "") or configured_region(profile)
        print("   Your home in the cloud will be on its card in the chat; building starts there.")
    first_run.record_home_choice(choice, region=region, profile=profile)


# ── Step 1: the agent harness ──


def _interactive(args: argparse.Namespace) -> bool:
    """Whether this run may ask the user a question on stdin."""
    if args.no_input:
        return False
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _check_harness(args: argparse.Namespace) -> int | None:
    """Report the configured harness's readiness; an exit code means stop.

    ``--skip-harness-check`` skips the probe entirely (no ``kiro-cli whoami``,
    no prompt): the dashboard's prerequisite gate still reports the harness.
    """
    from kiro_crew.acp_backends import ACP_BACKEND_KIRO

    if args.skip_harness_check:
        print("   Skipping the agent harness check (--skip-harness-check).")
        return None
    backend = KiroCrewConfig.load().agent.acp_backend
    interactive = _interactive(args)
    if backend == ACP_BACKEND_KIRO:
        installed = _check_kiro(interactive)
    else:
        installed = _check_other_backend(backend, interactive)
    if installed:
        return None
    print("   Re-run `kirocrew start` once it is installed, or pass --skip-harness-check.")
    return EXIT_HARNESS_MISSING


def _print_kiro_guided_in_browser() -> None:
    """The one line for a missing kiro-cli the dashboard will walk the user through.

    The dashboard's prerequisite gate probes kiro-cli whatever the harness, shows
    the official install steps and the sign-in, and re-checks on its own, so a
    person at a terminal is sent there instead of being told to re-run this.
    """
    print("⚠️  kiro-cli isn't installed yet; the browser will walk you through it.")


def _check_kiro(interactive: bool) -> bool:
    """kiro-cli: resolved, then signed in.

    False only when it is not installed and this run cannot ask, so there is no
    one at the browser to guide.
    """
    from kiro_crew.cli_doctor import _kiro_cli_signed_in
    from kiro_crew.kiro_cli import resolve_kiro_cli

    binary = resolve_kiro_cli()
    if not binary:
        if interactive:
            _print_kiro_guided_in_browser()
            return True
        _print_kiro_missing()
        return False
    signed_in = _kiro_cli_signed_in()
    if signed_in is True:
        print("✅ kiro-cli is installed and signed in.")
        return True
    if signed_in is None:
        print("⚠️  kiro-cli is installed; could not check whether it is signed in.")
        return True
    print("⚠️  kiro-cli is installed but not signed in.")
    if interactive:
        # No yes/no: the chat cannot start without it, so kiro-cli's own sign-in
        # runs straight away (the device-code variant on a headless host).
        print("   Signing in to Kiro with kiro-cli's own sign-in…")
        _run_kiro_login(_kiro_login_argv(binary))
        if _kiro_cli_signed_in() is True:
            print("✅ kiro-cli is signed in.")
            return True
        print("⚠️  kiro-cli is still not signed in; the dashboard will ask again.")
    _print_kiro_login_commands(binary)
    return True


def _kiro_login_argv(binary: str) -> list[str]:
    """kiro-cli's own sign-in, in the form that works on this host.

    With no browser to open here (an SSH session, no display) it is the
    device-code flow, which prints a URL and a code to finish on any other
    device. No identity flags:
    ``--license pro`` would force organization SSO, which cannot sign in the
    personal accounts a first run starts with, so that variant is only named
    (:func:`_print_kiro_login_commands`).
    """
    from kiro_crew.cloud.login import _browser_open_supported

    if _browser_open_supported() and not _in_ssh_session():
        return [binary, "login"]
    return [binary, "login", "--use-device-flow"]


def _run_kiro_login(argv: list[str]) -> int:
    """Run kiro-cli's sign-in attached to this terminal and return its exit code.

    The argv is the resolved kiro-cli binary plus fixed literals, and the user
    has just asked for it; the child inherits the terminal because the sign-in
    is interactive (it prints a URL and a code, and may prompt).
    """
    shown = subprocess.list2cmdline(argv) if platform_compat.IS_WINDOWS else shlex.join(argv)
    print(f"   Running: {shown}")
    try:
        return subprocess.run(argv, check=False).returncode  # noqa: S603 - fixed argv
    except OSError as exc:
        print(f"⚠️  Could not run kiro-cli: {exc}")
        return 1


def _print_kiro_login_commands(binary: str) -> None:
    from kiro_crew.kiro_prerequisite import login_commands_for

    personal, organization, _bundled = login_commands_for(binary, os.environ)
    print(f"   Sign in with: {personal}")
    print(f"   Organization SSO: {organization}")


def _print_kiro_missing() -> None:
    from kiro_crew.kiro_prerequisite import (
        KIRO_CLI_LOGIN_COMMAND,
        KIRO_CLI_SSO_LOGIN_COMMAND,
        OFFICIAL_INSTALL_DOCS_URL,
    )

    print("❌ kiro-cli, the agent harness Kiro Crew runs by default, is not installed.")
    print(f"   Install it from {OFFICIAL_INSTALL_DOCS_URL}")
    print(f"   then sign in with: {KIRO_CLI_LOGIN_COMMAND}")
    print(f"   (organization SSO: {KIRO_CLI_SSO_LOGIN_COMMAND})")
    alternatives = _installed_alternatives()
    if alternatives:
        print("   Or use an agent harness that is already installed here:")
        for backend, label in alternatives:
            print(f"     {label}: kirocrew config set agent.acp_backend {backend}")


def _installed_alternatives() -> list[tuple[str, str]]:
    """``(backend id, label)`` for each selectable harness installed on this host.

    Never raises: the list is advice beside a failure that is already reported.
    """
    try:
        from kiro_crew.acp_backends import selectable_backends
        from kiro_crew.agent_sdk.backend_install import INSTALLED, probe_backends

        selectable = selectable_backends()
        return [
            (state.backend, state.policy_id)
            for state in probe_backends()
            if state.installed == INSTALLED and state.backend in selectable
        ]
    except Exception:
        logger.debug("could not list installed agent harnesses", exc_info=True)
        return []


def _check_other_backend(backend: str, interactive: bool) -> bool:
    """A harness other than kiro-cli: its install probe.

    False only when MISSING, except on a terminal when kiro-cli is the only
    thing missing (KAS rides kiro-cli): the dashboard's prerequisite gate guides
    that install. It probes nothing else, so any other missing component still
    stops here with its install command.
    """
    from kiro_crew.agent_sdk.backend_install import (
        COMPONENT_KIRO_CLI,
        INSTALLED,
        MISSING,
        probe_backend,
    )
    from kiro_crew.agent_sdk.host_auth import declaration_for

    state = probe_backend(backend)
    label = state.policy_id
    if state.installed == MISSING:
        if interactive and state.missing_components == (COMPONENT_KIRO_CLI,):
            _print_kiro_guided_in_browser()
            return True
        missing = ", ".join(state.missing_components) or label
        print(f"❌ The configured agent harness ({label}) is not installed: missing {missing}.")
        if state.install_command:
            print(f"   Install it with: {state.install_command}")
        elif COMPONENT_KIRO_CLI in state.missing_components:
            from kiro_crew.kiro_prerequisite import OFFICIAL_INSTALL_DOCS_URL

            print(f"   Install kiro-cli from {OFFICIAL_INSTALL_DOCS_URL}")
        print('   Or switch back to kiro-cli: kirocrew config set agent.acp_backend ""')
        return False
    if state.installed == INSTALLED:
        print(f"✅ Agent harness {label} is installed.")
    else:
        print(f"⚠️  Could not check whether the agent harness {label} is installed.")
    print(f"   Its sign-in is not checked here: {declaration_for(backend).sign_in_remedy}")
    return True


# ── Step 2: the gateway ──


def _wait_until_ready(port: int, timeout: float) -> bool:
    """Poll ``/api/ready`` on *port* until it answers 200 or *timeout* passes."""
    deadline = time.monotonic() + timeout
    while True:
        if cli_server._probe_gateway_ready(port) == 200:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(cli_server._RESTART_READY_POLL_INTERVAL)


def _existing_gateway(port: int) -> _Gateway | int | None:
    """A gateway that is already this host's to use, an exit code, or ``None``.

    ``None`` means nothing serves *port* and no service is installed, so the
    caller spawns one. An installed service is never competed with: a gateway
    spawned beside it would hold the data-home lock the service's own start
    then fails on.
    """
    status = cli_server._probe_gateway_ready(port)
    if status == 200:
        print(f"✅ Kiro Crew is already running on port {port}.")
        return _Gateway(_HOW_REUSED)
    if status == 503:
        # A gateway that is still booting (or shutting down) answers 503.
        print(f"   A gateway on port {port} is starting; waiting for it ...")
        if _wait_until_ready(port, _SERVICE_READY_TIMEOUT_SECS):
            print(f"✅ Kiro Crew is running on port {port}.")
            return _Gateway(_HOW_REUSED)
        print(f"❌ The gateway on port {port} did not become ready.")
        print("   Check it with: kirocrew status   (logs: kirocrew logs)")
        return EXIT_GATEWAY_FAILED
    if status != 0:
        print(f"❌ Something other than a Kiro Crew gateway answers on port {port}.")
        print("   Pick another port: kirocrew start --port <port>")
        return EXIT_GATEWAY_FAILED
    if service_controller.installed_unit_path() is None or not _service_owns_this_home():
        return None
    if service_controller.is_service_active():
        print("   The Kiro Crew service is running; waiting for it to answer ...")
        if _wait_until_ready(port, _SERVICE_READY_TIMEOUT_SECS):
            print(f"✅ The Kiro Crew service is serving port {port}.")
            return _Gateway(_HOW_SERVICE)
        print(f"❌ The Kiro Crew service is running but is not answering on port {port}.")
        print("   Check it with: kirocrew service status   (logs: kirocrew logs)")
        return EXIT_GATEWAY_FAILED
    print("⚠️  A Kiro Crew service is installed but not running.")
    if current_platform() is Platform.LAUNCHD:
        if _start_launchd_service() and _wait_until_ready(port, _SERVICE_READY_TIMEOUT_SECS):
            print(f"✅ Started the Kiro Crew service; it is serving port {port}.")
            return _Gateway(_HOW_SERVICE)
        print("   launchd did not start it. Reload it with: kirocrew service install")
    else:
        print(f"   Start it with: {restart_command_hint()}")
    print("   Or run Kiro Crew in this terminal instead: kirocrew gateway")
    return EXIT_GATEWAY_FAILED


def _service_owns_this_home() -> bool:
    """Whether the installed service runs THIS data home.

    The service unit is machine-wide and runs the default data home. A run with
    its own ``KIROCREW_HOME`` (a second crew, a demo, a test) is not that
    service's crew, so it spawns its own gateway instead of waiting on a
    service that will never answer its port.
    """
    from kiro_crew.config.paths import _default_home, _valid_override_home

    override = _valid_override_home()
    if override is None:
        return True
    # The pure default path, not the process resolver: resolving the default
    # home would adopt it as this process's home.
    try:
        return override.resolve() == _default_home().resolve()
    except OSError:
        return False


def _start_launchd_service() -> bool:
    """Ask launchd to start the installed agent; True when it accepted.

    A launchd agent lives in the user's own domain, so this needs no sudo. It
    goes to ``macos.restart`` rather than ``controller.restart_service``: the
    controller only restarts an agent that is already running, and
    ``launchctl kickstart -k`` is what starts a loaded agent that is not.
    """
    from kiro_crew.service import macos

    print("   Starting it (launchd needs no sudo) ...")
    try:
        return macos.restart()
    except Exception:
        logger.warning("launchctl kickstart failed", exc_info=True)
        return False


def _lock_refusal(port: int) -> int | None:
    """An exit code when another gateway already owns this data home."""
    try:
        holder = lock_holder(config_dir())
    except LockProbeError as exc:
        print(f"❌ {exc}. Not starting a second gateway.")
        return EXIT_GATEWAY_FAILED
    if holder.pid is not None and holder.alive:
        print(
            f"❌ A Kiro Crew gateway (pid {holder.pid}) already owns {config_dir()} "
            f"but is not answering on port {port}."
        )
        print("   If it serves another port, pass it: kirocrew start --port <port>")
        print("   To stop it: kirocrew stop")
        return EXIT_GATEWAY_FAILED
    return None


def _report_not_ready(verdict: str, status: int | None, pid: int) -> None:
    if verdict == cli_server._READY_DIED:
        print(f"❌ The gateway (pid {pid}) exited during startup (exit status {status}).")
        print("   See why with: kirocrew logs")
    else:
        print(
            f"❌ The gateway (pid {pid}) did not become ready within "
            f"{int(_START_READY_TIMEOUT_SECS)}s. It may still be starting."
        )
        print("   Follow it with: kirocrew logs -f   (then: kirocrew token)")


def _spawn_background(port: int) -> _Gateway | int:
    refusal = _lock_refusal(port)
    if refusal is not None:
        return refusal
    prior_pid = run_marker.read_pid(port)
    print("   Starting Kiro Crew in the background ...")
    proc = cli_server._spawn_detached_gateway(port, no_open=True)
    verdict, status = cli_server._wait_gateway_ready(
        proc, port, prior_pid, _START_READY_TIMEOUT_SECS
    )
    if verdict != cli_server._READY_OK:
        _report_not_ready(verdict, status, proc.pid)
        return EXIT_GATEWAY_FAILED
    print(f"✅ Kiro Crew is running on port {port} (pid {proc.pid}).")
    return _Gateway(_HOW_DETACHED, proc.pid)


class _ForegroundGateway:
    """A ``kirocrew gateway`` child this process forwards signals to and reaps.

    The child gets its OWN process group, so a Ctrl-C at the terminal reaches
    only this process, which forwards it once. Sharing the group would deliver
    it twice, and a gateway reads a second SIGINT as "exit now, skip the
    graceful shutdown".
    """

    def __init__(self) -> None:
        self.proc: subprocess.Popen[bytes] | None = None
        self.forwarded: list[int] = []
        self._previous: dict[int, Any] = {}

    def install_forwarding(self) -> None:
        signals: list[int] = [signal.SIGINT, signal.SIGTERM]
        if platform_compat.IS_POSIX:
            # The terminal closing: the child has no terminal of its own to hang
            # up, so it would otherwise outlive this process unnoticed.
            signals.append(platform_compat.SIGHUP)
        for signum in signals:
            self._previous[signum] = signal.signal(signum, self._forward)

    def restore_handlers(self) -> None:
        for signum, previous in self._previous.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(signum, previous)
        self._previous.clear()

    def _forward(self, signum: int, _frame: FrameType | None) -> None:
        self.forwarded.append(signum)
        self.signal_child(signum)

    def signal_child(self, signum: int) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            if platform_compat.IS_POSIX:
                proc.send_signal(signal.SIGINT if signum == signal.SIGINT else signal.SIGTERM)
            elif ctrl_break is not None:
                # The only console event a child in its own process group receives.
                proc.send_signal(ctrl_break)
            else:
                proc.terminate()
        except OSError:
            pass  # exited between the poll and the signal

    def spawn_gateway(self, argv: list[str]) -> subprocess.Popen[bytes]:
        """Start the gateway child; it inherits this terminal's stdout and stderr."""
        self.proc = subprocess.Popen(  # noqa: S603 - argv from cli_server._gateway_argv
            argv,
            stdin=subprocess.DEVNULL,
            cwd=str(Path.home()),
            start_new_session=platform_compat.IS_POSIX,
            creationflags=platform_compat.CREATE_NEW_PROCESS_GROUP,
        )
        if self.forwarded:
            # A signal that arrived while Popen ran found no child to forward to.
            self.signal_child(self.forwarded[-1])
        return self.proc

    def reap(self) -> None:
        """Stop the child if it still runs, escalating to its whole process group."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        self.signal_child(signal.SIGTERM)
        try:
            proc.wait(timeout=TOTAL_SHUTDOWN_BUDGET_SECS)
            return
        except subprocess.TimeoutExpired:
            pass
        with contextlib.suppress(OSError, ValueError):
            platform_compat.kill_process_tree(proc.pid, platform_compat.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


def _exit_code_of(returncode: int) -> int:
    """A child's status as a shell exit code (128 + N for death by signal N)."""
    return returncode if returncode >= 0 else 128 - returncode


def _run_foreground(args: argparse.Namespace, port: int) -> int:
    refusal = _lock_refusal(port)
    if refusal is not None:
        return refusal
    prior_pid = run_marker.read_pid(port)
    child = _ForegroundGateway()
    child.install_forwarding()
    try:
        print("   Starting Kiro Crew in this terminal (Ctrl-C stops it) ...")
        proc = child.spawn_gateway(cli_server._gateway_argv(port, no_open=True))
        verdict, status = cli_server._wait_gateway_ready(
            proc, port, prior_pid, _START_READY_TIMEOUT_SECS
        )
        if child.forwarded:
            # Stopped by the user while it was starting: just wait it out.
            return _exit_code_of(proc.wait())
        if verdict == cli_server._READY_DIED:
            _report_not_ready(verdict, status, proc.pid)
            return EXIT_GATEWAY_FAILED
        if verdict == cli_server._READY_OK:
            _land(args, port)
            _print_summary(port, _Gateway(_HOW_FOREGROUND, proc.pid))
        else:
            print(
                f"⚠️  The gateway did not become ready within {int(_START_READY_TIMEOUT_SECS)}s;"
                " leaving it running here. Once it is up: kirocrew token"
            )
        return _exit_code_of(proc.wait())
    finally:
        # Reap BEFORE restoring the handlers: a second Ctrl-C during the grace
        # period is then forwarded (the gateway's own "stop now") instead of
        # raising here and leaving the child running.
        child.reap()
        child.restore_handlers()


# ── Steps 3 and 4: the landing URL and the browser ──


def _first_run_possible() -> bool:
    """Whether the gateway may still create a first-run session for this install."""
    try:
        dashboard = KiroCrewConfig.load().dashboard
    except Exception:
        return False
    return not (dashboard.onboarded or getattr(dashboard, "privacy_acked", False))


def _first_run_slot(wait_secs: float) -> str | None:
    """The first-run session's slot key, waiting up to *wait_secs* for it to appear."""
    from kiro_crew.first_run import read_first_run_slot

    deadline = time.monotonic() + wait_secs
    while True:
        slot = read_first_run_slot()
        if slot is not None:
            if _SLOT_KEY_RE.match(slot):
                return slot
            logger.warning("ignoring a first-run slot key of unexpected shape")
            return None
        if time.monotonic() >= deadline:
            return None
        time.sleep(_FIRST_RUN_SLOT_POLL_SECS)


def _landing_path() -> str:
    """``/chat?sid=<slot>`` for the main chat or the first-run chat, else the root."""
    from kiro_crew.first_run import read_main_slot

    main = read_main_slot()
    if main is not None and _SLOT_KEY_RE.match(main):
        return "/chat?" + urllib.parse.urlencode({"sid": main})
    wait = _FIRST_RUN_SLOT_WAIT_SECS if _first_run_possible() else 0.0
    slot = _first_run_slot(wait)
    if slot is None:
        return "/"
    return "/chat?" + urllib.parse.urlencode({"sid": slot})


def _request_token(port: int) -> str | None:
    """One ``GET /api/token/local`` attempt; ``None`` when there is no secret yet."""
    secret = read_local_secret(port)
    if not secret:
        return None
    url = (
        f"http://{cli_server._CLI_LOOPBACK}:{port}/api/token/local"
        f"?ttl={urllib.parse.quote(_LANDING_TOKEN_TTL)}"
    )
    req = urllib.request.Request(url, headers={"X-Local-Secret": secret})
    with loopback_urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    token = data.get("token") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token else None


def _mint_token(port: int) -> str | None:
    """A fresh dashboard session for *port*, minted the way ``kirocrew token`` does.

    ``None`` (with the reason on stderr) when the gateway refuses or never
    publishes its local secret. The token itself is never logged.
    """
    from kiro_crew.preflight import run_preflight_checks

    run_preflight_checks()
    deadline = time.monotonic() + _TOKEN_MINT_WAIT_SECS
    while True:
        try:
            token = _request_token(port)
        except urllib.error.HTTPError as exc:
            print(
                f"⚠️  The gateway refused to mint a sign-in link (HTTP {exc.code}).",
                file=sys.stderr,
            )
            return None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            print(f"⚠️  Could not mint a sign-in link: {exc}", file=sys.stderr)
            return None
        if token is not None:
            return token
        if time.monotonic() >= deadline:
            print("⚠️  The gateway has not published its local secret yet.", file=sys.stderr)
            return None
        time.sleep(cli_server._RESTART_READY_POLL_INTERVAL)


def _without_token(url: str) -> str:
    """*url* with its ``token`` query value dropped, for display next to a QR code."""
    parts = urllib.parse.urlsplit(url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key != "token"
    ]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _is_loopback_url(url: str) -> bool:
    """Whether *url* names this machine only, so another device cannot open it."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _in_ssh_session() -> bool:
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))


def _ssh_tunnel_hint(port: int) -> str:
    from kiro_crew.dashboard.urls import machine_hostname

    host = machine_hostname() or "<this-host>"
    return f"ssh -N -L {port}:127.0.0.1:{port} {host}"


def _land(args: argparse.Namespace, port: int) -> None:
    """Mint the landing URL and open it, or print it with a way to reach it."""
    from kiro_crew.cloud.login import _browser_open_supported, _open_browser

    path = _landing_path()
    host = resolve_dashboard_host(local_only=True)
    plain = f"http://{host}:{port}{path}"
    token = _mint_token(port)
    if token is None:
        print(f"   Open {plain} and sign in with a link from: kirocrew token")
        return
    url = cli_server._session_url(f"http://{host}:{port}", token, path)
    if not args.no_browser and _open_browser(url):
        print(f"✅ Opened {plain} in your browser.")
        return
    print()
    print("Open this link to sign in (it carries a session; do not share it):")
    others = cli_server._emit_session_urls(port, token, path)
    reachable = [other for other in others if not _is_loopback_url(other)]
    if reachable:
        _print_qr(reachable[0])
    elif _in_ssh_session() or not _browser_open_supported():
        print()
        print("The dashboard listens on this machine only. From your own computer, run:")
        print(f"   {_ssh_tunnel_hint(port)}")
        print("then open the link above there.")


def _print_qr(url: str) -> None:
    from kiro_crew.qr import render_qr_terminal

    try:
        drawing = render_qr_terminal(url)
    except Exception as exc:
        # Too long for any QR version is the realistic case; the link above
        # still works, so this is a missing convenience, not a failure. Only
        # the exception TYPE is logged: the payload carries a bearer session.
        logger.debug("could not draw the landing QR code (%s)", type(exc).__name__)
        return
    print()
    print(f"Or scan this on your phone to open {_without_token(url)}:")
    print(drawing, end="")


def _print_summary(port: int, gateway: _Gateway) -> None:
    print()
    if gateway.how == _HOW_FOREGROUND:
        print(f"Kiro Crew is running in this terminal on port {port}.")
        _print_home_hint()
        print("   Stop it:  press Ctrl-C")
    else:
        where = {
            _HOW_REUSED: "the gateway that was already running",
            _HOW_SERVICE: "the installed service",
            _HOW_DETACHED: f"a background gateway (pid {gateway.pid})",
        }[gateway.how]
        print(f"Kiro Crew is running on port {port}, in {where}.")
        _print_home_hint()
        print("   Stop it:  kirocrew stop")
        if gateway.how == _HOW_DETACHED:
            print("   Logs:     kirocrew logs -f")
    if gateway.how != _HOW_SERVICE and service_controller.installed_unit_path() is None:
        print("   Keep it running after logout and reboots:  kirocrew service install")
    print("   A new sign-in link any time:  kirocrew token")


def _moved_home() -> tuple[str, str, bool] | None:
    """``(name, open command, simulated)`` for the home this crew moved into, or ``None``.

    Read-only, from the newest committed home card that moved in. The command is
    rebuilt from the card's tag, region and profile by ``cloud.reconnect``, which
    refuses a value the cloud commands would, so nothing stored is printed as-is.
    """
    from kiro_crew import setup_cards as sc
    from kiro_crew.cloud.reconnect import home_name, open_command, reconnect_commands
    from kiro_crew.validation import ValidationError

    try:
        cards = sc.load_cards()
    except Exception:
        logger.debug("could not read the setup cards for the home hint", exc_info=True)
        return None
    for card in reversed(cards):
        outcome = card.outcome or {}
        if card.kind != sc.KIND_HOME or card.status != sc.STATUS_COMMITTED:
            continue
        if outcome.get("moved") is not True:
            continue
        home = outcome.get("home")
        home = home if isinstance(home, dict) else {}
        tag, region, profile = (str(home.get(key) or "") for key in ("tag", "region", "profile"))
        try:
            command = open_command(reconnect_commands(tag, region, profile))
        except ValidationError:
            return None
        name = str(home.get("name") or "")
        return (
            name if _HOME_NAME_RE.match(name) else home_name(tag),
            command,
            outcome.get("simulated") is True,
        )
    return None


def _print_home_hint() -> None:
    """Name the home this crew moved into, and the command that opens it."""
    found = _moved_home()
    if found is None:
        return
    name, command, simulated = found
    if simulated:
        print(f"   Your home in the cloud: {name} (simulated; this command finds no home)")
    else:
        print(f"   Your home in the cloud: {name}")
    print(f"   Open it:  {command}")
