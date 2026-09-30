"""Make a transferred setup conversation the new home's main chat.

The owner-only arrival endpoint runs after the normal, guarded transfer and
portability import. General session imports keep their provenance filing.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from kiro_crew import first_run
from kiro_crew import setup_cards as sc

if TYPE_CHECKING:
    from kiro_crew.dashboard.state import DashboardState


async def adopt_main_chat(
    state: "DashboardState",
    slot_key: str,
    title: str,
    stages: list[str],
    *,
    preferences: dict | None = None,
    outcome: dict | None = None,
) -> str:
    """Promote the arrived chat durably; retries reuse the same slot.

    Setup progress and personal preferences travel. Privacy consent,
    credentials and connection grants stay in their normal stores on this home.
    The previous welcome conversation is archived, never deleted.
    """
    from kiro_crew.agent_files import MAIN_CHAT_AGENT_NAME
    from kiro_crew.dashboard import setup_guardrails
    from kiro_crew.dashboard.chat_handlers import close_slot
    from kiro_crew.dashboard.chat_persistence import save_slot_off_loop
    from kiro_crew.dashboard.chat_utils import slot_history_key

    slot = state.get_slot(slot_key)
    if slot is None:
        raise sc.CardRejected("the transferred chat is not open", "slot_not_found")
    if (
        not slot_key.startswith("chat-")
        or getattr(slot, "channel_origin", None)
        or getattr(slot, "_slack_linked", False)
        or getattr(slot, "memory_mode", "persistent") != "persistent"
    ):
        raise sc.CardRejected("the transferred chat is not eligible", "slot_not_eligible")
    if slot.running:
        raise sc.CardRejected("the transferred chat is still working", "turn_running")

    from kiro_crew.dashboard import setup_transfer

    if preferences is not None:
        await setup_transfer.apply_preferences(preferences)
    await asyncio.to_thread(setup_transfer.complete_receipts, slot_key, outcome or {})
    slot.title = title
    slot._titled = True
    slot._title_origin = "user"
    slot._title_epoch = int(getattr(slot, "_title_epoch", 0)) + 1
    slot.folder_id = ""
    slot.pinned = True
    slot.agent = MAIN_CHAT_AGENT_NAME
    if not await save_slot_off_loop(
        state,
        slot,
        force=True,
        best_effort=False,
        expected_history_key=slot_history_key(slot),
        expected_slot_name=slot_key,
    ):
        raise sc.CardRejected("the transferred chat changed; retry the move", "slot_not_found")

    def _adopt(data: dict) -> None:
        previous = data.get("slot")
        if previous and previous != slot_key:
            data["arrival_previous_slot"] = previous
        data["slot"] = data["main"] = slot_key
        progress = data.setdefault("stages", {})
        for stage in (*stages, "stay_on"):
            if stage in first_run.STAGES:
                progress.setdefault(stage, time.time())

    recorded = await asyncio.to_thread(first_run.update_state, _adopt)
    setup_guardrails.track(state, slot_key)
    previous_key = recorded.get("arrival_previous_slot")
    previous = state.get_slot(previous_key) if previous_key else None
    if previous is not None and previous is not slot:

        def _idle() -> None:
            if previous.running:
                raise sc.CardRejected("the home's welcome chat is still working", "turn_running")

        _idle()
        await close_slot(state, previous, str(previous_key), pre_pop_check=_idle)
    await asyncio.to_thread(
        first_run.update_state, lambda data: data.pop("arrival_previous_slot", None)
    )
    state.push_slots_update()
    return slot_key
