"""The dashboard half of setup cards: propose, decide, and the first-run session.

Pins the invariants the RFC names:

* SC1 — nothing commits without an owner decision carrying the shown hash.
* SC2 — a credential typed into a card never appears in the card, the store,
  the transcript or a log record.
* SC3 — no committer writes a governance keystone file.
* SC6 — the first-run state file admits nothing.
* SC8 — a turn no person started cannot put a card in front of the user.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kiro_crew import first_run
from kiro_crew import setup_cards as sc
from kiro_crew.config.paths import data_home
from kiro_crew.dashboard import setup_flow

SENTINEL_SECRET = "sk-sentinel-5f1c9e2b7a4d4c1e9b3a"


class FakeSlot:
    def __init__(self, key: str = "chat-1-1") -> None:
        self.key = key
        self.messages: list[tuple[str, str, dict | None]] = []
        self.running = False
        self._in_stage_execution = False
        self.task = None
        self.pinned = False
        self.title = key
        self.queued: list[tuple[str, str, bool]] = []

    def append(self, role, content, cls="", ts="", *, broadcast=True, meta=None):
        self.messages.append((role, content, meta))

    def queue_append(self, content, kind="", meta=None, *, directive_user_origin=False, **_):
        self.queued.append((content, kind, directive_user_origin))
        return "q1"


class FakeState:
    def __init__(self) -> None:
        self.slots: dict[str, FakeSlot] = {}
        self.events: list[tuple[str, Any]] = []
        self.crons = None
        self.conversation_log = SimpleNamespace(list_sessions=lambda: [])

    def get_slot(self, key):
        return self.slots.get(key)

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))

    def push_slots_update(self, **_):
        pass

    def live_slot_count(self):
        return len(self.slots)

    @contextlib.contextmanager
    def suspend_slots_push(self):
        yield lambda *_: None

    def get_or_create_slot(self, name=None, agent="", **_):
        slot = FakeSlot(f"chat-{len(self.slots) + 1}-1")
        slot.agent = agent
        self.slots[slot.key] = slot
        return slot


@pytest.fixture
def state():
    st = FakeState()
    st.slots["chat-1-1"] = FakeSlot("chat-1-1")
    return st


@pytest.fixture
def dispatched(monkeypatch):
    """Record envelope turns instead of starting real model turns."""
    calls: list[tuple[str, str, str]] = []

    async def _fake(state, slot, text, inject_kind):
        calls.append((slot.key, inject_kind, text))

    monkeypatch.setattr(setup_flow, "_dispatch_envelope_turn", _fake)
    return calls


@pytest.fixture(autouse=True)
def _permit_governance(monkeypatch):
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)


@pytest.fixture(autouse=True)
def _no_aws_sign_in(monkeypatch):
    """No test here reaches the host's AWS CLI or kiro-cli; one that needs a sign-in sets it."""
    from kiro_crew.cloud import local_signin

    monkeypatch.setattr(local_signin, "detect", lambda profile="", **kw: None)
    monkeypatch.setattr(local_signin, "configured_region", lambda profile="": "")
    monkeypatch.setattr(local_signin, "kiro_signs_in_with_builder_id", lambda: False)
    monkeypatch.setattr(local_signin, "resolve_home_region", lambda profile, preferred="": "")
    monkeypatch.setattr(
        local_signin, "account_plan", lambda profile, region="": {"type": "unknown"}
    )
    monkeypatch.setattr(local_signin, "vcpu_quota", lambda profile, region: None)
    # The home step after privacy probes AWS for its card; never the real one.
    from kiro_crew.cloud import iam

    monkeypatch.setattr(iam, "reachability_check", lambda profile, region: {"reachable": False})


async def _propose(state, args, *, user_facing=True, slot="chat-1-1"):
    return await setup_flow.propose(
        state,
        state.slots[slot],
        f"dashboard:{slot}",
        args,
        producer_is_user_facing=user_facing,
    )


def _only_card(slot="chat-1-1") -> sc.SetupCard:
    cards = sc.list_cards(slot)
    assert len(cards) == 1, cards
    return cards[0]


@pytest.fixture(autouse=True)
def _harness_answers(monkeypatch):
    """No test here reaches a real harness: the sign-in check says ready unless a test says not."""
    from kiro_crew.dashboard import harness_readiness

    async def _ready(state, backend):
        return harness_readiness.Verdict(backend, harness_readiness.READY)

    monkeypatch.setattr(harness_readiness, "check", _ready)


def _live(slot_key: str, kind: str) -> sc.SetupCard:
    live = [c for c in sc.list_cards(slot_key) if c.kind == kind and not c.terminal]
    assert len(live) == 1, (kind, [(c.kind, c.status) for c in sc.list_cards(slot_key)])
    return live[0]


async def _decide_live(st, slot_key: str, kind: str, input_=None, decision="commit"):
    card = _live(slot_key, kind)
    return await setup_flow.decide(st, card.id, decision, card.payload_hash, input_ or {})


async def _to_privacy(st, slot_key: str, backend: str = "") -> sc.SetupCard:
    """Walk the scripted steps up to the privacy card, and return it."""
    await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": backend})
    await _decide_live(st, slot_key, sc.KIND_HARNESS_SIGNIN)
    return _live(slot_key, sc.KIND_PRIVACY)


async def _through_script(st, slot_key: str, *, path: str = "detailed") -> None:
    """Every scripted step, ending on the start path that sends the first turn."""
    privacy = await _to_privacy(st, slot_key)
    await setup_flow.decide(st, privacy.id, "commit", privacy.payload_hash, {"telemetry": False})
    await _decide_live(st, slot_key, sc.KIND_PATH, {"path": path})


def _kinds(slot_key: str) -> list[str]:
    return [c.kind for c in sorted(sc.list_cards(slot_key), key=lambda c: c.created_ts)]


