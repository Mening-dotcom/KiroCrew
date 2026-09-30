"""Moving the crew into a live home: the home card's "Move in", for real.

A simulated home walks ``setup_flow.MOVE_IN_STEPS`` and moves nothing. A home the
launch engine really built is registered in the Instances hub ("Added to Your
crews") under the EC2 instance id the card's build recorded, and :func:`move_in`
hands the crew to it in the four steps of :data:`LIVE_MOVE_IN_STEPS`, each shown
on the card as it runs:

1. **reach** -- open, or reuse, that instance's tunnel (``SshTunnelManager.connect``).
2. **pack** -- this crew's portability export (``portability.create_export_zip``):
   memory, schedules, skills, workspace, hooks, notifications, the persona files,
   and settings where the home has none. Secrets never ride it: the vault, ``.env``
   and connection grants are not in the export.
3. **chat** -- send the card's chat to the home over the session-transfer path
   (``build_transfer_bundle_async`` and ``SshTunnelManager.send_session_bundle``),
   which answers with the key of the chat's copy there.
4. **carry** -- point the archive's schedules owned by this chat at that copy,
   switch off here the schedules the home will run, then import the archive on the
   home through the same tunnel (``POST /api/portability/import``, merge mode).

The chat goes before the carry because a schedule reports to the chat its
``session_key`` names (``dashboard:<slot>``), and this chat's key means nothing on
the home. Learning the copy's key first lets the archive carry the right owner, so
a moved schedule never runs on the home under a key that names no chat there. Only
the archive changes: the jobs here keep their key, since this chat stays here.

SC5 (a job runs on exactly one crew) fixes the order inside step 4. The home's cron
service loads an imported job on its next sync, so a local copy still enabled when
the archive lands runs beside it. The moving schedules are therefore switched off
BEFORE the archive is sent, and switched back on when the carry fails, so a failed
move leaves this crew's schedules as they were. The card's ``private.move`` record
keeps each finished step, so pressing Move in again never sends the chat or the
archive twice. One window remains: a reply lost after the home applied the archive.
The schedules come back on here and run on both crews until the owner presses Move
in again, when the home's merge skips the names it already has and the local copies
go off for good.

A schedule that runs a command or a script stays here, enabled: the import pauses
such a job on the home and the export carries no script, so it is bound to this
computer.

Every failure raises :class:`~kiro_crew.setup_cards.CardRejected`, so ``decide``
returns the card to ``pending`` with the reason on it and the owner can retry.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.dashboard import setup_flow as sf
from kiro_crew.first_run import mark_stage
from kiro_crew.sel import sel

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState
    from kiro_crew.instances.registry import Instance

logger = logging.getLogger(__name__)

#: The move-in steps a live home's card shows, in order.
LIVE_MOVE_IN_STEPS: tuple[tuple[str, str], ...] = (
    ("reach", "Reach your home"),
    ("pack", "Pack memory, schedules, skills, settings and persona"),
    ("chat", "Bring this chat along"),
    ("carry", "Carry the rest to your home and unpack it there"),
)
#: The home's portability importer, reached through the instance tunnel.
IMPORT_PATH = "api/portability/import"
#: Largest import reply read back from the home; an honest one is a short summary.
_IMPORT_REPLY_MAX_BYTES = 256 * 1024
#: A code from the home is quoted only in this shape; the home's free text never is.
_PEER_CODE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
#: A summary item from the home is kept only in this shape, and only this many.
_ITEM_RE = re.compile(r"^[\w ./(),:;'-]{1,160}$")
_MAX_ITEMS = 40
#: The shape of a session key the home hands back for the chat's copy.
_REMOTE_KEY_RE = re.compile(r"^[A-Za-z0-9:_.-]{1,128}$")
#: Longest error text from the tunnel manager quoted on the card.
_REACH_ERROR_CHARS = 300
#: Most names the result text lists in one category; the rest are counted.
_LISTED_NAMES = 12
#: A finished step's detail when a retry skips it.
_DONE_EARLIER = "Done on the last try."

#: Why a schedule did not move, as the card and the result state it.
KEPT_RUNS_HERE = "it runs a command or script on this computer"
KEPT_HOME_REFUSED = "your home did not accept it"
KEPT_HOME_NO_STORE = "your home could not read its own schedule list"


@dataclass(frozen=True)
class _Job:
    id: str
    name: str


def _audit(card: sc.SetupCard, step: str, outcome: str, instance_id: str = "") -> None:
    sel().log_api_access(
        caller="setup_card",
        operation="setup_card.move_in",
        outcome=outcome,
        source="dashboard",
        resources=f"step:{step} instance:{instance_id} card:{card.id} session:{card.session_key}",
    )


class _Progress:
    """The move's steps on the card, written and broadcast at every change."""

    def __init__(self, state: "DashboardState", card: sc.SetupCard) -> None:
        self.state = state
        self.card = card
        self.base = dict(card.outcome or {})
        self.steps = [
            {"key": key, "label": label, "state": "pending", "detail": ""}
            for key, label in LIVE_MOVE_IN_STEPS
        ]
        self.current = ""

    async def mark(self, key: str, state: str, detail: str = "") -> None:
        step = next(s for s in self.steps if s["key"] == key)
        step["state"] = state
        step["detail"] = detail
        if state == "active":
            self.current = key
        self.card = await sf._finish(
            self.card, sc.STATUS_WORKING, outcome={**self.base, "move_steps": self.steps}
        )
        sf.broadcast(self.state, self.card)

    async def fail(self, message: str) -> None:
        if self.current:
            await self.mark(self.current, "failed", message[:1].upper() + message[1:])


