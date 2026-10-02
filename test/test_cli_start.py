"""``kirocrew start``: the harness check, the gateway decision and the landing URL.

Every collaborator that would reach the host is replaced at the seam
``cli_start`` reads it through -- the gateway probe, the spawn, the service
manager, the kiro-cli resolver and sign-in probe, the token mint, the browser --
so no test starts a gateway, runs kiro-cli, opens a browser or binds a port.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kiro_crew import cli_server, cli_start, platform_compat

PORT = 5476
TOKEN = "tok-3f9a.SESSION-NEVER-LOGGED"


def _args(**overrides: Any) -> argparse.Namespace:
    values = {
        "port": None,
        "no_browser": False,
        "foreground": False,
        "no_input": True,
        "skip_harness_check": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class _Config:
    """The two config reads cli_start makes, without a data home on disk."""

    def __init__(self, backend: str = "", onboarded: bool = False) -> None:
        self.agent = SimpleNamespace(acp_backend=backend)
        self.dashboard = SimpleNamespace(onboarded=onboarded, privacy_acked=False)


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _refuse(name: str):
    def _call(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError(f"{name} must not be called here")

    return _call


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A host with kiro-cli signed in and a gateway already serving PORT.

    Each test overrides the one fact it is about. ``opened`` and ``requests``
    record what reached the browser and the token endpoint.
    """
    state = SimpleNamespace(
        config=_Config(),
        opened=[],
        requests=[],
        slot=None,
        probe=200,
        browser_supported=True,
    )
    monkeypatch.setattr(cli_start.cli_server, "resolve_client_port", lambda port: port or PORT)
    # Only cli_start's own reads: _emit_session_urls keeps the real (empty) config.
    monkeypatch.setattr(cli_start, "KiroCrewConfig", SimpleNamespace(load=lambda: state.config))
    monkeypatch.setattr("kiro_crew.kiro_cli.resolve_kiro_cli", lambda: "/opt/kiro/bin/kiro-cli")
    monkeypatch.setattr("kiro_crew.cli_doctor._kiro_cli_signed_in", lambda: True)
    monkeypatch.setattr(cli_start, "_installed_alternatives", lambda: [])
    monkeypatch.setattr(cli_start.cli_server, "_probe_gateway_ready", lambda port: state.probe)
    monkeypatch.setattr(cli_start.cli_server, "_RESTART_READY_POLL_INTERVAL", 0)
    monkeypatch.setattr(
        cli_start.cli_server, "_spawn_detached_gateway", _refuse("_spawn_detached_gateway")
    )
    monkeypatch.setattr(cli_start.service_controller, "installed_unit_path", lambda: None)
    monkeypatch.setattr(cli_start.subprocess, "run", _refuse("subprocess.run"))
    monkeypatch.setattr("builtins.input", _refuse("input"))
    monkeypatch.setattr("kiro_crew.first_run.read_first_run_slot", lambda: state.slot)
    monkeypatch.setattr(cli_start, "_FIRST_RUN_SLOT_WAIT_SECS", 0.0)
    monkeypatch.setattr(cli_start, "_TOKEN_MINT_WAIT_SECS", 0.0)
    monkeypatch.setattr("kiro_crew.preflight.run_preflight_checks", lambda: None)
    monkeypatch.setattr(cli_start, "read_local_secret", lambda port: "local-secret")

    def _urlopen(req: Any, timeout: float) -> _Response:
        state.requests.append(req)
        return _Response(json.dumps({"token": TOKEN}).encode())

    monkeypatch.setattr(cli_start, "loopback_urlopen", _urlopen)

    def _open(url: str) -> bool:
        state.opened.append(url)
        return state.browser_supported

    monkeypatch.setattr("kiro_crew.cloud.login._open_browser", _open)
    monkeypatch.setattr(
        "kiro_crew.cloud.login._browser_open_supported", lambda: state.browser_supported
    )
    monkeypatch.setattr("kiro_crew.dashboard.urls.machine_hostname", lambda: "devbox")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    return state


# ── The agent harness ──


def _a_terminal(monkeypatch: pytest.MonkeyPatch, isatty: bool = True) -> None:
    """Stdin as ``_interactive`` reads it: a terminal, or a pipe when *isatty* is False."""
    monkeypatch.setattr(cli_start.sys, "stdin", SimpleNamespace(isatty=lambda: isatty))