class TestPropose:
    @pytest.mark.asyncio
    async def test_a_profile_proposal_becomes_a_card_row_and_event(self, state):
        out = await _propose(state, {"kind": "profile", "fields": {"bot_name": "Nova"}})
        assert out.startswith("Setup card shown")
        card = _only_card()
        role, _content, meta = state.slots["chat-1-1"].messages[-1]
        assert role == "inject"
        assert meta == {"setupCard": {"id": card.id, "kind": "profile"}}
        assert state.events[-1][0] == setup_flow.SETUP_CARD_EVENT
        assert state.events[-1][1]["card"]["hash"] == card.payload_hash

    @pytest.mark.asyncio
    async def test_s8_a_turn_no_person_started_shows_nothing(self, state):
        out = await _propose(
            state, {"kind": "profile", "fields": {"bot_name": "Nova"}}, user_facing=False
        )
        assert out.startswith("Error:")
        assert sc.list_cards("chat-1-1") == []
        assert state.slots["chat-1-1"].messages == []

    @pytest.mark.asyncio
    async def test_s6_the_first_run_state_file_admits_nothing(self, state):
        first_run.record_slot("chat-1-1")
        out = await _propose(
            state, {"kind": "profile", "fields": {"bot_name": "Nova"}}, user_facing=False
        )
        assert out.startswith("Error:")
        assert sc.list_cards("chat-1-1") == []

    @pytest.mark.asyncio
    async def test_governance_denial_shows_nothing(self, state, monkeypatch):
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: "not here")
        out = await _propose(state, {"kind": "profile", "fields": {"bot_name": "Nova"}})
        assert "blocked by policy" in out
        assert sc.list_cards("chat-1-1") == []

    @pytest.mark.asyncio
    async def test_the_model_cannot_propose_the_privacy_disclosure(self, state):
        out = await _propose(state, {"kind": "privacy"})
        assert out.startswith("Error:")

    @pytest.mark.asyncio
    async def test_an_identical_pending_card_is_not_shown_twice(self, state):
        args = {"kind": "profile", "fields": {"bot_name": "Nova"}}
        await _propose(state, args)
        out = await _propose(state, args)
        assert "already showing" in out
        assert len(sc.list_cards("chat-1-1")) == 1

    @pytest.mark.asyncio
    async def test_card_budget_before_the_first_kept_job(self, state, dispatched):
        for i in range(sc.CARD_BUDGET_BEFORE_FIRST_JOB):
            out = await _propose(state, {"kind": "profile", "fields": {"bot_name": f"N{i}"}})
            assert out.startswith("Setup card shown"), out
            card = [c for c in sc.list_cards("chat-1-1") if c.status == sc.STATUS_PENDING][0]
            await setup_flow.decide(state, card.id, "decline", card.payload_hash, {})
        out = await _propose(state, {"kind": "profile", "fields": {"bot_name": "Over"}})
        assert out.startswith("Error:") and "setup cards" in out

    @pytest.mark.asyncio
    async def test_one_card_waits_at_a_time_except_the_home(self, state, monkeypatch):
        from kiro_crew.cloud import simulated_engine

        monkeypatch.setenv(simulated_engine.SIMULATE_ENV, "1")
        await _propose(state, {"kind": "profile", "fields": {"bot_name": "Nova"}})
        out = await _propose(state, {"kind": "service"})
        assert out.startswith("Error:") and "One card at a time" in out
        out = await _propose(state, {"kind": "home"})
        assert out.startswith("Setup card shown")

    @pytest.mark.asyncio
    async def test_invalid_arguments_show_nothing(self, state):
        out = await _propose(state, {"kind": "cron", "name": "x", "prompt": "y", "every_secs": 5})
        assert out.startswith("Error:")
        assert sc.list_cards("chat-1-1") == []


class TestDecide:
    @pytest.mark.asyncio
    async def test_s1_a_wrong_hash_commits_nothing(self, state, dispatched):
        await _propose(state, {"kind": "profile", "fields": {"bot_name": "Nova"}})
        card = _only_card()
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(state, card.id, "commit", "f" * 64, {})
        assert exc.value.code == "card_hash_mismatch"
        assert _only_card().status == sc.STATUS_PENDING
        assert (
            not (data_home() / "config.json").exists()
            or "Nova" not in (data_home() / "config.json").read_text()
        )
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_profile_commit_writes_config_and_reports(self, state, dispatched):
        await _propose(
            state,
            {"kind": "profile", "fields": {"bot_name": "Nova", "technical_level": "codes"}},
        )
        card = _only_card()
        decided = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert decided.status == sc.STATUS_COMMITTED
        cfg = json.loads((data_home() / "config.json").read_text())
        assert cfg["agent"]["bot_name"] == "Nova"
        assert cfg["dashboard"]["user_technical_level"] == "codes"
        assert dispatched and dispatched[-1][1] == "setup_result"
        assert "committed" in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_a_decided_card_cannot_be_decided_again(self, state, dispatched):
        await _propose(state, {"kind": "profile", "fields": {"bot_name": "Nova"}})
        card = _only_card()
        await setup_flow.decide(state, card.id, "decline", card.payload_hash, {})
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert exc.value.code == "card_not_pending"
        assert "declined" in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_soul_commit_writes_the_persona_file(self, state, dispatched):
        await _propose(
            state, {"kind": "soul", "file": "SOUL", "content": "You are Nova. Be brief."}
        )
        card = _only_card()
        await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert sc.read_persona("SOUL") == "You are Nova. Be brief.\n"

    @pytest.mark.asyncio
    async def test_s2_a_credential_reaches_the_vault_and_nowhere_else(
        self, state, dispatched, caplog
    ):
        caplog.set_level(logging.DEBUG)
        await _propose(state, {"kind": "credential", "name": "OPENAI_KEY", "purpose": "tests"})
        card = _only_card()
        decided = await setup_flow.decide(
            state, card.id, "commit", card.payload_hash, {"value": SENTINEL_SECRET}
        )
        assert decided.outcome == {"ref": "secret://OPENAI_KEY"}
        from kiro_crew.config.paths import config_dir
        from kiro_crew.secrets.vault import SecretVault

        assert SecretVault(config_dir()).get("OPENAI_KEY").reveal() == SENTINEL_SECRET
        assert SENTINEL_SECRET not in json.dumps(decided.public())
        assert SENTINEL_SECRET not in (data_home() / "setup" / sc.CARDS_FILE).read_text()
        assert SENTINEL_SECRET not in json.dumps(state.events)
        assert all(SENTINEL_SECRET not in text for _, _, text in dispatched)
        assert all(SENTINEL_SECRET not in str(m) for m in state.slots["chat-1-1"].messages)
        assert SENTINEL_SECRET not in caplog.text

    @pytest.mark.asyncio
    async def test_an_empty_credential_leaves_the_card_waiting(self, state, dispatched):
        await _propose(state, {"kind": "credential", "name": "OPENAI_KEY", "purpose": "tests"})
        card = _only_card()
        decided = await setup_flow.decide(
            state, card.id, "commit", card.payload_hash, {"value": " "}
        )
        assert decided.status == sc.STATUS_PENDING
        assert decided.error["code"] == "credential_empty"
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_service_on_linux_waits_until_the_unit_exists(
        self, state, dispatched, monkeypatch
    ):
        from kiro_crew.service import controller
        from kiro_crew.service.common import Platform

        monkeypatch.setattr(controller, "current_platform", lambda: Platform.SYSTEMD)
        monkeypatch.setattr(controller, "installed_unit_path", lambda: None)
        await _propose(state, {"kind": "service"})
        card = _only_card()
        assert card.payload["needs_terminal"] is True
        assert "kirocrew service install" in card.payload["command"]
        decided = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert decided.status == sc.STATUS_PENDING
        assert decided.error["code"] == "service_not_installed"
        monkeypatch.setattr(controller, "installed_unit_path", lambda: Path("/etc/systemd/x"))
        # The machine's unit runs the default data home, not this relocated one.
        assert setup_flow._service_payload()["installed"] is False
        monkeypatch.setattr(setup_flow, "_service_serves_this_home", lambda: True)
        decided = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert decided.status == sc.STATUS_COMMITTED

    @pytest.mark.asyncio
    async def test_s3_no_committer_writes_a_keystone_file(self, state, dispatched, monkeypatch):
        from kiro_crew.config.paths import config_dir

        keystones = [
            "security_policy.json",
            "admission_policy.json",
            "computer_use.json",
            "profiles",
        ]
        for args, input_ in (
            ({"kind": "profile", "fields": {"bot_name": "Nova", "timezone": "UTC"}}, {}),
            ({"kind": "soul", "file": "USER", "content": "Works on backend."}, {}),
            ({"kind": "credential", "name": "SOME_TOKEN", "purpose": "x"}, {"value": "v"}),
        ):
            await _propose(state, args)
            card = [c for c in sc.list_cards("chat-1-1") if c.status == sc.STATUS_PENDING][0]
            await setup_flow.decide(state, card.id, "commit", card.payload_hash, input_)
        for name in keystones:
            assert not (config_dir() / name).exists(), name


