"""The setup-action registry: what it declares, and what it can never loosen.

``setup_flow`` and the ``setup_card`` tool read each kind from
``kiro_crew.setup_actions`` instead of branching on it. So the registry is where a
kind could quietly escape a check. This pins the two ways that would happen:

* the agent reaching a gateway-only kind (the privacy disclosure) through the
  tool's schema, the tool, or ``propose``;
* a registered kind whose commit, or a claimed extra decision, runs without the
  payload hash the owner was shown (SC1), or past a governance denial.

It also pins the tool's schema, which the registry now generates, against the
literal it replaced. The cross-layer parity is ``test_setup_action_parity.py``.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from kiro_crew import mcp_core, session_directive, setup_actions
from kiro_crew import setup_cards as sc
from kiro_crew.dashboard import setup_flow
from kiro_crew.mcp_tools import setup as setup_tools

_GATEWAY_ONLY = sorted(a.kind for a in setup_actions.ACTIONS if not a.proposable)
_GOVERNED = sorted(a.kind for a in setup_actions.ACTIONS if a.governed)
#: Every ``(kind, decision)`` the flow claims against the hash before it runs.
_CLAIMED = sorted(
    [(a.kind, sc.DECISION_COMMIT) for a in setup_actions.ACTIONS]
    + [
        (a.kind, name)
        for a in setup_actions.ACTIONS
        for name, d in a.decisions.items()
        if d.claimed
    ]
)
_UNCLAIMED = sorted(
    (a.kind, name)
    for a in setup_actions.ACTIONS
    for name, d in a.decisions.items()
    if not d.claimed
)


class _Slot:
    def __init__(self, key: str = "chat-1-1") -> None:
        self.key = key
        self.messages: list[Any] = []

    def append(self, *args: Any, **kwargs: Any) -> None:
        self.messages.append((args, kwargs))


class _State:
    def __init__(self) -> None:
        self.slots = {"chat-1-1": _Slot()}
        self.events: list[Any] = []
        self.crons = None
        self.conversation_log = SimpleNamespace(list_sessions=lambda: [])

    def get_slot(self, key: str) -> _Slot | None:
        return self.slots.get(key)

    def broadcast_ws_owners(self, msg_type: str, data: Any) -> None:
        self.events.append((msg_type, data))

    def push_slots_update(self, **_: Any) -> None:
        pass


@pytest.fixture
def state() -> _State:
    return _State()


@pytest.fixture(autouse=True)
def _no_result_turns(monkeypatch):
    async def _no_report(state, card):
        return None

    monkeypatch.setattr(setup_flow, "_report", _no_report)


@pytest.fixture
def spies(monkeypatch) -> list[tuple[str, str]]:
    """Swap every committer and claimed decision for a spy that records its call."""
    calls: list[tuple[str, str]] = []

    def _spy(kind: str, decision: str):
        async def _run(state, card, input_):
            calls.append((kind, decision))
            return await setup_flow._finish(card, sc.STATUS_COMMITTED, outcome={})

        return _run

    for action in setup_actions.ACTIONS:
        decisions = {
            name: dataclasses.replace(d, run=_spy(action.kind, name)) if d.claimed else d
            for name, d in action.decisions.items()
        }
        swapped = dataclasses.replace(
            action,
            commit=_spy(action.kind, sc.DECISION_COMMIT),
            decisions=decisions,
            on_claim=None,
        )
        monkeypatch.setitem(setup_actions._BY_KIND, action.kind, swapped)
    return calls


def _pending(kind: str, payload: dict[str, Any] | None = None) -> sc.SetupCard:
    return sc.create_card(
        slot="chat-1-1", session_key="dashboard:chat-1-1", kind=kind, payload=payload or {}
    )


class TestTheRegistryDeclares:
    def test_the_scripted_steps_are_the_gateway_only_kinds(self):
        # Privacy and the first run's steps before any model turn: nothing could
        # have proposed them, because no model can run until they are done.
        assert sorted(_GATEWAY_ONLY) == sorted(sc.SCRIPTED_KINDS)

    def test_only_a_gateway_only_kind_escapes_governance_or_the_result_turn(self):
        ungoverned = [a.kind for a in setup_actions.ACTIONS if not a.governed]
        unreported = [a.kind for a in setup_actions.ACTIONS if not a.reported]
        assert sorted(ungoverned) == sorted(_GATEWAY_ONLY)
        assert sorted(unreported) == sorted(_GATEWAY_ONLY)

    def test_every_proposable_kind_builds_and_summarizes_itself(self):
        for action in setup_actions.proposable():
            assert action.build is not None, action.kind
            assert action.summary, action.kind

    def test_every_extra_decision_is_on_the_wire_and_not_commit_or_decline(self):
        offered = {name for a in setup_actions.ACTIONS for name in a.decisions}
        assert not offered & {sc.DECISION_COMMIT, sc.DECISION_DECLINE}
        assert (
            set(setup_actions.decision_names()) == sc.DECISIONS
        ), "setup_cards.DECISIONS must name commit, decline and every kind's own decision"

    def test_a_further_scope_comes_with_its_check(self):
        for action in setup_actions.ACTIONS:
            further = [s for s in action.scopes if s != setup_actions.SETUP_SCOPE]
            assert bool(further) == (action.vet is not None), action.kind

    def test_argument_properties_shared_by_kinds_agree_on_their_schema(self):
        seen: dict[str, tuple[str, dict[str, Any]]] = {}
        for action in setup_actions.proposable():
            for prop, fragment in action.arguments.items():
                shape = {k: v for k, v in fragment.items() if k != "description"}
                if prop in seen:
                    assert shape == seen[prop][1], (
                        f"{action.kind!r} and {seen[prop][0]!r} both read {prop!r} "
                        "with different schemas"
                    )
                else:
                    seen[prop] = (action.kind, shape)


class TestTheToolSchema:
    """Generated from the registry, and the same schema the tool declared by hand."""

    def test_the_input_schema_includes_the_home_choice_step(self):
        schema = setup_tools.schemas()[0]["inputSchema"]
        assert schema == {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": sorted(sc.PROPOSABLE_KINDS)},
                "fields": {
                    "type": "object",
                    "description": (
                        "profile: any of bot_name, language (BCP-47), timezone (IANA), "
                        "technical_level (" + "|".join(sorted(sc.TECHNICAL_LEVELS)) + "), "
                        "role (" + "|".join(sorted(sc.USER_ROLES)) + ")"
                    ),
                },
                "file": {"type": "string", "enum": list(sc.SOUL_FILES)},
                "content": {"type": "string", "description": "soul: the full file"},
                "source_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "import: limit to these detected sources, by source id: the "
                        'first-run facts name each one\'s id, e.g. "claude_code" for '
                        "Claude Code"
                    ),
                },
                "provider": {"type": "string", "description": "connect: registry slug"},
                "name": {
                    "type": "string",
                    "description": "credential: UPPER_SNAKE vault name; cron: job name",
                },
                "purpose": {"type": "string", "description": "credential: why it is needed"},
                "hosts": {"type": "array", "items": {"type": "string"}},
                "channel": {"type": "string", "enum": sorted(sc.CHANNELS)},
                "prompt": {"type": "string", "description": "cron: what each run does"},
                "cron_expr": {"type": "string", "description": "cron: 5-field expression"},
                "every_secs": {"type": "integer", "description": "cron: interval, >= 3600"},
                "timezone": {"type": "string", "description": "cron: IANA timezone"},
                "step": {
                    "type": "string",
                    "enum": ["choose"],
                    "description": "home: choose: ask this machine or AWS, including after scheduling is skipped",
                },
                "region": {"type": "string", "description": "home: AWS region"},
                "profile": {"type": "string", "description": "home: AWS CLI profile"},
                "size": {"type": "string", "description": "home: size key, default light"},
            },
            "required": ["kind"],
        }
        # The same order too: the schema is serialized onto every request.
        assert list(schema["properties"]) == [
            "kind", "fields", "file", "content", "source_ids", "provider", "name", "purpose",
            "hosts", "channel", "prompt", "cron_expr", "every_secs", "timezone", "step", "region",
            "profile", "size",
        ]  # fmt: skip

    def test_the_description_names_every_proposable_kind_once(self):
        description = setup_tools.schemas()[0]["description"]
        assert description.startswith("Show the user a setup card they approve with one click.")
        assert description.endswith("Never ask the user to paste secrets into chat.")
        for action in setup_actions.proposable():
            assert description.count(f" {action.kind} (") == 1, action.kind

    def test_the_schema_is_a_copy_the_caller_may_not_mutate_into_the_registry(self):
        setup_tools.schemas()[0]["inputSchema"]["properties"]["hosts"]["items"]["type"] = "x"
        assert setup_tools.schemas()[0]["inputSchema"]["properties"]["hosts"]["items"] == {
            "type": "string"
        }


class TestTheAgentCannotReachAGatewayOnlyKind:
    @pytest.mark.parametrize("kind", _GATEWAY_ONLY)
    def test_it_is_not_in_the_tool_schema(self, kind: str):
        tool = setup_tools.schemas()[0]
        assert kind not in tool["inputSchema"]["properties"]["kind"]["enum"]
        assert f" {kind} (" not in tool["description"]

    @pytest.mark.parametrize("kind", _GATEWAY_ONLY)
    def test_the_tool_refuses_it_without_a_directive(self, kind: str, monkeypatch):
        monkeypatch.setattr(mcp_core, "_resolve_session_key_strict", lambda: "dashboard:chat-1-1")
        monkeypatch.setattr(setup_tools, "has_dashboard_surface", lambda sk: True)
        out = setup_tools.setup_card("setup_card", {"kind": kind})
        assert out.startswith("Error: kind must be one of ")
        assert not session_directive.has_marker(out)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", _GATEWAY_ONLY)
    async def test_propose_answers_it_as_an_unknown_kind_and_shows_nothing(
        self, kind: str, state, monkeypatch
    ):
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
        out = await setup_flow.propose(
            state,
            state.slots["chat-1-1"],
            "dashboard:chat-1-1",
            {"kind": kind},
            producer_is_user_facing=True,
        )
        assert out == f"Error: unknown setup card kind {kind!r}. Nothing was shown."
        assert sc.list_cards("chat-1-1") == []
        assert state.slots["chat-1-1"].messages == [] and state.events == []

    @pytest.mark.asyncio
    async def test_an_unregistered_kind_gets_the_same_answer(self, state):
        out = await setup_flow.propose(
            state,
            state.slots["chat-1-1"],
            "dashboard:chat-1-1",
            {"kind": "teleport"},
            producer_is_user_facing=True,
        )
        assert out == "Error: unknown setup card kind 'teleport'. Nothing was shown."
        assert sc.list_cards("chat-1-1") == []


class TestNoKindWeakensTheClick:
    """SC1 and the commit-time governance check hold for every registered decision."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("kind", "decision"), _CLAIMED)
    async def test_a_wrong_hash_runs_nothing(self, kind, decision, state, spies, monkeypatch):
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
        card = _pending(kind)
        with pytest.raises(sc.CardRejected) as err:
            await setup_flow.decide(state, card.id, decision, "0" * 64, {})
        assert err.value.code == "card_hash_mismatch"
        assert spies == []
        assert sc.get_card(card.id).status == sc.STATUS_PENDING

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("kind", "decision"), _CLAIMED)
    async def test_the_shown_hash_runs_exactly_its_own_committer(
        self, kind, decision, state, spies, monkeypatch
    ):
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
        card = _pending(kind)
        out = await setup_flow.decide(state, card.id, decision, card.payload_hash, {})
        assert spies == [(kind, decision)]
        assert out.status == sc.STATUS_COMMITTED

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("kind", "decision"), [(k, d) for k, d in _CLAIMED if k in _GOVERNED])
    async def test_a_governance_denial_runs_nothing(
        self, kind, decision, state, spies, monkeypatch
    ):
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: "denied here")
        card = _pending(kind)
        with pytest.raises(sc.CardRejected) as err:
            await setup_flow.decide(state, card.id, decision, card.payload_hash, {})
        assert err.value.code == "governance_denied"
        assert spies == []
        assert sc.get_card(card.id).status == sc.STATUS_PENDING

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("kind", "decision"), _UNCLAIMED)
    async def test_an_unclaimed_decision_is_refused_by_governance_and_a_wrong_hash(
        self, kind, decision, state, monkeypatch
    ):
        """A decision that guards itself still refuses both, and changes nothing."""
        # ``region_unknown``: the region decision is offered only while AWS named none.
        card = _pending(kind, {"region_unknown": True, "profile": "default"})
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: "denied here")
        with pytest.raises(sc.CardRejected) as denied:
            await setup_flow.decide(state, card.id, decision, card.payload_hash, {})
        assert denied.value.code == "governance_denied"
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
        with pytest.raises(sc.CardRejected) as stale:
            await setup_flow.decide(state, card.id, decision, "0" * 64, {})
        assert stale.value.code == "card_hash_mismatch"
        after = sc.get_card(card.id)
        assert (after.status, after.payload_hash) == (sc.STATUS_PENDING, card.payload_hash)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "decision", sorted(sc.DECISIONS - {sc.DECISION_COMMIT, sc.DECISION_DECLINE})
    )
    async def test_a_decision_the_kind_lacks_is_invalid(self, decision, state, spies):
        lacking = [a for a in setup_actions.ACTIONS if decision not in a.decisions]
        assert lacking
        for action in lacking:
            card = _pending(action.kind)
            with pytest.raises(sc.CardRejected) as err:
                await setup_flow.decide(state, card.id, decision, card.payload_hash, {})
            assert err.value.code == "invalid_decision"
            assert str(err.value) == setup_actions.decision_refusal(decision)
            assert sc.get_card(card.id).status == sc.STATUS_PENDING
        assert spies == []

    @pytest.mark.asyncio
    async def test_an_unknown_decision_is_invalid(self, state):
        card = _pending(sc.KIND_PROFILE, {"fields": {"bot_name": "Nova"}})
        with pytest.raises(sc.CardRejected) as err:
            await setup_flow.decide(state, card.id, "teleport", card.payload_hash, {})
        assert err.value.code == "invalid_decision"
        assert (
            str(err.value)
            == "decision must be commit, decline, change_engine, preview, aws_signin, region, "
            "choose or remove"
        )
