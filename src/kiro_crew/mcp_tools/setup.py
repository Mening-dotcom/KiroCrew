"""Setup tools: propose setup cards the user commits, and read setup progress.

``setup_card`` is a stateless session directive, like ``ask_question``: it
validates the proposal and returns a directive the session's own consumer
applies (``dashboard/setup_flow.py``). The gateway builds the card the owner
sees and only the owner's click commits it, so nothing this tool returns can
change a setting, store a secret or schedule a job by itself.

One tool with a ``kind`` rather than a tool per kind: every core tool's schema
rides on every request of every session, and the kinds share one lifecycle. The
kind enum, the argument properties and the description are generated from the
setup-action registry (``kiro_crew/setup_actions/``), agent-proposable kinds only.
"""

from __future__ import annotations

from typing import Any

from kiro_crew import mcp_core, setup_actions
from kiro_crew import setup_cards as sc
from kiro_crew.mcp_tools import control
from kiro_crew.session_surface import has_dashboard_surface


def schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "setup_card",
            "description": setup_actions.setup_card_description(),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": setup_actions.proposable_kinds()},
                    **setup_actions.setup_card_properties(),
                },
                "required": ["kind"],
            },
        },
        {
            "name": "setup_status",
            "description": (
                "Read this session's setup progress: first-run stages done and each setup "
                "card's status. Use it to check a card's outcome instead of trusting chat text."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _directive_args(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate the proposal for *kind* and return the args the applier gets.

    Validation runs here so the model gets a precise error before it is told a
    card exists; the applier validates again, because it is the side that acts.
    """
    action = setup_actions.get(kind)
    if action is None:
        return {"kind": kind}
    return {"kind": kind, **action.validate(args)}


def setup_card(name: str, args: dict[str, Any]) -> str:
    kind = str(args.get("kind", ""))
    kinds = setup_actions.proposable_kinds()
    if kind not in kinds:
        return "Error: kind must be one of " + ", ".join(kinds) + "."
    sk, _ = mcp_core.require_strict_session_key("setup_card")
    if sk and not has_dashboard_surface(sk):
        return (
            "Error: setup cards need an open dashboard chat "
            f"(this session is {sk!r}). Point the user to the matching Settings page instead."
        )
    try:
        directive_args = _directive_args(kind, args)
    except sc.CardRejected as exc:
        return f"Error: {exc}"
    return control._emit_directive(
        "setup_card",
        directive_args,
        f"Setup card ({kind}) requested for this session; the gateway shows it if it can. "
        "End your turn now; the user's decision arrives as a [Setup card result] message, "
        "and so does a card the gateway could not show.",
    )


def setup_status(name: str, args: dict[str, Any]) -> str:
    from kiro_crew.first_run import done_stages, read_first_run_slot, read_state

    sk, err = mcp_core.require_strict_session_key("Error: setup_status needs a session.")
    if err:
        return err
    cards = [c for c in sc.load_cards() if c.session_key == sk]
    lines = []
    slot = read_first_run_slot()
    if slot and sk == f"dashboard:{slot}":
        stages = done_stages()
        lines.append("First-run stages done: " + (", ".join(stages) if stages else "none") + ".")
        homes = [c for c in cards if c.kind == sc.KIND_HOME]
        if not homes and not read_state().get("home"):
            lines.append(
                'Next setup step: call setup_card(kind="home", step="choose"), even if '
                "the user skipped scheduling. Do not conclude setup before this choice."
            )
        elif homes and homes[-1].status not in (sc.STATUS_COMMITTED, sc.STATUS_DECLINED):
            lines.append(
                f"The home step is not finished yet: {sc.home_state_words(homes[-1])}. "
                "Do not say setup is done."
            )
    if not cards:
        lines.append("No setup cards in this session.")
    # The same plain states the crew overview uses: a raw ``waiting`` reads as
    # "the user owes it an answer" when it means the card's own work is running.
    for card in cards[-20:]:
        words = (
            sc.home_state_words(card) if card.kind == sc.KIND_HOME else sc.card_state_words(card)
        )
        line = f"- {card.kind} ({card.id}): {words}"
        if card.error:
            line += f" — {card.error.get('message', '')}"
        lines.append(line)
    return "\n".join(lines)


HANDLERS = {"setup_card": setup_card, "setup_status": setup_status}
