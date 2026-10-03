"""Whether the chosen agent harness can answer: the first run's sign-in step check.

The scripted first run (``setup_flow``: the ``harness`` and ``harness_signin``
cards) asks this once per owner click on the sign-in card's Continue, and never
on a timer. The verdict decides whether the next step shows; it is not a latch,
and nothing else reads it.

Per harness, by positive identity (harness-parity H5):

* the harnesses that run kiro-cli (``ACP_BACKENDS_KIRO_CLI_PREREQUISITE``) are
  answered by the Kiro prerequisite service's FRESH probe: the resolved binary,
  then ``whoami``. Never by spawning ``kiro-cli acp``: an unauthenticated kiro-cli
  opens an interactive browser sign-in for any subcommand it runs. KAS also counts
  as signed in when Crew's own vault holds a usable Kiro identity, because its
  relay draws the token from there instead;
* every other harness is answered by its install probe, then a handshake with no
  prompt: the session provider a real chat gets, built by the same factory from
  the same config (its sandbox mode and wrap included), started -- spawn,
  ``initialize``, ``session/new`` -- and shut down. A harness whose sign-in fails
  there is caught; one that only fails on its first prompt is caught by that turn,
  which re-shows the sign-in card (``setup_flow.reopen_signin_after_auth_failure``).
  A sandbox that refuses to start there is reported by its layer, in the words of
  the classified error, and never retried or downgraded.

The card also shows, before any click, whether the harness says it is signed in
(:func:`signed_in`). That answer comes from the harness's OWN status command, the
one its ``host_auth`` declaration names, run inside the sandbox its session gets.
It decides the card's words only; Continue still starts the harness, because a
harness can be signed in and still unable to start.

**Kiro Crew never reads another harness's credential files.** It asks the harness:
by its status command, or by starting it. A harness with neither cannot be asked,
and the first turn reports the truth.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.acp.client import AcpSandboxInitFailed
    from kiro_crew.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

#: The harness is installed and answered.
READY = "ready"
#: Its install probe says a component is missing.
NOT_INSTALLED = "not_installed"
#: It is installed and reported itself signed out.
NOT_SIGNED_IN = "not_signed_in"
#: The installed kiro-cli is too old for the ``acp`` command every session uses.
OUTDATED = "outdated"
#: The check itself could not finish (a timeout, a probe error).
CHECK_FAILED = "check_failed"
#: macOS refused the harness's own sandbox inside Kiro Crew's: Seatbelt does not nest.
SANDBOX_NESTED = "sandbox_nested"
#: Kiro Crew's own sandbox could not start the harness on this host.
SANDBOX_CREW = "sandbox_crew"
#: The harness's own sandbox refused, with no Kiro Crew sandbox around it.
SANDBOX_HARNESS = "sandbox_harness"

#: Longest the no-prompt handshake may take, a cold adapter install included.
HANDSHAKE_TIMEOUT_SECS = 90.0
_SHUTDOWN_TIMEOUT_SECS = 10.0
#: How much of a failure's own words the card keeps behind its detail disclosure.
_DETAIL_MAX_CHARS = 2000
#: How long a status command's answer is reused. Several open tabs poll the card.
STATUS_CACHE_SECS = 8.0
#: The macOS sandbox wrapper's own refusal line: Kiro Crew's layer failed to apply,
#: rather than a harness's sandbox failing inside it.
_SANDBOX_EXEC_LINE = re.compile(r"^\s*sandbox-exec:", re.MULTILINE)


@dataclass(frozen=True)
class Verdict:
    """What the check found. ``code`` is one of the constants above."""

    backend: str
    code: str
    #: English, for the card's error notice and the logs; the dashboard localizes
    #: by ``code`` and shows this only as the detail.
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.code == READY


async def check(state: "DashboardState", backend: str) -> Verdict:
    """Ask *backend* whether it can answer now. Never raises."""
    from kiro_crew.agent_sdk.backends import ACP_BACKENDS_KIRO_CLI_PREREQUISITE

    try:
        if backend in ACP_BACKENDS_KIRO_CLI_PREREQUISITE:
            return await _check_kiro_cli(state, backend)
        return await _check_other(backend)
    except Exception as exc:  # noqa: BLE001 - a failed check is a verdict, never a crash
        logger.warning("harness check for %r failed", backend, exc_info=True)
        return Verdict(backend, CHECK_FAILED, f"{type(exc).__name__}: {exc}"[:300])


async def _check_kiro_cli(state: "DashboardState", backend: str) -> Verdict:
    from kiro_crew.agent_sdk.backends import ACP_BACKENDS_HOST_AUTH_CALLBACK

    service = getattr(state, "kiro_prerequisite_service", None)
    if service is None:
        return Verdict(backend, CHECK_FAILED, "the Kiro CLI check is not available")
    snap: dict[str, Any] = await service.snapshot(force=True)
    if snap.get("sandbox_unavailable"):
        return Verdict(backend, CHECK_FAILED, str(snap.get("sandbox_detail") or "sandbox"))
    if snap.get("probe_timed_out"):
        return Verdict(backend, CHECK_FAILED, "the check timed out")
    if not snap.get("installed"):
        if snap.get("probe_error"):
            return Verdict(backend, CHECK_FAILED, str(snap.get("probe_error")))
        return Verdict(backend, NOT_INSTALLED, "Kiro CLI is not installed")
    if snap.get("acp_supported") is False:
        return Verdict(backend, OUTDATED, "this Kiro CLI is too old for the acp command")
    signed_in = bool(snap.get("authenticated"))
    if not signed_in and backend in ACP_BACKENDS_HOST_AUTH_CALLBACK:
        from kiro_crew.auth.bridge import vault_holds_identity

        signed_in = await asyncio.to_thread(vault_holds_identity)
    if not signed_in:
        return Verdict(backend, NOT_SIGNED_IN, "Kiro CLI is not signed in")
    return Verdict(backend, READY)


async def _check_other(backend: str) -> Verdict:
    from kiro_crew.agent_sdk import backend_install

    # On the loop, then the probe off it: the owner may have just run the install.
    backend_install.forget_for_recheck(backend)
    state = await asyncio.to_thread(backend_install.probe_backend, backend)
    if state.installed == backend_install.MISSING:
        missing = ", ".join(state.missing_components) or state.policy_id
        return Verdict(backend, NOT_INSTALLED, f"missing {missing}")
    return await _handshake(backend)


async def _handshake(backend: str) -> Verdict:
    """Start *backend* as a chat would and open a session with no prompt, then stop it.

    The provider comes from the session factory and the loaded config, so the check
    runs what the first turn will run: the configured sandbox mode, the same wrap and
    credential mask, the same harness selection. The factory names the harness from
    the config the harness card wrote; a check that would start another one says so
    instead of vouching for it.
    """
    from kiro_crew.acp.client import AcpAuthRequired, AcpSandboxInitFailed
    from kiro_crew.agent_files import MAIN_CHAT_AGENT_NAME
    from kiro_crew.config.loader import KiroCrewConfig, build_provider_factory
    from kiro_crew.config.paths import data_home

    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    factory = await asyncio.to_thread(build_provider_factory, cfg)
    work_dir = data_home() / "setup" / "harness-check"
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    provider = factory(
        f"setup-check:{backend or 'kiro'}", agent=MAIN_CHAT_AGENT_NAME, cwd=str(work_dir)
    )
    resolved = str(getattr(provider.client, "backend", "") or "")
    if resolved != backend:
        return Verdict(
            backend,
            CHECK_FAILED,
            f"a chat would start the {resolved or 'kiro'} engine, not the one chosen",
        )
    try:
        await asyncio.wait_for(provider.start(), timeout=HANDSHAKE_TIMEOUT_SECS)
    except AcpAuthRequired as exc:
        return Verdict(backend, NOT_SIGNED_IN, str(exc)[:_DETAIL_MAX_CHARS])
    except AcpSandboxInitFailed as exc:
        return Verdict(backend, sandbox_code(exc), str(exc)[:_DETAIL_MAX_CHARS])
    except asyncio.TimeoutError:
        return Verdict(backend, CHECK_FAILED, "the harness did not answer in time")
    except Exception as exc:  # noqa: BLE001 - reported on the card, never raised
        return Verdict(backend, CHECK_FAILED, f"{type(exc).__name__}: {exc}"[:_DETAIL_MAX_CHARS])
    finally:
        try:
            await asyncio.wait_for(provider.shutdown(), timeout=_SHUTDOWN_TIMEOUT_SECS)
        except Exception:  # noqa: BLE001 - teardown of our own child
            logger.debug("harness check provider shutdown failed", exc_info=True)
    return Verdict(backend, READY)


def sandbox_code(exc: "AcpSandboxInitFailed") -> str:
    """Which sandbox refused, as a verdict code, from the classified error alone.

    The layer is the one :func:`acp.client.sandbox_init_failure` read off the argv
    Kiro Crew built. On macOS a refusal inside Kiro Crew's own wrap that is not the
    wrapper's own ``sandbox-exec:`` line is the harness starting a sandbox of its
    own inside it, which Seatbelt refuses. The code only picks the plain words; the
    remedy, and whether any switch is offered, stay in the error's own message.
    """
    from kiro_crew.sandbox import SANDBOX_LAYER_HARNESS

    if exc.layer == SANDBOX_LAYER_HARNESS:
        return SANDBOX_HARNESS
    if sys.platform == "darwin" and not _SANDBOX_EXEC_LINE.search(exc.detail):
        return SANDBOX_NESTED
    return SANDBOX_CREW


# ── signed in? the harness's own status command ──

_status_cache: dict[str, tuple[float, bool | None]] = {}
_status_locks: dict[str, asyncio.Lock] = {}


async def signed_in(backend: str) -> bool | None:
    """Whether *backend* says it is signed in: ``True``, ``False``, or ``None`` (unknown).

    Asked through the harness's own status command (``host_auth``'s
    ``sign_in_status_command``), never by reading its credential files. ``None``
    when it declares no command, the command is not installed, it cannot run in the
    session's sandbox, it does not answer in time, or it answers in a shape its
    reading does not know (an older version without the command). Never raises.
    One answer per :data:`STATUS_CACHE_SECS` per harness, however many tabs ask.
    """
    lock = _status_locks.setdefault(backend, asyncio.Lock())
    async with lock:
        cached = _status_cache.get(backend)
        if cached is not None and time.monotonic() - cached[0] < STATUS_CACHE_SECS:
            return cached[1]
        try:
            answer = await _ask_status(backend)
        except Exception:  # noqa: BLE001 - unknown is an answer, never a crash
            logger.warning("sign-in status for %r failed", backend, exc_info=True)
            answer = None
        _status_cache[backend] = (time.monotonic(), answer)
        return answer


def forget_status(backend: str) -> None:
    """Drop the cached answer, so the next ask runs the command again."""
    _status_cache.pop(backend, None)


async def _ask_status(backend: str) -> bool | None:
    from kiro_crew.acp.client import resolve_harness_executable, run_sign_in_status_command
    from kiro_crew.agent_sdk.host_auth import declaration_for
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.config.paths import data_home

    declaration = declaration_for(backend)
    command = declaration.sign_in_status_command
    if not command:
        return None
    binary = await asyncio.to_thread(resolve_harness_executable, command[0])
    if not binary:
        return None
    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    work_dir = data_home() / "setup" / "harness-check"
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    answer = await run_sign_in_status_command(
        backend, [binary, *command[1:]], mode=cfg.agent.sandbox, work_dir=str(work_dir)
    )
    if answer is None:
        return None
    return read_sign_in_status(declaration.sign_in_status_reading, *answer)


def read_sign_in_status(reading: str, exit_code: int, output: str) -> bool | None:
    """Read a status command's answer by its declared *reading*. ``None``: unknown.

    Both directions need the exit code and the words to agree, so an error that
    happens to print one of them is not taken for an answer.
    """
    from kiro_crew.agent_sdk.host_auth import (
        SIGN_IN_STATUS_JSON_LOGGED_IN,
        SIGN_IN_STATUS_LOGGED_IN_LINE,
    )

    if reading == SIGN_IN_STATUS_JSON_LOGGED_IN:
        start = output.find("{")
        if start < 0:
            return None
        try:
            parsed, _ = json.JSONDecoder().raw_decode(output[start:])
        except ValueError:
            return None
        logged_in = parsed.get("loggedIn") if isinstance(parsed, dict) else None
        if logged_in is True and exit_code == 0:
            return True
        if logged_in is False and exit_code != 0:
            return False
        return None
    if reading == SIGN_IN_STATUS_LOGGED_IN_LINE:
        lines = [line.strip() for line in output.splitlines()]
        if exit_code == 0 and any(line.startswith("Logged in") for line in lines):
            return True
        if exit_code != 0 and any(line.startswith("Not logged in") for line in lines):
            return False
        return None
    return None
