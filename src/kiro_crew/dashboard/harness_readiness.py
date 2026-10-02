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
  prompt: spawn, ``initialize``, ``session/new``, shut down. A harness whose
  sign-in fails there is caught; one that only fails on its first prompt is caught
  by that turn, which re-shows the sign-in card
  (``setup_flow.reopen_signin_after_auth_failure``). Crew never reads another
  harness's credential files, so there is no third way to ask.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
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
#: The check itself could not finish (a sandbox refusal, a timeout, a probe error).
CHECK_FAILED = "check_failed"

#: Longest the no-prompt handshake may take, a cold adapter install included.
HANDSHAKE_TIMEOUT_SECS = 90.0
_SHUTDOWN_TIMEOUT_SECS = 10.0


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
    """Spawn *backend* and open a session with no prompt, then shut it down."""
    from kiro_crew.acp.client import AcpAuthRequired, AcpClient
    from kiro_crew.config.paths import data_home

    client = AcpClient(
        work_dir=data_home() / "setup" / "harness-check",
        model="auto",
        sandbox_mode="auto",
        session_key=f"setup-harness-check-{backend or 'kiro'}",
        acp_backend=backend,
    )
    try:
        await asyncio.wait_for(client.ensure_ready(), timeout=HANDSHAKE_TIMEOUT_SECS)
    except AcpAuthRequired as exc:
        return Verdict(backend, NOT_SIGNED_IN, str(exc)[:300])
    except asyncio.TimeoutError:
        return Verdict(backend, CHECK_FAILED, "the harness did not answer in time")
    except Exception as exc:  # noqa: BLE001 - reported on the card, never raised
        return Verdict(backend, CHECK_FAILED, f"{type(exc).__name__}: {exc}"[:300])
    finally:
        try:
            await asyncio.wait_for(client.shutdown(), timeout=_SHUTDOWN_TIMEOUT_SECS)
        except Exception:  # noqa: BLE001 - teardown of our own child
            logger.debug("harness check client shutdown failed", exc_info=True)
    return Verdict(backend, READY)
