"""The ``setup_card`` / ``setup_status`` MCP tools."""

from __future__ import annotations

import pytest

from kiro_crew import mcp_core, session_directive
from kiro_crew import setup_cards as sc
from kiro_crew.mcp_tools import setup as setup_tools


@pytest.fixture
def dashboard_key(monkeypatch):
    monkeypatch.setattr(mcp_core, "_resolve_session_key_strict", lambda: "dashboard:chat-1-1")
    monkeypatch.setattr(setup_tools, "has_dashboard_surface", lambda sk: True)
    return "dashboard:chat-1-1"


def test_a_valid_proposal_returns_a_directive(dashboard_key):
    out = setup_tools.setup_card(
        "setup_card", {"kind": "cron", "name": "Brief", "prompt": "Summarize", "every_secs": 86400}
    )
    decoded = session_directive.decode(out, "setup_card")
    assert decoded == {"kind": "cron", "name": "Brief", "prompt": "Summarize", "every_secs": 86400}


def test_an_invalid_proposal_is_an_error_not_a_directive(dashboard_key):
    out = setup_tools.setup_card("setup_card", {"kind": "cron", "name": "x"})
    assert out.startswith("Error:")
    assert not session_directive.has_marker(out)


def test_the_privacy_card_is_not_proposable(dashboard_key):
    out = setup_tools.setup_card("setup_card", {"kind": "privacy"})
    assert out.startswith("Error:")


def test_a_session_without_a_dashboard_gets_a_pointer_not_a_card(monkeypatch):
    monkeypatch.setattr(mcp_core, "_resolve_session_key_strict", lambda: "telegram:42")
    monkeypatch.setattr(setup_tools, "has_dashboard_surface", lambda sk: False)
    out = setup_tools.setup_card("setup_card", {"kind": "service"})
    assert out.startswith("Error:") and "dashboard" in out


def test_setup_status_reports_this_sessions_cards(dashboard_key):
    card = sc.create_card(
        slot="chat-1-1",
        session_key=dashboard_key,
        kind=sc.KIND_SERVICE,
        payload={"installed": False},
    )
    sc.create_card(
        slot="chat-2-2",
        session_key="dashboard:chat-2-2",
        kind=sc.KIND_PROFILE,
        payload={"fields": {}},
    )
    out = setup_tools.setup_status("setup_status", {})
    assert card.id in out and "waiting for the user's decision" in out
    assert "chat-2-2" not in out and "profile" not in out


def test_setup_status_says_a_building_home_needs_nothing_from_the_user(dashboard_key):
    """The raw "waiting" read as "the user owes it an answer" while the home built."""
    from kiro_crew import first_run

    first_run.record_slot("chat-1-1")
    card = sc.create_card(
        slot="chat-1-1",
        session_key=dashboard_key,
        kind=sc.KIND_HOME,
        payload={"region": "us-east-1", "size": {"key": "lite"}},
    )

    def _building(c: sc.SetupCard) -> None:
        c.status = sc.STATUS_WAITING
        c.outcome = {
            "steps": [{"key": "create", "label": "Create the instance", "state": "active"}]
        }

    sc.update_card(card.id, _building)
    out = setup_tools.setup_status("setup_status", {})
    building = "building in the background: Create the instance; nothing needed from the user"
    assert f"- home ({card.id}): {building}" in out
    assert f"The home step is not finished yet: {building}." in out
    assert "not settled" not in out and ": waiting" not in out


def test_the_tools_are_registered():
    names = {d["name"] for d in setup_tools.schemas()}
    assert names == set(setup_tools.HANDLERS) == {"setup_card", "setup_status"}
    assert "setup_card" in session_directive.DIRECTIVE_TOOLS


def test_status_keeps_home_as_the_next_step_when_no_job_was_kept(dashboard_key):
    from kiro_crew import first_run

    first_run.record_slot("chat-1-1")
    out = setup_tools.setup_status("setup_status", {})
    assert 'setup_card(kind="home", step="choose")' in out
    assert "even if the user skipped scheduling" in out
    first_run.record_home_choice("later")
    assert "Next setup step" not in setup_tools.setup_status("setup_status", {})


def test_home_rejects_an_unknown_proposal_step(dashboard_key):
    out = setup_tools.setup_card("setup_card", {"kind": "home", "step": "finish"})
    assert out.startswith("Error:")
    assert not session_directive.has_marker(out)


def test_a_pending_card_says_whether_another_card_can_still_show(dashboard_key):
    """Read off ``stack_exempt``: the waiting home card holds nothing back, a job does.

    "Waiting for the user's decision" alone read as "no other card can be shown",
    and the agent refused a job the user asked for while the home card waited.
    """
    home = sc.create_card(
        slot="chat-1-1",
        session_key=dashboard_key,
        kind=sc.KIND_HOME,
        payload={"region": "us-east-1", "size": {"key": "lite"}},
    )
    job = sc.create_card(
        slot="chat-1-1",
        session_key=dashboard_key,
        kind=sc.KIND_CRON,
        payload={"name": "Brief"},
    )
    out = setup_tools.setup_status("setup_status", {})
    assert (
        f"- home ({home.id}): waiting for the user's decision on its card; "
        "other cards can still be shown while it waits" in out
    )
    assert (
        f"- cron ({job.id}): waiting for the user's decision; "
        "a new card waits until it is decided" in out
    )


def test_the_wording_follows_the_stack_exempt_flag_not_the_kind(dashboard_key, monkeypatch):
    import dataclasses

    from kiro_crew import setup_actions

    card = sc.create_card(
        slot="chat-1-1", session_key=dashboard_key, kind=sc.KIND_CRON, payload={"name": "B"}
    )
    exempt = dataclasses.replace(setup_actions.get(sc.KIND_CRON), stack_exempt=True)
    monkeypatch.setitem(setup_actions._BY_KIND, sc.KIND_CRON, exempt)
    assert setup_actions.holds_others(card) is False
    assert "other cards can still be shown while it waits" in setup_tools.setup_status(
        "setup_status", {}
    )
