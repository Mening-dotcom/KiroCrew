"""The setup-card store and its per-kind proposal validation.

A setup card is a promise shown to the owner: its payload is what they approve,
and exactly one claim may move a pending card on. These tests pin the store's
half of that: immutable payloads, hash-bound single claims, tamper detection,
and the argument validation that refuses a proposal before it becomes a card.
"""

from __future__ import annotations

import json

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.first_run import setup_dir


def _make(kind: str = sc.KIND_PROFILE, payload: dict | None = None, slot: str = "chat-1-1"):
    return sc.create_card(
        slot=slot,
        session_key=f"dashboard:{slot}",
        kind=kind,
        payload=payload if payload is not None else {"fields": {"bot_name": "Nova"}},
    )


class TestStore:
    def test_create_get_and_list_round_trip(self):
        card = _make()
        assert sc.valid_card_id(card.id)
        assert sc.get_card(card.id).payload == {"fields": {"bot_name": "Nova"}}
        assert [c.id for c in sc.list_cards("chat-1-1")] == [card.id]
        assert sc.list_cards("chat-9-9") == []

    def test_public_view_never_carries_private_state(self):
        card = sc.create_card(
            slot="s",
            session_key="dashboard:s",
            kind=sc.KIND_CRON,
            payload={"name": "brief"},
            private={"prompt": "the full prompt", "job_id": "abc123"},
        )
        public = json.dumps(sc.get_card(card.id).public())
        assert "the full prompt" not in public
        assert "abc123" not in public
        assert "private" not in public

    def test_claim_requires_the_hash_the_owner_was_shown(self):
        card = _make()
        with pytest.raises(sc.CardRejected) as exc:
            sc.claim_pending(card.id, "0" * 64)
        assert exc.value.code == "card_hash_mismatch"
        assert sc.get_card(card.id).status == sc.STATUS_PENDING

    def test_a_card_is_claimed_once(self):
        card = _make()
        sc.claim_pending(card.id, card.payload_hash)
        with pytest.raises(sc.CardRejected) as exc:
            sc.claim_pending(card.id, card.payload_hash)
        assert exc.value.code == "card_not_pending"

    def test_payload_is_immutable_through_update(self):
        card = _make()

        def _rewrite(c):
            c.payload["fields"]["bot_name"] = "Mallory"

        with pytest.raises(RuntimeError):
            sc.update_card(card.id, _rewrite)
        assert sc.get_card(card.id).payload["fields"]["bot_name"] == "Nova"

    def test_a_payload_edited_on_disk_can_never_be_committed(self):
        card = _make()
        path = setup_dir() / sc.CARDS_FILE
        data = json.loads(path.read_text())
        data["cards"][0]["payload"]["fields"]["bot_name"] = "Mallory"
        path.write_text(json.dumps(data))
        reread = sc.get_card(card.id)
        assert reread.status == sc.STATUS_EXPIRED
        assert reread.error["code"] == "card_tampered"
        with pytest.raises(sc.CardRejected):
            sc.claim_pending(card.id, reread.payload_hash)

    def test_decided_cards_are_pruned_before_pending_ones(self, monkeypatch):
        monkeypatch.setattr(sc, "MAX_STORED_CARDS", 3)
        pending = _make(payload={"fields": {"bot_name": "Keep"}})
        decided = []
        for i in range(3):
            c = _make(payload={"fields": {"bot_name": f"Old{i}"}})
            sc.update_card(c.id, lambda card: setattr(card, "status", sc.STATUS_DECLINED))
            decided.append(c.id)
        remaining = {c.id for c in sc.load_cards()}
        assert pending.id in remaining
        assert len(remaining) == 3

    def test_high_stakes_kinds(self):
        assert _make(kind=sc.KIND_CREDENTIAL, payload={"name": "X"}).stakes == "high"
        assert _make().stakes == "low"

    def test_privacy_is_not_proposable(self):
        assert sc.KIND_PRIVACY not in sc.PROPOSABLE_KINDS

    def test_no_scripted_step_is_proposable(self):
        # The model cannot raise a step that comes before any model can answer.
        assert not set(sc.SCRIPTED_KINDS) & sc.PROPOSABLE_KINDS
        assert sc.SCRIPTED_KINDS == ("harness", "harness_signin", "privacy", "path")


