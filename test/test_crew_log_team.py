"""The crewmate team stamp on ``session/opened``."""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest
from chat_test_helpers import _make_state

from kiro_crew import crew_log as lg
from kiro_crew import crew_teams
from kiro_crew import members as members_mod
from kiro_crew.crew_log import CrewLog, emit
from kiro_crew.dashboard import chat_runner as cr
from kiro_crew.dashboard import create_rate_limit
from kiro_crew.dashboard import session_control as sc
from kiro_crew.dashboard.chat_utils import slot_history_key

TEAM = "0123456789ab"
OTHER_TEAM = "ba9876543210"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(emit.CREW_LOG_ENV, "1")
    monkeypatch.setattr(sc, "session_control_enabled", lambda: True)
    create_rate_limit.reset_for_tests()
    emit.reset_caches()
    yield
    emit.drain_for_shutdown(timeout=2.0)
    emit.reset_caches()
    create_rate_limit.reset_for_tests()


def _teams(monkeypatch, teams: list[crew_teams.Team]) -> None:
    monkeypatch.setattr(crew_teams, "read_teams", lambda: list(teams))


def _agent_resolves(monkeypatch) -> None:
    real = sc.resolve_agent_bindings
    monkeypatch.setattr(sc, "_workspace_name_for_dir", lambda cfg, ws_dir: "default")
    monkeypatch.setattr(
        sc,
        "resolve_agent_bindings",
        lambda cfg, agent_name=None, project_dir=None, **kwargs: dataclasses.replace(
            real(cfg, None, project_dir), requested_resolved=True
        ),
    )


def _member_thread(state, crewmate: str, *, bound: bool = True):
    """A crewmate's pinned thread, with the DM binding the member endpoint writes."""
    slug = members_mod.slug_for_name(crewmate)
    key = members_mod.member_slot_key(slug)
    if bound:
        members_mod.write_dm_binding(slug, member=crewmate, slot_key=key)
    return state.get_or_create_slot(key, agent=crewmate, mode=members_mod.DM_SLOT_MODE)


def _create(state, caller):
    created = asyncio.run(sc.create_session(state, caller_session_key=slot_history_key(caller)))
    child = state.get_slot(created["target"])
    assert child is not None
    return child


# --------------------------------------------------------------------------- #
# Stamping at mint
# --------------------------------------------------------------------------- #


