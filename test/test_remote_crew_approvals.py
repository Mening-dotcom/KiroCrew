"""A remote-bound session's tool approvals and approval mode reach its peer.

The peer runs the session's tools, so the peer owns every approval and the
approval mode. This side shows the peer's pending request as an actionable card,
forwards the user's decision or mode change, and changes local state only after
the peer accepts. Nothing here can widen what THIS gateway auto-approves.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

import kiro_crew
from kiro_crew.dashboard import remote_mirror, remote_relay
from kiro_crew.dashboard.remote_relay import (
    RemoteTurnError,
    forward_peer_approval,
    forward_peer_mode,
    relay_remote_turn,
)
from kiro_crew.dashboard.state import _ChatSlot
from kiro_crew.safety_override import safety_override


@pytest.fixture(autouse=True)
def _clean_mirror():
    remote_mirror.reset_for_tests()
    yield
    remote_mirror.reset_for_tests()


def _remote_slot(key: str = "chat-1") -> _ChatSlot:
    slot = _ChatSlot(key)
    slot.executor = "remote"
    slot.instance_id = "nobita"
    slot.remote_slot = "peer-chat-9"
    return slot


def _permission_row(request_id: str = "req-1", peer_mid: str = "peer-mid-1", **extra: str) -> dict:
    meta = {"request_id": request_id, "tool_call_id": "t1", "trust_grantable": "1", **extra}
    return {
        "type": "permission",
        "content": "Run ls",
        "cls": json.dumps(meta),
        "request_mid": peer_mid,
    }


_BOUND = {"origin": "native", "request_mid": "peer-mid-1"}


def _resolved_frame(request_id: str = "req-1", approved: bool = True) -> dict:
    payload = {"id": request_id, "approved": approved, "slot": "peer-chat-9"}
    return {"type": "relay:approval_resolved", "content": json.dumps(payload)}


def _replay(state, slot, row: dict) -> None:
    remote_relay._apply_row(state, slot, row, remote_relay._ChunkSequencer(slot))


def _row_meta(slot: _ChatSlot, request_id: str = "req-1") -> dict:
    for message in slot.messages:
        if message.get("role") == "permission":
            meta = json.loads(message["cls"])
            if meta.get("request_id") == request_id:
                return meta
    raise AssertionError(f"no permission row for {request_id}")


def _pending(slot: _ChatSlot) -> list[str]:
    return [k for k, f in slot._approval_futures.items() if not f.done()]


async def _stream(*records: bytes) -> AsyncIterator[bytes]:
    for record in records:
        yield record


def _sse(row: dict) -> bytes:
    return f"data: {json.dumps(row)}\n\n".encode()


def _bound_state(tmp_path):
    state = _make_state(tmp_path)
    state.broadcast_ws = MagicMock()
    slot = _remote_slot()
    state._slots[slot.key] = slot
    return state, slot


class TestRelayedPermissionCard:
    @pytest.mark.asyncio
    async def test_a_pending_peer_request_becomes_an_answerable_card(self, tmp_path):
        state, slot = _bound_state(tmp_path)

        _replay(state, slot, _permission_row())

        info = state.serialize_slot(slot)["pending_approval_info"]
        assert info["origin"] == "native"
        assert info["request_id"] == "req-1"
        assert info["request_mid"]

    @pytest.mark.asyncio
    async def test_a_request_the_peer_already_decided_offers_nothing(self, tmp_path):
        state, slot = _bound_state(tmp_path)

        _replay(state, slot, _permission_row(resolved="rejected"))

        assert _pending(slot) == []

    @pytest.mark.asyncio
    async def test_the_peers_own_decision_retires_the_card(self, tmp_path):
        """A peer timeout or a decision made on the peer's dashboard."""
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())

        _replay(state, slot, _resolved_frame(approved=False))

        assert _pending(slot) == []
        assert _row_meta(slot)["resolved"] == "rejected"
        frames = [c.args for c in state.broadcast_ws.call_args_list]
        assert ("approval_resolved", {"id": "req-1", "approved": False, "slot": "chat-1"}) in frames

    @pytest.mark.asyncio
    async def test_a_turn_that_ends_with_the_card_open_retires_it(self, tmp_path):
        state, slot = _bound_state(tmp_path)

        await relay_remote_turn(
            state, slot, "hi", chunks=_stream(_sse(_permission_row()), b"data: [DONE]\n\n")
        )

        assert _pending(slot) == []
        assert _row_meta(slot)["resolved"] == "rejected"

    @pytest.mark.asyncio
    async def test_a_truncated_turn_records_no_rejection_the_peer_never_got(self, tmp_path):
        """The peer may still be running, so nothing is decided on its behalf."""
        state, slot = _bound_state(tmp_path)

        await relay_remote_turn(state, slot, "hi", chunks=_stream(_sse(_permission_row())))

        assert _pending(slot) == []
        assert "resolved" not in _row_meta(slot)
        assert not [
            c for c in state.broadcast_ws.call_args_list if c.args[0] == "approval_resolved"
        ]

    @pytest.mark.asyncio
    async def test_a_bare_id_resolver_cannot_settle_the_card(self, tmp_path):
        """The generic resolver would hide the card while the peer keeps waiting."""
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())

        assert state.resolve_approval("req-1", True) is False
        assert _pending(slot) == ["req-1"]