async def move_in(state: "DashboardState", card: sc.SetupCard) -> sc.SetupCard:
    """Hand the crew and the card's chat to the live home; return the committed card."""
    progress = _Progress(state, card)
    stored = card.private.get("move")
    record: dict[str, Any] = dict(stored) if isinstance(stored, dict) else {}
    #: Jobs switched off here whose carry the home has not confirmed yet: the
    #: set a failure switches back on.
    unconfirmed: list[_Job] = []
    instance_id = ""
    try:
        await progress.mark("reach", "active")
        mgr, inst = await _reach(state, card)
        instance_id = inst.id
        await progress.mark("reach", "done", inst.name)
        _audit(card, "reach", "ok", instance_id)
        if record.get("instance") != inst.id:
            # A record for another home says nothing about this one.
            record = {"instance": inst.id}
        archive = b""
        moving: list[_Job] = []
        staying: list[_Job] = []
        if record.get("carried"):
            await progress.mark("pack", "done", _DONE_EARLIER)
        else:
            await progress.mark("pack", "active")
            archive, moving, staying = await _pack()
            await progress.mark("pack", "done", _pack_detail(moving, staying))
            _audit(card, "pack", "ok", instance_id)
        chat = _recorded_chat(record)
        if chat is not None:
            await progress.mark("chat", "done", _DONE_EARLIER)
        else:
            await progress.mark("chat", "active")
            chat = await _send_chat(state, mgr, inst.id, card.slot)
            record = await _save_record(card, {**record, "chat": chat})
            await progress.mark("chat", "done", "A copy continues on your home.")
            _audit(card, "chat", "ok", instance_id)
        if record.get("carried"):
            await progress.mark("carry", "done", _carry_detail(record))
        else:
            await progress.mark("carry", "active")
            archive, followed = await asyncio.to_thread(
                rebind_jobs_to_chat, archive, _chat_keys(card), _home_session_key(chat)
            )
            switched_off = await _switch_off(state, moving)
            unconfirmed = switched_off
            summary = await _carry(mgr, inst.id, archive)
            unconfirmed = []
            record = await _settle(
                state, card, record, summary, moving, staying, switched_off, followed
            )
            await progress.mark("carry", "done", _carry_detail(record))
            _audit(card, "carry", "ok", instance_id)
        if not record.get("adopted"):
            # The home's copy of this card is what the owner sees after the
            # switch, so it carries the way back too.
            reach = await reach_back(state, card)
            await _adopt_chat(
                state,
                mgr,
                inst.id,
                card.slot,
                chat["remote_key"],
                outcome={
                    "home": {
                        **reach.get("home", {}),
                        "name": inst.name,
                        "remote_key": chat["remote_key"],
                    },
                    "reconnect": reach.get("reconnect", []),
                    "jobs_moved": list(record.get("jobs_moved") or []),
                    "jobs_kept_here": list(record.get("jobs_kept_here") or []),
                    "carried": list(record.get("items") or []),
                    "reenter": await asyncio.to_thread(left_behind),
                },
            )
            record = await _save_record(card, {**record, "adopted": True, "settings_moved": True})
    except sc.CardRejected as exc:
        await _switch_on(state, unconfirmed)
        await progress.fail(str(exc))
        _audit(card, progress.current or "reach", f"failed:{exc.code}", instance_id)
        raise
    except asyncio.CancelledError:
        # A gateway stopping mid-move: the same rollback, and the card back to
        # pending so the owner can retry after the restart.
        with contextlib.suppress(Exception):
            await _switch_on(state, unconfirmed)
        with contextlib.suppress(Exception):
            await sf._back_to_pending(
                progress.card, "move_in_interrupted", "the move was interrupted" + _where(record)
            )
        raise
    except Exception:
        logger.exception("move-in for card %s failed", card.id)
        await _switch_on(state, unconfirmed)
        message = "something went wrong while moving" + _where(record)
        await progress.fail(message)
        _audit(card, progress.current or "reach", "failed:move_in_failed", instance_id)
        raise sc.CardRejected(message, "move_in_failed") from None
    await asyncio.to_thread(mark_stage, "stay_on")
    left = await asyncio.to_thread(left_behind)
    reach = await reach_back(state, card)
    return await sf._finish(
        progress.card,
        sc.STATUS_COMMITTED,
        outcome={
            **progress.base,
            "move_steps": progress.steps,
            "moved": True,
            "home": {**reach.get("home", {}), "instance_id": inst.id, "name": inst.name, **chat},
            "reconnect": reach.get("reconnect", []),
            "jobs_moved": list(record.get("jobs_moved") or []),
            "jobs_kept_here": list(record.get("jobs_kept_here") or []),
            "jobs_follow_chat": list(record.get("jobs_follow_chat") or []),
            "carried": list(record.get("items") or []),
            "settings_moved": bool(record.get("settings_moved")),
            "reenter": left,
        },
    )


