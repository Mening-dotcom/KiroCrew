"""A Slack chat turn runs a Crew-bound channel as the record's kiro agent.

``resolve_crew_agent_binding`` is the chat turns' read of the same rule the
dashboard, spawn and cron dispatches apply: a name that is a ``config.agents``
record collapses to the record's kiro agent and workspace directory, and any
other name passes through unchanged so a plain kiro agent name keeps its
meaning. Both chat call sites (``transport_dispatch`` and the native turn)
consume this helper; these tests pin the collapse itself.
"""

from __future__ import annotations

import json

import pytest

from kiro_crew.config import loader


@pytest.fixture
def crew_workspace(tmp_path, monkeypatch):
    """A config whose ``web-frontend`` agent record maps to a real workspace dir."""
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "web-frontend"
    ws.mkdir()
    (home / "config.json").write_text(
        json.dumps(
            {
                "agents": {
                    "web-frontend": {
                        "kiro_agent": "kirocrew",
                        "workspace": "web-frontend",
                    }
                },
                "workspaces": {"web-frontend": {"dir": str(ws)}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KIROCREW_HOME", str(home))
    loader._invalidate_config_cache()
    yield ws
    loader._invalidate_config_cache()


class TestResolveCrewAgentBinding:
    def test_a_crew_record_collapses_to_its_kiro_agent_and_workspace(self, crew_workspace):
        from kiro_crew.slack.handler import resolve_crew_agent_binding

        kiro_agent, cwd, alias = resolve_crew_agent_binding("web-frontend")
        assert kiro_agent == "kirocrew"
        assert cwd == str(crew_workspace)
        assert alias == "web-frontend"

    def test_a_plain_kiro_agent_name_passes_through(self, crew_workspace):
        """A name that is not a ``config.agents`` record must not be rewritten:
        ``resolve_agent_bindings`` falls back to the default agent for an
        unknown name, so an unconditional resolve would collapse a legitimate
        kiro mode onto the default."""
        from kiro_crew.slack.handler import resolve_crew_agent_binding

        assert resolve_crew_agent_binding("kirocrew-lite") == (None, None, None)

    def test_an_absent_name_passes_through(self, crew_workspace):
        from kiro_crew.slack.handler import resolve_crew_agent_binding

        assert resolve_crew_agent_binding(None) == (None, None, None)
        assert resolve_crew_agent_binding("") == (None, None, None)

    def test_an_unreadable_config_does_not_stop_a_turn(self, crew_workspace, monkeypatch):
        """Resolution failure answers the pass-through trio, never raises."""
        from kiro_crew.slack.handler import KiroCrewConfig, resolve_crew_agent_binding

        def _boom():
            raise OSError("config unreadable")

        monkeypatch.setattr(KiroCrewConfig, "load", staticmethod(_boom))
        assert resolve_crew_agent_binding("web-frontend") == (None, None, None)
