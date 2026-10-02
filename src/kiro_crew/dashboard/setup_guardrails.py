"""Guardrails for the one-chat first run: the stall watchdog and the quota notice.

Each guardrail is one deterministic system notice in the first-run chat (RFC
one-chat first run, §6.3 "Stall watchdog" and §6.7 "Quota awareness"). The rows
carry a kind from :data:`~kiro_crew.dashboard.system_notices.SYSTEM_NOTICE_KINDS`;
their English content is the fallback for readers without the dashboard catalog,
and the dashboard draws localized copy keyed on ``meta.kind`` and ``meta.reason``.

* **Stall.** A first-run turn whose progress markers have not moved for
  :data:`FIRST_RUN_STALL_SECS`, with no wait to explain the silence, gets ONE
  ``setup_stalled`` notice (``reason: no_output``). The verdict is the
  session-health classifier's (:mod:`kiro_crew.dashboard.session_health`): the
  same markers and wait reasons ``GET /api/sessions/health`` reports, sampled on
  a shorter window. This module adds no progress signal of its own. The ACP
  layer ends a silent turn only after text has streamed (its stale-turn cutoff)
  or while a tool call is open (its tool-stall cutoff); a turn that has produced
  nothing at all is otherwise bounded only by the hours-long turn ceiling, and
  that is the endless spinner this notice replaces.
* **Kickoff.** A ``[First run]`` kickoff turn that ends without a reply, or that
  cannot be dispatched at all, gets a ``setup_stalled`` notice with
  ``reason: kickoff_failed``. ``POST /api/setup/first-run/retry`` sends it again.
* **Quota.** A first-run turn that ends on a spent plan allowance (the
  ``usage_limit`` error row, tagged by ``chat_runner._terminal_error_meta`` from
  the raw provider frame, never from prose) gets ONE ``setup_quota`` notice per
  episode, and :func:`quota_paused` makes ``setup_flow.propose`` refuse new cards
  until a turn in that chat lands a reply.

Presentation and pacing only. The pause lives in memory and can only make a
proposal refuse, never admit one. The first-run state file chooses WHICH chat is
watched, the same way it chooses which chat opens (SC6).
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from typing import TYPE_CHECKING, Any

from kiro_crew.dashboard.system_notices import (
    SETUP_QUOTA_KIND,
    SETUP_STALLED_KIND,
    is_speech_row,
)

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState, _ChatSlot

logger = logging.getLogger(__name__)

#: A first-run turn whose progress markers have not moved for this long, with
#: nothing to wait on, is stalled. The ACP stale-turn cutoff for a turn that did
#: stream, applied to one that never did.
FIRST_RUN_STALL_SECS = 90.0
#: How often a watched turn is sampled.
_WATCH_POLL_SECS = 5.0

#: ``meta.reason`` of a ``setup_stalled`` notice.
STALL_REASON_NO_OUTPUT = "no_output"
STALL_REASON_KICKOFF_FAILED = "kickoff_failed"

#: Each gateway state's first-run chat, as ``setup_flow`` last saw it. Lets
#: :func:`watch_turn` skip every other chat without touching the disk; the
#: watcher re-reads the state file before it acts. Weak, so a state that is
#: gone takes its entry with it.
_first_run_slots: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()
#: First-run chats whose kickoff has been sent and not yet answered.
_kickoff_open: set[str] = set()
#: First-run chats whose last turn ended on a spent allowance.
_quota_paused: set[str] = set()
#: First-run chats with a kickoff retry in flight, so a double click sends one.
_retrying: set[str] = set()
#: Watcher tasks, held so they are not garbage collected mid-flight.
_tasks: set[asyncio.Task[Any]] = set()


def track(state: "DashboardState", slot_key: str) -> None:
    """Record *slot_key* as *state*'s first-run chat, the one chat watched."""
    try:
        _first_run_slots[state] = slot_key
    except TypeError:
        logger.debug("state %r cannot be tracked for first-run guardrails", state)


def expect_kickoff(state: "DashboardState", slot_key: str) -> None:
    """Record that the first-run kickoff was sent to *slot_key* and awaits a reply."""
    track(state, slot_key)
    _kickoff_open.add(slot_key)


def quota_paused(slot_key: str) -> bool:
    """Whether setup cards are paused in *slot_key* after a spent allowance."""
    return slot_key in _quota_paused


