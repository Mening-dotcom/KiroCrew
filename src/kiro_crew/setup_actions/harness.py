"""The ``harness`` card: which agent harness runs the crew, the first run's first step.

Gateway-only, like the privacy card: no model can run until a harness is chosen
and answers, so nothing could have proposed it, and a policy may not refuse it
(``governed=False``). The policy control is the harness list itself: the card
offers ``selectable_backends()`` after governance narrowed it, and the commit
re-checks the chosen id against that live set (harness-parity H3, H4), so a card
edited on disk cannot widen it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.agent_sdk.backends import ACP_BACKEND_CLAUDE, ACP_BACKEND_KAS, ACP_BACKEND_KIRO
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

#: The names an owner knows each harness by, as the Agent Backend panel shows them.
#: Product names, never translated. Any other harness is named by its install record.
_LABELS = {
    ACP_BACKEND_KIRO: "Kiro CLI",
    ACP_BACKEND_CLAUDE: "Claude Code",
    ACP_BACKEND_KAS: "KAS (kiro-agent)",
}


def harness_label(backend: str) -> str:
    """An owner-facing name for *backend*."""
    from kiro_crew.agent_sdk.tool_gate import label_for

    return _LABELS.get(backend) or label_for(backend)


def harness_options() -> list[dict[str, str]]:
    """The harnesses the card offers: Kiro first (the default, H1), then by name."""
    from kiro_crew.agent_sdk.backends import selectable_backends

    def _order(backend: str) -> tuple[int, str]:
        return (0 if backend == ACP_BACKEND_KIRO else 1, harness_label(backend).casefold())

    return [{"id": b, "label": harness_label(b)} for b in sorted(selectable_backends(), key=_order)]


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_harness(state, card, input_)


ACTION = SetupAction(
    kind=sc.KIND_HARNESS,
    commit=_commit,
    title=lambda card: "Choose the agent engine",
    proposable=False,
    governed=False,
    reported=False,
)
