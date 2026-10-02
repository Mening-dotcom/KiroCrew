"""The scripted sign-in step's check: whether the chosen harness can answer.

Pins the two rules that make the check safe to offer before any model turn:

* a harness that runs kiro-cli is asked through the Kiro prerequisite service's
  probe, and never by spawning ``kiro-cli acp`` (which, signed out, opens an
  interactive browser sign-in);
* any other harness is asked by its install probe and then a handshake with no
  prompt, whose own child is always shut down; Kiro Crew reads no credential file.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

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


class _Client:
    instances: list["_Client"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.shut = False
        self.outcome: Any = None
        _Client.instances.append(self)

    async def ensure_ready(self) -> None:
        if self.outcome is not None:
            raise self.outcome

    async def shutdown(self) -> None:
        self.shut = True


@pytest.fixture
def adapter(monkeypatch):
    """claude, installed; the handshake answered by a fake client."""
    from kiro_crew.acp import client as acp_client

    _Client.instances = []
    monkeypatch.setattr(acp_client, "AcpClient", _Client)
    monkeypatch.setattr(backend_install, "forget_for_recheck", lambda backend: None)
    monkeypatch.setattr(
        backend_install,
        "probe_backend",
        lambda backend: backend_install.BackendInstallState(
            backend, "claude", backend_install.INSTALLED
        ),
    )
    return _Client


class TestEveryOtherHarness:
    @pytest.mark.asyncio
    async def test_an_installed_harness_that_opens_a_session_is_ready(self, adapter):
        verdict = await hr.check(_state(), "claude")
        assert verdict.ready
        (client,) = adapter.instances
        assert client.kwargs["acp_backend"] == "claude" and client.shut

    @pytest.mark.asyncio
    async def test_a_sign_in_failure_on_the_handshake_is_not_signed_in(self, adapter, monkeypatch):
        from kiro_crew.acp.client import AcpAuthRequired

        original = adapter.__init__

        def _init(self, **kwargs: Any) -> None:
            original(self, **kwargs)
            self.outcome = AcpAuthRequired("Run claude to sign in.", backend="claude")

        monkeypatch.setattr(adapter, "__init__", _init)
        verdict = await hr.check(_state(), "claude")
        assert verdict.code == hr.NOT_SIGNED_IN and adapter.instances[0].shut

    @pytest.mark.asyncio
    async def test_a_harness_that_never_answers_is_a_failed_check(self, adapter, monkeypatch):
        monkeypatch.setattr(hr, "HANDSHAKE_TIMEOUT_SECS", 0.01)

        async def _hang(self: Any) -> None:
            await asyncio.sleep(5)

        monkeypatch.setattr(adapter, "ensure_ready", _hang)
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
