"""App-owned chat slots cannot acquire another surface's session authority."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

SLACK_KEY = "slack:1785370133.085469"
SLACK_STEM = "slack_1785370133.085469"
DISCORD_KEY = "discord:kirocrew:direct:123456"
DISCORD_STEM = "discord_kirocrew_direct_123456"
APP = "issue-radar"


@pytest.fixture
def state(tmp_path):
    """A real state with channel lookup controlled by the test."""
    from chat_test_helpers import _make_state

    st = _make_state(tmp_path)
    st.sessions.channel_key_for_stem.side_effect = lambda stem: (
        SLACK_KEY if stem == SLACK_STEM else DISCORD_KEY if stem == DISCORD_STEM else ""
    )
    st.sessions.get_slack_link.return_value = (None, None)
    return st


@pytest.mark.parametrize("name", [SLACK_STEM, SLACK_KEY, DISCORD_STEM])
def test_app_owned_channel_shaped_slot_stays_on_its_dashboard_session(state, name):
    from kiro_crew.dashboard.chat_utils import effective_session_key

    slot = state.get_or_create_slot(name, app=APP)

    assert slot._app == APP
    assert slot.linked_session_key == ""
    assert slot.to_dict()["linked_session_key"] == ""
    assert effective_session_key(slot) == f"dashboard:{slot.key}"


def test_app_creation_skips_channel_lookup(state):
    state.get_or_create_slot(SLACK_STEM, app=APP)

    state.sessions.channel_key_for_stem.assert_not_called()


def test_dashboard_channel_slot_keeps_legacy_autobind(state):
    from kiro_crew.dashboard.chat_utils import effective_session_key

    slot = state.get_or_create_slot(SLACK_STEM)

    assert slot.linked_session_key == SLACK_KEY
    assert effective_session_key(slot) == SLACK_KEY


def test_existing_dashboard_channel_slot_is_not_taken_over_by_app(state):
    original = state.get_or_create_slot(SLACK_STEM)

    same = state.get_or_create_slot(SLACK_STEM, app=APP)

    assert same is original
    assert same._app == ""
    assert same.linked_session_key == SLACK_KEY


def test_explicit_and_late_bindings_are_refused_with_sel(state):
    from kiro_crew.dashboard.chat_utils import effective_session_key

    with patch("kiro_crew.dashboard.state.sel") as sel_factory:
        explicit = state.get_or_create_slot("app-explicit", app=APP, linked_session_key=SLACK_KEY)
        late = state.get_or_create_slot("app-late", app=APP)
        late.linked_session_key = "cron:nightly"

    assert explicit.linked_session_key == ""
    assert late.linked_session_key == ""
    assert explicit.linked_session_claim == SLACK_KEY
    assert late.linked_session_claim == "cron:nightly"
    assert effective_session_key(explicit) == "dashboard:app-explicit"
    assert effective_session_key(late) == "dashboard:app-late"
    late.linked_session_key = ""
    assert late.linked_session_claim == ""
    assert sel_factory.return_value.log_api_access.call_count == 2
    sel_factory.return_value.log_api_access.assert_any_call(
        caller=APP,
        operation="slot_session_bind",
        outcome="denied",
        source="app_isolation",
        resources="slot=app-explicit",
        error="app-scoped slots cannot carry a linked session binding",
    )


def test_sel_failure_does_not_turn_refusal_into_restore_failure(state):
    slot = state.get_or_create_slot("app-slot", app=APP)

    with patch("kiro_crew.dashboard.state.sel", side_effect=RuntimeError("audit offline")):
        slot.linked_session_key = SLACK_KEY

    assert slot.linked_session_key == ""


def test_unscoped_slot_can_still_be_bound_after_creation(state):
    slot = state.get_or_create_slot("cron-nightly")

    slot.linked_session_key = "cron:nightly"

    assert slot.linked_session_key == "cron:nightly"


TASKRUNNER_KEY = "taskrunner:task-99:chat:deadbeef"


def test_app_own_taskrunner_binding_survives_when_app_set_after_binding(state):
    """Creation order (``_task_result_slot``): bind first, set ``_app`` second.

    The task-review chat is the app's OWN session and must keep routing to it.
    """
    from kiro_crew.dashboard.chat_utils import effective_session_key

    slot = state.get_or_create_slot("task-review-deadbeef", linked_session_key=TASKRUNNER_KEY)
    assert slot.linked_session_key == TASKRUNNER_KEY

    slot._app = APP

    assert slot._app == APP
    assert slot.linked_session_key == TASKRUNNER_KEY
    assert slot.linked_session_claim == TASKRUNNER_KEY
    assert effective_session_key(slot) == TASKRUNNER_KEY


def test_app_own_taskrunner_binding_survives_when_app_set_before_binding(state):
    """Restore order: ``_app`` is set first (chat_persistence L1380), binding second.

    This is the regression the Design review caught: the earlier setter refused
    the app's own ``taskrunner:`` key here and the chat lost its execution
    context after a restart.
    """
    from kiro_crew.dashboard.chat_utils import effective_session_key

    slot = state.get_or_create_slot("task-review-deadbeef")
    slot._app = APP

    slot.linked_session_key = TASKRUNNER_KEY

    assert slot.linked_session_key == TASKRUNNER_KEY
    assert slot.linked_session_claim == TASKRUNNER_KEY
    assert effective_session_key(slot) == TASKRUNNER_KEY


def test_foreign_binding_set_before_app_is_quarantined(state):
    """Reverse order with a NON-own binding: the slot becomes app-owned after a
    foreign binding landed. The invariant must still hold -- the foreign key is
    quarantined off the live route and kept only as an authorization claim.
    """
    from kiro_crew.dashboard.chat_utils import effective_session_key

    slot = state.get_or_create_slot("app-reverse")
    slot.linked_session_key = SLACK_KEY  # no _app yet, lands as a live binding
    assert slot.linked_session_key == SLACK_KEY

    slot._app = APP

    assert slot.linked_session_key == ""
    assert slot.linked_session_claim == SLACK_KEY
    assert effective_session_key(slot) == "dashboard:app-reverse"


def test_note_accepted_on_refused_claim_slot_is_not_dropped(state):
    """A note accepted on an app slot holding a REFUSED binding must survive.

    Regression for the drop GPT 6.1 flagged: the note endpoint returns 200 and
    the drain checks ``note_authorization_session_key`` (claim-aware). A note
    stamped with that key is kept; the old ``effective_session_key`` stamp
    (the dashboard session) mismatches the claim and is dropped every time.
    """
    from kiro_crew.dashboard.chat_utils import (
        effective_session_key,
        note_authorization_session_key,
    )

    slot = state.get_or_create_slot("app-note")
    slot._app = APP
    slot.linked_session_key = "cron:nightly"  # refused -> quarantined as a claim
    assert slot.linked_session_claim == "cron:nightly"
    assert effective_session_key(slot) == "dashboard:app-note"

    # What the FIXED handler stamps.
    slot._pending_context.append(
        {"noteSession": note_authorization_session_key(slot), "content": "kept"}
    )
    # What the OLD handler stamped (the bug): the live dashboard session.
    slot._pending_context.append(
        {"noteSession": effective_session_key(slot), "content": "dropped-by-old-stamp"}
    )

    dropped = slot.drop_foreign_authorized_notes()

    kept = [e.get("content") for e in slot._pending_context]
    assert "kept" in kept
    assert "dropped-by-old-stamp" not in kept
    assert dropped == 1


def test_unscoped_channel_metadata_still_restores_bound(tmp_path, monkeypatch):
    from chat_test_helpers import _make_state

    from kiro_crew.dashboard.chat_persistence import (
        _save_slot_to_history,
        restore_open_slots,
    )

    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    original = _make_state(tmp_path / "sessions")
    slot = original.get_or_create_slot(
        SLACK_STEM, linked_session_key=SLACK_KEY, channel_origin=True
    )
    slot.append("user", "hello", "msg msg-u")
    _save_slot_to_history(original, slot, force=True)
    original._persist_open_slots()

    restarted = _make_state(tmp_path / "sessions")
    restore_open_slots(restarted)

    revived = restarted._slots[SLACK_STEM]
    assert revived.linked_session_key == SLACK_KEY
    assert revived.channel_origin is True


def _chat_app(state, app_identity: str = "") -> web.Application:
    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        if app_identity:
            request["app"] = app_identity
        return await handler(request)

    from kiro_crew.dashboard.chat_handlers import api_chat

    app = web.Application(middlewares=[identity])
    app["state"] = state
    app.router.add_post("/api/chat", api_chat)
    return app


@pytest.mark.asyncio
async def test_app_chat_route_does_not_dispatch_on_channel_session(state):
    from kiro_crew.dashboard.chat_utils import effective_session_key

    with patch("kiro_crew.dashboard.chat_handlers.spawn_guarded_turn") as spawn:
        spawn.return_value = MagicMock()
        async with TestClient(TestServer(_chat_app(state, APP))) as client:
            response = await client.post("/api/chat", json={"slot": SLACK_STEM, "message": "hello"})

    assert response.status == 200
    slot = state._slots[SLACK_STEM]
    assert effective_session_key(slot) == f"dashboard:{SLACK_STEM}"
    assert slot.linked_session_key == ""


@pytest.mark.asyncio
async def test_dashboard_chat_route_keeps_channel_dispatch(state):
    from kiro_crew.dashboard.chat_utils import effective_session_key

    with patch("kiro_crew.dashboard.chat_handlers.spawn_guarded_turn") as spawn:
        spawn.return_value = MagicMock()
        async with TestClient(TestServer(_chat_app(state))) as client:
            response = await client.post("/api/chat", json={"slot": SLACK_STEM, "message": "hello"})

    assert response.status == 200
    assert effective_session_key(state._slots[SLACK_STEM]) == SLACK_KEY
