"""HTTP surface for setup cards and the first-run session.

Every route is owner-only. The decide route is the single commit path for a
setup card: it names the card, the payload hash the owner was shown, and the
decision, and ``setup_flow.decide`` re-checks all three before acting. A card's
secret input (a credential value, a bot token) travels in this request body
only; it is never logged, audited, echoed or stored on the card.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from kiro_crew import setup_cards as sc
from kiro_crew.dashboard.handlers._shared import read_bounded_json, require_owner_dashboard_request

logger = logging.getLogger(__name__)

#: A decide body carries at most a short secret or a small exclusion list.
_MAX_DECIDE_BODY_BYTES = 16 * 1024

_STATUS_FOR_CODE: dict[str, int] = {
    "card_not_found": 404,
    "card_not_pending": 409,
    "card_hash_mismatch": 409,
    "invalid_decision": 400,
    "governance_denied": 403,
    "slot_not_found": 404,
    "slot_not_eligible": 409,
    "privacy_not_acked": 409,
    "turn_running": 409,
    "kickoff_answered": 409,
    "setup_step_pending": 409,
}


def _error(message: str, code: str, status: int) -> web.Response:
    return web.json_response({"error": message, "code": code}, status=status)


def _card_or_404(card: sc.SetupCard | None) -> web.Response:
    if card is None:
        return _error("setup card not found", "card_not_found", 404)
    from kiro_crew.dashboard.setup_channel import owner_view

    return web.json_response(owner_view(card))


async def api_setup_cards_list(request: web.Request) -> web.Response:
    """GET /api/setup/cards?slot=<slotKey> — the slot's cards, oldest first."""
    denied = await require_owner_dashboard_request(request, "setup_cards.list")
    if denied is not None:
        return denied
    slot = str(request.query.get("slot") or "")
    if not slot:
        return _error("slot is required", "slot_required", 400)
    cards = await asyncio.to_thread(sc.list_cards, slot)
    from kiro_crew.dashboard.setup_channel import owner_view

    # owner_view: a waiting channel card's live pairing code is held in memory,
    # never in the store, and reaches only these owner-only routes.
    return web.json_response({"cards": [owner_view(c) for c in cards]})


async def api_setup_card_get(request: web.Request) -> web.Response:
    """GET /api/setup/cards/{card_id}."""
    denied = await require_owner_dashboard_request(request, "setup_cards.get")
    if denied is not None:
        return denied
    card_id = request.match_info.get("card_id", "")
    if not sc.valid_card_id(card_id):
        return _error("setup card not found", "card_not_found", 404)
    return _card_or_404(await asyncio.to_thread(sc.get_card, card_id))


async def api_setup_card_approvals(request: web.Request) -> web.Response:
    """GET /api/setup/cards/{card_id}/approvals — what a running job preview waits on.

    ``{"approvals": [{id, tool, tool_input, tool_purpose, ts}]}``: the pending
    approvals raised by this card's preview run, empty when none is running
    (``setup_preview.pending_for_card``). The card answers them through
    ``POST /api/approvals/{id}/{action}``, the same path as the approvals inbox.
    """
    denied = await require_owner_dashboard_request(request, "setup_cards.approvals")
    if denied is not None:
        return denied
    card_id = request.match_info.get("card_id", "")
    if not sc.valid_card_id(card_id):
        return _error("setup card not found", "card_not_found", 404)
    if await asyncio.to_thread(sc.get_card, card_id) is None:
        return _error("setup card not found", "card_not_found", 404)
    from kiro_crew.dashboard.setup_preview import pending_for_card

    return web.json_response({"approvals": pending_for_card(request.app["state"], card_id)})


async def api_setup_card_signin_status(request: web.Request) -> web.Response:
    """GET /api/setup/cards/{card_id}/signin-status — whether the card's harness is signed in.

    ``{"signed_in": true | false | null}`` for a sign-in card whose payload offers it
    (``sign_in_status``), from the harness's OWN status command, run in its session's
    sandbox (``harness_readiness.signed_in``). Kiro Crew never reads the harness's
    credential files to answer. ``null`` is unknown, and so is any card that is no
    longer pending: Continue still asks by starting the harness.
    """
    denied = await require_owner_dashboard_request(request, "setup_cards.signin_status")
    if denied is not None:
        return denied
    card_id = request.match_info.get("card_id", "")
    if not sc.valid_card_id(card_id):
        return _error("setup card not found", "card_not_found", 404)
    card = await asyncio.to_thread(sc.get_card, card_id)
    if card is None:
        return _error("setup card not found", "card_not_found", 404)
    if card.kind != sc.KIND_HARNESS_SIGNIN or card.payload.get("sign_in_status") is not True:
        return _error("this card has no sign-in status", "no_signin_status", 404)
    if card.status != sc.STATUS_PENDING:
        return web.json_response({"signed_in": None})
    from kiro_crew.dashboard.harness_readiness import signed_in

    return web.json_response({"signed_in": await signed_in(str(card.payload.get("backend", "")))})


