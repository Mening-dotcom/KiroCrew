"""The scripted sign-in step's check: whether the chosen harness can answer.

Pins the two rules that make the check safe to offer before any model turn:

* a harness that runs kiro-cli is asked through the Kiro prerequisite service's
  probe, and never by spawning ``kiro-cli acp`` (which, signed out, opens an
  interactive browser sign-in);
* any other harness is asked by its install probe and then a handshake with no
  prompt, built by the session factory from the loaded config (so it runs under
  the sandbox a session gets), whose own child is always shut down. A sandbox
  that refuses is named by its layer. Whether the harness is signed in is asked
  of its own status command; Kiro Crew reads no credential file.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from kiro_crew.agent_files import MAIN_CHAT_AGENT_NAME
from kiro_crew.agent_sdk import backend_install
from kiro_crew.dashboard import harness_readiness as hr


class _Service:
    def __init__(self, **snap: Any) -> None:
        self.snap = {"installed": True, "authenticated": True, "acp_supported": True, **snap}
        self.forced: list[bool] = []

    async def snapshot(self, *, force: bool = False, coalesce: bool = False) -> dict[str, Any]:
        self.forced.append(force)
        return dict(self.snap)


def _state(service: Any = None) -> SimpleNamespace:
    return SimpleNamespace(kiro_prerequisite_service=service)


@pytest.fixture
def no_spawn(monkeypatch):
    """The kiro-cli harnesses must never reach an ACP spawn."""
    from kiro_crew.acp import client as acp_client

    def _refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the Kiro CLI check must not spawn an ACP client")

    monkeypatch.setattr(acp_client, "AcpClient", _refuse)


class TestTheKiroCliHarnesses:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", ["", "kas"])
    async def test_a_signed_in_kiro_cli_is_ready_from_a_forced_probe(self, backend, no_spawn):
        service = _Service()
        verdict = await hr.check(_state(service), backend)
        assert verdict.ready and verdict.backend == backend
        assert service.forced == [True]

    @pytest.mark.asyncio
    async def test_a_missing_kiro_cli_is_not_installed(self, no_spawn):
        verdict = await hr.check(_state(_Service(installed=False)), "")
        assert verdict.code == hr.NOT_INSTALLED

    @pytest.mark.asyncio
    async def test_a_signed_out_kiro_cli_is_not_signed_in(self, no_spawn):
        verdict = await hr.check(_state(_Service(authenticated=False)), "")
        assert verdict.code == hr.NOT_SIGNED_IN

    @pytest.mark.asyncio
    async def test_kas_counts_crews_own_vault_identity(self, no_spawn, monkeypatch):
        from kiro_crew.auth import bridge

        monkeypatch.setattr(bridge, "vault_holds_identity", lambda: True)
        assert (await hr.check(_state(_Service(authenticated=False)), "kas")).ready
        # The plain Kiro harness signs in through kiro-cli's own store only.
        assert not (await hr.check(_state(_Service(authenticated=False)), "")).ready

    @pytest.mark.asyncio
    async def test_an_old_kiro_cli_and_a_failed_probe_are_their_own_verdicts(self, no_spawn):
        assert (await hr.check(_state(_Service(acp_supported=False)), "")).code == hr.OUTDATED
        sandbox = _Service(installed=False, sandbox_unavailable=True, sandbox_detail="EPERM")
        verdict = await hr.check(_state(sandbox), "")
        assert verdict.code == hr.CHECK_FAILED and verdict.detail == "EPERM"

    @pytest.mark.asyncio
    async def test_no_service_is_a_failed_check_not_a_crash(self, no_spawn):
        assert (await hr.check(_state(None), "")).code == hr.CHECK_FAILED


class _Provider:
    """What the session factory hands back, faked at its start and shutdown."""

    instances: list["_Provider"] = []

    def __init__(self, session_key: str, backend: str, **kwargs: Any) -> None:
        self.session_key = session_key
        self.kwargs = kwargs
        self.client = SimpleNamespace(backend=backend)
        self.shut = False
        self.outcome: Any = None
        _Provider.instances.append(self)

    async def start(self) -> None:
        if self.outcome is not None:
            raise self.outcome

    async def shutdown(self) -> None:
        self.shut = True


@pytest.fixture
def adapter(monkeypatch):
    """claude, installed; the handshake's provider faked behind the real factory seam."""
    from kiro_crew.config import loader

    _Provider.instances = []
    built: list[Any] = []

    def _build(cfg: Any) -> Any:
        built.append(cfg)

        def _factory(session_key: str, **kwargs: Any) -> _Provider:
            return _Provider(session_key, str(cfg.agent.acp_backend or ""), **kwargs)

        return _factory

    monkeypatch.setattr(loader, "build_provider_factory", _build)
    monkeypatch.setattr(backend_install, "forget_for_recheck", lambda backend: None)
    monkeypatch.setattr(
        backend_install,
        "probe_backend",
        lambda backend: backend_install.BackendInstallState(
            backend, "claude", backend_install.INSTALLED
        ),
    )
    _write_config({"agent": {"acp_backend": "claude"}})
    _Provider.built = built  # type: ignore[attr-defined]
    return _Provider