class TestCronPreviewThenKeep:
    class FakeCrons:
        def __init__(self) -> None:
            self.jobs: dict[str, SimpleNamespace] = {}
            self.enabled: dict[str, bool] = {}

        async def add_job_async(self, name, message, **kw):
            job = SimpleNamespace(id="job123", name=name, message=message, kw=kw)
            self.jobs[job.id] = job
            self.enabled[job.id] = kw["enabled"]
            return job

        def discard_finished_run(self, jid):
            return True

        def is_running(self, jid):
            return False

        async def _run(self, jid):
            job = self.jobs[jid]
            job.last_result = "3 reviews waiting; CI green"
            job.last_status = "ok"
            return True

        def run_job(self, jid):
            return self._run(jid)

        def attach_run_task(self, jid, task):
            pass

        async def get_job_async(self, jid):
            return self.jobs[jid]

        async def update_job_async(self, jid, **kw):
            self.jobs[jid].kw.update(kw)
            return self.jobs[jid]

        async def enable_job_async(self, jid, enabled=True, **_):
            self.enabled[jid] = enabled
            return True

        async def remove_job_async(self, jid, **_):
            self.jobs.pop(jid, None)
            return True

    @pytest.mark.asyncio
    async def test_preview_runs_a_disabled_job_and_keep_enables_it(self, state, dispatched):
        crons = self.FakeCrons()
        state.crons = crons
        await _propose(
            state,
            {
                "kind": "cron",
                "name": "Dev brief",
                "prompt": "Summarize my PRs",
                "cron_expr": "0 8 * * 1-5",
            },
        )
        card = _only_card()
        previewed = await setup_flow.decide(state, card.id, "preview", card.payload_hash, {})
        assert previewed.status == sc.STATUS_PENDING
        assert previewed.outcome["preview"] == {
            "status": "success",
            "text": "3 reviews waiting; CI green",
        }
        assert crons.enabled["job123"] is False
        assert crons.jobs["job123"].kw["silent"] is True
        assert crons.jobs["job123"].kw["hide_in_chat"] is True
        assert dispatched == []
        kept = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert kept.status == sc.STATUS_COMMITTED
        assert kept.outcome["job_id"] == "job123"
        assert crons.enabled["job123"] is True
        assert crons.jobs["job123"].kw["silent"] is False
        assert crons.jobs["job123"].kw["hide_in_chat"] is False
        assert "kept" in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_declining_after_a_preview_removes_the_job(self, state, dispatched):
        crons = self.FakeCrons()
        state.crons = crons
        await _propose(
            state, {"kind": "cron", "name": "Brief", "prompt": "Summarize", "every_secs": 86400}
        )
        card = _only_card()
        await setup_flow.decide(state, card.id, "preview", card.payload_hash, {})
        await setup_flow.decide(state, card.id, "decline", card.payload_hash, {})
        assert "job123" not in crons.jobs