async def api_setup_card_decide(request: web.Request) -> web.Response:
    """POST /api/setup/cards/{card_id}/decide — the owner's decision on a card.

    Body ``{"decision": <a decision name>, "hash": str, "input": {}}``.
    """
    denied = await require_owner_dashboard_request(request, "setup_cards.decide")
    if denied is not None:
        return denied
    card_id = request.match_info.get("card_id", "")
    if not sc.valid_card_id(card_id):
        return _error("setup card not found", "card_not_found", 404)
    body, err = await read_bounded_json(request, max_bytes=_MAX_DECIDE_BODY_BYTES)
    if body is None:
        return err or _error("body must be a JSON object", "invalid_body", 400)
    decision = body.get("decision")
    card_hash = body.get("hash")
    input_: Any = body.get("input") or {}
    if not isinstance(decision, str) or not isinstance(card_hash, str):
        return _error("decision and hash are required", "invalid_body", 400)
    if not isinstance(input_, dict):
        return _error("input must be an object", "invalid_body", 400)
    from kiro_crew.dashboard.origin import is_direct_local_request
    from kiro_crew.dashboard.setup_flow import decide

    try:
        card = await decide(
            request.app["state"],
            card_id,
            decision,
            card_hash,
            input_,
            # The home card's AWS sign-in opens a browser on THIS machine, so it
            # needs the owner's browser to be here, not behind a tunnel or proxy.
            same_machine=is_direct_local_request(request),
        )
    except sc.CardRejected as exc:
        return _error(str(exc), exc.code, _STATUS_FOR_CODE.get(exc.code, 422))
    return web.json_response(card.public())


async def api_setup_first_run(request: web.Request) -> web.Response:
    """GET /api/setup/first-run — the first-run session's slot, if any."""
    denied = await require_owner_dashboard_request(request, "setup_cards.first_run")
    if denied is not None:
        return denied
    from kiro_crew.first_run import read_first_run_slot

    slot = await asyncio.to_thread(read_first_run_slot)
    state = request.app["state"]
    active = bool(slot) and state.get_slot(slot) is not None
    return web.json_response({"slot": slot, "active": active})


async def api_setup_main_chat(request: web.Request) -> web.Response:
    """POST /api/setup/main-chat — body ``{"slot": str}``: make that chat the main chat."""
    denied = await require_owner_dashboard_request(request, "setup_cards.main_chat")
    if denied is not None:
        return denied
    body, err = await read_bounded_json(request, max_bytes=1024)
    if body is None:
        return err or _error("body must be a JSON object", "invalid_body", 400)
    slot = body.get("slot")
    if not isinstance(slot, str) or not slot:
        return _error("slot is required", "slot_required", 400)
    from kiro_crew.dashboard.setup_flow import make_main_chat

    try:
        main = await make_main_chat(request.app["state"], slot)
    except sc.CardRejected as exc:
        return _error(str(exc), exc.code, _STATUS_FOR_CODE.get(exc.code, 422))
    return web.json_response({"main_slot": main})


async def api_setup_first_run_retry(request: web.Request) -> web.Response:
    """POST /api/setup/first-run/retry — send the first-run kickoff again.

    The ``kickoff_failed`` notice's Try again button. See
    ``setup_guardrails.retry_kickoff`` for when it is refused.
    """
    denied = await require_owner_dashboard_request(request, "setup_cards.first_run_retry")
    if denied is not None:
        return denied
    from kiro_crew.dashboard.setup_guardrails import retry_kickoff

    try:
        slot = await retry_kickoff(request.app["state"])
    except sc.CardRejected as exc:
        return _error(str(exc), exc.code, _STATUS_FOR_CODE.get(exc.code, 422))
    return web.json_response({"slot": slot})


async def api_setup_home_arrival(request: web.Request) -> web.Response:
    """POST /api/setup/home-arrival — continue setup in its transferred chat."""
    denied = await require_owner_dashboard_request(request, "setup_cards.home_arrival")
    if denied is not None:
        return denied
    body, err = await read_bounded_json(request, max_bytes=1024 * 1024)
    if body is None:
        return err or _error("body must be a JSON object", "invalid_body", 400)
    slot, title, stages = body.get("slot"), body.get("title"), body.get("stages", [])
    if not (
        isinstance(slot, str)
        and 0 < len(slot) <= 128
        and isinstance(title, str)
        and 0 < len(title) <= 256
        and isinstance(stages, list)
        and len(stages) <= 8
        and all(isinstance(stage, str) for stage in stages)
    ):
        return _error("invalid home arrival", "invalid_body", 400)
    from kiro_crew.dashboard.setup_home_arrival import adopt_main_chat
    from kiro_crew.dashboard.setup_transfer import validate_preferences

    try:
        preferences = validate_preferences(body.get("preferences", {}))
        outcome = body.get("outcome", {})
        if not isinstance(outcome, dict):
            return _error("invalid home outcome", "invalid_body", 400)
        main = await adopt_main_chat(
            request.app["state"],
            slot,
            title,
            stages,
            preferences=preferences,
            outcome=outcome,
        )
    except sc.CardRejected as exc:
        return _error(str(exc), exc.code, _STATUS_FOR_CODE.get(exc.code, 422))
    except ValueError:
        return _error("invalid home preferences", "invalid_body", 400)
    return web.json_response({"main_slot": main})


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/setup/first-run", api_setup_first_run)
    app.router.add_post("/api/setup/first-run/retry", api_setup_first_run_retry)
    app.router.add_get("/api/setup/cards", api_setup_cards_list)
    app.router.add_get("/api/setup/cards/{card_id}", api_setup_card_get)
    app.router.add_get("/api/setup/cards/{card_id}/approvals", api_setup_card_approvals)
    app.router.add_get("/api/setup/cards/{card_id}/signin-status", api_setup_card_signin_status)
    app.router.add_post("/api/setup/cards/{card_id}/decide", api_setup_card_decide)
    app.router.add_post("/api/setup/main-chat", api_setup_main_chat)
    app.router.add_post("/api/setup/home-arrival", api_setup_home_arrival)
