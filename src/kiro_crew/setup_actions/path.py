"""The ``path`` card: get started with tips, or a more detailed setup (UX.3).

The last scripted step. Its commit records the owner's answer and sends the first
model turn, whose facts name the path so the agent follows it. Gateway-only and
not refusable by policy: it changes nothing but which way the conversation goes.
Like every step after the harness card, it offers "Choose a different engine".
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

    return await sf._commit_path(state, card, input_)


ACTION = SetupAction(
    kind=sc.KIND_PATH,
    commit=_commit,
    title=lambda card: "How to start",
    proposable=False,
    governed=False,
    reported=False,
    decisions={sc.DECISION_CHANGE_ENGINE: CHANGE_ENGINE},
)