class TestThePeerWireNamesTheRowInstance:
    def test_a_relayed_permission_row_carries_its_own_mid(self):
        """Built by the peer's real drain, read by the real relay reader."""
        from kiro_crew.dashboard.chat_utils import _build_stream_chunk

        peer = _ChatSlot("peer-chat-9")
        stored = peer.append("permission", "Run ls", json.dumps({"request_id": "req-1"}))
        record = json.loads(_build_stream_chunk(stored, include_row_meta=True))

        assert record["request_mid"] == stored["meta"]["mid"]
        assert "request_mid" not in json.loads(_build_stream_chunk(stored))

    @pytest.mark.asyncio
    async def test_the_mirrored_row_keeps_the_peer_instance(self, tmp_path):
        from kiro_crew.dashboard.chat_utils import _build_stream_chunk

        peer = _ChatSlot("peer-chat-9")
        stored = peer.append("permission", "Run ls", json.dumps({"request_id": "req-1"}))
        state, slot = _bound_state(tmp_path)

        _replay(state, slot, json.loads(_build_stream_chunk(stored, include_row_meta=True)))

        assert _row_meta(slot)["peer_request_mid"] == stored["meta"]["mid"]


class TestApprovalDecisionReachesThePeer:
    @pytest.fixture
    def forward(self, monkeypatch):
        spy = AsyncMock(return_value=None)
        monkeypatch.setattr("kiro_crew.dashboard.chat_handlers.forward_peer_approval", spy)
        return spy

    @pytest.mark.parametrize("action", ["approved", "rejected_once", "trust", "yolo"])
    @pytest.mark.asyncio
    async def test_the_decision_is_forwarded_and_nothing_local_is_widened(
        self, tmp_path, forward, action
    ):
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/slots/chat-1/approve", json={"action": action, "request_id": "req-1"}
            )

        assert resp.status == 200
        forwarded = forward.call_args.args[2]
        one_shot = _BOUND if action in ("approved", "rejected_once") else {}
        assert forwarded == {"action": action, "request_id": "req-1", **one_shot}
        assert _pending(slot) == []
        assert _row_meta(slot)["resolved"] == (
            "trust"
            if action == "trust"
            else "rejected_once" if action == "rejected_once" else "approved"
        )
        assert slot._trust is False
        assert safety_override().is_active() is False

    @pytest.mark.parametrize("action", ["approved", "rejected", "rejected_once"])
    @pytest.mark.asyncio
    async def test_a_card_click_bound_to_its_row_reaches_the_peer(self, tmp_path, forward, action):
        """The composer card sends the slot route with the row it showed."""
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())
        local_mid = slot.approval_instance("req-1")

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/slots/chat-1/approve",
                json={
                    "action": action,
                    "request_id": "req-1",
                    "request_mid": local_mid,
                    "origin": "native",
                },
            )

        assert resp.status == 200
        assert forward.call_args.args[2] == {"action": action, "request_id": "req-1", **_BOUND}
        assert _row_meta(slot)["resolved"] == action

    @pytest.mark.asyncio
    async def test_a_stale_card_click_never_reaches_a_newer_request(self, tmp_path, forward):
        """Card A expired and B reuses the id: A's click names A's row and is refused."""
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())
        stale_mid = slot.approval_instance("req-1")
        _replay(state, slot, _permission_row(peer_mid="peer-mid-2"))

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/slots/chat-1/approve",
                json={
                    "action": "approved",
                    "request_id": "req-1",
                    "request_mid": stale_mid,
                    "origin": "native",
                },
            )

        assert resp.status == 404
        forward.assert_not_called()
        assert _pending(slot) == ["req-1"]

    @pytest.mark.asyncio
    async def test_a_bare_id_never_decides_a_remote_card(self, tmp_path, forward):
        """No slot or row names the request, so the generic route decides nothing."""
        from kiro_crew.dashboard.handlers.sessions import api_approval_resolve

        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())
        app = _make_app(state)
        app.router.add_post("/api/approvals/{id}/{action}", api_approval_resolve)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/approvals/req-1/approve")

        assert resp.status == 404
        forward.assert_not_called()
        assert _pending(slot) == ["req-1"]

    @pytest.mark.asyncio
    async def test_a_request_reusing_the_id_mid_forward_stays_pending(self, tmp_path, monkeypatch):
        """The decision is bound to the row the card showed, here and on the peer."""
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())

        async def _reuse(*_args):
            _replay(state, slot, _permission_row(peer_mid="peer-mid-2"))

        spy = AsyncMock(side_effect=_reuse)
        monkeypatch.setattr("kiro_crew.dashboard.chat_handlers.forward_peer_approval", spy)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/slots/chat-1/approve", json={"action": "approved", "request_id": "req-1"}
            )

        assert resp.status == 200
        assert spy.call_args.args[2]["request_mid"] == "peer-mid-1"
        assert _pending(slot) == ["req-1"]
        rows = [json.loads(m["cls"]) for m in slot.messages if m.get("role") == "permission"]
        assert [r.get("resolved") for r in rows] == ["approved", None]

    @pytest.mark.asyncio
    async def test_a_row_without_the_peer_instance_is_not_forwarded(self, tmp_path, forward):
        """No card is offered for it, and a direct call still reaches nothing."""
        state, slot = _bound_state(tmp_path)
        row = _permission_row()
        del row["request_mid"]
        _replay(state, slot, row)

        assert _pending(slot) == []
        assert state.serialize_slot(slot)["pending_approval_info"] is None

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/slots/chat-1/approve", json={"action": "approved", "request_id": "req-1"}
            )

        assert resp.status == 404
        forward.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_scoped_grant_carries_its_pattern_to_the_peer(self, tmp_path, forward):
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/slots/chat-1/approve",
                json={"action": "trust_command", "request_id": "req-1", "pattern": "ls -la"},
            )

        assert resp.status == 200
        assert forward.call_args.args[2]["pattern"] == "ls -la"
        assert slot._trusted_patterns == set()

    @pytest.mark.asyncio
    async def test_a_peer_refusal_keeps_the_card_retryable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "kiro_crew.dashboard.chat_handlers.forward_peer_approval",
            AsyncMock(side_effect=RemoteTurnError("crew says no")),
        )
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/slots/chat-1/approve", json={"action": "approved", "request_id": "req-1"}
            )
            body = await resp.json()

        assert resp.status == 502
        assert body["code"] == "remote_approval_failed"
        assert _pending(slot) == ["req-1"]
        assert "resolved" not in _row_meta(slot)

    @pytest.mark.asyncio
    async def test_a_half_bound_slot_is_refused_before_the_peer(self, tmp_path, forward):
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())
        slot.instance_id = ""

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post(
                "/api/chat/slots/chat-1/approve", json={"action": "approved", "request_id": "req-1"}
            )

        assert resp.status == 409
        forward.assert_not_called()