def watch_turn(state: "DashboardState", slot: "_ChatSlot") -> None:
    """Watch the top-level turn running in the current task.

    Called at the start of every top-level dashboard turn; returns at once for
    any chat that is not the first-run chat.
    """
    try:
        tracked = _first_run_slots.get(state)
    except TypeError:  # a state that cannot be weakly referenced was never tracked
        return
    if tracked is None or tracked != getattr(slot, "key", None):
        return
    try:
        turn = asyncio.current_task()
        if turn is None:
            return
        messages = getattr(slot, "messages", None) or []
        start_row = messages[-1] if messages else None
        task = asyncio.create_task(_watch(state, slot, turn, start_row))
    except Exception:
        logger.warning("first-run turn watch failed to start", exc_info=True)
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _is_setup_chat(slot_key: str) -> bool:
    """Whether *slot_key* is the first-run chat and has not become the main chat."""
    from kiro_crew.first_run import read_state

    data = read_state()
    return data.get("slot") == slot_key and data.get("main") != slot_key


async def _watch(
    state: "DashboardState",
    slot: "_ChatSlot",
    turn: "asyncio.Task[Any]",
    start_row: dict[str, Any] | None,
) -> None:
    from kiro_crew.dashboard.session_health import (
        HEALTH_STALLED,
        SessionHealthMonitor,
        snapshot_state,
    )

    try:
        if not await asyncio.to_thread(_is_setup_chat, slot.key):
            return
        monitor = SessionHealthMonitor(
            stall_after_secs=FIRST_RUN_STALL_SECS, include_log_scan=False
        )
        noticed = False
        while not turn.done():
            await asyncio.wait({turn}, timeout=_WATCH_POLL_SECS)
            if turn.done() or noticed:
                continue
            mono = time.monotonic()
            snap = next(
                (s for s in snapshot_state(state, mono_now=mono).slots if s.key == slot.key),
                None,
            )
            health = monitor.classify_slot(snap, mono_now=mono) if snap is not None else None
            if health is not None and health.classification == HEALTH_STALLED:
                post_stalled(state, slot, STALL_REASON_NO_OUTPUT)
                noticed = True
        _settle(state, slot, turn, _rows_since(slot, start_row))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("first-run turn watch failed for %s", slot.key, exc_info=True)


