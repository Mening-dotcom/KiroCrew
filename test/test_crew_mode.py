"""Crew Mode: the Settings default and the conductor prompt.

Crew Mode is a UI over the existing ``kirocrew-conductor``: a chat with the
switch on simply runs on that agent, so there is no Crew Mode field on chat
creation. The dashboard's own new-chat gestures create the chat with that
agent when ``dashboard.default_crew_mode`` is on; this file pins the setting
and the prompt section a person sees.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.config.loader import KiroCrewConfig

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
    # Widgets are dashboard-only; channels and scheduled runs get plain text.
    assert "Widgets are for the dashboard chat only" in section
