"""The ``harness_signin`` card: install and sign in to the chosen harness.

Gateway-only and not refusable by policy, like the ``harness`` card. It commits
nothing on the host: its Continue asks the harness whether it can answer
(``dashboard/harness_readiness.py``), and only a yes shows the next step. After
one failed check the owner may continue without one; the first turn then reports
the truth, and a sign-in failure there shows this card again. Kiro Crew only
names the harness's own install and sign-in; it runs neither.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_harness_signin(state, card, input_)


ACTION = SetupAction(
    kind=sc.KIND_HARNESS_SIGNIN,
    commit=_commit,
    title=lambda card: f"Sign in to {card.payload.get('label') or 'the agent engine'}",
    proposable=False,
    governed=False,
    reported=False,
)