def _write_config(data: dict[str, Any]) -> None:
    from kiro_crew.config.paths import data_home

    data_home().mkdir(parents=True, exist_ok=True)
    (data_home() / "config.json").write_text(json.dumps(data))


def _fail_with(adapter: Any, monkeypatch: Any, outcome: BaseException) -> None:
    original = adapter.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.outcome = outcome

    monkeypatch.setattr(adapter, "__init__", _init)


class TestEveryOtherHarness:
    @pytest.mark.asyncio
    async def test_an_installed_harness_that_opens_a_session_is_ready(self, adapter):
        verdict = await hr.check(_state(), "claude")
        assert verdict.ready
        (provider,) = adapter.instances
        assert provider.session_key == "setup-check:claude" and provider.shut
        assert provider.kwargs["agent"] == MAIN_CHAT_AGENT_NAME

    @pytest.mark.asyncio
    async def test_a_sign_in_failure_on_the_handshake_is_not_signed_in(self, adapter, monkeypatch):
        from kiro_crew.acp.client import AcpAuthRequired

        _fail_with(
            adapter, monkeypatch, AcpAuthRequired("Run claude to sign in.", backend="claude")
        )
        verdict = await hr.check(_state(), "claude")
        assert verdict.code == hr.NOT_SIGNED_IN and adapter.instances[0].shut

    @pytest.mark.asyncio
    async def test_a_harness_that_never_answers_is_a_failed_check(self, adapter, monkeypatch):
        monkeypatch.setattr(hr, "HANDSHAKE_TIMEOUT_SECS", 0.01)

        async def _hang(self: Any) -> None:
            await asyncio.sleep(5)

        monkeypatch.setattr(adapter, "start", _hang)
        verdict = await hr.check(_state(), "claude")
        assert verdict.code == hr.CHECK_FAILED and adapter.instances[0].shut

    @pytest.mark.asyncio
    async def test_a_missing_harness_is_never_spawned(self, adapter, monkeypatch):
        monkeypatch.setattr(
            backend_install,
            "probe_backend",
            lambda backend: backend_install.BackendInstallState(
                backend, "claude", backend_install.MISSING, ("claude-agent-acp",)
            ),
        )
        verdict = await hr.check(_state(), "claude")
        assert verdict.code == hr.NOT_INSTALLED and "claude-agent-acp" in verdict.detail
        assert adapter.instances == []

    @pytest.mark.asyncio
    async def test_a_check_that_would_start_another_harness_vouches_for_nothing(
        self, adapter, monkeypatch
    ):
        """The factory names the harness from config; a mismatch is never started."""
        _write_config({"agent": {"acp_backend": "codex"}})
        verdict = await hr.check(_state(), "claude")
        assert verdict.code == hr.CHECK_FAILED
        assert adapter.instances[0].shut is False, "a mismatched provider must not start"


