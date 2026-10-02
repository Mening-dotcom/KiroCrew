"""Setup actions: one registered, self-describing module per setup-card kind.

Each module here defines one kind (``privacy``, ``profile``, ... ``home``) as a
:class:`SetupAction`: what the agent may send for it (the ``setup_card`` tool's
argument schema, and the check that runs in the MCP server), how the gateway
builds the card the owner sees, what the owner's click commits, the decisions it
offers beside commit and decline, and the flags ``dashboard/setup_flow.py`` reads
instead of branching on the kind. ``mcp_tools/setup.py`` generates the tool's
kind enum, argument properties and description from :data:`ACTIONS`.

The security of a card is not an action's to change: ``setup_flow`` alone checks
the turn's provenance (SC8), governance on ``capabilities.setup`` for every kind,
and the payload hash a click must carry (SC1). An action adds checks (``vet``);
none removes one.

Import-light: the MCP server imports this, so an action module reaches the
dashboard only inside its functions. Adding a kind, and the parity test that
fails when a layer is missed: ``docs/system-specs/modules/first-run.md``,
"Adding a setup action".
"""

from __future__ import annotations

import copy
from typing import Any

from kiro_crew.setup_actions import (
    channel,
    connect,
    credential,
    cron,
    harness,
    harness_signin,
    home,
    import_,
    path,
    privacy,
    profile,
    service,
    soul,
)
from kiro_crew.setup_actions.base import SETUP_SCOPE, Decision, SetupAction
from kiro_crew.setup_cards import DECISION_COMMIT, DECISION_DECLINE

__all__ = [
    "ACTIONS",
    "SETUP_SCOPE",
    "Decision",
    "SetupAction",
    "decision_names",
    "decision_refusal",
    "get",
    "proposable",
    "proposable_kinds",
    "setup_card_description",
    "setup_card_properties",
]

#: Every registered kind, in the order the tool's schema and description list them.
ACTIONS: tuple[SetupAction, ...] = (
    privacy.ACTION,
    profile.ACTION,
    soul.ACTION,
    import_.ACTION,
    connect.ACTION,
    credential.ACTION,
    channel.ACTION,
    cron.ACTION,
    service.ACTION,
    home.ACTION,
    harness.ACTION,
    harness_signin.ACTION,
    path.ACTION,
)

_BY_KIND: dict[str, SetupAction] = {a.kind: a for a in ACTIONS}

_DESCRIPTION_LEAD = "Show the user a setup card they approve with one click. Kinds: "
_DESCRIPTION_TAIL = (
    ". Nothing changes until the user clicks. End your turn after calling it; the "
    "decision arrives as a [Setup card result] message. Never ask the user to paste "
    "secrets into chat."
)


def get(kind: str) -> SetupAction | None:
    """The action registered for *kind*, or ``None``."""
    return _BY_KIND.get(kind)


def proposable() -> tuple[SetupAction, ...]:
    """The actions the agent may propose, in registry order."""
    return tuple(a for a in ACTIONS if a.proposable)


def proposable_kinds() -> list[str]:
    """The ``setup_card`` tool's ``kind`` enum."""
    return sorted(a.kind for a in proposable())


def decision_names() -> tuple[str, ...]:
    """Every decision a card can be sent: commit and decline, then each kind's own."""
    names = [DECISION_COMMIT, DECISION_DECLINE]
    for action in ACTIONS:
        names.extend(n for n in action.decisions if n not in names)
    return tuple(names)


def decision_refusal(decision: str) -> str:
    """What ``decide`` answers when a card whose kind lacks *decision* is sent it."""
    for action in ACTIONS:
        offered = action.decisions.get(decision)
        if offered is not None:
            return offered.refusal
    return "this card has no such decision"


def setup_card_description() -> str:
    """The ``setup_card`` tool's description: one clause per proposable kind."""
    kinds = ", ".join(f"{a.kind} ({a.summary})" for a in proposable())
    return _DESCRIPTION_LEAD + kinds + _DESCRIPTION_TAIL


def setup_card_properties() -> dict[str, dict[str, Any]]:
    """The ``setup_card`` tool's argument properties, merged over the proposable kinds.

    A property several kinds read (``name``: a credential's vault name, a job's
    name) keeps the first kind's schema, and its description joins each kind's
    own as ``kind: text``.
    """
    merged: dict[str, dict[str, Any]] = {}
    notes: dict[str, list[str]] = {}
    for action in proposable():
        for prop, fragment in action.arguments.items():
            if prop not in merged:
                merged[prop] = copy.deepcopy(
                    {k: v for k, v in fragment.items() if k != "description"}
                )
            note = fragment.get("description")
            if note:
                notes.setdefault(prop, []).append(f"{action.kind}: {note}")
    for prop, spec in merged.items():
        if prop in notes:
            spec["description"] = "; ".join(notes[prop])
    return merged
