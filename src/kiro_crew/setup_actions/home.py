"""The ``home`` card: a permanent home in the owner's own AWS account.

One card carries the whole journey: Sign in to AWS (``aws_signin``), the region
when AWS names none (``region``), Build, the home's own Kiro sign-in, then Move
in. The first run's own card (payload ``offer``, the gateway's step, not the
agent's) comes when the job step is kept or skipped and starts one step earlier: the
question where the crew lives (``choose``), which moves the same card on to the
cloud's steps or settles it on this machine. A build a gateway restart cut short
leaves a failed card that can remove what it created (``remove``). Its build runs
in the background, so a pending home card neither holds other proposals back nor
is held back by one. The flow is ``dashboard/setup_flow.py``, ``setup_aws_signin.py``,
``home_signin.py`` and ``setup_move_in.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import Decision, SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

#: Payload flag of the home card the gateway shows as the first run's own step.
HOME_STEP_KEY = "offer"
#: Payload key naming the step the card is at, while it is not yet the build.
HOME_PHASE_KEY = "step"
#: That step while the card asks where the crew lives (private ``phase`` too).
HOME_CHOICE_STEP = "choose"
#: ``input.where`` of the ``choose`` decision.
HOME_HERE = "here"
HOME_CLOUD = "cloud"


def _validate(args: dict[str, Any]) -> dict[str, Any]:
    settings = sc.build_home(args)
    if "step" in args:
        if args["step"] != HOME_CHOICE_STEP:
            raise sc.CardRejected("the home proposal step must be choose", "home_step_invalid")
        settings["step"] = HOME_CHOICE_STEP
    return settings


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from kiro_crew.dashboard import setup_flow as sf

    settings = _validate(args)
    build = (
        sf._choice_payload if settings.pop("step", None) == HOME_CHOICE_STEP else sf._home_payload
    )
    return await asyncio.to_thread(build, settings)


async def _after_report(state: "DashboardState", card: sc.SetupCard) -> None:
    from kiro_crew.dashboard import setup_flow as sf

    if card.status in (sc.STATUS_COMMITTED, sc.STATUS_DECLINED) and not (
        (card.outcome or {}).get("moved") and not (card.outcome or {}).get("simulated")
    ):
        await sf.graduate(state, card.slot)


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_home(state, card, input_)


async def _aws_signin(
    state: "DashboardState",
    card: sc.SetupCard,
    card_hash: str,
    input_: dict[str, Any],
    *,
    same_machine: bool,
) -> sc.SetupCard:
    from kiro_crew.dashboard.setup_aws_signin import decide_signin

    return await decide_signin(state, card, card_hash, input_, same_machine=same_machine)


async def _region(
    state: "DashboardState",
    card: sc.SetupCard,
    card_hash: str,
    input_: dict[str, Any],
    *,
    same_machine: bool,
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._decide_home_region(state, card, card_hash, input_)


async def _choose(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._choose_home(state, card, input_)


async def _remove(
    state: "DashboardState",
    card: sc.SetupCard,
    card_hash: str,
    input_: dict[str, Any],
    *,
    same_machine: bool,
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._decide_home_remove(state, card, card_hash, input_)


async def _on_claim(card: sc.SetupCard, same_machine: bool) -> sc.SetupCard:
    # Whether a build started by this click may open the home's Kiro sign-in
    # page in the owner's browser (``home_signin``).
    from kiro_crew.dashboard.home_signin import record_browser_here

    return await record_browser_here(card, same_machine)


def _result_detail(card: sc.SetupCard) -> str:
    outcome = card.outcome or {}
    if outcome.get("stayed"):
        return (
            " The user keeps the crew on this machine. Offer the keep-running service now "
            '(setup_card kind "service"), in one sentence: Kiro Crew stays available '
            "only while it runs."
        )
    if outcome.get("moved"):
        from kiro_crew.dashboard.setup_move_in import result_detail, simulated_result_detail

        return (
            simulated_result_detail(outcome) if outcome.get("simulated") else result_detail(outcome)
        )
    return " The home is ready."


ACTION = SetupAction(
    kind=sc.KIND_HOME,
    commit=_commit,
    title=lambda card: "Your home in the cloud",
    summary=(
        "a permanent home in the user's own AWS account, built in the background; the "
        "card shows the monthly cost and the user starts the build"
    ),
    arguments={
        "step": {
            "type": "string",
            "enum": [HOME_CHOICE_STEP],
            "description": "choose: ask this machine or AWS, including after scheduling is skipped",
        },
        "region": {"type": "string", "description": "AWS region"},
        "profile": {"type": "string", "description": "AWS CLI profile"},
        "size": {
            "type": "string",
            "description": f"size key, default {sc.HOME_DEFAULT_SIZE}",
        },
    },
    validate=_validate,
    build=_build,
    decisions={
        sc.DECISION_AWS_SIGNIN: Decision(
            run=_aws_signin, refusal="only a home card signs in to AWS", claimed=False
        ),
        sc.DECISION_REGION: Decision(
            run=_region, refusal="this card does not ask for a region", claimed=False
        ),
        sc.DECISION_CHOOSE: Decision(
            run=_choose, refusal="only a home card asks where the crew lives"
        ),
        sc.DECISION_REMOVE: Decision(
            run=_remove, refusal="only a home card removes what its build created", claimed=False
        ),
    },
    on_claim=_on_claim,
    result_detail=_result_detail,
    after_report=_after_report,
    stack_exempt=True,
    gateway_card=lambda card: bool(card.payload.get(HOME_STEP_KEY)),
)
