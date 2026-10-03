"""Setup cards: server-side pending actions the user commits by clicking.

The agent PROPOSES a setup action (connect a service, keep a scheduled job, store
a credential, ...) through the ``setup_card`` session directive. The gateway
validates the proposal, builds the card's display payload itself, and stores it
here. Only an owner's click on the rendered card, carrying the payload hash the
owner was shown, commits the action (see ``dashboard/setup_flow.py``). The model
never sees a commit button and cannot forge one: its text is not an input to any
commit, and a changed payload has a different hash and so is a different card.

This module is the pure half: the record, the durable store, and the per-kind
argument validation that needs no dashboard state. It performs blocking file IO;
async callers go through ``asyncio.to_thread``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from kiro_crew.atomic_write import atomic_write
from kiro_crew.first_run import setup_dir
from kiro_crew.platform_compat import file_lock, open_lock_file

logger = logging.getLogger(__name__)

CARDS_FILE = "cards.json"
_LOCK_FILE = "cards.lock"
_STORE_VERSION = 1
#: Cards kept on disk. The oldest DECIDED cards are pruned past this; pending
#: cards are never pruned, because a pending card is a promise shown to a user.
MAX_STORED_CARDS = 400
#: Cards a session may show before its first kept job. Beyond this the agent is
#: interrogating rather than helping, and approval fatigue sets in.
CARD_BUDGET_BEFORE_FIRST_JOB = 8

KIND_PRIVACY = "privacy"
KIND_PROFILE = "profile"
KIND_SOUL = "soul"
KIND_IMPORT = "import"
KIND_CONNECT = "connect"
KIND_CREDENTIAL = "credential"
KIND_CHANNEL = "channel"
KIND_CRON = "cron"
KIND_SERVICE = "service"
KIND_HOME = "home"
#: The first run's scripted steps before any model turn (RFC one-chat first run
#: §5.1): which agent harness runs the crew, its install and sign-in, and how the
#: owner wants to start. The gateway shows each one itself, in this order with the
#: privacy card between the sign-in and the path.
KIND_HARNESS = "harness"
KIND_HARNESS_SIGNIN = "harness_signin"
KIND_PATH = "path"

#: Every card kind the store accepts.
CARD_KINDS: frozenset[str] = frozenset(
    {
        KIND_PRIVACY,
        KIND_PROFILE,
        KIND_SOUL,
        KIND_IMPORT,
        KIND_CONNECT,
        KIND_CREDENTIAL,
        KIND_CHANNEL,
        KIND_CRON,
        KIND_SERVICE,
        KIND_HOME,
        KIND_HARNESS,
        KIND_HARNESS_SIGNIN,
        KIND_PATH,
    }
)
#: Kinds the MODEL may propose. The privacy disclosure is deterministic: the
#: gateway shows it before the first model turn, and a model-authored copy of a
#: disclosure is exactly what must not exist.
PROPOSABLE_KINDS: frozenset[str] = CARD_KINDS - {
    KIND_PRIVACY,
    KIND_HARNESS,
    KIND_HARNESS_SIGNIN,
    KIND_PATH,
}
#: The first run's scripted steps, in the order the gateway shows them. While a
#: card of one of these kinds is live in the first-run chat, no model turn can run
#: there yet, so the chat's composer is locked (``setup_flow.scripted_lock``).
SCRIPTED_KINDS: tuple[str, ...] = (KIND_HARNESS, KIND_HARNESS_SIGNIN, KIND_PRIVACY, KIND_PATH)
#: The two ways the owner can start once the scripted steps are done (UX.3).
PATH_TIPS = "tips"
PATH_DETAILED = "detailed"
PATHS: tuple[str, ...] = (PATH_TIPS, PATH_DETAILED)
#: Kinds whose commit touches a credential, an outside account, or something
#: that keeps running. They render visibly differently and never auto-advance.
HIGH_STAKES_KINDS: frozenset[str] = frozenset(
    {KIND_CONNECT, KIND_CREDENTIAL, KIND_CHANNEL, KIND_SERVICE, KIND_HOME}
)

STATUS_PENDING = "pending"
STATUS_WORKING = "working"
STATUS_WAITING = "waiting"
STATUS_COMMITTED = "committed"
STATUS_DECLINED = "declined"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_COMMITTED, STATUS_DECLINED, STATUS_FAILED, STATUS_EXPIRED}
)

DECISION_COMMIT = "commit"
DECISION_DECLINE = "decline"
DECISION_PREVIEW = "preview"
#: The home card's "Sign in to AWS" (``dashboard/setup_aws_signin.py``).
DECISION_AWS_SIGNIN = "aws_signin"
#: The home card's region picker, shown when no region answers (``setup_flow``).
DECISION_REGION = "region"
#: A failed home card's "Remove what it created", after a restart cut its build short.
DECISION_REMOVE = "remove"
#: The first run's "Where should your crew live?" answer on the home card (``input.where``).
DECISION_CHOOSE = "choose"
#: A scripted step's "Choose a different engine": back to the harness card (``setup_flow``).
DECISION_CHANGE_ENGINE = "change_engine"
DECISIONS: frozenset[str] = frozenset(
    {
        DECISION_COMMIT,
        DECISION_DECLINE,
        DECISION_PREVIEW,
        DECISION_AWS_SIGNIN,
        DECISION_REGION,
        DECISION_REMOVE,
        DECISION_CHOOSE,
        DECISION_CHANGE_ENGINE,
    }
)

#: Largest SOUL.md / USER.md a card may carry. Small by design: the files are
#: injected into every session's context, and the directive channel that carries
#: the proposal is itself bounded.
SOUL_MAX_CHARS = 3000
SOUL_FILES: tuple[str, ...] = ("SOUL", "USER")
_CRON_PROMPT_MAX = 2000
_CRON_NAME_MAX = 80
_CRON_SUMMARY_MAX = 280
#: Shortest recurring interval a proposed job may use. A first-run job that
#: fires more than hourly is noise, and a free harness tier pays for every run.
CRON_MIN_EVERY_SECS = 3600
_PURPOSE_MAX = 200
_MAX_HOSTS = 5
_BOT_NAME_MAX = 40
CHANNELS: dict[str, str] = {"telegram": "Telegram"}
TECHNICAL_LEVELS: frozenset[str] = frozenset({"codes", "somewhat-technical", "non-technical"})
USER_ROLES: frozenset[str] = frozenset(
    {"developer", "designer", "product-manager", "data-ml", "it-ops"}
)

#: Directory under the data home holding the primary agent's SOUL.md / USER.md.
PERSONA_DIR_NAME = "persona"


def persona_path(file: str) -> Path:
    """Where the primary agent's ``SOUL.md`` / ``USER.md`` live."""
    if file not in SOUL_FILES:
        raise ValueError(f"unknown persona file {file!r}")
    from kiro_crew.config.paths import data_home

    return data_home() / PERSONA_DIR_NAME / f"{file}.md"


