"""The ``import`` card: bring another agent's setup on this machine over."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

#: Most detected sources one proposal may name.
_MAX_SOURCE_IDS = 10


def _validate(args: dict[str, Any]) -> dict[str, Any]:
    ids = args.get("source_ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise sc.CardRejected("source_ids must be a list of strings", "invalid_argument")
    return {"source_ids": ids[:_MAX_SOURCE_IDS]}


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._build_import(args)


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_import(state, card, input_)


def _result_detail(card: sc.SetupCard) -> str:
    outcome = card.outcome or {}
    detail = (
        f" Imported {outcome.get('imported_count', 0)} items; "
        f"{outcome.get('jobs_added_disabled', 0)} imported jobs were added DISABLED "
        "for the user to review."
    )
    jobs = [j for j in outcome.get("jobs") or [] if isinstance(j, dict)]
    if jobs:
        listed = "; ".join(
            f"{j.get('name', '')} ({j.get('schedule', '')}): {j.get('prompt', '')}" for j in jobs
        )
        detail += (
            f" The imported jobs: {listed}. To keep one, propose a cron card with the "
            "prompt adapted to this install; there is no need to look them up."
        )
    return detail


ACTION = SetupAction(
    kind=sc.KIND_IMPORT,
    commit=_commit,
    title=lambda card: "Bring your setup over",
    summary="bring another agent's setup over",
    arguments={
        "source_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "limit to these detected sources, by source id: the first-run facts name "
                'each one\'s id, e.g. "claude_code" for Claude Code'
            ),
        },
    },
    validate=_validate,
    build=_build,
    result_detail=_result_detail,
)