class TestTheCheckRunsWhatASessionRuns:
    """The check is built by the session factory from the loaded config, so the
    sandbox it runs under is the one the first turn will get, not a constant."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["off", "strict"])
    async def test_the_configured_sandbox_mode_reaches_the_spawn(self, mode, monkeypatch):
        from kiro_crew.providers.acp import AcpProvider

        _write_config({"agent": {"acp_backend": "claude", "sandbox": mode}})
        monkeypatch.setattr(backend_install, "forget_for_recheck", lambda backend: None)
        monkeypatch.setattr(
            backend_install,
            "probe_backend",
            lambda backend: backend_install.BackendInstallState(
                backend, "claude", backend_install.INSTALLED
            ),
        )
        seen: list[tuple[str, str]] = []

        async def _start(self: Any) -> None:
            seen.append((self._client.backend, self._client._sandbox_mode))

        async def _shutdown(self: Any) -> None:
            return None

        monkeypatch.setattr(AcpProvider, "start", _start)
        monkeypatch.setattr(AcpProvider, "shutdown", _shutdown)
        verdict = await hr.check(_state(), "claude")
        assert verdict.ready, verdict
        assert seen == [("claude", mode)]


#: The error the owner's Mac showed on Continue, verbatim: Claude Code, started by
#: claude-agent-acp inside Kiro Crew's sandbox, could not start a sandbox of its own.
RECORDED_DETAILS = (
    "Claude Code process exited with code 1. stderr: sandbox initialization failed: "
    "Operation not permitted\nError: Failed to spawn child process\n\nCaused by:\n"
    "    Invalid argument (os error 22)"
)


class TestASandboxThatRefusesIsNamedByItsLayer:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("crew_wrap", "platform", "code"),
        [
            (True, "darwin", hr.SANDBOX_NESTED),
            (True, "linux", hr.SANDBOX_CREW),
            (False, "darwin", hr.SANDBOX_HARNESS),
        ],
        ids=["nested-on-macos", "crew-elsewhere", "harness-only"],
    )
    async def test_the_recorded_refusal_reaches_the_card_in_plain_words(
        self, adapter, monkeypatch, crew_wrap, platform, code
    ):
        from kiro_crew.acp.client import AcpError, sandbox_init_failure_from_error

        refusal = await sandbox_init_failure_from_error(
            AcpError("JSON-RPC error", code=-32603, details=RECORDED_DETAILS),
            crew_wrap=crew_wrap,
        )
        assert refusal is not None
        _fail_with(adapter, monkeypatch, refusal)
        monkeypatch.setattr(hr.sys, "platform", platform)
        verdict = await hr.check(_state(), "claude")
        assert verdict.code == code
        # The detail is the classified error's own message, remedy included, and
        # an uncorroborated refusal never hands out the switch that turns it off.
        assert verdict.detail == str(refusal)
        assert "agent.sandbox off" not in verdict.detail
        assert adapter.instances[0].shut

    def test_the_wrappers_own_refusal_is_not_called_nesting(self, monkeypatch):
        from kiro_crew.acp.client import AcpSandboxInitFailed
        from kiro_crew.sandbox import SANDBOX_LAYER_CREW

        monkeypatch.setattr(hr.sys, "platform", "darwin")
        own = AcpSandboxInitFailed(
            layer=SANDBOX_LAYER_CREW, detail="sandbox-exec: sandbox_apply: Operation not permitted"
        )
        assert hr.sandbox_code(own) == hr.SANDBOX_CREW


class TestTheHarnessSaysWhetherItIsSignedIn:
    """Signed in is asked of the harness's own status command, never its files."""

    @pytest.mark.parametrize(
        ("exit_code", "output", "answer"),
        [
            (0, '{\n  "loggedIn": true,\n  "authMethod": "third_party"\n}', True),
            (1, '{"loggedIn": false}', False),
            # An older Claude Code that does not know the command.
            (1, "error: unknown option '--json'", None),
            # The two halves disagree: not an answer.
            (1, '{"loggedIn": true}', None),
            (0, 'warning: something\n{"loggedIn": true}', True),
        ],
    )
    def test_claude_reads_its_json_answer(self, exit_code, output, answer):
        from kiro_crew.agent_sdk.host_auth import SIGN_IN_STATUS_JSON_LOGGED_IN

        assert hr.read_sign_in_status(SIGN_IN_STATUS_JSON_LOGGED_IN, exit_code, output) is answer

    @pytest.mark.parametrize(
        ("exit_code", "output", "answer"),
        [
            (0, "Logged in using ChatGPT", True),
            (1, "Not logged in", False),
            # A managed build with no login of its own: unknown, never "signed out".
            (1, "Login is not required. This Codex uses managed credentials.", None),
            # An older Codex without the subcommand.
            (2, "error: unexpected argument 'status' found", None),
        ],
    )
    def test_codex_reads_its_status_line(self, exit_code, output, answer):
        from kiro_crew.agent_sdk.host_auth import SIGN_IN_STATUS_LOGGED_IN_LINE

        assert hr.read_sign_in_status(SIGN_IN_STATUS_LOGGED_IN_LINE, exit_code, output) is answer

    @pytest.fixture
    def status_runs(self, monkeypatch):
        from kiro_crew.acp import client as acp_client

        hr._status_cache.clear()
        runs: list[tuple[str, list[str], str]] = []
        answers: dict[str, tuple[int, str] | None] = {}

        async def _run(backend: str, argv: list[str], *, mode: str, work_dir: str) -> Any:
            runs.append((backend, argv, mode))
            return answers.get(backend)

        monkeypatch.setattr(acp_client, "run_sign_in_status_command", _run)
        monkeypatch.setattr(acp_client, "resolve_harness_executable", lambda name: f"/bin/{name}")
        return runs, answers

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("backend", "argv", "answer", "expected"),
        [
            (
                "claude",
                ["/bin/claude", "auth", "status", "--json"],
                (0, '{"loggedIn": true}'),
                True,
            ),
            (
                "claude",
                ["/bin/claude", "auth", "status", "--json"],
                (1, '{"loggedIn": false}'),
                False,
            ),
            ("codex", ["/bin/codex", "login", "status"], (0, "Logged in using ChatGPT"), True),
            ("codex", ["/bin/codex", "login", "status"], (1, "Not logged in"), False),
        ],
    )
    async def test_each_harness_is_asked_its_own_command_in_its_sandbox(
        self, status_runs, backend, argv, answer, expected
    ):
        runs, answers = status_runs
        _write_config({"agent": {"sandbox": "strict"}})
        answers[backend] = answer
        assert await hr.signed_in(backend) is expected
        assert runs == [(backend, argv, "strict")]

    @pytest.mark.asyncio
    async def test_a_missing_status_command_is_unknown_and_spawns_nothing(
        self, status_runs, monkeypatch
    ):
        from kiro_crew.acp import client as acp_client

        runs, _ = status_runs
        monkeypatch.setattr(acp_client, "resolve_harness_executable", lambda name: None)
        assert await hr.signed_in("codex") is None
        assert runs == []

    @pytest.mark.asyncio
    async def test_a_command_that_cannot_answer_is_unknown(self, status_runs):
        runs, answers = status_runs
        answers["claude"] = None  # did not run, or timed out
        assert await hr.signed_in("claude") is None
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_a_harness_with_no_status_command_is_never_asked(self, status_runs):
        runs, _ = status_runs
        assert await hr.signed_in("opencode") is None
        assert runs == []

    @pytest.mark.asyncio
    async def test_tabs_polling_together_share_one_answer(self, status_runs):
        runs, answers = status_runs
        answers["claude"] = (0, '{"loggedIn": true}')
        results = await asyncio.gather(*(hr.signed_in("claude") for _ in range(4)))
        assert results == [True] * 4 and len(runs) == 1
        hr.forget_status("claude")
        assert await hr.signed_in("claude") is True and len(runs) == 2

    def test_the_readiness_module_reads_no_harness_credential_file(self):
        """The rule this module documents: it asks the harness, never its files."""
        import inspect

        from kiro_crew.agent_sdk.host_auth import AGENT_AUTH_DECLARATIONS

        source = inspect.getsource(hr)
        for declaration in AGENT_AUTH_DECLARATIONS:
            for leaf in declaration.credential_leaves:
                assert leaf not in source, f"harness_readiness names {leaf!r}"