def _where(record: dict[str, Any]) -> str:
    """Where the schedules are after a failed step, as the end of its sentence."""
    if record.get("carried"):
        return "; everything is already on your home, so press Move in again to finish"
    return ", and the schedules still run here; press Move in again"


async def _save_record(card: sc.SetupCard, record: dict[str, Any]) -> dict[str, Any]:
    """Keep the move's finished steps on the card, for a retry to skip them."""

    def _remember(c: sc.SetupCard) -> None:
        c.private["move"] = record

    await asyncio.to_thread(sc.update_card, card.id, _remember)
    return record


# ── reach ───────────────────────────────────────────────────────────────────


#: How long the restart waits so the card's refusal reaches the browser first.
_RESTART_DELAY_SECS = 2.0


async def _turn_on_remote_crew(state: "DashboardState") -> None:
    """Enable ``instances.enabled`` and restart the gateway once, in the background."""
    from kiro_crew.dashboard.handlers.updates import _restart_gateway

    def _enable(data: dict[str, Any]) -> None:
        data.setdefault("instances", {})["enabled"] = True

    await sf._update_config(_enable)
    if getattr(state, "_gateway_restart_in_progress", False):
        return

    async def _restart() -> None:
        await asyncio.sleep(_RESTART_DELAY_SECS)
        try:
            await _restart_gateway(state)
        except Exception:
            logger.exception("restart after turning on Remote Crew failed")

    task = asyncio.create_task(_restart())
    tasks = getattr(state, "_background_tasks", None)
    if isinstance(tasks, set):
        tasks.add(task)
        task.add_done_callback(tasks.discard)


def _registered_home(registry: Any, target: str) -> "Instance | None":
    """The Instances record the launch registered for EC2 instance *target*."""
    if not target:
        return None
    for inst in registry.list():
        if target in (inst.ssm_target, inst.ssh_host):
            return inst
    return None