def read_persona(file: str) -> str | None:
    """The persona file's text (capped), or ``None`` when absent/unreadable."""
    try:
        text = persona_path(file).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        logger.warning("persona file %s unreadable", file, exc_info=True)
        return None
    return text[:SOUL_MAX_CHARS]


_CARD_ID_RE = re.compile(r"^sc-[0-9a-f]{16}$")
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
#: Vault names another subsystem owns; a card must not overwrite them.
_RESERVED_SECRET_PREFIXES: tuple[str, ...] = ("CONNECTIONS_",)
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{1,63})+$")
_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")
_BOT_NAME_RE = re.compile(r"^[\w][\w .'-]{0,39}$", re.UNICODE)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class CardRejected(ValueError):
    """A proposal or decision that cannot become (or change) a card."""

    def __init__(self, message: str, code: str = "card_rejected") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class SetupCard:
    id: str
    slot: str
    session_key: str
    kind: str
    status: str = STATUS_PENDING
    payload: dict[str, Any] = field(default_factory=dict)
    payload_hash: str = ""
    outcome: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    created_ts: float = 0.0
    decided_ts: float | None = None
    #: Server-only state (a job id, a provider URL, a pairing code's expiry).
    #: Never serialized to the browser or the model.
    private: dict[str, Any] = field(default_factory=dict)

    @property
    def stakes(self) -> str:
        return "high" if self.kind in HIGH_STAKES_KINDS else "low"

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def _served_outcome(self) -> dict[str, Any] | None:
        """The outcome as served, with a home's reconnect commands rebuilt.

        The browser shows those commands for the owner to copy into a terminal,
        so they are derived again from the home's tag, region and profile rather
        than read from this store (``cloud.reconnect.commands_for_home``).
        """
        outcome = self.outcome
        if self.kind != KIND_HOME or not isinstance(outcome, dict) or "reconnect" not in outcome:
            return outcome
        from kiro_crew.cloud.reconnect import commands_for_home

        return {**outcome, "reconnect": commands_for_home(outcome.get("home"))}

    def public(self) -> dict[str, Any]:
        """The card as the owner's browser sees it."""
        return {
            "id": self.id,
            "slot": self.slot,
            "kind": self.kind,
            "status": self.status,
            "stakes": self.stakes,
            "hash": self.payload_hash,
            "payload": self.payload,
            "outcome": self._served_outcome(),
            "error": self.error,
            "created_ts": self.created_ts,
            "decided_ts": self.decided_ts,
            "classic": classic_for(self.kind),
            **({"historical": True} if self.private.get("home_handoff_receipt") else {}),
        }