def _spawns_a_gateway(host: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Nothing serves the port, so a detached gateway is spawned; returns the ports spawned."""
    host.probe = 0
    spawned: list[int] = []

    def _spawn(port: int, *, no_open: bool = False) -> SimpleNamespace:
        spawned.append(port)
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(cli_start.cli_server, "_spawn_detached_gateway", _spawn)
    monkeypatch.setattr(
        cli_start.cli_server,
        "_wait_gateway_ready",
        lambda proc, port, prior, timeout: (cli_server._READY_OK, None),
    )
    monkeypatch.setattr(cli_start, "lock_holder", lambda home: SimpleNamespace(pid=None))
    monkeypatch.setattr(cli_start.run_marker, "read_pid", lambda port: None)
    return spawned


def test_missing_kiro_cli_with_no_input_exits_3_with_the_official_guidance(
    host, monkeypatch, capsys
) -> None:
    from kiro_crew.kiro_prerequisite import KIRO_CLI_LOGIN_COMMAND, OFFICIAL_INSTALL_DOCS_URL

    _a_terminal(monkeypatch)
    monkeypatch.setattr("kiro_crew.kiro_cli.resolve_kiro_cli", lambda: None)
    monkeypatch.setattr(cli_start, "_installed_alternatives", lambda: [("claude", "claude")])
    monkeypatch.setattr(
        cli_start.cli_server, "_probe_gateway_ready", _refuse("_probe_gateway_ready")
    )

    assert cli_start.run_start(_args(no_input=True)) == cli_start.EXIT_HARNESS_MISSING

    out = capsys.readouterr().out
    assert OFFICIAL_INSTALL_DOCS_URL in out
    assert KIRO_CLI_LOGIN_COMMAND in out
    assert "kirocrew config set agent.acp_backend claude" in out
    assert "Re-run `kirocrew start` once it is installed" in out
    assert host.opened == []


def test_missing_kiro_cli_with_no_terminal_exits_3(host, monkeypatch, capsys) -> None:
    # Piped stdin (a script, CI): nobody is at a browser to be guided.
    _a_terminal(monkeypatch, isatty=False)
    monkeypatch.setattr("kiro_crew.kiro_cli.resolve_kiro_cli", lambda: None)
    monkeypatch.setattr(
        cli_start.cli_server, "_probe_gateway_ready", _refuse("_probe_gateway_ready")
    )

    assert cli_start.run_start(_args(no_input=False)) == cli_start.EXIT_HARNESS_MISSING
    assert "is not installed" in capsys.readouterr().out
    assert host.opened == []


def test_missing_kiro_cli_on_a_terminal_starts_the_gateway_and_opens_the_browser(
    host, monkeypatch, capsys
) -> None:
    # The dashboard's prerequisite gate walks the install and the sign-in, so
    # a person at a terminal is sent there instead of being told to re-run.
    from kiro_crew.kiro_prerequisite import OFFICIAL_INSTALL_DOCS_URL

    host.config = _Config(onboarded=True)
    _a_terminal(monkeypatch)
    monkeypatch.setattr("kiro_crew.kiro_cli.resolve_kiro_cli", lambda: None)
    monkeypatch.setattr("kiro_crew.cli_doctor._kiro_cli_signed_in", _refuse("signed_in"))
    spawned = _spawns_a_gateway(host, monkeypatch)
    host.slot = "chat-1"

    assert cli_start.run_start(_args(no_input=False)) == cli_start.EXIT_OK

    out = capsys.readouterr().out
    assert "kiro-cli isn't installed yet; the browser will walk you through it." in out
    assert OFFICIAL_INSTALL_DOCS_URL not in out
    assert "Re-run `kirocrew start`" not in out
    assert spawned == [PORT]
    assert len(host.opened) == 1
    assert "/chat?sid=chat-1" in host.opened[0]


@pytest.mark.parametrize("backend", ["", "claude"])
def test_a_fresh_install_on_a_terminal_leaves_the_harness_to_the_browser(
    host, monkeypatch, capsys, backend
) -> None:
    # The first-run chat asks which agent engine to use and walks through its
    # install and sign-in, so the terminal neither checks the default nor runs
    # its sign-in: no probe, no kiro-cli spawn, whatever is configured.
    host.config = _Config(backend=backend)
    _a_terminal(monkeypatch)
    monkeypatch.setattr("kiro_crew.kiro_cli.resolve_kiro_cli", _refuse("resolve_kiro_cli"))
    monkeypatch.setattr("kiro_crew.cli_doctor._kiro_cli_signed_in", _refuse("signed_in"))
    monkeypatch.setattr(
        "kiro_crew.agent_sdk.backend_install.probe_backend", _refuse("probe_backend")
    )
    spawned = _spawns_a_gateway(host, monkeypatch)

    assert cli_start.run_start(_args(no_input=False)) == cli_start.EXIT_OK

    out = capsys.readouterr().out
    assert "Your browser will ask which agent engine to use" in out
    assert spawned == [PORT]
    assert len(host.opened) == 1


def test_a_fresh_install_with_no_input_still_checks_the_harness(host, monkeypatch, capsys) -> None:
    # A script has no one at a browser to ask, so today's check stands.
    _a_terminal(monkeypatch)
    monkeypatch.setattr("kiro_crew.kiro_cli.resolve_kiro_cli", lambda: None)
    monkeypatch.setattr(
        cli_start.cli_server, "_probe_gateway_ready", _refuse("_probe_gateway_ready")
    )

    assert cli_start.run_start(_args(no_input=True)) == cli_start.EXIT_HARNESS_MISSING
    assert "Your browser will ask" not in capsys.readouterr().out


def test_kas_missing_only_kiro_cli_on_a_terminal_is_guided_in_the_browser(
    host, monkeypatch, capsys
) -> None:
    # KAS rides kiro-cli, and the gate probes kiro-cli whatever the harness.
    from kiro_crew.agent_sdk import backend_install

    host.config = _Config(backend="kas", onboarded=True)
    _a_terminal(monkeypatch)
    monkeypatch.setattr(
        backend_install,
        "probe_backend",
        lambda backend: backend_install.BackendInstallState(
            backend, "kas", backend_install.MISSING, (backend_install.COMPONENT_KIRO_CLI,)
        ),
    )

    assert cli_start.run_start(_args(no_input=False)) == cli_start.EXIT_OK
    assert "the browser will walk you through it" in capsys.readouterr().out
    assert len(host.opened) == 1


def test_another_missing_harness_on_a_terminal_still_exits_3(host, monkeypatch, capsys) -> None:
    # The gate probes kiro-cli only, so it cannot guide an adapter install.
    from kiro_crew.agent_sdk import backend_install

    host.config = _Config(backend="claude", onboarded=True)
    _a_terminal(monkeypatch)
    monkeypatch.setattr(
        backend_install,
        "probe_backend",
        lambda backend: backend_install.BackendInstallState(
            backend,
            "claude",
            backend_install.MISSING,
            ("claude-agent-acp",),
            "npm install -g example-acp-adapter",
        ),
    )
    monkeypatch.setattr(
        cli_start.cli_server, "_probe_gateway_ready", _refuse("_probe_gateway_ready")
    )

    assert cli_start.run_start(_args(no_input=False)) == cli_start.EXIT_HARNESS_MISSING
    assert "npm install -g example-acp-adapter" in capsys.readouterr().out
    assert host.opened == []


def test_skip_harness_check_starts_without_probing_the_harness(host, monkeypatch) -> None:
    monkeypatch.setattr("kiro_crew.kiro_cli.resolve_kiro_cli", _refuse("resolve_kiro_cli"))
    monkeypatch.setattr("kiro_crew.cli_doctor._kiro_cli_signed_in", _refuse("signed_in"))

    assert cli_start.run_start(_args(skip_harness_check=True)) == cli_start.EXIT_OK
    assert len(host.opened) == 1


def test_signed_out_with_no_input_prints_the_sign_in_and_continues(
    host, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("kiro_crew.cli_doctor._kiro_cli_signed_in", lambda: False)

    assert cli_start.run_start(_args(no_input=True)) == cli_start.EXIT_OK

    out = capsys.readouterr().out
    assert "not signed in" in out
    assert "Sign in with: " in out
    assert "login --use-device-flow --license pro" in out
    # The dashboard's prerequisite gate still covers sign-in, so the start goes on.
    assert len(host.opened) == 1


def test_signed_out_on_a_terminal_runs_the_harness_own_device_login(host, monkeypatch) -> None:
    host.config = _Config(onboarded=True)
    answers = iter([False, True])
    monkeypatch.setattr("kiro_crew.cli_doctor._kiro_cli_signed_in", lambda: next(answers))
    monkeypatch.setattr(cli_start, "_interactive", lambda args: True)
    # Nothing is asked first: the chat cannot start without a sign-in.
    monkeypatch.setattr("builtins.input", _refuse("input"))
    # A headless host: the sign-in must be the device-code flow.
    monkeypatch.setattr("kiro_crew.cloud.login._browser_open_supported", lambda: False)
    ran: list[list[str]] = []

    def _run(argv: list[str], check: bool) -> subprocess.CompletedProcess[bytes]:
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cli_start.subprocess, "run", _run)

    assert cli_start.run_start(_args(no_input=False)) == cli_start.EXIT_OK
    assert ran == [["/opt/kiro/bin/kiro-cli", "login", "--use-device-flow"]]


def test_another_missing_harness_exits_3_with_its_install_command(
    host, monkeypatch, capsys
) -> None:
    from kiro_crew.agent_sdk import backend_install

    host.config = _Config(backend="claude")
    monkeypatch.setattr(
        backend_install,
        "probe_backend",
        lambda backend: backend_install.BackendInstallState(
            backend,
            "claude",
            backend_install.MISSING,
            ("claude-agent-acp",),
            "npm install -g example-acp-adapter",
        ),
    )

    assert cli_start.run_start(_args()) == cli_start.EXIT_HARNESS_MISSING
    out = capsys.readouterr().out
    assert "missing claude-agent-acp" in out
    assert "npm install -g example-acp-adapter" in out


# ── The gateway ──


def test_a_serving_gateway_is_reused(host, capsys) -> None:
    assert cli_start.run_start(_args()) == cli_start.EXIT_OK
    out = capsys.readouterr().out
    assert f"already running on port {PORT}" in out
    assert "kirocrew service install" in out


def test_no_gateway_spawns_one_detached_that_does_not_open_its_own_tab(host, monkeypatch) -> None:
    host.probe = 0
    spawned: list[tuple[int, bool]] = []

    def _spawn(port: int, *, no_open: bool = False) -> SimpleNamespace:
        spawned.append((port, no_open))
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(cli_start.cli_server, "_spawn_detached_gateway", _spawn)
    monkeypatch.setattr(
        cli_start.cli_server,
        "_wait_gateway_ready",
        lambda proc, port, prior, timeout: (cli_server._READY_OK, None),
    )
    monkeypatch.setattr(cli_start, "lock_holder", lambda home: SimpleNamespace(pid=None))
    monkeypatch.setattr(cli_start.run_marker, "read_pid", lambda port: None)

    assert cli_start.run_start(_args()) == cli_start.EXIT_OK
    assert spawned == [(PORT, True)]
    assert len(host.opened) == 1


def test_a_spawned_gateway_that_dies_exits_1_and_opens_nothing(host, monkeypatch, capsys) -> None:
    host.probe = 0
    monkeypatch.setattr(
        cli_start.cli_server,
        "_spawn_detached_gateway",
        lambda port, *, no_open=False: SimpleNamespace(pid=4242),
    )
    monkeypatch.setattr(
        cli_start.cli_server,
        "_wait_gateway_ready",
        lambda proc, port, prior, timeout: (cli_server._READY_DIED, 1),
    )
    monkeypatch.setattr(cli_start, "lock_holder", lambda home: SimpleNamespace(pid=None))
    monkeypatch.setattr(cli_start.run_marker, "read_pid", lambda port: None)

    assert cli_start.run_start(_args()) == cli_start.EXIT_GATEWAY_FAILED
    assert "exited during startup (exit status 1)" in capsys.readouterr().out
    assert host.opened == []


def test_a_live_lock_holder_on_another_port_is_named_not_competed_with(
    host, monkeypatch, capsys
) -> None:
    host.probe = 0
    monkeypatch.setattr(
        cli_start, "lock_holder", lambda home: SimpleNamespace(pid=31337, alive=True)
    )

    assert cli_start.run_start(_args()) == cli_start.EXIT_GATEWAY_FAILED
    out = capsys.readouterr().out
    assert "(pid 31337)" in out
    assert "kirocrew start --port" in out


def test_an_installed_but_stopped_service_is_started_by_its_manager_not_competed_with(
    host, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli_start, "_service_owns_this_home", lambda: True)
    host.probe = 0
    monkeypatch.setattr(
        cli_start.service_controller, "installed_unit_path", lambda: Path("/etc/unit")
    )
    monkeypatch.setattr(cli_start.service_controller, "is_service_active", lambda: False)
    monkeypatch.setattr(cli_start, "current_platform", lambda: cli_start.Platform.SYSTEMD)
    monkeypatch.setattr(
        cli_start, "restart_command_hint", lambda: "sudo systemctl restart kirocrew"
    )

    assert cli_start.run_start(_args()) == cli_start.EXIT_GATEWAY_FAILED
    out = capsys.readouterr().out
    assert "installed but not running" in out
    assert "sudo systemctl restart kirocrew" in out


def test_a_stopped_launchd_agent_is_kickstarted(host, monkeypatch) -> None:
    monkeypatch.setattr(cli_start, "_service_owns_this_home", lambda: True)
    probes = iter([0, 200])
    monkeypatch.setattr(cli_start.cli_server, "_probe_gateway_ready", lambda port: next(probes))
    monkeypatch.setattr(
        cli_start.service_controller, "installed_unit_path", lambda: Path("/Library/agent.plist")
    )
    monkeypatch.setattr(cli_start.service_controller, "is_service_active", lambda: False)
    monkeypatch.setattr(cli_start, "current_platform", lambda: cli_start.Platform.LAUNCHD)
    kicked: list[bool] = []
    monkeypatch.setattr("kiro_crew.service.macos.restart", lambda: kicked.append(True) or True)

    assert cli_start.run_start(_args()) == cli_start.EXIT_OK
    assert kicked == [True]
    assert len(host.opened) == 1


def test_something_else_on_the_port_is_reported(host, capsys) -> None:
    host.probe = 404
    assert cli_start.run_start(_args()) == cli_start.EXIT_GATEWAY_FAILED
    assert "Something other than a Kiro Crew gateway" in capsys.readouterr().out


# ── The landing URL and the browser ──


def test_the_landing_url_opens_the_first_run_chat(host, capsys) -> None:
    host.slot = "chat-1"
    assert cli_start.run_start(_args()) == cli_start.EXIT_OK
    assert host.opened == [f"http://localhost:{PORT}/chat?sid=chat-1&token={TOKEN}"]
    # Minted the way `kirocrew token` mints: loopback, the local secret header.
    (req,) = host.requests
    assert req.full_url.startswith(f"http://127.0.0.1:{PORT}/api/token/local?ttl=")
    assert req.get_header("X-local-secret") == "local-secret"
    out = capsys.readouterr().out
    assert f"Opened http://localhost:{PORT}/chat?sid=chat-1 in your browser" in out
    assert TOKEN not in out


def test_without_a_first_run_session_it_lands_on_the_dashboard_root(host) -> None:
    host.slot = None
    assert cli_start.run_start(_args()) == cli_start.EXIT_OK
    assert host.opened == [f"http://localhost:{PORT}/?token={TOKEN}"]


def test_a_slot_key_of_unexpected_shape_never_reaches_the_url(host) -> None:
    host.slot = "../x?token=planted&evil=1"
    assert cli_start.run_start(_args()) == cli_start.EXIT_OK
    assert host.opened == [f"http://localhost:{PORT}/?token={TOKEN}"]


def test_an_onboarded_install_does_not_wait_for_a_first_run_session(host, monkeypatch) -> None:
    host.config = _Config(onboarded=True)
    waits: list[float] = []
    real = cli_start._first_run_slot
    monkeypatch.setattr(cli_start, "_first_run_slot", lambda wait: waits.append(wait) or real(wait))
    monkeypatch.setattr(cli_start, "_FIRST_RUN_SLOT_WAIT_SECS", 10.0)
    assert cli_start.run_start(_args()) == cli_start.EXIT_OK
    assert waits == [0.0]


def test_no_browser_prints_the_url_and_never_opens_one(host, monkeypatch, capsys) -> None:
    monkeypatch.setattr("kiro_crew.cloud.login._open_browser", _refuse("_open_browser"))
    host.slot = "chat-1"

    assert cli_start.run_start(_args(no_browser=True)) == cli_start.EXIT_OK
    out = capsys.readouterr().out
    assert f"http://localhost:{PORT}/chat?sid=chat-1&token={TOKEN}" in out


def test_loopback_only_over_ssh_prints_a_tunnel_hint_not_a_qr_code(
    host, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 50000 10.0.0.1 22")
    monkeypatch.setattr(cli_start.cli_server, "_emit_session_urls", lambda port, token, path: [])
    monkeypatch.setattr("kiro_crew.qr.render_qr_terminal", _refuse("render_qr_terminal"))

    assert cli_start.run_start(_args(no_browser=True)) == cli_start.EXIT_OK
    assert f"ssh -N -L {PORT}:127.0.0.1:{PORT} devbox" in capsys.readouterr().out


def test_a_loopback_dashboard_url_is_not_a_qr_target(host, monkeypatch, capsys) -> None:
    """``dashboard.url`` spelled ``127.0.0.1`` is printed, but a phone cannot open it."""
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 50000 10.0.0.1 22")
    monkeypatch.setattr(
        cli_start.cli_server,
        "_emit_session_urls",
        lambda port, token, path: [f"http://127.0.0.1:{PORT}/?token={token}"],
    )
    monkeypatch.setattr("kiro_crew.qr.render_qr_terminal", _refuse("render_qr_terminal"))

    assert cli_start.run_start(_args(no_browser=True)) == cli_start.EXIT_OK
    assert "ssh -N -L" in capsys.readouterr().out


def test_a_reachable_origin_gets_a_qr_code_of_that_origin(host, monkeypatch, capsys) -> None:
    host.slot = "chat-1"
    remote = f"https://crew.example.test/chat?sid=chat-1&token={TOKEN}"
    monkeypatch.setattr(
        cli_start.cli_server, "_emit_session_urls", lambda port, token, path: [remote]
    )
    drawn: list[str] = []

    def _draw(payload: str) -> str:
        drawn.append(payload)
        return "QR-DRAWING\n"

    monkeypatch.setattr("kiro_crew.qr.render_qr_terminal", _draw)

    assert cli_start.run_start(_args(no_browser=True)) == cli_start.EXIT_OK
    assert drawn == [remote]
    out = capsys.readouterr().out
    assert "QR-DRAWING" in out
    assert "open https://crew.example.test/chat?sid=chat-1:" in out


def test_a_refused_token_still_names_the_dashboard(host, monkeypatch, capsys) -> None:
    import urllib.error

    def _refused(req: Any, timeout: float) -> _Response:
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(cli_start, "loopback_urlopen", _refused)
    assert cli_start.run_start(_args()) == cli_start.EXIT_OK
    captured = capsys.readouterr()
    assert "HTTP 403" in captured.err
    assert f"Open http://localhost:{PORT}/ " in captured.out
    assert host.opened == []


def test_the_token_never_reaches_a_log_record(host, monkeypatch, caplog) -> None:
    """Printed and handed to the browser, which is the point -- never logged."""
    caplog.set_level(logging.DEBUG)
    host.slot = "chat-1"
    assert cli_start.run_start(_args()) == cli_start.EXIT_OK

    # The printed path, with a QR drawing that fails and is logged at debug.
    monkeypatch.setattr(
        cli_start.cli_server,
        "_emit_session_urls",
        lambda port, token, path: [f"https://crew.example.test/?token={token}"],
    )

    def _overflow(payload: str) -> str:
        raise ValueError(f"cannot encode {payload}")

    monkeypatch.setattr("kiro_crew.qr.render_qr_terminal", _overflow)
    assert cli_start.run_start(_args(no_browser=True)) == cli_start.EXIT_OK

    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in str(record.args)
        assert record.exc_info is None or TOKEN not in str(record.exc_info[1])


# ── --foreground ──


class _FakeChild:
    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.kwargs = kwargs
        self.pid = 999_999_999
        self.signals: list[int] = []
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, signum: int) -> None:
        self.signals.append(signum)
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_foreground_runs_the_gateway_as_a_child_and_reaps_it_on_every_exit(
    host, monkeypatch
) -> None:
    host.probe = 0
    children: list[_FakeChild] = []

    def _popen(argv: list[str], **kwargs: Any) -> _FakeChild:
        children.append(_FakeChild(argv, **kwargs))
        return children[-1]

    monkeypatch.setattr(cli_start.subprocess, "Popen", _popen)
    monkeypatch.setattr(
        cli_start.cli_server,
        "_wait_gateway_ready",
        lambda proc, port, prior, timeout: (cli_server._READY_OK, None),
    )
    monkeypatch.setattr(cli_start, "lock_holder", lambda home: SimpleNamespace(pid=None))
    monkeypatch.setattr(cli_start.run_marker, "read_pid", lambda port: None)

    def _broken_landing(args: argparse.Namespace, port: int) -> None:
        raise RuntimeError("landing failed")

    monkeypatch.setattr(cli_start, "_land", _broken_landing)
    monkeypatch.setattr(cli_server, "_own_console_script", lambda: "/opt/kc/bin/kirocrew")
    before = signal.getsignal(signal.SIGINT)

    with pytest.raises(RuntimeError):
        cli_start.run_start(_args(foreground=True))

    (child,) = children
    assert child.argv == ["/opt/kc/bin/kirocrew", "gateway", "--no-open", "--port", str(PORT)]
    assert child.kwargs["start_new_session"] is (sys.platform != "win32")
    # The failure after the spawn still stopped the child, and the handlers are back.
    assert child.signals, "the foreground gateway was left running"
    assert signal.getsignal(signal.SIGINT) is before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal forwarding")
def test_a_forwarded_signal_reaches_the_child_once() -> None:
    child = cli_start._ForegroundGateway()
    fake = _FakeChild(["kirocrew", "gateway"])
    fake.send_signal = fake.signals.append  # type: ignore[method-assign]
    child.proc = fake  # type: ignore[assignment]

    child._forward(signal.SIGINT, None)
    child._forward(platform_compat.SIGHUP, None)

    # SIGINT stays SIGINT (the gateway's graceful Ctrl-C); a hang-up becomes SIGTERM.
    assert fake.signals == [signal.SIGINT, signal.SIGTERM]


def test_a_child_that_ignores_sigterm_has_its_group_killed(monkeypatch) -> None:
    class _Stubborn(_FakeChild):
        def send_signal(self, signum: int) -> None:
            self.signals.append(signum)

        def wait(self, timeout: float | None = None) -> int:
            if self.returncode is None:
                raise subprocess.TimeoutExpired("gateway", timeout or 0)
            return self.returncode

    killed: list[tuple[int, int]] = []

    def _kill_tree(pid: int, sig: int) -> bool:
        killed.append((pid, sig))
        stubborn.returncode = -9
        return True

    stubborn = _Stubborn(["kirocrew", "gateway"])
    monkeypatch.setattr(cli_start.platform_compat, "kill_process_tree", _kill_tree)
    monkeypatch.setattr(cli_start, "TOTAL_SHUTDOWN_BUDGET_SECS", 0)
    child = cli_start._ForegroundGateway()
    child.proc = stubborn  # type: ignore[assignment]

    child.reap()

    assert killed == [(stubborn.pid, cli_start.platform_compat.SIGKILL)]


def test_a_child_killed_by_a_signal_exits_like_a_shell_would() -> None:
    assert cli_start._exit_code_of(0) == 0
    assert cli_start._exit_code_of(3) == 3
    assert cli_start._exit_code_of(-15) == 143


# ── Registration ──


def test_start_is_listed_fourth_in_start_here() -> None:
    from kiro_crew import cli_help

    section, commands = cli_help.COMMAND_GROUPS[0]
    assert section == "Start here"
    assert [name for name, _summary in commands] == ["gateway", "service", "doctor", "start"]


def test_start_help_names_its_flags(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("KIROCREW_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["kirocrew", "start", "--help"])
    from kiro_crew.cli import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: kirocrew start")
    for flag in ("--port", "--no-browser", "--foreground", "--no-input", "--skip-harness-check"):
        assert flag in out


# ── Step 0: the home choice ──


class TestHomeChoice:
    """``kirocrew start`` asks nothing about the home; the first-run chat does."""

    @pytest.fixture(autouse=True)
    def _fresh_install(self, monkeypatch):
        monkeypatch.setattr(cli_start, "_first_run_possible", lambda: True)

    def _args(self, **kw):
        import argparse

        base = dict(home=None, aws_region="", aws_profile="", no_input=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_an_onboarded_install_ignores_the_flag(self, monkeypatch, capsys):
        from kiro_crew import first_run

        monkeypatch.setattr(cli_start, "_first_run_possible", lambda: False)
        cli_start._choose_home(self._args(home="cloud"))
        assert "home" not in first_run.read_state()
        assert "move you to the cloud" in capsys.readouterr().out

    def test_no_flag_asks_nothing_calls_no_aws_and_records_nothing(self, monkeypatch):
        from kiro_crew import first_run
        from kiro_crew.cloud import aws

        monkeypatch.setattr(cli_start, "_interactive", lambda args: True)
        monkeypatch.setattr("builtins.input", _refuse("input"))
        monkeypatch.setattr(aws, "run_aws", _refuse("run_aws"))
        cli_start._choose_home(self._args())
        assert "home" not in first_run.read_state()

    def test_an_explicit_cloud_choice_is_recorded_for_the_first_run(self, monkeypatch, capsys):
        from kiro_crew import first_run
        from kiro_crew.cloud import aws

        monkeypatch.setattr(aws, "run_aws", _refuse("run_aws"))
        cli_start._choose_home(self._args(home="cloud", aws_region="eu-west-1"))
        assert first_run.read_state()["home"] == {"choice": "cloud", "region": "eu-west-1"}
        assert "card in the chat" in capsys.readouterr().out

    def test_a_cloud_choice_takes_the_profile_region(self, tmp_path, monkeypatch):
        from kiro_crew import first_run

        config = tmp_path / "config"
        config.write_text("[profile work]\nregion = eu-north-1\n")
        monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
        cli_start._choose_home(self._args(home="cloud", aws_profile="work"))
        assert first_run.read_state()["home"] == {
            "choice": "cloud",
            "region": "eu-north-1",
            "profile": "work",
        }

    def test_a_cloud_choice_with_no_region_records_none(self, tmp_path, monkeypatch):
        # A new AWS account works in its own region only; the home card finds it.
        from kiro_crew import first_run

        monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
        cli_start._choose_home(self._args(home="cloud"))
        assert first_run.read_state()["home"] == {"choice": "cloud"}

    def test_an_explicit_here_choice_is_recorded(self):
        from kiro_crew import first_run

        cli_start._choose_home(self._args(home="here"))
        assert first_run.read_state()["home"] == {"choice": "here"}


def test_a_relocated_home_never_waits_on_the_machine_service(host, monkeypatch) -> None:
    """A second crew (its own KIROCREW_HOME) spawns its own gateway, not the service's."""
    monkeypatch.setattr(
        cli_start.service_controller, "installed_unit_path", lambda: Path("/etc/unit")
    )
    monkeypatch.setattr(cli_start.service_controller, "is_service_active", lambda: True)
    assert cli_start._service_owns_this_home() is False


def test_the_main_chat_is_where_start_lands(monkeypatch) -> None:
    from kiro_crew import first_run

    first_run.record_slot("chat-1-1790000000")
    first_run.record_main("chat-1-1790000000")
    monkeypatch.setattr(cli_start, "_first_run_possible", lambda: False)
    assert cli_start._landing_path() == "/chat?sid=chat-1-1790000000"


# ── The home this crew moved into ──


class TestHomeHint:
    """After the running line, a crew that moved into a home names it and how to open it."""

    TAG = "kc-4d5e6f"

    def _moved(self, **outcome: Any) -> None:
        from kiro_crew import setup_cards as sc

        card = sc.create_card(
            slot="chat-1-1", session_key="dashboard:chat-1-1", kind=sc.KIND_HOME, payload={}
        )

        def _done(c: sc.SetupCard) -> None:
            c.status = sc.STATUS_COMMITTED
            c.outcome = {"moved": True, **outcome}

        sc.update_card(card.id, _done)

    def _home(self, **over: Any) -> dict[str, str]:
        return {
            "name": "Kiro Crew Cloud (kc-4d5e6f)",
            "tag": self.TAG,
            "region": "eu-west-1",
            "profile": "",
            **over,
        }

    def test_the_home_and_its_open_command_follow_the_running_line(self, host, capsys) -> None:
        self._moved(home=self._home())
        assert cli_start.run_start(_args()) == cli_start.EXIT_OK
        lines = capsys.readouterr().out.splitlines()
        running = next(i for i, line in enumerate(lines) if "Kiro Crew is running on port" in line)
        assert lines[running + 1] == "   Your home in the cloud: Kiro Crew Cloud (kc-4d5e6f)"
        assert lines[running + 2] == (
            f"   Open it:  kirocrew cloud connect --tag {self.TAG} --region eu-west-1"
        )

    def test_a_simulated_home_says_its_command_finds_nothing(self, host, capsys) -> None:
        self._moved(home=self._home(profile="work"), simulated=True)
        cli_start.run_start(_args())
        out = capsys.readouterr().out
        assert "(simulated; this command finds no home)" in out
        assert f"--tag {self.TAG} --region eu-west-1 --profile work" in out

    def test_a_planted_name_is_replaced_by_the_tags_own(self, host, capsys) -> None:
        self._moved(home=self._home(name="\x1b]0;owned\x07 Evil"))
        cli_start.run_start(_args())
        out = capsys.readouterr().out
        assert "\x1b" not in out
        assert "Your home in the cloud: Kiro Crew Cloud (kc-4d5e6f)" in out

    @pytest.mark.parametrize("bad", [{"tag": "kc;rm -rf ~"}, {"region": ""}, {"profile": "-x"}])
    def test_a_home_record_that_does_not_validate_prints_no_command(
        self, host, capsys, bad
    ) -> None:
        self._moved(home=self._home(**bad))
        cli_start.run_start(_args())
        assert "Your home in the cloud" not in capsys.readouterr().out

    def test_a_crew_that_never_moved_prints_nothing_about_a_home(self, host, capsys) -> None:
        cli_start.run_start(_args())
        assert "Your home in the cloud" not in capsys.readouterr().out