def test_a_child_of_a_team_members_thread_is_stamped_with_that_team(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    _agent_resolves(monkeypatch)
    _teams(monkeypatch, [crew_teams.Team(id=TEAM, name="rsi", members=["researcher"])])
    lead = _member_thread(state, "researcher")

    child = _create(state, lead)
    assert child._crew_log_team == TEAM
    assert cr._crew_log_team(child) == TEAM

    # The whole tree under the member carries the stamp, not only its first level.
    grandchild = _create(state, child)
    assert grandchild._crew_log_team == TEAM


def test_a_root_that_is_on_no_team_stamps_nothing(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    _agent_resolves(monkeypatch)
    _teams(monkeypatch, [crew_teams.Team(id=TEAM, name="rsi", members=["someone-else"])])

    loner = _create(state, _member_thread(state, "researcher"))
    assert loner._crew_log_team == ""

    # A person's own tab is not a member thread, whatever its agent is called.
    tab = state.get_or_create_slot("chat-1", agent="researcher")
    assert _create(state, tab)._crew_log_team == ""


def test_a_member_change_does_not_move_a_stamp_already_made(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    _agent_resolves(monkeypatch)
    _teams(monkeypatch, [crew_teams.Team(id=TEAM, name="rsi", members=["researcher"])])
    lead = _member_thread(state, "researcher")
    child = _create(state, lead)

    # The member leaves the team and joins another one.
    _teams(monkeypatch, [crew_teams.Team(id=OTHER_TEAM, name="ops", members=["researcher"])])

    assert child._crew_log_team == TEAM
    # A session the stamped child creates keeps the child's stamp: the chain's root
    # was decided when the child was minted, and the document is not read again.
    assert _create(state, child)._crew_log_team == TEAM
    # A NEW child of the root is minted under the document as it is now.
    assert _create(state, lead)._crew_log_team == OTHER_TEAM


def test_an_unreadable_team_document_stamps_nothing_and_still_creates(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    _agent_resolves(monkeypatch)

    def _unreadable():
        raise crew_teams.TeamsUnreadable("damaged")

    monkeypatch.setattr(crew_teams, "read_teams", _unreadable)
    child = _create(state, _member_thread(state, "researcher"))
    assert child._crew_log_team == ""


def test_the_stamp_is_used_only_on_a_slot_this_process_minted():
    # A restore never sets the witness, so a stamp that arrived any other way is
    # not recorded -- the same fence ``_crew_log_lineage`` keeps for ``parent``.
    assert cr._crew_log_team(SimpleNamespace(_crew_log_team=TEAM, _lineage_minted=True)) == TEAM
    assert cr._crew_log_team(SimpleNamespace(_crew_log_team=TEAM, _lineage_minted=False)) == ""
    assert cr._crew_log_team(SimpleNamespace(_crew_log_team=TEAM)) == ""


def test_a_minted_caller_passes_on_only_a_well_formed_stamp():
    minted = SimpleNamespace(_lineage_minted=True, _crew_log_team="not-a-team-id", mode="")
    assert sc._crew_log_team_stamp(minted, TEAM) == ""
    # A minted caller is never stamped from a root lookup, even when it is a member's
    # thread by mode: its team is the one its own root gave it.
    minted_member = SimpleNamespace(
        _lineage_minted=True, _crew_log_team="", mode=members_mod.DM_SLOT_MODE, agent="researcher"
    )
    assert sc._crew_log_team_stamp(minted_member, TEAM) == ""


def test_a_slot_that_only_claims_member_mode_is_not_a_member_root(tmp_path, monkeypatch):
    # ``mode`` and ``agent`` come back from the agent-writable transcript on a
    # restore, so a worker whose transcript says ``mode="member"`` must not be read
    # as the crewmate's own thread.
    state = _make_state(tmp_path)
    _agent_resolves(monkeypatch)
    _teams(monkeypatch, [crew_teams.Team(id=TEAM, name="rsi", members=["researcher"])])

    forged = state.get_or_create_slot("chat-9", agent="researcher", mode=members_mod.DM_SLOT_MODE)
    assert _create(state, forged)._crew_log_team == ""

    # A member key with no DM binding behind it is not corroborated either.
    unbound = _member_thread(state, "researcher", bound=False)
    assert _create(state, unbound)._crew_log_team == ""

    # A binding naming another crewmate does not vouch for this one.
    other = members_mod.member_slot_key("analyst")
    members_mod.write_dm_binding("analyst", member="someone-else", slot_key=other)
    claimed = state.get_or_create_slot(other, agent="analyst", mode=members_mod.DM_SLOT_MODE)
    assert _create(state, claimed)._crew_log_team == ""


def _seeded(state, name: str, **kwargs):
    """A live slot with a settled, persisted transcript, so the fork core can copy it."""
    from kiro_crew.dashboard.chat_persistence import _save_slot_to_history

    slot = state.get_or_create_slot(name, **kwargs)
    slot.append("user", "question", "msg msg-u")
    slot.append("assistant", "answer", "msg msg-a")
    slot.drain()
    _save_slot_to_history(state, slot, closed=False)
    slot._resumed_count = len(slot.messages)
    slot._disk_window_len = len(slot.messages)
    slot._dirty = False
    return slot


def _fork(state, caller):
    from unittest.mock import MagicMock

    from kiro_crew.dashboard import chat_fork

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(chat_fork, "sel", lambda: MagicMock())
        patch.setattr(sc, "sel", lambda: MagicMock())
        result = asyncio.run(sc.fork_session(state, caller_session_key=slot_history_key(caller)))
    child = state.get_slot(result["target"])
    assert child is not None
    return child


def test_a_fork_made_by_a_stamped_session_carries_its_stamp(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    _teams(monkeypatch, [])
    worker = _seeded(state, "chat-5")
    worker._lineage_minted = True
    worker._crew_log_team = TEAM

    assert _fork(state, worker)._crew_log_team == TEAM


def test_a_fork_made_by_an_unstamped_session_carries_none(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    _teams(monkeypatch, [crew_teams.Team(id=TEAM, name="rsi", members=["kirocrew"])])
    # A person's own tab: not a member thread, so the document is not consulted.
    tab = _seeded(state, "chat-6")

    assert _fork(state, tab)._crew_log_team == ""


# --------------------------------------------------------------------------- #
# The entry
# --------------------------------------------------------------------------- #


def _opened_data(session_id: str) -> dict:
    handle = CrewLog.open(lg.KIND_SESSION, session_id)
    opened = [e for e in handle.iter_from(1) if e.type == "session/opened"]
    assert len(opened) == 1
    return dict(opened[0].data)


def test_the_opened_entry_records_the_team_only_when_one_is_stamped():
    emit.on_session_opened("acp-team-1", agent="worker", slot="chat-2", team=TEAM)
    emit.on_session_opened("acp-team-2", agent="worker", slot="chat-3")
    emit.on_session_opened("acp-team-3", agent="worker", slot="chat-4", team="../etc")
    emit.drain_for_shutdown(timeout=2.0)
    assert _opened_data("acp-team-1")["team"] == TEAM
    assert "team" not in _opened_data("acp-team-2")
    assert "team" not in _opened_data("acp-team-3")