class TestFirstRun:
    @pytest.mark.asyncio
    async def test_a_fresh_install_opens_on_the_welcome_and_the_harness_choice(self):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        assert slot_key and st.slots[slot_key].pinned
        assert first_run.read_first_run_slot() == slot_key
        card = _only_card(slot_key)
        assert card.kind == sc.KIND_HARNESS
        # Kiro first and the default (harness-parity H1), from the selectable set.
        assert card.payload["options"][0] == {"id": "", "label": "Kiro CLI"}
        assert card.payload["default"] == "" and card.payload["current"] == ""
        # The chat that becomes the main chat carries the session tools from the start.
        assert st.slots[slot_key].agent == "kirocrew-main"
        # Not an empty chat: the gateway's welcome comes first, then the card's row.
        (role, text, meta), (card_role, _t, card_meta) = st.slots[slot_key].messages
        assert role == "inject" and meta == {"setupStep": {"step": "welcome", "card": card.id}}
        assert text.startswith("(Setup step shown to the user:")
        assert card_role == "inject" and card_meta == {
            "setupCard": {"id": card.id, "kind": "harness"}
        }

    @pytest.mark.asyncio
    async def test_it_is_idempotent_across_restarts(self):
        st = FakeState()
        first = await setup_flow.ensure_first_run_session(st)
        again = await setup_flow.ensure_first_run_session(st)
        assert first == again
        assert len(sc.list_cards(first)) == 1

    @pytest.mark.asyncio
    async def test_an_install_with_sessions_gets_none(self):
        st = FakeState()
        st.conversation_log = SimpleNamespace(list_sessions=lambda: [{"key": "old"}])
        assert await setup_flow.ensure_first_run_session(st) is None
        assert first_run.read_first_run_slot() is None

    @pytest.mark.asyncio
    async def test_an_onboarded_install_gets_none(self):
        (data_home() / "config.json").write_text(json.dumps({"dashboard": {"onboarded": True}}))
        st = FakeState()
        assert await setup_flow.ensure_first_run_session(st) is None

    @pytest.mark.asyncio
    async def test_acknowledging_privacy_sets_the_flag_and_shows_the_start_path(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        card = await _to_privacy(st, slot_key)
        decided = await setup_flow.decide(
            st, card.id, "commit", card.payload_hash, {"telemetry": False}
        )
        assert decided.status == sc.STATUS_COMMITTED
        cfg = json.loads((data_home() / "config.json").read_text())
        assert cfg["dashboard"]["privacy_acked"] is True
        assert cfg["telemetry"]["beacon_enabled"] is False
        assert "privacy" in first_run.done_stages()
        # No model turn yet: the start path is the last scripted step.
        assert dispatched == []
        assert _live(slot_key, sc.KIND_PATH).payload == {"options": ["tips", "detailed"]}

    @pytest.mark.asyncio
    async def test_a_first_run_chat_from_before_the_scripted_steps_starts_on_privacy(
        self, dispatched
    ):
        # A first-run chat created with the privacy card first keeps its own order.
        st = FakeState()
        st.slots["chat-1-1"] = FakeSlot("chat-1-1")
        first_run.record_slot("chat-1-1")
        card = sc.create_card(
            slot="chat-1-1", session_key="dashboard:chat-1-1", kind=sc.KIND_PRIVACY, payload={}
        )
        await setup_flow.decide(st, card.id, "commit", card.payload_hash, {"telemetry": False})
        assert [(k, kind) for k, kind, _ in dispatched] == [("chat-1-1", "first_run")]
        assert "acknowledged the privacy disclosure" in dispatched[0][2]


class TestHomeInTheBackground:
    @pytest.mark.asyncio
    async def test_build_then_move_in_on_the_simulated_engine(self, state, dispatched, monkeypatch):
        from kiro_crew.cloud import simulated_engine

        monkeypatch.setenv(simulated_engine.SIMULATE_ENV, "1")
        monkeypatch.setattr(setup_flow, "_CONNECT_POLL_SECS", 0.01)
        monkeypatch.setattr(setup_flow.asyncio, "sleep", _fast_sleep)
        state.cloud_launch_engine = simulated_engine.SimulatedLaunchEngine(step_secs=0)
        state.cloud_launch_sync = True
        out = await _propose(state, {"kind": "home", "region": "us-west-2"})
        assert out.startswith("Setup card shown"), out
        card = _only_card()
        assert card.stakes == "high"
        assert card.payload["simulated"] is True
        assert card.payload["monthly_usd"] == sc.monthly_estimate_usd("light")
        building = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert building.status == sc.STATUS_WAITING
        for _ in range(200):
            current = sc.get_card(card.id)
            if current.status == sc.STATUS_COMMITTED:
                break
            await _real_sleep(0.01)
        moved = sc.get_card(card.id)
        assert moved.status == sc.STATUS_COMMITTED
        assert moved.outcome["moved"] is True and moved.outcome["simulated"] is True
        assert [s["state"] for s in moved.outcome["move_steps"]] == ["done"] * 4
        assert "moved into its home" in dispatched[-1][2]
        # The commands a real home would answer, from the simulated launch's own tag.
        tag = moved.outcome["home"]["tag"]
        assert re.fullmatch(r"kc-[0-9a-f]{6}", tag)
        assert moved.outcome["home"]["name"] == f"Kiro Crew Cloud ({tag})"
        open_cmd = f"kirocrew cloud connect --tag {tag} --region us-west-2"
        assert moved.outcome["reconnect"][0] == {"purpose": "open", "command": open_cmd}
        assert "simulated" in dispatched[-1][2] and f"`{open_cmd}`" in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_a_real_home_waits_for_aws_sign_in(self, state, dispatched, monkeypatch):
        from kiro_crew.cloud import iam

        monkeypatch.setattr(iam, "reachability_check", lambda profile, region: {"reachable": False})
        await _propose(state, {"kind": "home"})
        card = _only_card()
        assert card.payload["aws_signed_in"] is False
        decided = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert decided.status == sc.STATUS_PENDING
        assert decided.error["code"] == "aws_not_signed_in"

    @pytest.mark.asyncio
    async def test_a_real_home_signs_in_the_way_this_machine_does(
        self, state, dispatched, monkeypatch
    ):
        from kiro_crew.cloud import iam
        from kiro_crew.cloud.login_target import ACCOUNT_TYPE_IDENTITY_CENTER
        from kiro_crew.dashboard import handlers_cloud
        from kiro_crew.dashboard.handlers import sessions

        monkeypatch.setattr(iam, "reachability_check", lambda profile, region: {"reachable": True})

        async def _identity():
            return {
                "account_type": ACCOUNT_TYPE_IDENTITY_CENTER,
                "start_url": "https://example.awsapps.com/start",
                "region": "us-east-1",
            }

        monkeypatch.setattr(sessions, "fetch_local_identity", _identity)
        seen: dict = {}

        async def _start(state_, **kw):
            seen.update(kw)
            return None, handlers_cloud.LaunchRefusal({"error": "stop", "code": "stop_here"}, 409)

        monkeypatch.setattr(handlers_cloud, "start_launch_job", _start)
        await _propose(state, {"kind": "home"})
        card = _only_card()
        await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        target = seen["login_target"]
        assert target.is_identity_center and target.start_url.startswith("https://example.")
        # The region whoami reports rides along, so the job can be read back.
        assert target.region == "us-east-1"

    @pytest.mark.asyncio
    async def test_the_first_run_offers_the_home_the_owner_chose(self, dispatched, monkeypatch):
        from kiro_crew.cloud import simulated_engine

        monkeypatch.setenv(simulated_engine.SIMULATE_ENV, "1")
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        state_file = first_run.read_state()
        state_file["home"] = {"choice": "cloud", "region": "eu-west-1"}
        first_run.write_state(state_file)
        await _through_script(st, slot_key)
        assert _kinds(slot_key) == ["harness", "harness_signin", "privacy", "path", "home"]
        assert "home in the cloud" in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_privacy_shows_no_home_card(self, dispatched, monkeypatch):
        from kiro_crew.cloud import iam

        monkeypatch.setattr(iam, "reachability_check", _refuse_detect)
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _through_script(st, slot_key)
        # Where the crew lives is asked at the first kept job, not before the Hello.
        assert sc.KIND_HOME not in _kinds(slot_key)
        assert "Where should your crew live?" not in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_the_home_step_is_not_one_of_the_agents_cards(self, dispatched, state):
        # The gateway's step leaves the agent its whole card budget.
        home = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_HOME,
            payload={"region": "us-east-1", setup_flow.HOME_STEP_KEY: True},
            private={},
        )
        assert home.payload[setup_flow.HOME_STEP_KEY]
        for i in range(sc.CARD_BUDGET_BEFORE_FIRST_JOB - 1):
            await _propose(state, {"kind": "profile", "fields": {"bot_name": f"N{i}"}})
            sc.update_card(
                sc.list_cards("chat-1-1")[-1].id,
                lambda c: setattr(c, "status", sc.STATUS_DECLINED),
            )
        out = await _propose(state, {"kind": "profile", "fields": {"bot_name": "Last"}})
        assert out.startswith("Setup card shown"), out

    @pytest.mark.asyncio
    async def test_a_scripted_here_answer_is_never_asked_again(self, dispatched, monkeypatch):
        from kiro_crew.cloud import iam

        monkeypatch.setattr(iam, "reachability_check", _refuse_detect)
        first_run.record_home_choice("here")
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _through_script(st, slot_key)
        assert sc.KIND_HOME not in _kinds(slot_key)
        assert "Where should your crew live?" not in dispatched[-1][2]