async def _reach(state: "DashboardState", card: sc.SetupCard) -> tuple[Any, "Instance"]:
    """Open the home's tunnel; return ``(manager, instance)``."""
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.instances.registry import InstancesRegistry
    from kiro_crew.instances.ssh_tunnel_manager import TunnelState

    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    mgr = getattr(state, "instances_manager", None)
    if not cfg.instances.enabled or mgr is None:
        # The same gate every /api/instances route applies: the tunnel control
        # plane is opt-in and starts only at gateway boot. Pressing Move in on a
        # home this chat built IS that opt-in, so turn it on and restart once;
        # the chat comes back and the owner presses Move in again.
        await _turn_on_remote_crew(state)
        raise sc.CardRejected(
            "moving in reaches your home through Remote Crew, so it is now on and Kiro "
            "Crew is restarting; press Move in again when this chat is back",
            "move_in_restarting",
        )
    registry = getattr(state, "instances_registry", None) or InstancesRegistry()
    target = str(card.private.get("instance_id") or "")
    inst = await asyncio.to_thread(_registered_home, registry, target)
    if inst is None:
        raise sc.CardRejected(
            "your home is not in Your crews; add it under Settings → Remote Crew, then "
            "press Move in again",
            "move_in_home_not_registered",
        )
    if inst.connection_method == "fargate":
        # A fargate task serves only its turn API: no dashboard, so no importer.
        raise sc.CardRejected(
            "this home runs no dashboard, so a crew cannot move into it",
            "move_in_home_unsupported",
        )
    try:
        status = await mgr.connect(inst.id)
    except KeyError:
        raise sc.CardRejected(
            "your home is no longer in Your crews; add it under Settings → Remote Crew, "
            "then press Move in again",
            "move_in_home_not_registered",
        ) from None
    if status.state is not TunnelState.CONNECTED:
        reason = " ".join(str(status.error or "").split())[:_REACH_ERROR_CHARS]
        raise sc.CardRejected(
            "your home could not be reached"
            + (f" ({reason})" if reason else "")
            + "; check it is running under Your crews, then press Move in again",
            "move_in_unreachable",
        )
    return mgr, inst


# ── pack ────────────────────────────────────────────────────────────────────


async def _pack() -> tuple[bytes, list[_Job], list[_Job]]:
    from kiro_crew.portability import create_export_zip

    archive, _manifest = await asyncio.to_thread(create_export_zip)
    moving, staying = jobs_in_archive(archive)
    return archive, moving, staying