def _rows_since(slot: "_ChatSlot", start_row: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The slot's rows appended after *start_row* (all rows when it is gone)."""
    rows = list(getattr(slot, "messages", None) or [])
    if start_row is not None:
        for index in range(len(rows) - 1, -1, -1):
            if rows[index] is start_row:
                return rows[index + 1 :]
    return rows


def _outcome(rows: list[dict[str, Any]]) -> str:
    """``landed``, ``quota`` or ``silent``: what the turn's LAST word was."""
    from kiro_crew.dashboard.chat_utils import USAGE_LIMIT_KIND

    outcome = "silent"
    for row in rows:
        if not isinstance(row, dict):
            continue
        role, meta = row.get("role"), row.get("meta")
        if role == "error" and isinstance(meta, dict) and meta.get("kind") == USAGE_LIMIT_KIND:
            outcome = "quota"
        elif role == "assistant" and is_speech_row(role, row.get("content"), meta):
            outcome = "landed"
    return outcome


def _followed(slot: "_ChatSlot", turn: "asyncio.Task[Any]") -> bool:
    """Whether a recovery or queued turn picks up where *turn* left off."""
    nxt = getattr(slot, "task", None)
    if nxt is not None and nxt is not turn and not nxt.done():
        return True
    return bool(getattr(slot, "queue_depth", 0))


def _settle(
    state: "DashboardState",
    slot: "_ChatSlot",
    turn: "asyncio.Task[Any]",
    rows: list[dict[str, Any]],
) -> None:
    outcome = _outcome(rows)
    if outcome == "landed":
        _quota_paused.discard(slot.key)
        _kickoff_open.discard(slot.key)
    elif outcome == "quota":
        _kickoff_open.discard(slot.key)
        if slot.key not in _quota_paused:
            _quota_paused.add(slot.key)
            _post(state, slot, _QUOTA_TEXT, {"kind": SETUP_QUOTA_KIND})
    elif slot.key in _kickoff_open and not _followed(slot, turn):
        # A turn that a retry ladder or the queue follows is judged by that turn.
        kickoff_failed(state, slot)


_QUOTA_TEXT = (
    "Your model allowance has run out, so the agent cannot reply until it resets or "
    "you switch to a plan or model that has capacity. Setup cards already in this chat "
    "and classic setup (/onboarding) still work, and this chat keeps its place: send a "
    "message when the allowance is back."
)


def _stalled_text(reason: str) -> str:
    if reason == STALL_REASON_KICKOFF_FAILED:
        return (
            "Setup did not start: the agent did not answer. Try again, or use classic "
            "setup (/onboarding) instead."
        )
    return (
        f"No reply for {int(FIRST_RUN_STALL_SECS)} seconds, so something may be stuck. "
        "Stop the reply and send your message again, or use classic setup (/onboarding)."
    )


def kickoff_failed(state: "DashboardState", slot: "_ChatSlot") -> None:
    """Post the notice that the first-run kickoff got no reply."""
    _kickoff_open.discard(slot.key)
    post_stalled(state, slot, STALL_REASON_KICKOFF_FAILED)


def post_stalled(state: "DashboardState", slot: "_ChatSlot", reason: str) -> None:
    """Post the ``setup_stalled`` notice for *reason* in *slot*."""
    meta: dict[str, Any] = {"kind": SETUP_STALLED_KIND, "reason": reason}
    if reason == STALL_REASON_NO_OUTPUT:
        meta["secs"] = int(FIRST_RUN_STALL_SECS)
    _post(state, slot, _stalled_text(reason), meta)


def _post(state: "DashboardState", slot: "_ChatSlot", text: str, meta: dict[str, Any]) -> None:
    slot.append("assistant", text, "msg msg-system", meta=meta)
    push = getattr(state, "push_slots_update", None)
    if callable(push):
        push()
    logger.info("first-run guardrail notice in %s: %s", slot.key, meta)


def kickoff_handled(slot_key: str) -> None:
    """Stop waiting on *slot_key*'s kickoff: another step answers its silence.

    The scripted sign-in step shown again after the first turn failed to sign in
    is the remedy, so the "did not start" notice must not land on top of it.
    """
    _kickoff_open.discard(slot_key)


def kickoff_failed_shown(slot: "_ChatSlot") -> bool:
    """Whether *slot*'s newest notice already says the kickoff got no reply."""
    for row in reversed(list(getattr(slot, "messages", None) or [])):
        if not isinstance(row, dict):
            continue
        meta = row.get("meta")
        if row.get("role") == "assistant" and isinstance(meta, dict):
            return (
                meta.get("kind") == SETUP_STALLED_KIND
                and meta.get("reason") == STALL_REASON_KICKOFF_FAILED
            )
    return False


def kickoff_answered(slot: "_ChatSlot") -> bool:
    """Whether the agent replied after the last kickoff row in *slot*."""
    for row in reversed(list(getattr(slot, "messages", None) or [])):
        if not isinstance(row, dict):
            continue
        role, meta = row.get("role"), row.get("meta")
        if role == "inject" and isinstance(meta, dict) and meta.get("injectKind") == "first_run":
            return False
        if role == "assistant" and is_speech_row(role, row.get("content"), meta):
            return True
    return False


async def retry_kickoff(state: "DashboardState") -> str:
    """Send the first-run kickoff again, on the owner's click; return the slot key.

    Refused (``CardRejected``) when there is no live first-run chat, the privacy
    card is not acknowledged yet, a turn is still running there, or the agent
    already answered the kickoff. The retried turn carries user provenance for
    the same reason the first one does: it exists because the owner clicked.
    """
    from kiro_crew import setup_cards as sc
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.dashboard.setup_flow import scripted_lock, start_first_run_turn
    from kiro_crew.first_run import read_first_run_slot

    slot_key = await asyncio.to_thread(read_first_run_slot)
    slot = state.get_slot(slot_key) if slot_key else None
    if slot is None:
        raise sc.CardRejected("there is no first-run chat", "slot_not_found")
    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    if not cfg.dashboard.privacy_acked:
        raise sc.CardRejected("answer the privacy card first", "privacy_not_acked")
    if await asyncio.to_thread(scripted_lock, slot.key):
        raise sc.CardRejected("finish the setup step in this chat first", "setup_step_pending")
    if slot.running or slot.key in _retrying:
        raise sc.CardRejected("a turn is still running in this chat", "turn_running")
    if kickoff_answered(slot):
        raise sc.CardRejected("setup already started in this chat", "kickoff_answered")
    _retrying.add(slot.key)
    try:
        await start_first_run_turn(state, slot)
    finally:
        _retrying.discard(slot.key)
    return slot.key