class TestBuilders:
    def test_profile_accepts_known_fields_and_rejects_bad_values(self):
        built = sc.build_profile(
            {"fields": {"bot_name": "Nova", "language": "pt-BR", "timezone": "Europe/Berlin"}}
        )
        assert built == {
            "fields": {"bot_name": "Nova", "language": "pt-BR", "timezone": "Europe/Berlin"}
        }
        for bad in (
            {"fields": {}},
            {"fields": {"bot_name": "{inject}"}},
            {"fields": {"timezone": "Mars/Olympus"}},
            {"fields": {"technical_level": "wizard"}},
            {"fields": {"language": "not a tag"}},
        ):
            with pytest.raises(sc.CardRejected):
                sc.build_profile(bad)

    def test_soul_is_capped(self):
        assert (
            sc.build_soul({"file": "SOUL", "content": "Be brief."}, None)["content"]
            == "Be brief.\n"
        )
        with pytest.raises(sc.CardRejected):
            sc.build_soul({"file": "SOUL", "content": "x" * (sc.SOUL_MAX_CHARS + 1)}, None)
        with pytest.raises(sc.CardRejected):
            sc.build_soul({"file": "AGENTS", "content": "x"}, None)

    def test_cron_keeps_the_full_prompt_private_and_runs_at_most_hourly(self):
        payload, private = sc.build_cron(
            {"name": "Dev brief", "prompt": "p" * 500, "cron_expr": "0 8 * * 1-5"}
        )
        assert payload["schedule_human"] == "weekdays at 08:00"
        assert len(payload["prompt_summary"]) < 500
        assert private == {"prompt": "p" * 500, "schedule": {"cron_expr": "0 8 * * 1-5"}}
        with pytest.raises(sc.CardRejected):
            sc.build_cron({"name": "x", "prompt": "y", "every_secs": 60})
        with pytest.raises(sc.CardRejected):
            sc.build_cron({"name": "x", "prompt": "y", "cron_expr": "nonsense"})
        # The hourly floor holds for cron expressions too, not only every_secs.
        for too_often in ("*/15 * * * *", "0,30 9 * * *", "* * * * *"):
            with pytest.raises(sc.CardRejected, match="at most hourly"):
                sc.build_cron({"name": "x", "prompt": "y", "cron_expr": too_often})
        sc.build_cron({"name": "x", "prompt": "y", "cron_expr": "0 * * * *"})
        with pytest.raises(sc.CardRejected):
            sc.build_cron({"name": "x", "prompt": "y"})

    def test_credential_names_are_upper_snake_and_not_reserved(self):
        assert (
            sc.build_credential({"name": "github_token", "purpose": "PRs"})["name"]
            == "GITHUB_TOKEN"
        )
        with pytest.raises(sc.CardRejected):
            sc.build_credential({"name": "CONNECTIONS_GITHUB_CLIENT_SECRET", "purpose": "x"})
        with pytest.raises(sc.CardRejected):
            sc.build_credential({"name": "BAD NAME", "purpose": "x"})
        with pytest.raises(sc.CardRejected):
            sc.build_credential({"name": "OK_NAME", "purpose": "x", "hosts": ["not a host"]})

    @pytest.mark.parametrize(
        "schedule,expected",
        [
            ({"every_secs": 86400}, "every day"),
            ({"every_secs": 7200}, "every 2 hours"),
            ({"cron_expr": "30 7 * * *"}, "every day at 07:30"),
            ({"cron_expr": "0 17 * * 5"}, "every Friday at 17:00"),
            ({"cron_expr": "*/5 * * * *"}, "on the schedule */5 * * * *"),
        ],
    )
    def test_humanize_schedule(self, schedule, expected):
        assert sc.humanize_schedule(schedule) == expected