def _archive_crons(zf: zipfile.ZipFile) -> tuple[str, dict[str, Any]] | None:
    """``(member, store)`` for the archive's ``crons.json``, or ``None``."""
    member = next((n for n in zf.namelist() if PurePosixPath(n).parts[1:] == ("crons.json",)), None)
    if member is None:
        return None
    try:
        data = json.loads(zf.read(member).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return (member, data) if isinstance(data, dict) else None


def jobs_in_archive(archive: bytes) -> tuple[list[_Job], list[_Job]]:
    """``(moving, staying)``: the ENABLED jobs in *archive*'s ``crons.json``.

    Read from the archive rather than the live store, so the set switched off
    here is exactly the set that arrives enabled on the home. A job that runs a
    command or a script is ``staying`` (see the module docstring); a disabled
    job is neither, because it travels disabled and there is nothing to hand over.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        found = _archive_crons(zf)
    if found is None:
        return [], []
    jobs = found[1].get("jobs")
    moving: list[_Job] = []
    staying: list[_Job] = []
    for job in jobs if isinstance(jobs, list) else []:
        if not isinstance(job, dict):
            continue
        job_id, name = job.get("id"), job.get("name")
        if not isinstance(job_id, str) or not isinstance(name, str):
            continue
        if job.get("enabled") is not True or job.get("user_paused") or job.get("auto_paused"):
            continue
        (staying if job.get("command") or job.get("script") else moving).append(_Job(job_id, name))
    return moving, staying


def rebind_jobs_to_chat(
    archive: bytes, old_keys: frozenset[str], new_key: str
) -> tuple[bytes, list[str]]:
    """Point *archive*'s jobs owned by this chat (*old_keys*) at *new_key*.

    Returns ``(archive, names)``: a copy of *archive* whose ``crons.json`` names
    *new_key* as those jobs' ``session_key``, and the jobs changed. Every job
    owned by the chat follows it, enabled or not, since a paused one reports
    there once it is switched on. *archive* comes back unchanged when no job
    matches or *new_key* is empty. Only ``crons.json`` is rewritten; every other
    member is streamed across as it is.
    """
    if not new_key or not old_keys:
        return archive, []
    with zipfile.ZipFile(io.BytesIO(archive)) as src:
        found = _archive_crons(src)
        if found is None:
            return archive, []
        member, data = found
        jobs = data.get("jobs")
        names = []
        for job in jobs if isinstance(jobs, list) else []:
            if isinstance(job, dict) and job.get("session_key") in old_keys:
                job["session_key"] = new_key
                names.append(str(job.get("name") or ""))
        if not names:
            return archive, []
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                copy.compress_type = info.compress_type
                copy.external_attr = info.external_attr
                if info.filename == member:
                    dst.writestr(copy, json.dumps(data, indent=2, ensure_ascii=False).encode())
                    continue
                with src.open(info) as r, dst.open(copy, "w", force_zip64=True) as w:
                    shutil.copyfileobj(r, w)
    return out.getvalue(), names


def _chat_keys(card: sc.SetupCard) -> frozenset[str]:
    """The owner keys a job created in this chat carries (``dashboard:`` keys have one spelling)."""
    return frozenset(k for k in (card.session_key, f"dashboard:{card.slot}") if k)


def _home_session_key(chat: dict[str, Any]) -> str:
    """The session key of the chat's copy on the home, or ``""`` when it is unknown."""
    key = chat.get("remote_key")
    if not isinstance(key, str) or not _REMOTE_KEY_RE.match(key):
        return ""
    return key if key.startswith("dashboard:") else f"dashboard:{key}"


def _recorded_chat(record: dict[str, Any]) -> dict[str, Any] | None:
    """The chat step's result from an earlier try at this home, or ``None``."""
    chat = record.get("chat")
    if not isinstance(chat, dict):
        return None
    key = chat.get("remote_key")
    messages = chat.get("messages")
    return {
        "remote_key": key if isinstance(key, str) and _REMOTE_KEY_RE.match(key) else "",
        "messages": messages if isinstance(messages, int) else 0,
        "resume_mode": chat.get("resume_mode", "prefix"),
        "home_setup_version": chat.get("home_setup_version"),
    }


def _pack_detail(moving: list[_Job], staying: list[_Job]) -> str:
    if not moving and not staying:
        return "No schedules are running here."
    return f"{len(moving)} schedules to hand over, {len(staying)} stay here."


# ── carry ───────────────────────────────────────────────────────────────────


async def _switch_off(state: "DashboardState", jobs: list[_Job]) -> list[_Job]:
    """Switch *jobs* off here before the archive leaves; return the ones switched."""
    off: list[_Job] = []
    try:
        for job in jobs:
            if await state.crons.enable_job_async(job.id, False):
                off.append(job)
    except Exception:
        logger.warning("pausing schedules for the move failed", exc_info=True)
        await _switch_on(state, off)
        raise sc.CardRejected(
            "the schedules here could not be paused for the move, so nothing was sent; "
            "press Move in again",
            "move_in_jobs_busy",
        ) from None
    return off


async def _switch_on(state: "DashboardState", jobs: list[_Job]) -> None:
    """Switch *jobs* back on here. Best effort: each one is tried."""
    for job in jobs:
        try:
            await state.crons.enable_job_async(job.id, True)
        except Exception:
            logger.warning("could not switch schedule %s back on after the move", job.id)


def _multipart(boundary: str, archive: bytes) -> bytes:
    """*archive* as the one ``file`` part the home's importer reads."""
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="move-in.zip"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    )
    return head.encode("ascii") + archive + f"\r\n--{boundary}--\r\n".encode("ascii")


def _peer_code(payload: Any) -> str:
    code = payload.get("code") if isinstance(payload, dict) else None
    return code if isinstance(code, str) and _PEER_CODE_RE.match(code) else ""


async def _carry(mgr: Any, instance_id: str, archive: bytes) -> dict[str, Any]:
    """Import *archive* on the home; return the home's import summary.

    Goes through ``SshTunnelManager.proxy_request``, the tunnel carrier that keeps
    the minted credential inside the manager. The path is this module's constant,
    never a caller's, which is why the chat proxy's browser-path allowlist does
    not apply here.
    """
    import aiohttp

    from kiro_crew.dashboard.handlers._shared import read_capped_response
    from kiro_crew.instances.ssh_tunnel_manager import ProxyRequestError

    boundary = f"kirocrew-move-in-{uuid.uuid4().hex}"
    try:
        async with mgr.proxy_request(
            instance_id,
            "POST",
            IMPORT_PATH,
            params={"mode": "merge"},
            data=_multipart(boundary, archive),
            content_type=f"multipart/form-data; boundary={boundary}",
        ) as resp:
            status = resp.status
            raw = await read_capped_response(resp, _IMPORT_REPLY_MAX_BYTES)
    except ProxyRequestError as exc:
        code = exc.code if _PEER_CODE_RE.match(exc.code) else "unreachable"
        raise sc.CardRejected(
            f"your home did not confirm the move ({code}), so the schedules still run "
            "here; press Move in again",
            "move_in_carry_failed",
        ) from None
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise sc.CardRejected(
            "your home did not confirm the move (the connection dropped), so the schedules "
            "still run here; press Move in again",
            "move_in_carry_failed",
        ) from None
    reply: Any = None
    if len(raw) <= _IMPORT_REPLY_MAX_BYTES:
        try:
            reply = json.loads(raw)
        except ValueError:
            reply = None
    if not (200 <= status < 300) or not isinstance(reply, dict) or reply.get("ok") is not True:
        code = _peer_code(reply) or f"HTTP {status}"
        raise sc.CardRejected(
            f"your home refused the move ({code}), so the schedules still run here; press "
            "Move in again",
            "move_in_carry_refused",
        )
    summary = reply.get("summary")
    return summary if isinstance(summary, dict) else {}