class TestWhereTheCrewLives:
    """Keeping or skipping the job step leads to one home card before completion."""

    async def _keep_first_job(self, st, dispatched) -> str:
        slot_key = await setup_flow.ensure_first_run_session(st)
        crons = TestCronPreviewThenKeep.FakeCrons()
        crons.jobs["job123"] = SimpleNamespace(id="job123", kw={})
        st.crons = crons
        cron = sc.create_card(
            slot=slot_key,
            session_key=f"dashboard:{slot_key}",
            kind=sc.KIND_CRON,
            payload={"name": "Brief", "schedule_human": "every weekday at 08:00"},
            private={"job_id": "job123"},
        )
        kept = await setup_flow.decide(st, cron.id, "commit", cron.payload_hash, {})
        assert kept.status == sc.STATUS_COMMITTED
        return slot_key

    @staticmethod
    def _home(slot_key: str) -> sc.SetupCard:
        homes = [c for c in sc.list_cards(slot_key) if c.kind == sc.KIND_HOME]
        assert len(homes) == 1, homes
        return homes[0]

    @pytest.mark.asyncio
    async def test_the_choice_card_comes_after_the_kept_jobs_result(self, monkeypatch):
        # Shown before the result turn, the tray read the new card as one the
        # chat had moved past and opened it folded.
        seen_at_dispatch: list[int] = []

        async def _record(state_, slot, text, inject_kind):
            homes = [c for c in sc.list_cards(slot.key) if c.kind == sc.KIND_HOME]
            seen_at_dispatch.append(len(homes))

        monkeypatch.setattr(setup_flow, "_dispatch_envelope_turn", _record)
        st = FakeState()
        slot_key = await self._keep_first_job(st, None)
        assert seen_at_dispatch[-1] == 0, "the home card was shown before the job's result"
        assert self._home(slot_key).payload["step"] == "choose"

    @pytest.mark.asyncio
    async def test_the_first_kept_job_brings_the_choice_card_once(self, dispatched, monkeypatch):
        from kiro_crew.cloud import iam

        # The question asks nothing of AWS.
        monkeypatch.setattr(iam, "reachability_check", _refuse_detect)
        st = FakeState()
        slot_key = await self._keep_first_job(st, dispatched)
        home = self._home(slot_key)
        assert home.payload[setup_flow.HOME_STEP_KEY] is True
        assert home.payload["step"] == "choose" and home.private["phase"] == "choose"
        offered = {k for keys, _ in sc.HOME_PLAN_SIZES.values() for k in keys}
        assert home.payload["from_usd"] == min(
            sc.monthly_estimate_usd(k, home.payload["region"]) for k in offered
        )
        assert home.stakes == "high" and home.status == sc.STATUS_PENDING
        assert first_run.read_main_slot() is None
        assert not any(
            (meta or {}).get("kind") == "main_chat" for _, _, meta in st.slots[slot_key].messages
        )
        # The kept job's result tells the agent the question is on screen, and why now.
        result = dispatched[-1][2]
        assert "Where should your crew live?" in result
        assert "do not ask it again in prose or say setup is done" in result
        # A second kept job asks nothing again.
        cron = sc.create_card(
            slot=slot_key,
            session_key=f"dashboard:{slot_key}",
            kind=sc.KIND_CRON,
            payload={"name": "Watch", "schedule_human": "hourly"},
            private={"job_id": "job123"},
        )
        await setup_flow.decide(st, cron.id, "commit", cron.payload_hash, {})
        self._home(slot_key)
        assert "Where should your crew live?" not in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_skipping_the_job_shows_home_after_the_result_without_keeping_a_job(
        self, monkeypatch
    ):
        from kiro_crew.cloud import iam

        monkeypatch.setattr(iam, "reachability_check", _refuse_detect)
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        st.crons = TestCronPreviewThenKeep.FakeCrons()
        seen = []

        async def _record(state_, slot, text, inject_kind):
            seen.append((text, [c for c in sc.list_cards(slot.key) if c.kind == sc.KIND_HOME]))

        monkeypatch.setattr(setup_flow, "_dispatch_envelope_turn", _record)
        await _propose(
            st,
            {"kind": "cron", "name": "Brief", "prompt": "Summarize", "every_secs": 86400},
            slot=slot_key,
        )
        cron = next(c for c in sc.list_cards(slot_key) if c.kind == sc.KIND_CRON)
        await setup_flow.decide(st, cron.id, "preview", cron.payload_hash, {})
        skipped = await setup_flow.decide(st, cron.id, "decline", cron.payload_hash, {})
        assert skipped.status == sc.STATUS_DECLINED
        assert skipped.outcome["home_choice"] is True
        assert "Where should your crew live?" in seen[-1][0]
        assert "job is kept" not in seen[-1][0]
        assert seen[-1][1] == []
        assert self._home(slot_key).payload["step"] == "choose"
        assert st.crons.jobs == {}
        assert "job_kept" not in first_run.done_stages()
        assert first_run.read_main_slot() is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", ["here", "decline"])
    async def test_a_spoken_job_skip_can_go_straight_to_home_and_finish_without_a_job(
        self, dispatched, monkeypatch, answer
    ):
        from kiro_crew import mcp_core, session_directive
        from kiro_crew.cloud import iam
        from kiro_crew.mcp_tools import setup as setup_tools

        monkeypatch.setattr(iam, "reachability_check", _refuse_detect)
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        monkeypatch.setattr(
            mcp_core, "_resolve_session_key_strict", lambda: f"dashboard:{slot_key}"
        )
        monkeypatch.setattr(setup_tools, "has_dashboard_surface", lambda sk: True)
        request = setup_tools.setup_card("setup_card", {"kind": "home", "step": "choose"})
        args = session_directive.decode(request, "setup_card")
        assert args and args["step"] == "choose"
        assert (await _propose(st, args, slot=slot_key)).startswith("Setup card shown")
        home = self._home(slot_key)
        assert home.payload["step"] == "choose"
        assert first_run.read_main_slot() is None
        assert not await setup_flow.graduate(st, slot_key)
        assert "already has a card" in await _propose(st, args, slot=slot_key)
        self._home(slot_key)
        if answer == "here":
            await setup_flow.decide(st, home.id, "choose", home.payload_hash, {"where": "here"})
            assert "job they kept" not in dispatched[-1][2]
        else:
            await setup_flow.decide(st, home.id, "decline", home.payload_hash, {})
        assert first_run.read_main_slot() == slot_key
        assert "job_kept" not in first_run.done_stages()
        assert not [c for c in sc.list_cards(slot_key) if c.kind == sc.KIND_CRON]

    @pytest.mark.asyncio
    async def test_home_choice_survives_the_optional_card_budget(self, state):
        for n in range(sc.CARD_BUDGET_BEFORE_FIRST_JOB):
            card = sc.create_card(
                slot="chat-1-1",
                session_key="dashboard:chat-1-1",
                kind=sc.KIND_PROFILE,
                payload={"fields": {"bot_name": str(n)}},
            )
            sc.claim_pending(card.id, card.payload_hash, to_status=sc.STATUS_DECLINED)
        out = await _propose(state, {"kind": "home", "step": "choose"})
        assert out.startswith("Setup card shown")
        assert self._home("chat-1-1").payload["step"] == "choose"

    @pytest.mark.asyncio
    async def test_home_choice_still_requires_user_provenance_and_governance(
        self, state, monkeypatch
    ):
        args = {"kind": "home", "step": "choose"}
        assert (await _propose(state, args, user_facing=False)).startswith("Error:")
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: "denied")
        assert "blocked by policy" in await _propose(state, args)
        assert not sc.list_cards("chat-1-1")

    @pytest.mark.asyncio
    async def test_a_scripted_answer_or_a_home_card_already_there_asks_nothing(self, dispatched):
        first_run.record_home_choice("later")
        st = FakeState()
        slot_key = await self._keep_first_job(st, dispatched)
        assert not [c for c in sc.list_cards(slot_key) if c.kind == sc.KIND_HOME]

    @pytest.mark.asyncio
    async def test_an_agents_home_card_is_not_asked_again(self, dispatched, monkeypatch):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        proposed = await _propose(st, {"kind": "home"}, slot=slot_key)
        assert proposed.startswith("Setup card shown"), proposed
        crons = TestCronPreviewThenKeep.FakeCrons()
        crons.jobs["job123"] = SimpleNamespace(id="job123", kw={})
        st.crons = crons
        cron = sc.create_card(
            slot=slot_key,
            session_key=f"dashboard:{slot_key}",
            kind=sc.KIND_CRON,
            payload={"name": "Brief", "schedule_human": "daily"},
            private={"job_id": "job123"},
        )
        await setup_flow.decide(st, cron.id, "commit", cron.payload_hash, {})
        home = self._home(slot_key)
        assert "step" not in home.payload and setup_flow.HOME_STEP_KEY not in home.payload

    @pytest.mark.asyncio
    async def test_this_machine_settles_it_and_the_agent_offers_the_service(self, dispatched):
        st = FakeState()
        slot_key = await self._keep_first_job(st, dispatched)
        home = self._home(slot_key)
        out = await setup_flow.decide(st, home.id, "choose", home.payload_hash, {"where": "here"})
        assert out.status == sc.STATUS_COMMITTED and out.outcome == {"stayed": True}
        assert 'kind "service"' in dispatched[-1][2]
        assert first_run.read_main_slot() == slot_key

    @pytest.mark.asyncio
    async def test_the_cloud_moves_the_same_card_on_under_a_new_hash(self, dispatched, monkeypatch):
        from kiro_crew.cloud import iam

        st = FakeState()
        slot_key = await self._keep_first_job(st, dispatched)
        home = self._home(slot_key)
        monkeypatch.setattr(
            iam,
            "reachability_check",
            lambda profile, region: {"reachable": True, "account": "123456789012"},
        )
        out = await setup_flow.decide(st, home.id, "choose", home.payload_hash, {"where": "cloud"})
        assert out.id == home.id and out.status == sc.STATUS_PENDING
        assert out.payload_hash != home.payload_hash
        assert "step" not in out.payload and out.payload[setup_flow.HOME_STEP_KEY] is True
        assert out.payload["aws_signed_in"] is True and out.payload["aws_account"] == "…9012"
        assert out.payload["size_options"] and out.private["phase"] == "build"
        assert first_run.read_main_slot() is None
        assert not await setup_flow.graduate(st, slot_key)
        assert st.events[-1][1]["card"]["hash"] == out.payload_hash
        # The old face commits nothing.
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(st, home.id, "commit", home.payload_hash, {})
        assert exc.value.code == "card_hash_mismatch"

    @pytest.mark.asyncio
    async def test_build_before_the_choice_and_a_bad_answer_are_refused(self, dispatched):
        st = FakeState()
        slot_key = await self._keep_first_job(st, dispatched)
        home = self._home(slot_key)
        out = await setup_flow.decide(st, home.id, "commit", home.payload_hash, {})
        assert out.status == sc.STATUS_PENDING and out.error["code"] == "home_choose_first"
        out = await setup_flow.decide(st, home.id, "choose", home.payload_hash, {"where": "moon"})
        assert out.status == sc.STATUS_PENDING and out.error["code"] == "home_choice_invalid"
        # A card past the question takes no answer.
        agent_home = sc.create_card(
            slot=slot_key,
            session_key=f"dashboard:{slot_key}",
            kind=sc.KIND_HOME,
            payload={"region": "us-east-1"},
            private={"phase": "build", "settings": {"region": "us-east-1"}},
        )
        out = await setup_flow.decide(
            st, agent_home.id, "choose", agent_home.payload_hash, {"where": "cloud"}
        )
        assert out.error["code"] == "invalid_decision"