class TestTheSignInStatusRoute:
    """``GET /api/setup/cards/{id}/signin-status``: owner-only, and only for a card
    whose payload offers it."""

    @staticmethod
    def _app() -> Any:
        from aiohttp import web

        from kiro_crew.dashboard.handlers import setup_cards as handlers

        app = web.Application()
        app["state"] = SimpleNamespace()
        handlers.register_routes(app)
        return app

    @pytest.mark.asyncio
    async def test_the_owner_reads_what_the_harness_says(self, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew import setup_cards as sc
        from kiro_crew.dashboard.handlers import setup_cards as handlers

        async def _owner(request: Any, operation: str) -> None:
            return None

        asked: list[str] = []

        async def _signed_in(backend: str) -> bool:
            asked.append(backend)
            return True

        monkeypatch.setattr(handlers, "require_owner_dashboard_request", _owner)
        monkeypatch.setattr(hr, "signed_in", _signed_in)
        card = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_HARNESS_SIGNIN,
            payload={"backend": "claude", "flow": "own", "sign_in_status": True},
        )
        plain = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_HARNESS_SIGNIN,
            payload={"backend": "", "flow": "kiro_cli"},
        )
        async with TestClient(TestServer(self._app())) as client:
            r = await client.get(f"/api/setup/cards/{card.id}/signin-status")
            assert r.status == 200 and (await r.json()) == {"signed_in": True}
            r = await client.get(f"/api/setup/cards/{plain.id}/signin-status")
            assert r.status == 404
            sc.update_card(card.id, lambda c: setattr(c, "status", sc.STATUS_COMMITTED))
            r = await client.get(f"/api/setup/cards/{card.id}/signin-status")
            assert (await r.json()) == {"signed_in": None}
        assert asked == ["claude"], "a decided card must not run the harness's command"

    @pytest.mark.asyncio
    async def test_a_stranger_cannot_ask(self, monkeypatch):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew import setup_cards as sc
        from kiro_crew.dashboard.handlers import setup_cards as handlers

        async def _refuse(request: Any, operation: str) -> Any:
            return web.json_response({"error": "owner only", "code": "owner_only"}, status=403)

        monkeypatch.setattr(handlers, "require_owner_dashboard_request", _refuse)
        card = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_HARNESS_SIGNIN,
            payload={"backend": "claude", "flow": "own", "sign_in_status": True},
        )
        async with TestClient(TestServer(self._app())) as client:
            r = await client.get(f"/api/setup/cards/{card.id}/signin-status")
            assert r.status == 403