async def _settle(
    state: "DashboardState",
    card: sc.SetupCard,
    record: dict[str, Any],
    summary: dict[str, Any],
    moving: list[_Job],
    staying: list[_Job],
    switched_off: list[_Job],
    followed: list[str],
) -> dict[str, Any]:
    """Read the home's import summary, hand each schedule to one crew, record it.

    A schedule the home did not take comes back on here. The record is saved on
    the card so a retry does not carry again.
    """
    items = [i for i in summary.get("items") or [] if isinstance(i, str) and _ITEM_RE.match(i)]
    refused_merges = summary.get("refused_merges") or []
    rejected = {str(n) for n in summary.get("rejected_crons") or [] if isinstance(n, str)}
    kept = [{"name": j.name, "reason": KEPT_RUNS_HERE} for j in staying]
    if isinstance(refused_merges, list) and "crons" in refused_merges:
        back, moved = list(moving), []
        kept += [{"name": j.name, "reason": KEPT_HOME_NO_STORE} for j in moving]
    else:
        back = [j for j in moving if j.name in rejected]
        moved = [j for j in moving if j.name not in rejected]
        kept += [{"name": j.name, "reason": KEPT_HOME_REFUSED} for j in back]
    await _switch_on(state, [j for j in switched_off if j in back])
    return await _save_record(
        card,
        {
            **record,
            "carried": True,
            "items": items[:_MAX_ITEMS],
            "settings_moved": any(i.startswith("config (") for i in items),
            "jobs_moved": [{"id": j.id, "name": j.name} for j in moved],
            "jobs_kept_here": kept,
            "jobs_follow_chat": followed,
        },
    )


def _carry_detail(record: dict[str, Any]) -> str:
    moved = len(record.get("jobs_moved") or [])
    kept = len(record.get("jobs_kept_here") or [])
    if not moved and not kept:
        return "Unpacked. No schedules to hand over."
    return f"Unpacked. {moved} schedules now run on your home and are off here; {kept} stay here."


# ── chat ────────────────────────────────────────────────────────────────────


def _hold_for_publication(state: "DashboardState", publication_key: str, bundle: Any) -> None:
    """Revalidate the bundle's transcript chain right before it is sent.

    The same check ``api_instances_send_session`` makes: a membership change since
    the bundle was assembled raises ``SnapshotUnstable``.
    """
    log = state.conversation_log
    if log is not None:
        expected_keys = getattr(bundle, "publication_keys", (publication_key,))
        with log.publication_hold(publication_key, expected_keys=expected_keys):
            pass