def _refuse_detect(*args, **kw):
    raise AssertionError("the AWS CLI must not be called once the home is answered")


_real_sleep = __import__("asyncio").sleep


async def _fast_sleep(secs):
    await _real_sleep(0)


class TestMainChat:
    @pytest.mark.asyncio
    async def test_a_failed_home_can_be_replaced_before_setup_completes(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        failed = sc.create_card(
            slot=slot_key, session_key=f"dashboard:{slot_key}", kind=sc.KIND_HOME, payload={}
        )
        await setup_flow._finish(failed, sc.STATUS_FAILED)
        assert not await setup_flow.graduate(st, slot_key)
        replacement = sc.create_card(
            slot=slot_key, session_key=f"dashboard:{slot_key}", kind=sc.KIND_HOME, payload={}
        )
        await setup_flow.decide(st, replacement.id, "decline", replacement.payload_hash, {})
        assert first_run.read_main_slot() == slot_key

    @pytest.mark.asyncio
    async def test_a_settled_home_choice_graduates_the_first_run_chat(
        self, dispatched, monkeypatch
    ):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        first_run.record_home_choice("here")
        slot = st.slots[slot_key]
        slot._title_epoch = 0
        monkeypatch.setattr(setup_flow, "_agent_name", lambda: "Nova")
        assert await setup_flow.graduate(st, slot_key) is True
        assert first_run.read_main_slot() == slot_key
        assert slot.title == "Nova" and slot._title_origin == "user" and slot.pinned
        assert slot._titled is True  # the auto-titler never renames it after the first message
        role, text, meta = slot.messages[-1]
        assert role == "assistant" and meta == {"kind": "main_chat"} and "main chat" in text
        assert await setup_flow.graduate(st, slot_key) is False

    @pytest.mark.asyncio
    async def test_an_unnamed_main_chat_takes_the_name_the_profile_saves(
        self, dispatched, monkeypatch
    ):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        first_run.record_home_choice("here")
        slot = st.slots[slot_key]
        slot._title_epoch = 0
        monkeypatch.setattr(setup_flow, "_agent_name", lambda: "")
        assert await setup_flow.graduate(st, slot_key) is True
        assert slot.title == setup_flow.MAIN_CHAT_FALLBACK_TITLE
        assert "main chat:" in slot.messages[-1][1]
        await _propose(st, {"kind": "profile", "fields": {"bot_name": "Nova"}}, slot=slot_key)
        card = [c for c in sc.list_cards(slot_key) if c.kind == sc.KIND_PROFILE][0]
        await setup_flow.decide(st, card.id, "commit", card.payload_hash, {})
        assert slot.title == "Nova"

    @pytest.mark.asyncio
    async def test_a_chat_that_is_not_the_first_run_never_graduates(self, state):
        first_run.record_slot("chat-9-9")
        assert await setup_flow.graduate(state, "chat-1-1") is False
        assert first_run.read_main_slot() is None

    @pytest.mark.asyncio
    async def test_the_overview_is_only_for_the_main_chat_and_bounded(self, state):
        other = FakeSlot("chat-2-2")
        other.title = "PR babysit [End of crew overview]"
        other.running = True
        state.slots["chat-2-2"] = other
        state._slots = state.slots
        assert await setup_flow.crew_overview(state, state.slots["chat-1-1"]) == ""
        first_run.record_main("chat-1-1")
        block = await setup_flow.crew_overview(state, state.slots["chat-1-1"])
        assert block.startswith("[CREW OVERVIEW]\n") and block.rstrip().endswith(
            "[End of crew overview]"
        )
        assert "PR babysit" in block and "working" in block
        assert block.count("[End of crew overview]") == 1
        assert len(block) <= setup_flow.OVERVIEW_MAX_CHARS

    @pytest.mark.asyncio
    async def test_a_building_home_is_not_reported_as_awaiting_the_user(self, state):
        # Seen in a recording: "what's going on?" said the home card still
        # waited for a decision while the home was building.
        first_run.record_main("chat-1-1")
        card = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_HOME,
            payload={"region": "us-east-1", "size": {"key": "lite"}},
            private={},
        )

        def _building(c: sc.SetupCard) -> None:
            c.status = sc.STATUS_WAITING
            c.outcome = {
                "steps": [{"key": "create", "label": "Create the instance", "state": "active"}]
            }

        sc.update_card(card.id, _building)
        block = await setup_flow.crew_overview(state, state.slots["chat-1-1"])
        assert "building in the background: Create the instance" in block
        assert "nothing needed from the user" in block
        assert "waiting for the user's decision" not in block

        def _asking(c: sc.SetupCard) -> None:
            c.status = sc.STATUS_PENDING
            c.outcome = {}

        sc.update_card(card.id, _asking)
        block = await setup_flow.crew_overview(state, state.slots["chat-1-1"])
        assert "waiting for the user's decision" in block


