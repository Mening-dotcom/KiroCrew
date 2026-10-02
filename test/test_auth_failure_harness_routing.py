"""A turn's sign-in failure is routed by the harness that raised it.

Two consequences of ``AcpAuthRequired`` in ``chat_runner._run_chat``:

* the Kiro prerequisite service is latched signed out only when the failing
  harness signs in through kiro-cli's identity store; a Claude or Codex sign-in
  failure says nothing about kiro-cli;
* a scripted first run's sign-in step is shown again
  (``setup_flow.reopen_signin_after_auth_failure`` decides whether it applies),
  and nothing that step does can break the turn's teardown.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_test_helpers import _make_state

from kiro_crew.acp.client import AcpAuthRequired
from kiro_crew.acp_backends import ACP_BACKEND_CLAUDE, ACP_BACKEND_KAS, ACP_BACKEND_KIRO
from kiro_crew.dashboard import setup_flow
from kiro_crew.dashboard.chat_runner import _run_chat


def _state_and_slot(tmp_path: Path, backend: str):
    """The real turn ``test_active_turn_session_key`` runs, signed out on *backend*."""
    state = _make_state(tmp_path)
    state.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), False, False))
    state.sessions.release = MagicMock()
    state.sessions.reset = AsyncMock()
    state.sessions.set_approval_policy = MagicMock()
    state.sessions.check_context_usage = MagicMock()
    state.sessions.get_slack_link = MagicMock(return_value=(None, None))
    state.sessions.record_failure = AsyncMock()
    state.broadcast_ws = MagicMock()
    state.push_slots_update = MagicMock()
    state.is_yolo_active = MagicMock(return_value=False)
    state._background_tasks = set()
    state.kiro_prerequisite_service = MagicMock()
    slot = state.get_or_create_slot("auth-routing-slot")
    slot.append("user", "hello", "msg msg-u")
    client = state.sessions.get_or_create.return_value[0]
    client.shutdown = AsyncMock()

    async def _signed_out(msg):
        raise AcpAuthRequired("signed out", backend=backend)
        yield  # pragma: no cover - generator shape only

    client.stream = _signed_out
    client.stream_command = _signed_out
    return state, slot


def _error_rows(slot) -> list[dict]:
    return [m for m in slot.messages if isinstance(m, dict) and m.get("role") == "error"]


@pytest.fixture
def reopened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _record(state, slot) -> bool:
        calls.append(slot.key)
        return False

    monkeypatch.setattr(setup_flow, "reopen_signin_after_auth_failure", _record)
    return calls


@pytest.mark.asyncio
async def test_another_harness_failing_to_sign_in_leaves_the_kiro_latch_alone(
    tmp_path, reopened
) -> None:
    state, slot = _state_and_slot(tmp_path, ACP_BACKEND_CLAUDE)

    await _run_chat(state, slot, "test message")

    state.kiro_prerequisite_service.mark_signed_out.assert_not_called()
    assert _error_rows(slot), "the sign-in failure is still reported in the chat"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", [ACP_BACKEND_KIRO, ACP_BACKEND_KAS])
async def test_a_kiro_cli_harness_failing_to_sign_in_latches_the_kiro_service(
    tmp_path, reopened, backend
) -> None:
    state, slot = _state_and_slot(tmp_path, backend)

    await _run_chat(state, slot, "test message")

    state.kiro_prerequisite_service.mark_signed_out.assert_called_once()


@pytest.mark.asyncio
async def test_the_first_run_sign_in_step_is_asked_to_show_again(tmp_path, reopened) -> None:
    state, slot = _state_and_slot(tmp_path, ACP_BACKEND_CLAUDE)

    await _run_chat(state, slot, "test message")

    assert reopened == [slot.key]


@pytest.mark.asyncio
async def test_a_failing_sign_in_step_never_breaks_the_turn(tmp_path, monkeypatch) -> None:
    async def _boom(state, slot) -> bool:
        raise RuntimeError("card store unreadable")

    monkeypatch.setattr(setup_flow, "reopen_signin_after_auth_failure", _boom)
    state, slot = _state_and_slot(tmp_path, ACP_BACKEND_CLAUDE)

    await _run_chat(state, slot, "test message")

    assert _error_rows(slot)
    assert slot._active_turn_session_key == ""
