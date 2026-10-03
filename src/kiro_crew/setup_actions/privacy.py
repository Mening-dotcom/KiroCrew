"""The ``privacy`` card: the disclosure the gateway shows before the first model turn.

Gateway-only. A model-authored copy of a disclosure is exactly what must not
exist, so the agent cannot propose one, and a policy cannot refuse its
acknowledgement: nothing else in the first run starts without it. In the
scripted first run it comes after the harness sign-in, its commit shows the
start path, and it offers "Choose a different engine"; in a first-run chat from
before those steps its commit starts the first model turn itself. Either way the
card is not reported back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction
from kiro_crew.setup_actions.harness import CHANGE_ENGINE

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_privacy(state, card, input_)


ACTION = SetupAction(
    kind=sc.KIND_PRIVACY,
    commit=_commit,
    title=lambda card: "Privacy",
    proposable=False,
    governed=False,
    reported=False,
    decisions={sc.DECISION_CHANGE_ENGINE: CHANGE_ENGINE},
)