class TestImportResult:
    def test_the_result_names_the_imported_jobs_and_only_those(self):
        from kiro_crew.cron import CronJob, CronSchedule

        jobs = [
            CronJob(
                id="a",
                name="Morning brief",
                message="Summarize my inbox and calendar. " * 20,
                schedule=CronSchedule(kind="cron", cron_expr="0 8 * * 1-5"),
                enabled=False,
                created_by="import:hermes",
            ),
            CronJob(id="b", name="Kept here", message="x", created_by="setup_card"),
        ]
        st = SimpleNamespace(crons=SimpleNamespace(list_jobs=lambda include_disabled=False: jobs))
        listed = setup_flow._imported_jobs(st, ["hermes"])
        assert [j["name"] for j in listed] == ["Morning brief"]
        assert len(listed[0]["prompt"]) <= setup_flow._IMPORTED_PROMPT_CHARS
        card = SimpleNamespace(
            kind=sc.KIND_IMPORT,
            status=sc.STATUS_COMMITTED,
            payload={},
            error=None,
            outcome={"imported_count": 6, "jobs_added_disabled": 1, "jobs": listed},
        )
        text = setup_flow._result_text(card)
        assert "Morning brief (" in text and "propose a cron card" in text


class TestScriptedSteps:
    """UX.2 / UX.3: the steps before any model turn, shown and advanced by the gateway alone."""

    @pytest.mark.asyncio
    async def test_no_model_turn_runs_before_the_start_path(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": ""})
        await _decide_live(st, slot_key, sc.KIND_HARNESS_SIGNIN)
        privacy = _live(slot_key, sc.KIND_PRIVACY)
        await setup_flow.decide(st, privacy.id, "commit", privacy.payload_hash, {})
        assert dispatched == []
        await _decide_live(st, slot_key, sc.KIND_PATH, {"path": "tips"})
        assert [(k, kind) for k, kind, _ in dispatched] == [(slot_key, "first_run")]
        assert _kinds(slot_key) == ["harness", "harness_signin", "privacy", "path"]
        text = dispatched[0][2]
        assert "$crew-setup" in text and "finished the setup steps" in text
        assert "Agent engine the user chose: Kiro CLI. It answered" in text
        assert "Start path the user chose: get started with tips" in text

    @pytest.mark.asyncio
    async def test_the_detailed_path_is_named_in_the_kickoff(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _through_script(st, slot_key, path="detailed")
        assert "Start path the user chose: a more detailed setup" in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_each_step_opens_with_a_gateway_row_that_opens_no_turn(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _through_script(st, slot_key)
        steps = [
            meta["setupStep"]["step"]
            for role, _text, meta in st.slots[slot_key].messages
            if meta and "setupStep" in meta
        ]
        assert steps == ["welcome", "signin", "privacy", "path"]
        for role, text, meta in st.slots[slot_key].messages:
            if meta and ("setupStep" in meta or "setupCard" in meta):
                assert role == "inject" and "injectKind" not in meta

    @pytest.mark.asyncio
    async def test_the_first_turn_reads_the_steps_as_the_gateways_not_its_own_words(
        self, dispatched
    ):
        from kiro_crew.context import build_session_replay

        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _through_script(st, slot_key)
        rows = [{"role": r, "content": c} for r, c, _m in st.slots[slot_key].messages]
        replay = build_session_replay(None, f"dashboard:{slot_key}", pending_messages=rows)
        assert "Inject: (Setup step shown to the user: welcome to Kiro Crew" in replay
        assert "Assistant:" not in replay

    @pytest.mark.asyncio
    async def test_the_chosen_engine_is_written_and_its_own_sign_in_shown(self, monkeypatch):
        from kiro_crew.agent_sdk import backend_install

        monkeypatch.setattr(
            backend_install,
            "probe_backend",
            lambda backend: backend_install.BackendInstallState(
                backend, "codex", backend_install.MISSING, ("codex-acp",), "npm i -g codex-acp"
            ),
        )
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": "codex"})
        cfg = json.loads((data_home() / "config.json").read_text())
        assert cfg["agent"]["acp_backend"] == "codex"
        signin = _live(slot_key, sc.KIND_HARNESS_SIGNIN)
        assert signin.payload["flow"] == "own" and signin.payload["backend"] == "codex"
        assert signin.payload["install_command"] == "npm i -g codex-acp"
        assert signin.payload["sign_in"]  # the harness's own declared remedy, verbatim
        step = [m for _r, _t, m in st.slots[slot_key].messages if m and "setupStep" in m][-1]
        assert (
            step["setupStep"]["step"] == "signin" and step["setupStep"]["label"] == "OpenAI Codex"
        )

    @pytest.mark.asyncio
    async def test_the_kiro_engines_use_the_kiro_cli_flow(self):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": "kas"})
        assert _live(slot_key, sc.KIND_HARNESS_SIGNIN).payload == {
            "backend": "kas",
            "label": "KAS (kiro-agent)",
            "flow": "kiro_cli",
        }

    @pytest.mark.asyncio
    async def test_an_engine_not_offered_or_not_allowed_is_refused(self, monkeypatch):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        card = await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": "not-a-harness"})
        assert card.status == sc.STATUS_PENDING and card.error["code"] == "harness_invalid"
        # A policy that narrowed the set after the card was shown: the live set decides.
        from kiro_crew.agent_sdk import backends

        monkeypatch.setattr(backends, "selectable_backends", lambda: frozenset({""}))
        card = await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": "claude"})
        assert card.status == sc.STATUS_PENDING and card.error["code"] == "harness_denied"
        assert (
            not (data_home() / "config.json").exists()
            or "claude" not in (data_home() / "config.json").read_text()
        )

    @pytest.mark.asyncio
    async def test_a_failed_check_returns_the_card_and_then_allows_continuing(self, monkeypatch):
        from kiro_crew.dashboard import harness_readiness

        async def _signed_out(state, backend):
            return harness_readiness.Verdict(backend, harness_readiness.NOT_SIGNED_IN, "nope")

        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": ""})
        # Continuing without a check needs one failed check first.
        card = await _decide_live(st, slot_key, sc.KIND_HARNESS_SIGNIN, {"skip": True})
        assert card.status == sc.STATUS_PENDING and card.error["code"] == "harness_check_first"
        monkeypatch.setattr(harness_readiness, "check", _signed_out)
        card = await _decide_live(st, slot_key, sc.KIND_HARNESS_SIGNIN)
        assert card.status == sc.STATUS_PENDING and card.error["code"] == "harness_not_signed_in"
        assert sc.KIND_PRIVACY not in _kinds(slot_key)
        card = await _decide_live(st, slot_key, sc.KIND_HARNESS_SIGNIN, {"skip": True})
        assert card.status == sc.STATUS_COMMITTED and card.outcome == {"verified": False}
        assert _live(slot_key, sc.KIND_PRIVACY)

    @pytest.mark.asyncio
    async def test_a_skipped_check_is_named_in_the_kickoff(self, dispatched, monkeypatch):
        from kiro_crew.dashboard import harness_readiness

        async def _timed_out(state, backend):
            return harness_readiness.Verdict(backend, harness_readiness.CHECK_FAILED, "timeout")

        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": ""})
        monkeypatch.setattr(harness_readiness, "check", _timed_out)
        await _decide_live(st, slot_key, sc.KIND_HARNESS_SIGNIN)
        await _decide_live(st, slot_key, sc.KIND_HARNESS_SIGNIN, {"skip": True})
        privacy = _live(slot_key, sc.KIND_PRIVACY)
        await setup_flow.decide(st, privacy.id, "commit", privacy.payload_hash, {})
        await _decide_live(st, slot_key, sc.KIND_PATH, {"path": "tips"})
        assert "continued without a sign-in check" in dispatched[-1][2]

    @pytest.mark.asyncio
    async def test_the_lock_holds_in_the_first_run_chat_until_the_last_step(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        assert setup_flow.scripted_lock(slot_key) == sc.KIND_HARNESS
        assert setup_flow.scripted_lock("chat-9-9") is None
        assert setup_flow.scripted_first_run_active() is True
        privacy = await _to_privacy(st, slot_key)
        assert setup_flow.scripted_lock(slot_key) == sc.KIND_PRIVACY
        await setup_flow.decide(st, privacy.id, "commit", privacy.payload_hash, {})
        assert setup_flow.scripted_lock(slot_key) == sc.KIND_PATH
        await _decide_live(st, slot_key, sc.KIND_PATH, {"path": "tips"})
        assert setup_flow.scripted_lock(slot_key) is None

    @pytest.mark.asyncio
    async def test_a_lost_card_store_unlocks_the_chat(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        (first_run.setup_dir() / sc.CARDS_FILE).unlink()
        assert setup_flow.scripted_lock(slot_key) is None


class TestScriptedStepsAfterARestart:
    @pytest.mark.asyncio
    async def test_a_check_a_restart_cut_short_waits_for_the_owner_again(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _decide_live(st, slot_key, sc.KIND_HARNESS, {"backend": ""})
        signin = _live(slot_key, sc.KIND_HARNESS_SIGNIN)
        sc.claim_pending(signin.id, signin.payload_hash)  # the click, then the restart
        assert await setup_flow.ensure_first_run_session(st) == slot_key
        again = sc.get_card(signin.id)
        assert again.status == sc.STATUS_PENDING and again.error["code"] == "step_interrupted"
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_a_step_a_restart_never_showed_is_shown_on_the_next_start(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        harness = _live(slot_key, sc.KIND_HARNESS)
        # Committed, then the gateway stopped before the next card was created.
        sc.claim_pending(harness.id, harness.payload_hash)
        sc.update_card(harness.id, lambda c: setattr(c, "status", sc.STATUS_COMMITTED))
        await setup_flow.ensure_first_run_session(st)
        assert _live(slot_key, sc.KIND_HARNESS_SIGNIN)
        await setup_flow.ensure_first_run_session(st)
        assert _kinds(slot_key).count(sc.KIND_HARNESS_SIGNIN) == 1

    @pytest.mark.asyncio
    async def test_a_finished_script_never_starts_a_turn_on_its_own(self, dispatched, monkeypatch):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _through_script(st, slot_key)
        assert len(dispatched) == 1
        slot = st.slots[slot_key]
        # The restart: the kickoff never got a reply, so Try again is offered.
        slot.messages = [{"role": r, "content": c, "meta": m} for r, c, m in slot.messages] + [
            {"role": "inject", "content": "[First run] ...", "meta": {"injectKind": "first_run"}}
        ]
        appended = []
        monkeypatch.setattr(
            slot, "append", lambda role, content, cls="", **kw: appended.append(kw.get("meta"))
        )
        await setup_flow.ensure_first_run_session(st)
        assert len(dispatched) == 1
        assert appended == [{"kind": "setup_stalled", "reason": "kickoff_failed"}]
        slot.messages.append({"role": "assistant", "content": "x", "meta": appended[0]})
        await setup_flow.ensure_first_run_session(st)
        assert len(appended) == 1  # never posted twice


class TestSignInAgainAfterTheFirstReplyFailed:
    @pytest.mark.asyncio
    async def test_the_sign_in_step_comes_back_and_its_commit_resends_the_kickoff(self, dispatched):
        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _through_script(st, slot_key)
        slot = st.slots[slot_key]
        assert await setup_flow.reopen_signin_after_auth_failure(st, slot) is True
        again = _live(slot_key, sc.KIND_HARNESS_SIGNIN)
        assert setup_flow.scripted_lock(slot_key) == sc.KIND_HARNESS_SIGNIN
        step = [m for _r, _t, m in slot.messages if m and "setupStep" in m][-1]
        assert step["setupStep"]["step"] == "signin_again"
        # Not twice while the step waits.
        assert await setup_flow.reopen_signin_after_auth_failure(st, slot) is False
        await setup_flow.decide(st, again.id, "commit", again.payload_hash, {})
        assert [kind for _k, kind, _t in dispatched] == ["first_run", "first_run"]
        assert _kinds(slot_key).count(sc.KIND_PRIVACY) == 1

    @pytest.mark.asyncio
    async def test_after_the_agent_answered_the_error_row_is_the_whole_signal(
        self, dispatched, monkeypatch
    ):
        from kiro_crew.dashboard import setup_guardrails

        st = FakeState()
        slot_key = await setup_flow.ensure_first_run_session(st)
        await _through_script(st, slot_key)
        monkeypatch.setattr(setup_guardrails, "kickoff_answered", lambda slot: True)
        assert await setup_flow.reopen_signin_after_auth_failure(st, st.slots[slot_key]) is False

    @pytest.mark.asyncio
    async def test_a_chat_without_the_scripted_steps_is_left_alone(self, dispatched):
        st = FakeState()
        st.slots["chat-1-1"] = FakeSlot("chat-1-1")
        first_run.record_slot("chat-1-1")
        assert await setup_flow.reopen_signin_after_auth_failure(st, st.slots["chat-1-1"]) is False