async def _send_chat(
    state: "DashboardState", mgr: Any, instance_id: str, slot_key: str
) -> dict[str, Any]:
    """Send the chat at *slot_key* to the home; return where its copy lives."""
    from kiro_crew.dashboard.chat_utils import slot_history_key
    from kiro_crew.dashboard.session_transfer import (
        SnapshotUnstable,
        TranscriptBusy,
        TranscriptWithheld,
        build_transfer_bundle_async,
        local_instance_label,
    )

    slot = state.get_slot(slot_key)
    if slot is None:
        raise sc.CardRejected(
            "this chat is not open, so it could not be brought along", "move_in_chat_missing"
        )
    not_persistent = sc.CardRejected(
        "this chat keeps no transcript, so it cannot be brought along",
        "move_in_chat_not_persistent",
    )
    if getattr(slot, "memory_mode", "persistent") != "persistent":
        raise not_persistent
    try:
        bundle = await build_transfer_bundle_async(
            state, slot, origin=local_instance_label(), with_home_setup=True
        )
        if bundle.get("layer_b_skipped"):
            raise SnapshotUnstable("the setup provider context is not ready")
        await asyncio.to_thread(_hold_for_publication, state, slot_history_key(slot), bundle)
    except (TranscriptBusy, SnapshotUnstable):
        raise sc.CardRejected(
            "this chat could not be copied consistently just now, and nothing else has "
            "moved yet; press Move in again",
            "move_in_chat_busy",
        ) from None
    except TranscriptWithheld:
        raise not_persistent from None
    ok, payload = await mgr.send_session_bundle(instance_id, bundle)
    if not ok:
        code = _peer_code(payload) or "transfer_peer_refused"
        raise sc.CardRejected(
            f"your home did not take this chat ({code}), and nothing else has moved yet; "
            "press Move in again",
            "move_in_chat_failed",
        )
    key = payload.get("key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not _REMOTE_KEY_RE.fullmatch(key):
        raise sc.CardRejected("your home did not return the chat's address", "move_in_chat_failed")
    if payload.get("home_setup_version") != 1 or (
        bundle.get("layer_b") and payload.get("resume_mode") != "session_load"
    ):
        raise sc.CardRejected(
            "your home did not confirm the complete setup conversation; update it and retry",
            "move_in_chat_failed",
        )
    return {
        "remote_key": key,
        "messages": len(bundle.get("messages", [])),
        "resume_mode": payload.get("resume_mode", "prefix"),
        "home_setup_version": payload.get("home_setup_version"),
    }


async def _adopt_chat(
    state: "DashboardState",
    mgr: Any,
    instance_id: str,
    source_key: str,
    remote_key: str,
    *,
    outcome: dict[str, Any] | None = None,
) -> None:
    """Finish the dedicated home handoff without changing general imports."""
    from kiro_crew.dashboard.handlers._shared import read_capped_response
    from kiro_crew.dashboard.setup_transfer import read_preferences
    from kiro_crew.first_run import done_stages

    source = state.get_slot(source_key)
    body = {
        "slot": remote_key,
        "title": str(getattr(source, "title", "") or sf.FIRST_RUN_TITLE)[:256],
        "stages": await asyncio.to_thread(done_stages),
        "preferences": await asyncio.to_thread(read_preferences),
        "outcome": outcome or {},
    }
    async with mgr.proxy_request(
        instance_id,
        "POST",
        "api/setup/home-arrival",
        data=json.dumps(body).encode(),
        content_type="application/json",
    ) as resp:
        raw = await read_capped_response(resp, _IMPORT_REPLY_MAX_BYTES)
        try:
            reply = json.loads(raw)
        except ValueError:
            reply = None
        if resp.status == 409 and _peer_code(reply) == "turn_running":
            raise sc.CardRejected("your home's welcome chat is finishing", "move_in_chat_busy")
        if not (
            resp.status == 200 and isinstance(reply, dict) and reply.get("main_slot") == remote_key
        ):
            raise sc.CardRejected(
                "your chat has arrived, but your home could not make it the main chat; "
                "update Kiro Crew on your home and retry Move in",
                "move_in_adopt_failed",
            )


# ── coming back ─────────────────────────────────────────────────────────────


async def reach_back(state: "DashboardState", card: sc.SetupCard) -> dict[str, Any]:
    """How to reach the home again, for the committed outcome; ``{}`` when unknown.

    ``{"home": {name, tag, region, profile}, "reconnect": [{purpose, command}]}``.
    The tag, region and profile are the build's own, read from the launch job the
    card's build recorded (``private.job_id``); the commands come from
    ``cloud.reconnect``, which refuses a value the cloud commands would refuse,
    so a record that does not read leaves the outcome without commands rather
    than with one that cannot work. No token, URL or credential is in any of it.
    """
    from kiro_crew.cloud.reconnect import home_name, reconnect_commands
    from kiro_crew.dashboard.handlers_cloud import _store
    from kiro_crew.validation import ValidationError

    job_id = str(card.private.get("job_id") or "")
    job = await asyncio.to_thread(_store(state).get, job_id) if job_id else None
    if job is None:
        return {}
    tag = str(job.tag or "")
    region = str(job.region or card.payload.get("region") or "")
    profile = str(job.profile or "")
    try:
        commands = reconnect_commands(tag, region, profile)
    except ValidationError:
        logger.warning("home card %s: its launch record names no usable home", card.id)
        return {}
    return {
        "home": {"name": home_name(tag), "tag": tag, "region": region, "profile": profile},
        "reconnect": commands,
    }


def _open_line(outcome: dict[str, Any]) -> str:
    """The result sentence that tells the agent how the user comes back."""
    from kiro_crew.cloud.reconnect import open_command

    reconnect = outcome.get("reconnect")
    command = open_command(reconnect) if isinstance(reconnect, list) else ""
    if not command:
        return ""
    return (
        " To open the home again from a terminal (this computer, or another one with "
        f"Kiro Crew installed and signed in to AWS): `{command}`. Tell the user that in "
        "one line."
    )


# ── what stays behind ───────────────────────────────────────────────────────


def left_behind() -> dict[str, list[str]]:
    """What the owner sets up again on the home: secret NAMES and connections.

    Names only, never a value: the vault's entries, the credential file's
    credentials (``config.loader.CREDENTIAL_KEYS``, the owner id aside, which the
    home has its own of), and the curated connections this install holds a
    grant for. None of them is in the export.
    """
    from kiro_crew.config.loader import CRED_OWNER_ID, CREDENTIAL_KEYS, read_env_file_credential
    from kiro_crew.config.paths import config_dir
    from kiro_crew.secrets.vault import SecretVault

    secrets: set[str] = set()
    try:
        secrets.update(SecretVault(config_dir()).list_names())
    except Exception:
        logger.warning("listing vault names for the move-in result failed", exc_info=True)
    secrets.update(
        key for key in CREDENTIAL_KEYS if key != CRED_OWNER_ID and read_env_file_credential(key)
    )
    connections: list[str] = []
    try:
        from kiro_crew.connections.registry import get_visible_providers
        from kiro_crew.mcp_grant import grant_presence

        connections = sorted(
            str(p.get("name") or p["slug"])
            for p in get_visible_providers()
            if grant_presence(str(p["mcp_url"]))
        )
    except Exception:
        logger.warning("listing connections for the move-in result failed", exc_info=True)
    return {"secrets": sorted(secrets), "connections": connections}


def _names(values: list[str]) -> str:
    listed = ", ".join(sf._plain(v) for v in values[:_LISTED_NAMES])
    more = len(values) - _LISTED_NAMES
    return listed + (f" and {more} more" if more > 0 else "")


def result_detail(outcome: dict[str, Any]) -> str:
    """The ``[Setup card result]`` detail for a committed live move-in."""
    home = outcome.get("home")
    name = sf._plain((home.get("name") if isinstance(home, dict) else "") or "the home")
    text = (
        f" The crew moved into its home in the cloud ({name}). This chat was copied there "
        "and the user continues it on the home: open the home under Your crews, where the "
        "copy is titled after this chat. This chat stays here as the old copy."
    )
    moved = [str(j.get("name", "")) for j in outcome.get("jobs_moved") or [] if isinstance(j, dict)]
    if moved:
        text += f" These schedules now run on the home and are switched off here: {_names(moved)}."
    followed = [str(n) for n in outcome.get("jobs_follow_chat") or []]
    if followed:
        text += (
            " These schedules reported to this chat and now report to its copy on the home: "
            f"{_names(followed)}."
        )
    kept = [j for j in outcome.get("jobs_kept_here") or [] if isinstance(j, dict)]
    if kept:
        listed = "; ".join(
            f"{sf._plain(j.get('name', ''))} ({j.get('reason', '')})" for j in kept[:_LISTED_NAMES]
        )
        text += f" These schedules stay here and still run on this computer: {listed}."
    if not outcome.get("settings_moved"):
        text += (
            " The home kept its own settings (name, language, timezone); the user sets them "
            "there if they want them to match."
        )
    left = outcome.get("reenter")
    left = left if isinstance(left, dict) else {}
    secrets = [str(s) for s in left.get("secrets") or []]
    connections = [str(c) for c in left.get("connections") or []]
    if secrets:
        text += (
            " Secrets never travel; the user enters these again on the home (names only, you "
            f"never see the values): {_names(secrets)}."
        )
    if connections:
        text += f" Connections to grant again on the home: {_names(connections)}."
    text += " Tell the user this in a few short lines."
    return text + _open_line(outcome)


def simulated_result_detail(outcome: dict[str, Any]) -> str:
    """The ``[Setup card result]`` detail for a committed simulated move-in."""
    text = " The crew moved into its home in the cloud; keep helping the user from here."
    line = _open_line(outcome)
    if line:
        text += (
            " This home is simulated: nothing exists in AWS, so the command below finds no "
            "home; say so if you mention it." + line
        )
    return text
