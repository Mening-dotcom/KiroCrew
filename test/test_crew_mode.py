"""Crew Mode: the slot-create rule, the Settings default and the conductor prompt.

POST /api/chat/slots: Crew Mode at birth.

Crew Mode is a UI over the existing ``kirocrew-conductor``: a chat with the
switch on runs on that agent. ``dashboard.default_crew_mode`` starts new plain
chats on the default agent that way, and an explicit ``crew_mode`` from the
composer switch wins over the setting. Everything that is not a new plain chat
on the default agent keeps exactly the agent it asked for.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from kiro_crew.agent_files import CREW_MODE_AGENT_NAME
from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.dashboard import chat_handlers


def _config(*, default_crew_mode: bool, default_agent: str = "") -> KiroCrewConfig:
    cfg = KiroCrewConfig()
    cfg.default_agent = default_agent
    cfg.dashboard.default_crew_mode = default_crew_mode
    return cfg


@pytest.fixture
def dashboard_state(tmp_path: Any) -> Any:
    return _make_state(tmp_path)


async def _create(
    state: Any, monkeypatch: pytest.MonkeyPatch, cfg: KiroCrewConfig, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    import kiro_crew.config.loader as loader_mod

    # The conductor is a template the gateway installs at startup; the test
    # home has none, so declare it installed the way the gateway would.
    monkeypatch.setattr(loader_mod, "_MATERIALIZED_AGENTS_READY", True)
    monkeypatch.setattr(loader_mod, "_MATERIALIZED_AGENTS", {"kirocrew", CREW_MODE_AGENT_NAME})
    monkeypatch.setattr(chat_handlers, "KiroCrewConfig", SimpleNamespace(load=lambda: cfg))
    monkeypatch.setattr(chat_handlers, "schedule_eager_spawn", lambda *a, **k: None)
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/slots", chat_handlers.api_chat_slot_create)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/chat/slots", json=payload)
        return resp.status, await resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["", "kirocrew"])
async def test_setting_on_starts_a_default_agent_chat_on_the_conductor(
    dashboard_state: Any, monkeypatch: pytest.MonkeyPatch, agent: str
) -> None:
    payload = {"name": "fresh", **({"agent": agent} if agent else {})}
    status, data = await _create(
        dashboard_state, monkeypatch, _config(default_crew_mode=True), payload
    )
    assert status == 200, data
    assert dashboard_state._slots["fresh"].agent == CREW_MODE_AGENT_NAME


@pytest.mark.asyncio
async def test_setting_off_keeps_the_default_agent(
    dashboard_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, data = await _create(
        dashboard_state, monkeypatch, _config(default_crew_mode=False), {"name": "plain"}
    )
    assert status == 200, data
    assert dashboard_state._slots["plain"].agent != CREW_MODE_AGENT_NAME


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "crew_mode", "expect_conductor"),
    [(True, False, False), (False, True, True), (True, True, True), (False, False, False)],
)
async def test_explicit_switch_value_wins_over_the_setting(
    dashboard_state: Any,
    monkeypatch: pytest.MonkeyPatch,
    setting: bool,
    crew_mode: bool,
    expect_conductor: bool,
) -> None:
    status, data = await _create(
        dashboard_state,
        monkeypatch,
        _config(default_crew_mode=setting),
        {"name": "picked", "agent": "kirocrew", "crew_mode": crew_mode},
    )
    assert status == 200, data
    assert (dashboard_state._slots["picked"].agent == CREW_MODE_AGENT_NAME) is expect_conductor


@pytest.mark.asyncio
async def test_another_agent_is_never_switched(
    dashboard_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, data = await _create(
        dashboard_state,
        monkeypatch,
        _config(default_crew_mode=True),
        {"name": "other", "agent": "custom-x", "crew_mode": True},
    )
    assert status == 200, data
    assert dashboard_state._slots["other"].agent == "custom-x"


@pytest.mark.asyncio
async def test_non_boolean_crew_mode_is_refused(
    dashboard_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, data = await _create(
        dashboard_state,
        monkeypatch,
        _config(default_crew_mode=True),
        {"name": "bad", "crew_mode": "yes"},
    )
    assert status == 400
    assert data["code"] == "invalid_crew_mode"
    assert "bad" not in dashboard_state._slots


@pytest.mark.parametrize(
    "kwargs",
    [
        {"remote": True},
        {"recreate": True},
        {"mode": "design-critique"},
        {"app": "some-app"},
        {"agent_kind": "member"},
    ],
)
def test_only_a_new_local_plain_owner_chat_qualifies(kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "agent": "",
        "agent_kind": "",
        "mode": "",
        "crew_mode": True,
        "remote": False,
        "recreate": False,
        "app": "",
    }
    cfg = _config(default_crew_mode=True)
    assert chat_handlers._crew_mode_applies_at_birth(cfg, **base) is True
    assert chat_handlers._crew_mode_applies_at_birth(cfg, **{**base, **kwargs}) is False


def test_configured_default_agent_counts_as_the_default() -> None:
    cfg = _config(default_crew_mode=True, default_agent="sales-agent")
    common: dict[str, Any] = {
        "agent_kind": "",
        "mode": "",
        "crew_mode": None,
        "remote": False,
        "recreate": False,
        "app": "",
    }
    assert chat_handlers._crew_mode_applies_at_birth(cfg, agent="sales-agent", **common)
    assert not chat_handlers._crew_mode_applies_at_birth(cfg, agent="other", **common)


# ── dashboard.default_crew_mode: the Settings -> Chat option ──


@pytest.fixture()
def cfg_file(tmp_path: Any) -> Any:
    from unittest.mock import patch

    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    with patch("kiro_crew.config.loader.config_path", return_value=p):
        yield p


@pytest.fixture()
def config_app(cfg_file: Any) -> Any:
    from unittest.mock import MagicMock, patch

    from dashboard_owner_helpers import as_owner

    from kiro_crew.dashboard.handlers.files import api_dashboard_config

    app = web.Application()
    app.router.add_put("/api/dashboard/config", api_dashboard_config)
    app.router.add_get("/api/dashboard/config", api_dashboard_config)
    with patch("kiro_crew.dashboard.handlers.sel", return_value=MagicMock()):
        yield as_owner(app)


def test_default_is_off() -> None:
    assert KiroCrewConfig().dashboard.default_crew_mode is False


def test_a_hand_edited_non_bool_loads_as_off(cfg_file: Any) -> None:
    import json

    cfg_file.write_text(json.dumps({"dashboard": {"default_crew_mode": "yes"}}), encoding="utf-8")
    assert KiroCrewConfig.load().dashboard.default_crew_mode is False


@pytest.mark.asyncio
async def test_put_persists_and_get_echoes(config_app: Any) -> None:
    async with TestClient(TestServer(config_app)) as client:
        resp = await client.put("/api/dashboard/config", json={"default_crew_mode": True})
        assert resp.status == 200, await resp.text()
        got = await (await client.get("/api/dashboard/config")).json()
    assert KiroCrewConfig.load().dashboard.default_crew_mode is True
    assert got["default_crew_mode"] is True


@pytest.mark.asyncio
async def test_put_rejects_a_non_bool(config_app: Any) -> None:
    async with TestClient(TestServer(config_app)) as client:
        resp = await client.put("/api/dashboard/config", json={"default_crew_mode": "on"})
        body = await resp.json()
    assert resp.status == 400
    assert body["code"] == "invalid_default_crew_mode"
    assert KiroCrewConfig.load().dashboard.default_crew_mode is False


# ── the conductor's prompt: what a person sees when Crew Mode is on ──


def test_conductor_prompt_carries_the_intro_and_the_task_board() -> None:
    from kiro_crew import agent

    prompt = agent._CONDUCTOR_SYSTEM_PROMPT
    section = prompt.split("## Talking to the person", 1)[1].split("## If a conductor", 1)[0]
    assert "Crew Mode" in section
    assert "I'm your Conductor" in section
    assert '"Needs you" comes FIRST' in section
    assert "Task board" in section
    # A child conductor reports to its parent through work_report, not widgets.
    assert "when a conductor dispatched you" in section