class TestApprovalModeReachesThePeer:
    @pytest.fixture
    def forward(self, monkeypatch):
        spy = AsyncMock(return_value=None)
        monkeypatch.setattr("kiro_crew.dashboard.chat_handlers.forward_peer_mode", spy)
        return spy

    @pytest.mark.parametrize("mode", ["normal", "trust_reads", "trust"])
    @pytest.mark.asyncio
    async def test_a_session_mode_is_applied_on_the_peer_only(self, tmp_path, forward, mode):
        """This side cannot verify the peer's resulting mode, so it records none."""
        state, slot = _bound_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": mode, "slot": "chat-1"})

        assert resp.status == 200
        assert forward.call_args.args[1:] == (slot, mode)
        assert (slot._trust, slot._trust_reads) == (False, False)

    @pytest.mark.asyncio
    async def test_yolo_arms_the_peer_and_never_this_gateway(self, tmp_path, forward):
        state, slot = _bound_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "yolo", "slot": "chat-1"})

        assert resp.status == 200
        assert forward.call_args.args[1:] == (slot, "yolo")
        assert safety_override().is_active() is False

    @pytest.mark.asyncio
    async def test_a_peer_refusal_changes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "kiro_crew.dashboard.chat_handlers.forward_peer_mode",
            AsyncMock(side_effect=RemoteTurnError("policy forbids it")),
        )
        state, slot = _bound_state(tmp_path)

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "trust", "slot": "chat-1"})
            body = await resp.json()

        assert resp.status == 502
        assert body == {"ok": False, "error": "policy forbids it", "code": "remote_mode_failed"}
        assert slot._trust is False

    @pytest.mark.asyncio
    async def test_an_all_sessions_trust_leaves_the_peers_card_pending(self, tmp_path, forward):
        """That change never reached the peer, so its tool still waits."""
        state, slot = _bound_state(tmp_path)
        _replay(state, slot, _permission_row())

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "trust"})

        assert resp.status == 200
        forward.assert_not_called()
        assert _pending(slot) == ["req-1"]
        # Nor does the remote slot claim a Trust its peer never received.
        assert slot._trust is False


