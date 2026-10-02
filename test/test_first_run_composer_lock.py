"""The first-run chat takes no message until its scripted steps are done (UX.2).

No agent harness is ready to answer before them, so ``api_chat`` refuses a send
there with ``setup_step_pending``. The refusal comes before the pasted-secret
capture, so a refused message is stored nowhere, and it applies to the first-run
chat only: every other chat sends as it always did.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from kiro_crew import first_run
from kiro_crew import setup_cards as sc


def _harness_card(slot_key: str) -> sc.SetupCard:
    return sc.create_card(
        slot=slot_key,
        session_key=f"dashboard:{slot_key}",
        kind=sc.KIND_HARNESS,
        payload={"options": [{"id": "", "label": "Kiro CLI"}], "current": "", "default": ""},
    )


async def _post(state, body, monkeypatch, *, ran: list[str]):
    async def fake_run_chat(st, sl, msg, *, _directive_user_origin):
        ran.append(sl.key)
        sl.append("chunk", "ack", "chunk")

    monkeypatch.setattr("kiro_crew.dashboard.chat_handlers._run_chat", fake_run_chat)
    async with TestClient(TestServer(_make_app(state))) as client:
        resp = await client.post("/api/chat", json=body, timeout=None)
        status = resp.status
        payload = await resp.json() if status != 200 else None
        resp.close()
    await asyncio.sleep(0.05)
    return status, payload


@pytest.mark.asyncio
async def test_the_first_run_chat_refuses_a_send_while_a_step_waits(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    slot = state.get_or_create_slot("chat-1-1")
    first_run.record_slot(slot.key)
    _harness_card(slot.key)
    before = list(slot.messages)

    async def _never(*_a, **_k):
        raise AssertionError("a refused message must not reach the paste capture")

    monkeypatch.setattr("kiro_crew.dashboard.secret_capture.capture_pasted_secrets", _never)
    ran: list[str] = []
    status, body = await _post(
        state, {"message": "my key is sk-not-real-123", "slot": slot.key}, monkeypatch, ran=ran
    )
    assert status == 409
    assert body["code"] == "setup_step_pending" and body["step"] == "harness"
    assert ran == [] and list(slot.messages) == before


@pytest.mark.asyncio
async def test_every_other_chat_sends_as_before(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    first = state.get_or_create_slot("chat-1-1")
    first_run.record_slot(first.key)
    _harness_card(first.key)
    ran: list[str] = []
    status, _ = await _post(state, {"message": "hello", "slot": "chat-2-2"}, monkeypatch, ran=ran)
    assert status == 200 and ran == ["chat-2-2"]


@pytest.mark.asyncio
async def test_the_lock_lifts_once_the_last_step_is_decided(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    slot = state.get_or_create_slot("chat-1-1")
    first_run.record_slot(slot.key)
    card = _harness_card(slot.key)
    sc.claim_pending(card.id, card.payload_hash)
    sc.update_card(card.id, lambda c: setattr(c, "status", sc.STATUS_COMMITTED))
    ran: list[str] = []
    status, _ = await _post(state, {"message": "hello", "slot": slot.key}, monkeypatch, ran=ran)
    assert status == 200 and ran == [slot.key]