def payload_hash(kind: str, payload: dict[str, Any]) -> str:
    """SHA-256 over the canonical ``{kind, payload}`` the owner is shown."""
    canonical = json.dumps(
        {"kind": kind, "payload": payload}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_card_id() -> str:
    return f"sc-{secrets.token_hex(8)}"


def valid_card_id(card_id: str) -> bool:
    return isinstance(card_id, str) and bool(_CARD_ID_RE.match(card_id))


def classic_for(kind: str) -> dict[str, str]:
    """Where "use classic setup" leads for *kind*."""
    routes = {
        KIND_PROFILE: "/settings",
        KIND_SOUL: "/settings",
        KIND_CONNECT: "/connections",
        KIND_CREDENTIAL: "/settings",
        KIND_CHANNEL: "/settings",
        KIND_CRON: "/schedule",
        KIND_HOME: "/settings",
    }
    if kind == KIND_IMPORT:
        return {"kind": "event", "target": "mc-start-import"}
    if kind in routes:
        return {"kind": "route", "target": routes[kind]}
    return {"kind": "none", "target": ""}


# ── store ────────────────────────────────────────────────────────────────────


def _store_path() -> Path:
    return setup_dir() / CARDS_FILE


def _read_all(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("setup card store at %s is not JSON; starting empty", path)
        return []
    cards = data.get("cards") if isinstance(data, dict) else None
    return [c for c in cards if isinstance(c, dict)] if isinstance(cards, list) else []


def _from_record(rec: dict[str, Any]) -> SetupCard | None:
    try:
        card = SetupCard(
            id=str(rec["id"]),
            slot=str(rec.get("slot", "")),
            session_key=str(rec.get("session_key", "")),
            kind=str(rec["kind"]),
            status=str(rec.get("status", STATUS_PENDING)),
            payload=dict(rec.get("payload") or {}),
            payload_hash=str(rec.get("payload_hash", "")),
            outcome=rec.get("outcome") if isinstance(rec.get("outcome"), dict) else None,
            error=rec.get("error") if isinstance(rec.get("error"), dict) else None,
            created_ts=float(rec.get("created_ts", 0.0)),
            decided_ts=(float(rec["decided_ts"]) if rec.get("decided_ts") is not None else None),
            private=dict(rec.get("private") or {}),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not valid_card_id(card.id) or card.kind not in CARD_KINDS:
        return None
    # A record whose payload does not match its hash was edited on disk; it
    # can never be committed, so it is surfaced as expired rather than trusted.
    if card.payload_hash != payload_hash(card.kind, card.payload) and not card.terminal:
        card.status = STATUS_EXPIRED
        card.error = {"code": "card_tampered", "message": "This card changed on disk."}
    return card


def _write_all(path: Path, cards: list[SetupCard]) -> None:
    if len(cards) > MAX_STORED_CARDS:
        decided = [c for c in cards if c.terminal]
        decided.sort(key=lambda c: c.created_ts)
        drop = {c.id for c in decided[: len(cards) - MAX_STORED_CARDS]}
        cards = [c for c in cards if c.id not in drop]
    body = {"version": _STORE_VERSION, "cards": [asdict(c) for c in cards]}
    atomic_write(path, json.dumps(body, sort_keys=True) + "\n", mode=0o600)


@contextmanager
def _locked() -> Iterator[list[SetupCard]]:
    """Hold the exclusive store lock for a read-modify-write of every card."""
    with open_lock_file(setup_dir() / _LOCK_FILE) as fd, file_lock(fd, exclusive=True):
        yield [c for c in (_from_record(r) for r in _read_all(_store_path())) if c]


def load_cards() -> list[SetupCard]:
    """Every stored card, oldest first."""
    cards = [c for c in (_from_record(r) for r in _read_all(_store_path())) if c]
    cards.sort(key=lambda c: c.created_ts)
    return cards


def get_card(card_id: str) -> SetupCard | None:
    if not valid_card_id(card_id):
        return None
    for card in load_cards():
        if card.id == card_id:
            return card
    return None


def list_cards(slot: str) -> list[SetupCard]:
    return [c for c in load_cards() if c.slot == slot]


def create_card(
    *,
    slot: str,
    session_key: str,
    kind: str,
    payload: dict[str, Any],
    private: dict[str, Any] | None = None,
) -> SetupCard:
    if kind not in CARD_KINDS:
        raise CardRejected(f"unknown setup card kind {kind!r}", "unknown_kind")
    card = SetupCard(
        id=new_card_id(),
        slot=slot,
        session_key=session_key,
        kind=kind,
        payload=payload,
        payload_hash=payload_hash(kind, payload),
        created_ts=time.time(),
        private=dict(private or {}),
    )
    with _locked() as cards:
        cards.append(card)
        _write_all(_store_path(), cards)
    return card


def update_card(card_id: str, mutate: Callable[[SetupCard], None]) -> SetupCard:
    """Apply *mutate* to the stored card under the store lock and return it.

    *mutate* may change status, outcome, error, private and decided_ts. It must
    not change the payload: the payload is what the owner approved, so a card
    whose display changes is a different card.
    """
    with _locked() as cards:
        for card in cards:
            if card.id != card_id:
                continue
            before = (card.kind, json.dumps(card.payload, sort_keys=True), card.payload_hash)
            mutate(card)
            after = (card.kind, json.dumps(card.payload, sort_keys=True), card.payload_hash)
            if before != after:
                raise RuntimeError("a setup card's payload is immutable once shown")
            _write_all(_store_path(), cards)
            return card
    raise CardRejected("setup card not found", "card_not_found")


def replace_payload(
    card_id: str, payload: dict[str, Any], *, settings: dict[str, Any] | None = None
) -> SetupCard:
    """Show a PENDING card a new payload, under a new hash; returns the card.

    The one sanctioned change to what a card shows, for facts the gateway only
    learns later (the home card's region, plan and sizes after the owner signs in
    to AWS from it). A click carrying the old hash is refused as
    ``card_hash_mismatch``, so nothing commits against a face the owner no
    longer sees. *settings* replaces ``private["settings"]`` in the same write.
    """
    with _locked() as cards:
        for card in cards:
            if card.id != card_id:
                continue
            if card.status != STATUS_PENDING:
                raise CardRejected("this card is not waiting for a decision", "card_not_pending")
            card.payload = dict(payload)
            card.payload_hash = payload_hash(card.kind, card.payload)
            if settings is not None:
                card.private["settings"] = dict(settings)
            _write_all(_store_path(), cards)
            return card
    raise CardRejected("setup card not found", "card_not_found")


def claim_pending(card_id: str, card_hash: str, *, to_status: str = STATUS_WORKING) -> SetupCard:
    """Move a PENDING card whose hash matches *card_hash* to *to_status*.

    The single transition that admits a commit: it refuses a card that is not
    pending (a double click, a replay, a decided card) and a hash the owner was
    not shown, atomically, under the store lock.
    """
    import hmac

    def _claim(card: SetupCard) -> None:
        if card.status != STATUS_PENDING:
            raise CardRejected("this card is not waiting for a decision", "card_not_pending")
        if not hmac.compare_digest(str(card_hash), card.payload_hash):
            raise CardRejected("this card changed since it was shown", "card_hash_mismatch")
        card.status = to_status
        card.error = None

    return update_card(card_id, _claim)


def count_cards(slot: str) -> int:
    return len(list_cards(slot))


def slot_has_kept_job(slot: str) -> bool:
    return any(c.kind == KIND_CRON and c.status == STATUS_COMMITTED for c in list_cards(slot))


# ── a card's state in words (the crew overview and ``setup_status``) ─────────


def plain_title(text: Any, limit: int = 60) -> str:
    """A title safe to quote inside a context block: no brackets, one line, bounded."""
    out = " ".join(str(text or "").split()).replace("[", "(").replace("]", ")")
    return out if len(out) <= limit else out[: limit - 1] + "…"


def card_state_words(card: SetupCard) -> str:
    """A card's status in words the model reads correctly.

    The raw ``waiting`` means the card's own work is running (a build, a
    sign-in, a preview), not that the user owes it an answer; printed as is,
    the main chat told the user a home that was building still awaited them.
    """
    if card.status == STATUS_PENDING:
        return "waiting for the user's decision"
    if card.status in (STATUS_WAITING, STATUS_WORKING):
        return "in progress, nothing needed from the user"
    return card.status


def home_state_words(card: SetupCard) -> str:
    """Where a home card stands, in the same plain words as :func:`card_state_words`."""
    outcome = card.outcome or {}
    if outcome.get("moved"):
        if outcome.get("arrived"):
            return "this home; signed in to Kiro; move complete"
        return "moved in"
    if outcome.get("ready"):
        return "ready to move in (the card offers Move in)"
    if outcome.get("needs_signin"):
        return "built, waiting for the user to sign it in to Kiro on its card"
    if outcome.get("stayed"):
        return "not wanted: the user keeps the crew on this machine"
    if card.status in (STATUS_WAITING, STATUS_WORKING):
        steps = [s for s in outcome.get("steps") or [] if isinstance(s, dict)]
        active = next((s for s in steps if s.get("state") == "active"), None)
        where = f": {plain_title(str(active.get('label') or ''))}" if active else ""
        return f"building in the background{where}; nothing needed from the user"
    if card.status == STATUS_PENDING:
        return "waiting for the user's decision on its card"
    return card.status


# ── per-kind argument validation (no dashboard state needed) ─────────────────


def _str_arg(args: dict[str, Any], key: str, *, max_len: int, required: bool = True) -> str:
    value = args.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise CardRejected(f"{key} must be a string", "invalid_argument")
    value = value.strip()
    if required and not value:
        raise CardRejected(f"{key} is required", "invalid_argument")
    if len(value) > max_len:
        raise CardRejected(f"{key} is longer than {max_len} characters", "invalid_argument")
    return value


def build_profile(args: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, str] = {}
    raw: dict[str, Any] = args["fields"] if isinstance(args.get("fields"), dict) else args
    bot_name = raw.get("bot_name")
    if bot_name:
        name = _str_arg({"v": bot_name}, "v", max_len=_BOT_NAME_MAX)
        if not _BOT_NAME_RE.match(name) or "{" in name or "}" in name:
            raise CardRejected("bot_name has characters a name cannot use", "invalid_argument")
        fields["bot_name"] = name
    language = raw.get("language")
    if language:
        if not isinstance(language, str) or not _LANGUAGE_TAG_RE.match(language.strip()):
            raise CardRejected(
                "language must be a BCP-47 tag such as 'en' or 'pt-BR'", "invalid_argument"
            )
        fields["language"] = language.strip()
    timezone = raw.get("timezone")
    if timezone:
        fields["timezone"] = _validate_timezone(timezone)
    level = raw.get("technical_level")
    if level:
        if level not in TECHNICAL_LEVELS:
            raise CardRejected(
                "technical_level must be one of " + ", ".join(sorted(TECHNICAL_LEVELS)),
                "invalid_argument",
            )
        fields["technical_level"] = level
    role = raw.get("role")
    if role:
        if role not in USER_ROLES:
            raise CardRejected(
                "role must be one of " + ", ".join(sorted(USER_ROLES)), "invalid_argument"
            )
        fields["role"] = role
    if not fields:
        raise CardRejected("a profile card needs at least one field", "invalid_argument")
    return {"fields": fields}


def _validate_timezone(value: Any) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise CardRejected(
            "timezone must be an IANA name such as 'Europe/Berlin'", "invalid_argument"
        )
    name = value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CardRejected(f"unknown timezone {name!r}", "invalid_argument") from None
    return name


def build_soul(args: dict[str, Any], previous: str | None) -> dict[str, Any]:
    file = args.get("file", "SOUL")
    if file not in SOUL_FILES:
        raise CardRejected("file must be SOUL or USER", "invalid_argument")
    content = args.get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise CardRejected("content is required", "invalid_argument")
    content = content.strip() + "\n"
    if len(content) > SOUL_MAX_CHARS:
        raise CardRejected(
            f"{file}.md is capped at {SOUL_MAX_CHARS} characters; keep it light", "invalid_argument"
        )
    return {"file": file, "content": content, "previous": previous}


#: How many upcoming fires the frequency check looks at: enough to cover a week
#: of an hourly-or-slower schedule's shortest gap.
_CRON_GAP_SAMPLES = 200


def _cron_min_gap_secs(expr: str) -> float:
    """The shortest gap between consecutive fires of *expr*, from a fixed Monday."""
    from datetime import datetime

    from croniter import croniter  # type: ignore[import-untyped]

    it = croniter(expr, datetime(2026, 1, 5, 0, 0))
    prev = it.get_next(float)
    shortest = float("inf")
    for _ in range(_CRON_GAP_SAMPLES):
        nxt = it.get_next(float)
        shortest = min(shortest, nxt - prev)
        prev = nxt
    return shortest


def build_cron(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a job proposal; return ``(payload, private)``.

    The full prompt stays in *private*: the card shows a summary, and the job is
    created from the private copy, which never changes after the card is shown.
    """
    name = _str_arg(args, "name", max_len=_CRON_NAME_MAX)
    prompt = _str_arg(args, "prompt", max_len=_CRON_PROMPT_MAX)
    timezone = ""
    if args.get("timezone"):
        timezone = _validate_timezone(args["timezone"])
    cron_expr: Any = args.get("cron_expr") or ""
    every_secs: Any = args.get("every_secs")
    if bool(cron_expr) == bool(every_secs):
        raise CardRejected("give exactly one of cron_expr or every_secs", "invalid_argument")
    schedule: dict[str, Any]
    if cron_expr:
        if not isinstance(cron_expr, str) or len(cron_expr) > 100:
            raise CardRejected("cron_expr must be a 5-field cron expression", "invalid_argument")
        from croniter import croniter  # type: ignore[import-untyped]

        if len(cron_expr.split()) != 5 or not croniter.is_valid(cron_expr):
            raise CardRejected(f"invalid cron expression {cron_expr!r}", "invalid_argument")
        if _cron_min_gap_secs(cron_expr.strip()) < CRON_MIN_EVERY_SECS:
            raise CardRejected(
                "a proposed job runs at most hourly; this cron expression fires more often",
                "invalid_argument",
            )
        schedule = {"cron_expr": cron_expr.strip()}
    else:
        try:
            secs = int(every_secs)
        except (TypeError, ValueError):
            raise CardRejected("every_secs must be an integer", "invalid_argument") from None
        if secs < CRON_MIN_EVERY_SECS:
            raise CardRejected(
                f"a proposed job runs at most hourly (every_secs >= {CRON_MIN_EVERY_SECS})",
                "invalid_argument",
            )
        schedule = {"every_secs": secs}
    summary = prompt if len(prompt) <= _CRON_SUMMARY_MAX else prompt[: _CRON_SUMMARY_MAX - 1] + "…"
    payload = {
        "name": name,
        "prompt_summary": summary,
        "schedule_human": humanize_schedule(schedule),
        "timezone": timezone,
    }
    private = {"prompt": prompt, "schedule": schedule}
    return payload, private


def humanize_schedule(schedule: dict[str, Any]) -> str:
    """A short English description of a schedule, for the card and the model."""
    if "every_secs" in schedule:
        secs = int(schedule["every_secs"])
        if secs % 86400 == 0:
            days = secs // 86400
            return "every day" if days == 1 else f"every {days} days"
        if secs % 3600 == 0:
            hours = secs // 3600
            return "every hour" if hours == 1 else f"every {hours} hours"
        return f"every {secs // 60} minutes"
    expr = str(schedule.get("cron_expr", ""))
    parts = expr.split()
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit() and parts[2:4] == ["*", "*"]:
        at = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
        dow = parts[4]
        if dow == "*":
            return f"every day at {at}"
        if dow in ("1-5", "MON-FRI", "mon-fri"):
            return f"weekdays at {at}"
        names = {
            "0": "Sunday",
            "1": "Monday",
            "2": "Tuesday",
            "3": "Wednesday",
            "4": "Thursday",
            "5": "Friday",
            "6": "Saturday",
            "7": "Sunday",
        }
        if dow in names:
            return f"every {names[dow]} at {at}"
    return f"on the schedule {expr}"


def build_credential(args: dict[str, Any]) -> dict[str, Any]:
    name = _str_arg(args, "name", max_len=64).upper()
    if not _SECRET_NAME_RE.match(name):
        raise CardRejected("name must be UPPER_SNAKE_CASE, e.g. GITHUB_TOKEN", "invalid_argument")
    if name.startswith(_RESERVED_SECRET_PREFIXES):
        raise CardRejected(
            f"{name} is managed elsewhere and cannot be set here", "invalid_argument"
        )
    purpose = _str_arg(args, "purpose", max_len=_PURPOSE_MAX)
    hosts_raw = args.get("hosts") or []
    if not isinstance(hosts_raw, list) or len(hosts_raw) > _MAX_HOSTS:
        raise CardRejected(
            f"hosts must be a list of at most {_MAX_HOSTS} hostnames", "invalid_argument"
        )
    hosts: list[str] = []
    for host in hosts_raw:
        if not isinstance(host, str) or not _HOST_RE.match(host.strip().lower()):
            raise CardRejected(f"invalid hostname {host!r}", "invalid_argument")
        hosts.append(host.strip().lower())
    return {"name": name, "purpose": purpose, "hosts": hosts}


def build_channel(args: dict[str, Any]) -> dict[str, Any]:
    channel = args.get("channel", "")
    if channel not in CHANNELS:
        raise CardRejected(
            "channel must be one of " + ", ".join(sorted(CHANNELS)), "invalid_argument"
        )
    return {"channel": channel, "label": CHANNELS[channel]}


#: The size a home card carries when it offers no size options (its single size);
#: which sizes are offered, and which is preselected, is ``HOME_PLAN_SIZES``.
HOME_DEFAULT_SIZE = "light"
#: What a home card says about each size it can offer: a plain label, and a note
#: code for what the size runs well (the dashboard words each code).
HOME_SIZE_OFFERS: dict[str, dict[str, str]] = {
    "lite": {"label": "Lite", "note": "lite_tradeoffs"},
    "economy": {"label": "Economy", "note": "all_on"},
    "starter": {"label": "Starter", "note": "free_plan_credits"},
    "small": {"label": "Small", "note": "few_chats"},
    "light": {"label": "Standard", "note": "many_chats"},
}
#: The sizes each AWS plan is offered, and the one preselected. A Free-plan size
#: (``free_plan_ok``) joins the paid plan's list only when it is no dearer than
#: the paid plan's default. A plan not known yet (not signed in, or unreadable)
#: gets the Free plan's list: a new account starts on it, and Starter builds on
#: every plan. Adding a size is a tier in ``cloud/sizes.py`` plus an entry here.
HOME_PLAN_SIZES: dict[str, tuple[tuple[str, ...], str]] = {
    "FREE": (("lite", "starter", "light"), "starter"),
    "PAID": (("lite", "economy", "small", "light", "starter"), "small"),
}
_HOME_PLAN_NOT_KNOWN = "FREE"
_WEEKS_PER_MONTH = 52 / 12
#: The region a home is built in when neither the card nor the AWS profile names one.
HOME_DEFAULT_REGION = "us-east-1"
#: Hours in an average month, for turning an hourly price into a monthly one.
_HOURS_PER_MONTH = 730
_REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d$")
_PROFILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def build_home(args: dict[str, Any]) -> dict[str, Any]:
    """Validate a home proposal's region/profile/size and return its settings."""
    from kiro_crew.cloud.sizes import TIERS_BY_KEY

    region = str(args.get("region") or HOME_DEFAULT_REGION).strip()
    if not _REGION_RE.match(region):
        raise CardRejected(f"invalid AWS region {region!r}", "invalid_argument")
    profile = str(args.get("profile") or "").strip()
    if profile and not _PROFILE_RE.match(profile):
        raise CardRejected(f"invalid AWS profile name {profile!r}", "invalid_argument")
    size = str(args.get("size") or HOME_DEFAULT_SIZE).strip()
    if size not in TIERS_BY_KEY:
        raise CardRejected(
            "size must be one of " + ", ".join(sorted(TIERS_BY_KEY)), "invalid_argument"
        )
    return {"region": region, "profile": profile, "size": size}


def validate_home_region(region: Any) -> str:
    """The owner's pick of a home's region, checked on the server.

    It must have the shape :func:`build_home` accepts and be one of the regions a
    home can be built in (``cloud.local_signin.HOME_REGIONS``), the list the
    card's picker shows.
    """
    from kiro_crew.cloud.local_signin import HOME_REGIONS

    value = region.strip() if isinstance(region, str) else ""
    if not _REGION_RE.match(value) or value not in HOME_REGIONS:
        raise CardRejected("choose one of the regions on the card", "home_region_not_offered")
    return value


def monthly_estimate_usd(size_key: str, region: str = "") -> int:
    """A rounded monthly on-demand estimate for *size_key* in *region*: instance plus disk.

    Priced from ``cloud/sizes.py``'s per-region table; a region not in it gets the
    us-east-1 figure, which the card shows as "about" like every other.
    """
    from kiro_crew.cloud.sizes import get_tier, region_prices

    tier = get_tier(size_key)
    hourly, gp3, _where = region_prices(tier, region)
    return int(round(hourly * _HOURS_PER_MONTH + tier.disk_gb * gp3))


def home_size_options(
    plan: dict[str, Any] | None, region: str = ""
) -> tuple[list[dict[str, Any]], str]:
    """The size options a home card offers, cheapest first, and the one preselected.

    *plan* is the account's AWS plan (``cloud.local_signin.account_plan``), or
    ``None`` when not signed in; which sizes each plan gets is
    :data:`HOME_PLAN_SIZES`. Every price is *region*'s, so whether a Free-plan
    size is dearer than the paid plan's default is decided there. A Free-plan size
    also carries how many weeks the plan's remaining credits pay for it.
    """
    from kiro_crew.cloud.sizes import get_tier

    plan_type = str((plan or {}).get("type") or "")
    keys, default = HOME_PLAN_SIZES.get(plan_type, HOME_PLAN_SIZES[_HOME_PLAN_NOT_KNOWN])
    credits = (plan or {}).get("credits_usd")
    ceiling = monthly_estimate_usd(default, region)
    options: list[dict[str, Any]] = []
    for key in keys:
        tier = get_tier(key)
        monthly = monthly_estimate_usd(key, region)
        if plan_type == "PAID" and tier.free_plan_ok and key != default and monthly > ceiling:
            continue
        option: dict[str, Any] = {
            "key": key,
            "label": HOME_SIZE_OFFERS[key]["label"],
            "note": HOME_SIZE_OFFERS[key]["note"],
            "instance_type": tier.instance_type,
            "vcpu": tier.vcpu,
            "ram_gb": tier.ram_gb,
            "monthly_usd": monthly,
            "free_plan_ok": tier.free_plan_ok,
        }
        if tier.free_plan_ok and isinstance(credits, (int, float)) and credits > 0 and monthly:
            option["credits_usd"] = credits
            option["credit_weeks"] = max(1, int(credits / (monthly / _WEEKS_PER_MONTH)))
        options.append(option)
    options.sort(key=lambda o: (o["monthly_usd"], o["key"]))
    return options, default


def validate_slug(slug: Any) -> str:
    if not isinstance(slug, str) or not _SLUG_RE.match(slug.strip().lower()):
        raise CardRejected("provider must be a registry slug such as 'github'", "invalid_argument")
    return slug.strip().lower()