class _FakeUpstream:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.content = SimpleNamespace(read=AsyncMock(return_value=body))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _mgr_returning(status: int, body: bytes):
    mgr = MagicMock()
    mgr.peer_version = AsyncMock(return_value=(True, kiro_crew.__version__))
    mgr.proxy_request = MagicMock(return_value=_FakeUpstream(status, body))
    return mgr


class TestPeerRoutes:
    @pytest.mark.asyncio
    async def test_a_decision_reaches_the_peers_own_slot(self, tmp_path):
        state = _make_state(tmp_path)
        state.instances_manager = mgr = _mgr_returning(200, b'{"ok": true}')

        await forward_peer_approval(
            state, _remote_slot(), {"action": "approved", "request_id": "req-1"}
        )

        args, kwargs = mgr.proxy_request.call_args
        assert args[2] == "api/chat/slots/peer-chat-9/approve"
        assert json.loads(kwargs["data"]) == {"action": "approved", "request_id": "req-1"}

    @pytest.mark.asyncio
    async def test_a_mode_change_names_the_peers_own_slot(self, tmp_path):
        state = _make_state(tmp_path)
        state.instances_manager = mgr = _mgr_returning(200, b'{"ok": true}')

        await forward_peer_mode(state, _remote_slot(), "yolo")

        args, kwargs = mgr.proxy_request.call_args
        assert args[2] == "api/chat/mode"
        assert json.loads(kwargs["data"]) == {"mode": "yolo", "slot": "peer-chat-9"}

    @pytest.mark.asyncio
    async def test_the_peers_refusal_is_what_the_user_reads(self, tmp_path):
        state = _make_state(tmp_path)
        state.instances_manager = _mgr_returning(403, b'{"error": "mode disabled by policy"}')

        with pytest.raises(RemoteTurnError, match="mode disabled by policy"):
            await forward_peer_mode(state, _remote_slot(), "yolo")
