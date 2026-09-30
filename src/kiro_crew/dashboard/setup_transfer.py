"""The display history and inert card receipts of a first-run home handoff.

Ordinary session copies still carry speech only. A home handoff additionally
carries the setup decisions the owner saw, from the same transcript snapshot.
Receipts live in the card store so transcript text cannot supply active buttons.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

from kiro_crew import setup_cards as sc
from kiro_crew.security import redact_credentials, redact_exfiltration_urls

VERSION = 1
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 5000
RECEIPT_KEY = "home_handoff_receipt"
_ROLES = frozenset({"user", "assistant", "inject", "system", "tool"})
_META_KEYS = frozenset(
    {
        "mid",
        "setupCard",
        "injectKind",
        "kind",
        "opened",
        "resolved",
        "card",
        "turn_stats",
        "tool_call_id",
        "purpose",
        "input",
        "tool_name",
        "mcp_server",
        "done",
        "output",
    }
)


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        raise ValueError("setup handoff is nested too deeply")
    if isinstance(value, str):
        value, _ = redact_exfiltration_urls(value)
        return redact_credentials(value)[0]
    if isinstance(value, list):
        return [_scrub(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {_scrub(k, depth + 1): _scrub(v, depth + 1) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("invalid setup handoff value")


def build(history: list[dict], slot_key: str) -> dict[str, Any]:
    """Read card faces off-loop beside the guarded transcript snapshot."""
    ids = {
        m.get("meta", {}).get("setupCard", {}).get("id")
        for m in history
        if isinstance(m.get("meta"), dict) and isinstance(m["meta"].get("setupCard"), dict)
    }
    cards = [c.public() for c in sc.list_cards(slot_key) if c.id in ids]
    return validate({"version": VERSION, "rows": history, "cards": cards})


def validate(raw: Any) -> dict[str, Any]:
    """Bound and redact presentation data on both sides of the tunnel."""
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise ValueError("unsupported setup handoff")
    if len(json.dumps(raw, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError("setup handoff is too large")
    raw_rows, raw_cards = raw.get("rows"), raw.get("cards")
    if not isinstance(raw_rows, list) or not 0 < len(raw_rows) <= MAX_ROWS:
        raise ValueError("invalid setup history")
    if not isinstance(raw_cards, list) or len(raw_cards) > sc.MAX_STORED_CARDS:
        raise ValueError("invalid setup receipts")
    cards = []
    ids: set[str] = set()
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            raise ValueError("invalid setup receipt")
        cid, kind = str(raw_card.get("id") or ""), raw_card.get("kind")
        if not sc.valid_card_id(cid) or cid in ids or kind not in sc.CARD_KINDS:
            raise ValueError("invalid setup receipt identity")
        if not isinstance(raw_card.get("payload"), dict):
            raise ValueError("invalid setup receipt payload")
        status = raw_card.get("status")
        if status not in sc.TERMINAL_STATUSES | {
            sc.STATUS_PENDING,
            sc.STATUS_WORKING,
            sc.STATUS_WAITING,
        }:
            raise ValueError("invalid setup receipt status")
        card = _scrub(
            {
                k: raw_card.get(k)
                for k in (
                    "id",
                    "kind",
                    "status",
                    "payload",
                    "outcome",
                    "error",
                    "created_ts",
                    "decided_ts",
                )
            }
        )
        for field in ("outcome", "error"):
            if card[field] is not None and not isinstance(card[field], dict):
                raise ValueError("invalid setup receipt result")
        for field in ("created_ts", "decided_ts"):
            if card[field] is not None and (
                isinstance(card[field], bool) or not isinstance(card[field], (int, float))
            ):
                raise ValueError("invalid setup receipt time")
        # A device sign-in and its code are live host state, never history.
        if kind == sc.KIND_HOME:
            card["outcome"] = {
                k: v
                for k, v in (card["outcome"] or {}).items()
                if k not in {"signin", "aws_signin", "auto_move"}
            }
        ids.add(cid)
        cards.append(card)
    rows = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict) or raw_row.get("role") not in _ROLES:
            raise ValueError("invalid setup history role")
        content, ts = raw_row.get("content", ""), raw_row.get("ts", "")
        if not isinstance(content, str) or not isinstance(ts, str):
            raise ValueError("invalid setup history row")
        meta = raw_row.get("meta") or {}
        if not isinstance(meta, dict):
            raise ValueError("invalid setup history metadata")
        row: dict[str, Any] = {"role": raw_row["role"], "content": content, "ts": ts}
        if row["role"] != "user":
            row["content"] = _scrub(content)
        meta = _scrub({k: v for k, v in meta.items() if k in _META_KEYS})
        ref = meta.get("setupCard")
        if ref is not None and (not isinstance(ref, dict) or ref.get("id") not in ids):
            raise ValueError("setup card receipt is missing")
        if meta:
            row["meta"] = meta
        rows.append(row)
    return {"version": VERSION, "rows": rows, "cards": cards}


def remap(handoff: dict) -> tuple[list[dict], list[dict]]:
    """Give receipts new identities without modifying the validated snapshot."""
    data = copy.deepcopy(handoff)
    ids = {c["id"]: sc.new_card_id() for c in data["cards"]}
    for card in data["cards"]:
        card["id"] = ids[card["id"]]
    for row in data["rows"]:
        meta = row.get("meta", {})
        if isinstance(meta.get("setupCard"), dict):
            meta["setupCard"]["id"] = ids[meta["setupCard"]["id"]]
        if meta.get("card") in ids:
            meta["card"] = ids[meta["card"]]
        if row["role"] == "assistant" and meta.get("kind") == "home_signin":
            meta["resolved"] = True
            row["content"] = (
                "Your cloud home is signed in to Kiro. Continue this conversation here."
            )
    return data["rows"], data["cards"]


def install_receipts(slot_key: str, receipts: list[dict]) -> None:
    """Install inert, terminal receipts; no source authority or private state."""
    with sc._locked() as cards:
        for rec in receipts:
            status = rec["status"]
            cards.append(
                sc.SetupCard(
                    id=rec["id"],
                    slot=slot_key,
                    session_key=f"dashboard:{slot_key}",
                    kind=rec["kind"],
                    status=status if status in sc.TERMINAL_STATUSES else sc.STATUS_EXPIRED,
                    payload=rec["payload"],
                    payload_hash=sc.payload_hash(rec["kind"], rec["payload"]),
                    outcome=rec["outcome"],
                    error=rec["error"],
                    created_ts=rec["created_ts"] or 0.0,
                    decided_ts=rec["decided_ts"],
                    private={RECEIPT_KEY: True},
                )
            )
        sc._write_all(sc._store_path(), cards)


def remove_receipts(receipts: list[dict]) -> None:
    ids = {c["id"] for c in receipts}
    with sc._locked() as cards:
        sc._write_all(sc._store_path(), [c for c in cards if c.id not in ids])


def read_preferences() -> dict[str, Any]:
    """Only setup's personal preferences cross; runtime/security config stays local."""
    from kiro_crew.config.loader import config_path

    try:
        config = json.loads(config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    dash, agent = config.get("dashboard", {}), config.get("agent", {})
    fields = {
        "bot_name": agent.get("bot_name") or dash.get("bot_name"),
        "language": dash.get("language"),
        "timezone": config.get("timezone"),
        "technical_level": dash.get("user_technical_level"),
        "role": dash.get("user_role"),
    }
    persona = {}
    for name in sc.SOUL_FILES:
        try:
            persona[name] = sc.persona_path(name).read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
    return validate_preferences(
        {"fields": {k: v for k, v in fields.items() if v}, "persona": persona}
    )


def validate_preferences(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("fields", {}), dict):
        raise ValueError("invalid setup preferences")
    raw_fields = raw.get("fields", {})
    fields = sc.build_profile({"fields": raw_fields})["fields"] if raw_fields else {}
    persona = raw.get("persona", {})
    if not isinstance(persona, dict) or any(
        name not in sc.SOUL_FILES or not isinstance(value, str) or len(value) > 64_000
        for name, value in persona.items()
    ):
        raise ValueError("invalid setup persona")
    return {"fields": fields, "persona": persona}


async def apply_preferences(preferences: dict) -> None:
    import asyncio

    from kiro_crew.atomic_write import atomic_write
    from kiro_crew.dashboard.setup_flow import _update_config

    fields = preferences["fields"]

    def mutate(data: dict) -> None:
        dash = data.setdefault("dashboard", {})
        if "bot_name" in fields:
            data.setdefault("agent", {})["bot_name"] = fields["bot_name"]
            dash["bot_name"] = fields["bot_name"]
        for source, target in (
            ("language", "language"),
            ("technical_level", "user_technical_level"),
            ("role", "user_role"),
        ):
            if source in fields:
                dash[target] = fields[source]
        if "timezone" in fields:
            data["timezone"] = fields["timezone"]

    if fields:
        await _update_config(mutate)
    for name, content in preferences["persona"].items():
        await asyncio.to_thread(atomic_write, sc.persona_path(name), content, mode=0o600)


def complete_receipts(slot_key: str, outcome: dict) -> None:
    """Resolve the carried home card only after the archive and preferences land."""
    cards = [
        c for c in sc.list_cards(slot_key) if c.private.get(RECEIPT_KEY) and c.kind == sc.KIND_HOME
    ]
    if cards:

        def complete(card: sc.SetupCard) -> None:
            card.status = sc.STATUS_COMMITTED
            card.error = None
            card.outcome = {**outcome, "moved": True, "arrived": True, "settings_moved": True}

        sc.update_card(cards[-1].id, complete)
