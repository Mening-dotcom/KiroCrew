"""A failed run whose conversation a ``spawn_continue`` adopted is never re-spawned
by the dashboard's retry route.

The reported shape, built from the real run records: a ``spawn_run`` member
hit ``turn_limit:100`` and was left ``failed``; ``spawn_continue`` adopted its
conversation and the continuation finished the task. The failed card stays on
the panel, so ``Retry failed (N)`` keeps offering it, and ``POST
/api/spawn/{id}/retry`` re-spawns the ORIGINAL prompt -- original task text,
no conversation key, a new run id, the same worktree -- once per click, each
a second writer on work the continuation has already done.

Two halves of one fence, each pinned on both sides:

- the ROUTE refuses such a run with a typed 409 and starts nothing, and still
  re-spawns a failed run nobody continued;
- the MARK (``SubagentInfo.superseded_by``) is set on the original when a
  continuation is accepted, is NOT set when the continuation is refused, and
  is what ``continuation_of`` answers once the continuation itself has left the
  registry -- the registry scan behind it answers for a continuation that
  reached the registry without the mark.

And the CLAIM that makes the fence hold across the awaits on both sides: the
retry route and both ``continue_conversation`` entries reserve the
conversation's admission before their first await, so a retry and a
continuation racing on one failed run are serialized -- the loser gets its
side's typed refusal, an admitted retry holds the conversation while its run
is in flight, and a refusal on either side rolls the claim back.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from kiro_crew import subagent_persistence as sp
from kiro_crew.dashboard.handlers import messaging as handlers
from kiro_crew.execution_context import ExecutionContext, MemoryStoreRef
from kiro_crew.subagent import SubagentInfo, SubagentManager

# ``SubagentManager.spawn`` refuses while the host looks short of memory, which
# is the runner's state, not this file's input.
pytestmark = pytest.mark.usefixtures("healthy_host_memory")

# Subagent-registry isolation is the root conftest's autouse
# ``_isolate_subagents_dir``; every manager built here is closed at teardown.


@pytest.fixture(autouse=True)
def _close_subagent_managers(close_subagent_managers):
    """The body lives in ``conftest``."""


ORIGINAL = "orig0000deadbeef"
CONTINUATION = "cont0000deadbeef"
PARENT = "dashboard:main"
TASK = "implement the change in the worktree, run the verify target, commit"


def _mock_sessions(resumed: bool = True) -> MagicMock:
    """The SessionManager double ``test_subagent_continuable`` builds, trimmed to
    what a continuation's prelude and run read."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    provider = AsyncMock()
    provider.start = AsyncMock()
    provider.shutdown = AsyncMock()
    provider.context_usage_pct = lambda: 0.0
    provider.context_window_tokens = lambda: 100000
    provider.context_used_tokens = lambda: 0
    provider.session_id = "sid-123"
    provider.cwd = ""

    async def _empty_stream(*_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
        return
        yield  # noqa: unreachable -- makes this an async generator

    provider.stream = MagicMock(side_effect=lambda *a, **kw: _empty_stream())
    sessions.get_or_create = AsyncMock(return_value=(provider, True, resumed))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.get_agent_selection = MagicMock(
        side_effect=lambda key: ("template", sessions.get_agent(key))
    )
    sessions.mark_continuable = MagicMock()
    sessions.unmark_continuable = MagicMock()
    sessions.is_continuable = MagicMock(return_value=False)
    sessions.resumable_sid = MagicMock(return_value="sid-123")
    sessions.forget_conversation = MagicMock(return_value="sid-123")
    sessions.conversation_provider = MagicMock(return_value="acp")
    sessions.get_provider = MagicMock(return_value=None)
    return sessions


def _manager(sessions: MagicMock | None = None) -> SubagentManager:
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks.on_tool_call = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = True
    return SubagentManager(sessions=sessions or _mock_sessions(), ctx_builder=ctx)


def _execution() -> ExecutionContext:
    return ExecutionContext(None, MemoryStoreRef("default"), "template", "kirocrew")


def _turn_limited_original(cwd: str) -> SubagentInfo:
    """The failed member exactly as ``_run_inner``'s turn-limit arm leaves it,
    with the run folder and the ``turn_limit`` tombstone it writes."""
    sp.create_agent_folder(ORIGINAL, task=TASK, parent_session=PARENT, max_turns=100)
    info = SubagentInfo(
        id=ORIGINAL,
        task=TASK,
        parent_session_key=PARENT,
        max_turns=100,
        cwd=cwd,
        execution_context=_execution(),
    )
    info._raw_task = TASK
    info.result = "_Partial output._"
    info.error = "turn_limit:100"
    info.done = True
    SubagentManager._write_tombstone(info, "turn_limit")
    return info


def _finished_continuation() -> SubagentInfo:
    """The ``spawn_continue`` run on the original's conversation, finished and
    delivered -- the record the reporter's resumed run left behind."""
    sp.create_agent_folder(CONTINUATION, task="carry on", parent_session=PARENT)
    sp.write_result_chunk(CONTINUATION, "verify is green, committed")
    sp.mark_delivered(CONTINUATION, elapsed=900.0, credits=0.0)
    info = SubagentInfo(
        id=CONTINUATION,
        task="carry on",
        parent_session_key=PARENT,
        keep=True,
        conversation_key=f"subagent:{ORIGINAL}",
        execution_context=_execution(),
    )
    info.result = "verify is green, committed"
    info.done = True
    return info


class _Req:
    """The request the retry route reads: ``app["state"]``, the route id, and
    the owner-identity fields the spawn helpers probe (dashboard owner, no app
    claim) -- the same reads ``test_handlers_messaging_coverage``'s double
    answers."""

    def __init__(self, state: Any, agent_id: str) -> None:
        self.app = {"state": state}
        self.match_info = {"agent_id": agent_id}
        self.headers: dict[str, str] = {}
        self.remote = "127.0.0.1"
        self._extra: dict[str, Any] = {"app": "", "user": "owner"}

    def __contains__(self, key: str) -> bool:
        return key in self._extra

    def __getitem__(self, key: str) -> Any:
        return self._extra[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._extra.get(key, default)


def _retry(manager: SubagentManager, agent_id: str) -> web.Response:
    state = MagicMock()
    state.subagents = manager
    return asyncio.run(handlers.api_spawn_retry(_Req(state, agent_id)))


def _payload(resp: web.Response) -> dict[str, Any]:
    body = resp.body
    assert isinstance(body, (bytes, bytearray))
    return json.loads(body)


def _recording_spawn(manager: SubagentManager, started: list[dict[str, Any]]) -> None:
    """Replace both spawn entries with recorders: the route must not even ASK
    for a run, so no run is launched behind the assertion."""

    async def spawn_async(task: str, **kwargs: Any) -> SubagentInfo:
        started.append({"task": task, **kwargs})
        return SubagentInfo(id="fresh0000deadbeef", task=task)

    def spawn(task: str, **kwargs: Any) -> SubagentInfo:
        started.append({"task": task, **kwargs})
        return SubagentInfo(id="fresh0000deadbeef", task=task)

    manager.spawn_async = spawn_async  # type: ignore[method-assign]
    manager.spawn = spawn  # type: ignore[method-assign]


def _run_folders() -> set[str]:
    return {p.name for p in sp._subagents_dir().iterdir() if p.is_dir()}


RETRY = "retry000deadbeef"


def _gated_spawn(
    manager: SubagentManager,
    started: list[dict[str, Any]],
    *,
    park: Any,
    gate: asyncio.Event,
    parked: asyncio.Event,
) -> None:
    """Replace ``spawn_async`` with a recorder that PARKS the call *park*
    selects until *gate* is set -- the awaited store accept inside
    ``spawn_async`` (``taskq_accept_record`` on the writer thread), made
    deterministic -- and admits every call: one carrying a
    ``conversation_key`` as the run ``CONTINUATION`` on that key, any other as
    the fresh run ``RETRY``. The admitted run is registered live, as the real
    ``spawn_async`` registers a started run before it returns. ``parked``
    reports that the gated call has reached its await, so the test interleaves
    on that event instead of on a sleep."""

    async def spawn_async(task: str, **kwargs: Any) -> SubagentInfo:
        started.append({"task": task, **kwargs})
        if park(kwargs):
            parked.set()
            await gate.wait()
        conv_key = str(kwargs.get("conversation_key") or "")
        if conv_key:
            info = SubagentInfo(id=CONTINUATION, task=task, keep=True, conversation_key=conv_key)
        else:
            info = SubagentInfo(id=RETRY, task=task)
        manager._agents[info.id] = info
        return info

    manager.spawn_async = spawn_async  # type: ignore[method-assign]


def _state(manager: SubagentManager) -> Any:
    state = MagicMock()
    state.subagents = manager
    return state


class TestRetryAndContinuationAdmissionRace:
    """A panel Retry click and a continuation (a ``spawn_continue``, or the
    follow-up watcher's automatic dispatch) driven CONCURRENTLY on ONE failed
    run start exactly one run. Each side's gate is a single read, and each
    side awaits between that read and its own spawn -- the retry route's warm
    of the project agents, ``spawn_async``'s store accept on the continuation
    side -- so without the fence the other side's read lands in that window,
    finds nothing, and two runs enter one worktree.

    The fence has two halves. The conversation's admission LOCK is held by
    both event-loop paths from their first check through their spawn and
    settle, so the second claimant waits and reads the first's outcome: the
    retry gets the typed 409 naming the continuation that was admitted, the
    continuation gets a ``conversation_busy`` naming the retry run in flight.
    The CLAIM, reserved before the first await and rolled back on refusal, is
    what a reader without the lock sees -- the sync ``continue_conversation``
    entry is refused at once, and ``continuation_of`` answers ``pending`` for
    a continuation whose spawn has not returned yet.

    Every interleaving is driven by injected gates on the documented awaits,
    never by sleeps."""

    @pytest.mark.asyncio
    async def test_retry_first_exactly_one_run_starts_and_the_continuation_is_refused(
        self, tmp_path
    ) -> None:
        """Retry first: its gate read found no continuation, and it is parked at
        ``warm_project_agents_for_spawn`` (the await between that read and the
        spawn) when the continuation arrives. The continuation waits on the
        lock, then finds the retry's run admitted and in flight: it is refused,
        nothing of it is launched, and the retry's run is the only one spawned."""
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        original.agent = "worker"  # the route warms the project agents only for a named one
        manager._agents[ORIGINAL] = original
        started: list[dict[str, Any]] = []
        gate, parked = asyncio.Event(), asyncio.Event()
        _gated_spawn(manager, started, park=lambda _kw: False, gate=gate, parked=parked)

        async def warm_parked(*_args: Any, **_kwargs: Any) -> None:
            parked.set()
            await gate.wait()

        with (
            patch.object(handlers, "warm_project_agents_for_spawn", warm_parked),
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            retry = asyncio.ensure_future(handlers.api_spawn_retry(_Req(_state(manager), ORIGINAL)))
            await parked.wait()
            # The retry has passed its gate and is parked before its spawn.
            assert started == []
            continuation = asyncio.ensure_future(
                manager.continue_conversation_async(ORIGINAL, "carry on")
            )
            gate.set()
            resp, child = await asyncio.gather(retry, continuation)

        # Exactly one run was asked for, the retry's, with no conversation key.
        assert [s["task"] for s in started] == [TASK]
        assert "conversation_key" not in started[0]
        assert resp.status == 200, _payload(resp)
        assert _payload(resp)["id"] == RETRY
        assert child is not None and child.done, "the continuation was admitted beside the retry"
        assert child.error.startswith("conversation_busy"), child.error
        assert RETRY in child.error and ORIGINAL in child.error
        assert original.superseded_by == ""
        # The retry's run holds the conversation; the lock went with its last user.
        assert manager._conversation_admissions == {ORIGINAL: ("retry", RETRY)}
        assert manager._conversation_admission_locks == {}

    @pytest.mark.asyncio
    async def test_continuation_first_exactly_one_run_starts_and_the_retry_is_refused(
        self, tmp_path
    ) -> None:
        """Continuation first: it passed the busy check and is parked inside
        ``spawn_async`` (the awaited store accept, before the child exists in
        any registry and before the mark lands) when the Retry click arrives.
        The retry waits on the lock, then finds the mark: the typed 409 naming
        the continuation, nothing launched, and the continuation's run is the
        only one spawned."""
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        manager._agents[ORIGINAL] = original
        started: list[dict[str, Any]] = []
        gate, parked = asyncio.Event(), asyncio.Event()
        _gated_spawn(
            manager,
            started,
            park=lambda kw: bool(kw.get("conversation_key")),
            gate=gate,
            parked=parked,
        )

        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            continuation = asyncio.ensure_future(
                manager.continue_conversation_async(ORIGINAL, "carry on")
            )
            await parked.wait()
            # The continuation is inside its spawn: no child in any registry
            # yet, no mark yet -- the state the retry's gate read lands on.
            assert manager._agents.keys() == {ORIGINAL}
            assert original.superseded_by == ""
            retry = asyncio.ensure_future(handlers.api_spawn_retry(_Req(_state(manager), ORIGINAL)))
            gate.set()
            child, resp = await asyncio.gather(continuation, retry)

        # Exactly one run was asked for, the continuation's, on the conversation.
        assert [s["task"] for s in started] == ["carry on"]
        assert started[0]["conversation_key"] == f"subagent:{ORIGINAL}"
        assert resp.status == 409, _payload(resp)
        body = _payload(resp)
        assert body["code"] == "superseded_by_continuation"
        assert body["continued_by"] == CONTINUATION
        assert child is not None and not child.error, child.error
        assert child.id == CONTINUATION
        assert original.superseded_by == CONTINUATION
        assert manager.continuation_of(ORIGINAL) == CONTINUATION
        # The settled continuation released its claim; the mark is the record.
        assert manager._conversation_admissions == {}
        assert manager._conversation_admission_locks == {}

    @pytest.mark.asyncio
    async def test_a_refused_continuation_rolls_the_reservation_back_under_the_lock(
        self, tmp_path
    ) -> None:
        """The reservation a continuation takes before ``spawn_async`` is rolled
        back when the spawn refuses it, so the retry that waited on the lock
        is admitted, exactly as if the continuation had never been asked for."""
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        manager._agents[ORIGINAL] = original
        started: list[dict[str, Any]] = []
        gate, parked = asyncio.Event(), asyncio.Event()
        refusal = SubagentInfo(
            id="refused00deadbeef",
            task="carry on",
            done=True,
            error="spawn refused: task store unavailable (locked)",
        )

        async def spawn_async(task: str, **kwargs: Any) -> SubagentInfo:
            started.append({"task": task, **kwargs})
            if kwargs.get("conversation_key"):
                parked.set()
                await gate.wait()
                return refusal
            info = SubagentInfo(id=RETRY, task=task)
            manager._agents[info.id] = info
            return info

        manager.spawn_async = spawn_async  # type: ignore[method-assign]
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            continuation = asyncio.ensure_future(
                manager.continue_conversation_async(ORIGINAL, "carry on")
            )
            await parked.wait()
            retry = asyncio.ensure_future(handlers.api_spawn_retry(_Req(_state(manager), ORIGINAL)))
            gate.set()
            child, resp = await asyncio.gather(continuation, retry)

        assert child is refusal
        assert original.superseded_by == ""
        assert resp.status == 200, _payload(resp)
        assert _payload(resp)["id"] == RETRY
        assert [s["task"] for s in started] == ["carry on", TASK]
        assert manager._conversation_admissions == {ORIGINAL: ("retry", RETRY)}
        assert manager._conversation_admission_locks == {}

    @pytest.mark.asyncio
    async def test_the_reservation_is_what_a_reader_without_the_lock_sees(self, tmp_path) -> None:
        """While either side is parked mid-admission with the lock held, the
        claim stands for it: the sync ``continue_conversation`` entry (which
        cannot wait) is refused at once, and ``continuation_of`` answers
        ``pending`` for a continuation whose spawn has not returned yet."""
        # A retry parked at its agent warm.
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        original.agent = "worker"
        manager._agents[ORIGINAL] = original
        started: list[dict[str, Any]] = []
        gate, parked = asyncio.Event(), asyncio.Event()
        _gated_spawn(manager, started, park=lambda _kw: False, gate=gate, parked=parked)

        async def warm_parked(*_args: Any, **_kwargs: Any) -> None:
            parked.set()
            await gate.wait()

        with (
            patch.object(handlers, "warm_project_agents_for_spawn", warm_parked),
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            retry = asyncio.ensure_future(handlers.api_spawn_retry(_Req(_state(manager), ORIGINAL)))
            await parked.wait()
            assert manager._conversation_admissions == {ORIGINAL: ("retry", "")}
            sync_child = manager.continue_conversation(ORIGINAL, "carry on")
            gate.set()
            resp = await retry
        assert sync_child is not None and sync_child.done
        assert sync_child.error.startswith("conversation_busy"), sync_child.error
        assert f"a retry of run {ORIGINAL} is being admitted" in sync_child.error
        assert resp.status == 200 and [s["task"] for s in started] == [TASK]

        # A continuation parked inside its spawn.
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        manager._agents[ORIGINAL] = original
        started, gate, parked = [], asyncio.Event(), asyncio.Event()
        _gated_spawn(
            manager,
            started,
            park=lambda kw: bool(kw.get("conversation_key")),
            gate=gate,
            parked=parked,
        )
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            continuation = asyncio.ensure_future(
                manager.continue_conversation_async(ORIGINAL, "carry on")
            )
            await parked.wait()
            assert manager._conversation_admissions == {ORIGINAL: ("continuation", "")}
            assert manager.continuation_of(ORIGINAL) == "pending"
            sync_child = manager.continue_conversation(ORIGINAL, "another")
            gate.set()
            child = await continuation
        assert sync_child is not None and sync_child.done
        assert f"a continuation of run {ORIGINAL} is being admitted" in sync_child.error
        assert child is not None and child.id == CONTINUATION
        assert manager.continuation_of(ORIGINAL) == CONTINUATION
        assert [s["task"] for s in started] == ["carry on"]
        assert manager._conversation_admission_locks == {}


class TestAdmissionLockLifecycle:
    """The admission lock has ONE entry, the ``async with`` handle, so it is never
    held outside a scope that releases it. A claimant cancelled anywhere in its
    entry -- waiting for the lock, or during the stale-hold observation that
    runs once the lock is held -- or a body that raises, leaves the lock map as
    it was found and the next claimant is admitted. Red on ``5d22fab457``: the
    observation ran after the acquire and before any releasing scope, so the
    follow-up watcher cancelled there by a parent teardown or a stage boundary
    left the lock held, and every later retry and continuation of that
    conversation waited on it forever."""

    def _parked_store(self) -> tuple[MagicMock, asyncio.Event, asyncio.Event]:
        """A task store whose off-loop read PARKS until *gate* is set and reports
        reaching it on *reading* -- the await the entry performs for a retry's
        claim naming a run the loop cannot see, made deterministic."""
        store = MagicMock()
        reading, gate = asyncio.Event(), asyncio.Event()

        async def run(fn: Any, *args: Any) -> Any:
            reading.set()
            await gate.wait()
            return fn(*args)

        store.run = run
        store.state_of = MagicMock(return_value="queued")
        return store, reading, gate

    @pytest.mark.asyncio
    async def test_a_claimant_cancelled_during_the_hold_observation_leaves_the_lock_free(
        self, tmp_path
    ) -> None:
        manager = _manager()
        manager._agents[ORIGINAL] = _turn_limited_original(str(tmp_path))
        # A retry's claim naming a run in no registry: the normal state once that
        # run has ended, and the one case the entry reads the durable row for.
        manager._conversation_admissions[ORIGINAL] = ("retry", RETRY)
        store, reading, gate = self._parked_store()
        manager._taskq = store
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            # The follow-up watcher's dispatch, cancelled by a parent teardown while
            # the entry has the lock and is reading the retry's row.
            watcher = asyncio.ensure_future(
                manager.continue_conversation_async(ORIGINAL, "carry on")
            )
            await reading.wait()
            watcher.cancel()
            with pytest.raises(asyncio.CancelledError):
                await watcher
            # The map is as it was found: no lock record outlives the cancelled entry.
            assert manager._conversation_admission_locks == {}
            # The next claimant is admitted rather than parked behind a lock nobody
            # can release; the retry's row has ended, so its hold lapses on this read.
            gate.set()
            store.state_of = MagicMock(return_value="cancelled")
            started: list[dict[str, Any]] = []
            _recording_spawn(manager, started)
            resp = await asyncio.wait_for(
                handlers.api_spawn_retry(_Req(_state(manager), ORIGINAL)), timeout=5
            )
        assert resp.status == 200, _payload(resp)
        assert [s["task"] for s in started] == [TASK]
        assert manager._conversation_admission_locks == {}

    @pytest.mark.asyncio
    async def test_a_claimant_cancelled_while_waiting_counts_itself_out(self, tmp_path) -> None:
        """A second claimant parked at the lock is cancelled: it leaves no trace in
        the record's user count, and the record goes with the holder's exit."""
        manager = _manager()
        manager._agents[ORIGINAL] = _turn_limited_original(str(tmp_path))
        holder = manager.conversation_admission(ORIGINAL)
        await holder.__aenter__()
        waiter = asyncio.ensure_future(manager.continue_conversation_async(ORIGINAL, "carry on"))
        await asyncio.sleep(0)  # the waiter reaches ``lock.acquire()`` and parks
        assert manager._conversation_admission_locks[ORIGINAL].users == 2
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert manager._conversation_admission_locks[ORIGINAL].users == 1
        await holder.__aexit__(None, None, None)
        assert manager._conversation_admission_locks == {}

    @pytest.mark.asyncio
    async def test_a_body_that_raises_releases_the_lock_and_rolls_the_claim_back(
        self, tmp_path
    ) -> None:
        """An exception between the route's gate and its spawn -- here the agent
        warm -- leaves neither the lock nor the retry's claim behind."""
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        original.agent = "worker"  # the route warms the project agents only for a named one
        manager._agents[ORIGINAL] = original
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)

        async def warm_fails(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("agent warm failed")

        with (
            patch.object(handlers, "warm_project_agents_for_spawn", warm_fails),
            pytest.raises(RuntimeError, match="agent warm failed"),
        ):
            await handlers.api_spawn_retry(_Req(_state(manager), ORIGINAL))
        assert started == []
        assert manager._conversation_admissions == {}
        assert manager._conversation_admission_locks == {}


class TestRetryHoldsTheConversation:
    """An admitted retry is a fresh run of the failed prompt in the same
    worktree: while it is in flight the conversation refuses a continuation
    (``conversation_busy`` naming the retry) and a second retry (typed 409
    ``retry_in_flight``), and the hold lapses with the run -- finished,
    cancelled while queued, or evicted after finishing -- with no hook on the
    run's own end."""

    def _admitted_retry(self, manager: SubagentManager, cwd: str) -> SubagentInfo:
        original = _turn_limited_original(cwd)
        manager._agents[ORIGINAL] = original
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)
        resp = _retry(manager, ORIGINAL)
        assert resp.status == 200, _payload(resp)
        assert [s["task"] for s in started] == [TASK]
        retry = SubagentInfo(id="fresh0000deadbeef", task=TASK, cwd=cwd)
        manager._agents[retry.id] = retry  # the run the recorder stood in for, live
        return retry

    def test_a_live_retry_refuses_a_continuation_from_both_entries(self, tmp_path) -> None:
        manager = _manager()
        retry = self._admitted_retry(manager, str(tmp_path))
        assert manager._conversation_admissions == {ORIGINAL: ("retry", retry.id)}
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            sync_child = manager.continue_conversation(ORIGINAL, "carry on")
            async_child = asyncio.run(manager.continue_conversation_async(ORIGINAL, "carry on"))
        for child in (sync_child, async_child):
            assert child is not None and child.done
            assert child.error.startswith("conversation_busy"), child.error
            assert retry.id in child.error and ORIGINAL in child.error
        assert started == []
        assert manager._agents[ORIGINAL].superseded_by == ""
        # The refusal rolled nothing of the retry's claim back.
        assert manager._conversation_admissions == {ORIGINAL: ("retry", retry.id)}

    def test_a_live_retry_refuses_a_second_retry(self, tmp_path) -> None:
        manager = _manager()
        retry = self._admitted_retry(manager, str(tmp_path))
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)

        resp = _retry(manager, ORIGINAL)

        assert resp.status == 409, _payload(resp)
        body = _payload(resp)
        assert body["code"] == "retry_in_flight"
        assert body["retried_by"] == retry.id
        assert started == []

    def test_the_hold_lapses_with_the_run(self, tmp_path) -> None:
        """Finished, popped from the registry after finishing, cancelled out
        of the queue: each is 'in no registry', and each releases the hold on
        the next read -- the conversation is continuable and retryable again."""
        manager = _manager()
        retry = self._admitted_retry(manager, str(tmp_path))
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)
        # Still queued (an unstarted window entry) or popped by the pump and
        # not yet registered: in flight either way.
        del manager._agents[retry.id]
        manager._queue.append({"task": TASK, "_preassigned_id": retry.id})
        assert manager._run_in_flight(retry.id) is not None
        manager._queue.clear()
        manager._dispatching_ids.add(retry.id)
        assert manager._run_in_flight(retry.id) is not None
        assert _retry(manager, ORIGINAL).status == 409
        # A resume entry names a resident run and is not this run's queue row.
        manager._dispatching_ids.discard(retry.id)
        manager._queue.append({"_resume_id": retry.id, "_preassigned_id": retry.id})
        assert manager._run_in_flight(retry.id) is None
        manager._queue.clear()
        # Back in the registry and finished: the hold lapses on the next read.
        manager._agents[retry.id] = retry
        retry.done = True
        retry.error = "turn_limit:100"
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            child = manager.continue_conversation(ORIGINAL, "carry on")
        assert child is not None and not child.error, child.error
        assert [s["task"] for s in started] == ["carry on"]
        assert manager._agents[ORIGINAL].superseded_by == child.id
        # The settled continuation released its own claim; the mark is what
        # refuses the retry from here on.
        assert manager._conversation_admissions == {}
        assert _payload(_retry(manager, ORIGINAL))["code"] == "superseded_by_continuation"

    def test_a_refused_retry_rolls_its_claim_back(self, tmp_path) -> None:
        """Capacity (``spawn`` answered None), a typed spawn refusal, and a
        record read that failed: none of them is an admitted run, so none of
        them holds the conversation afterwards."""
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        manager._agents[ORIGINAL] = original
        manager.spawn_async = AsyncMock(return_value=None)  # type: ignore[method-assign]
        assert _retry(manager, ORIGINAL).status == 429
        assert manager._conversation_admissions == {}
        refusal = SubagentInfo(id="refused00deadbeef", task=TASK, done=True, error="spawn refused")
        manager.spawn_async = AsyncMock(return_value=refusal)  # type: ignore[method-assign]
        assert _retry(manager, ORIGINAL).status == 400
        assert manager._conversation_admissions == {}
        original.execution_context = None
        with patch(
            "kiro_crew.subagent_persistence.read_run_execution",
            side_effect=OSError("state unreadable"),
        ):
            resp = _retry(manager, ORIGINAL)
        assert resp.status == 400 and _payload(resp)["code"] == "memory_unavailable"
        assert manager._conversation_admissions == {}
        # Nothing stands in a continuation's way.
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)
        original.execution_context = _execution()
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            child = manager.continue_conversation(ORIGINAL, "carry on")
        assert child is not None and not child.error, child.error

    def test_a_refused_continuation_rolls_its_claim_back(self, tmp_path) -> None:
        """``conversation_gone`` from the prelude and a store refusal from the
        spawn leave no claim behind and no mark: the retry route still admits
        the run, and then holds it."""
        sessions = _mock_sessions()
        sessions.resumable_sid = MagicMock(return_value="")
        manager = _manager(sessions)
        original = _turn_limited_original(str(tmp_path))
        manager._agents[ORIGINAL] = original
        with patch("kiro_crew.subagent.sel"):
            gone = manager.continue_conversation(ORIGINAL, "carry on")
        assert gone is not None and gone.error.startswith("conversation_gone"), gone.error
        assert manager._conversation_admissions == {}
        manager = _manager()
        manager._agents[ORIGINAL] = original
        refusal = SubagentInfo(
            id="refused00deadbeef",
            task="carry on",
            done=True,
            error="spawn refused: task store unavailable (locked)",
        )
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
            patch.object(manager, "spawn", return_value=refusal),
        ):
            assert manager.continue_conversation(ORIGINAL, "carry on") is refusal
        assert manager._conversation_admissions == {}
        assert original.superseded_by == ""
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)
        assert _retry(manager, ORIGINAL).status == 200
        assert manager._conversation_admissions == {ORIGINAL: ("retry", "fresh0000deadbeef")}

    def test_a_store_only_retry_row_keeps_the_hold_until_the_row_ends(self, tmp_path) -> None:
        """A retry whose durable row waits OUTSIDE the dispatch window (a
        saturated window keeps a just-accepted row store-only) is in no
        in-memory registry, yet it will run: the hold stands on the row's
        non-terminal state and lapses only once the row is terminal or gone --
        from the sync entry (the row read on the calling thread) and from the
        event-loop paths (the row read on the store's writer thread)."""
        manager = _manager()
        retry = self._admitted_retry(manager, str(tmp_path))
        del manager._agents[retry.id]  # store-only: in none of the loop's registries
        assert manager._run_in_flight(retry.id) is None
        store = MagicMock()
        store.state_of = MagicMock(return_value="queued")

        async def run(fn: Any, *args: Any) -> Any:
            return fn(*args)

        store.run = run
        manager._taskq = store
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            sync_child = manager.continue_conversation(ORIGINAL, "carry on")
            async_child = asyncio.run(manager.continue_conversation_async(ORIGINAL, "carry on"))
        for child in (sync_child, async_child):
            assert child is not None and child.done
            assert child.error.startswith("conversation_busy") and retry.id in child.error
        assert _payload(_retry(manager, ORIGINAL))["code"] == "retry_in_flight"
        assert started == []
        assert store.state_of.call_args_list == [((retry.id,),)] * 3
        assert manager._conversation_admissions == {ORIGINAL: ("retry", retry.id)}
        # A row the store cannot read keeps the hold too: the run it names may be live.
        store.state_of = MagicMock(side_effect=RuntimeError("writer thread busy"))
        assert _retry(manager, ORIGINAL).status == 409
        assert manager._conversation_admissions == {ORIGINAL: ("retry", retry.id)}
        # Terminal row: the hold lapses on the next read, and the retry is admitted.
        store.state_of = MagicMock(return_value="cancelled")
        assert _retry(manager, ORIGINAL).status == 200
        assert manager._conversation_admissions == {ORIGINAL: ("retry", "fresh0000deadbeef")}
        # An absent row (no durable record of the run) lapses the same way.
        manager._conversation_admissions[ORIGINAL] = ("retry", "gone0000deadbeef")
        store.state_of = MagicMock(return_value=None)
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            child = manager.continue_conversation(ORIGINAL, "carry on")
        assert child is not None and not child.error, child.error

    def test_a_run_cancelled_before_it_started_is_a_refusal_not_an_adoption(self, tmp_path) -> None:
        """``spawn`` answers a row cancelled while it awaited its claim (a parent
        Stop-all) as ``done`` and ``user_stopped`` with no error. That is a
        terminal answer, so it neither marks the original superseded (the
        retry route would otherwise answer a permanent 409 naming a run that
        never started) nor holds the conversation for a retry."""
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        manager._agents[ORIGINAL] = original
        stopped = SubagentInfo(id="stopped0deadbeef", task="carry on", queued=True)
        stopped.done = True
        stopped.user_stopped = True
        assert not stopped.error
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
            patch.object(manager, "spawn", return_value=stopped),
        ):
            assert manager.continue_conversation(ORIGINAL, "carry on") is stopped
        assert original.superseded_by == ""
        assert manager.continuation_of(ORIGINAL) == ""
        assert manager._conversation_admissions == {}
        # The same answer to a retry's spawn holds nothing.
        manager.spawn_async = AsyncMock(return_value=stopped)  # type: ignore[method-assign]
        _retry(manager, ORIGINAL)
        assert manager._conversation_admissions == {}
        # And the mark's own guard reads the same way.
        manager._note_adoption(ORIGINAL, stopped)
        assert original.superseded_by == ""


class TestRetryRouteAfterAContinuation:
    def test_a_failed_run_whose_conversation_was_continued_is_refused_and_nothing_starts(
        self, tmp_path
    ) -> None:
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        continuation = _finished_continuation()
        manager._agents[ORIGINAL] = original
        manager._agents[CONTINUATION] = continuation
        folders_before = _run_folders()
        assert folders_before == {ORIGINAL, CONTINUATION}
        assert original.outcome == "failed"
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)

        resp = _retry(manager, ORIGINAL)

        assert resp.status == 409, _payload(resp)
        body = _payload(resp)
        assert body["code"] == "superseded_by_continuation"
        assert body["continued_by"] == CONTINUATION
        assert CONTINUATION in body["error"]
        # Relaunched NOTHING: no spawn was asked for and no run folder appeared.
        assert started == []
        assert _run_folders() == folders_before

    def test_a_failed_run_nobody_continued_is_still_retried(self, tmp_path) -> None:
        """The other half of the fence: the refusal is exactly as wide as adoption."""
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        manager._agents[ORIGINAL] = original
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)

        resp = _retry(manager, ORIGINAL)

        assert resp.status == 200, _payload(resp)
        assert _payload(resp) == {
            "id": "fresh0000deadbeef",
            "retried_from": ORIGINAL,
            "status": "spawned",
        }
        assert [s["task"] for s in started] == [TASK]
        assert started[0]["cwd"] == str(tmp_path)
        assert "conversation_key" not in started[0]

    def test_the_mark_outlives_the_continuation_in_the_registry(self, tmp_path) -> None:
        """A continuation dismissed from the panel (popped from ``_agents``) still
        counts: the mark sits on the original's own record."""
        manager = _manager()
        original = _turn_limited_original(str(tmp_path))
        original.superseded_by = CONTINUATION
        manager._agents[ORIGINAL] = original
        started: list[dict[str, Any]] = []
        _recording_spawn(manager, started)

        resp = _retry(manager, ORIGINAL)

        assert resp.status == 409
        assert _payload(resp)["continued_by"] == CONTINUATION
        assert started == []


class TestAdoptionMark:
    @pytest.mark.asyncio
    async def test_an_accepted_continuation_marks_the_original_superseded(self) -> None:
        await asyncio.to_thread(sp.create_agent_folder, ORIGINAL, memory_mode="persistent")
        await asyncio.to_thread(sp.write_run_agent, ORIGINAL, "")
        manager = _manager(_mock_sessions(resumed=True))
        original = SubagentInfo(id=ORIGINAL, task=TASK, done=True, error="turn_limit:100")
        manager._agents[ORIGINAL] = original
        with (
            patch("kiro_crew.subagent.Stats"),
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
        ):
            child = manager.continue_conversation(ORIGINAL, "carry on")
            assert child is not None and not child.error, child.error
            await manager._tasks[child.id]
        assert child.conversation_key == f"subagent:{ORIGINAL}"
        assert original.superseded_by == child.id
        assert manager.continuation_of(ORIGINAL) == child.id
        # The mark is the original's; the continuation's own record is unmarked
        # and nothing has continued IT.
        assert manager.continuation_of(child.id) == ""

    def test_a_refused_continuation_leaves_the_original_retryable(self) -> None:
        sp.create_agent_folder(ORIGINAL, memory_mode="persistent")
        manager = _manager()
        original = SubagentInfo(id=ORIGINAL, task=TASK, done=True, error="turn_limit:100")
        manager._agents[ORIGINAL] = original
        refusal = SubagentInfo(
            id="refused00deadbeef",
            task="carry on",
            done=True,
            error="spawn refused: task store unavailable (locked)",
        )
        with (
            patch("kiro_crew.subagent.sel"),
            patch.object(manager, "_promote_conversation", return_value=object()),
            patch.object(manager, "spawn", return_value=refusal),
        ):
            child = manager.continue_conversation(ORIGINAL, "carry on")
        assert child is refusal
        assert original.superseded_by == ""
        assert manager.continuation_of(ORIGINAL) == ""

    def test_the_registry_scan_answers_for_an_unmarked_continuation(self) -> None:
        """A continuation that reached the registry without passing the mark --
        a durable row re-dispatched under its original params -- is the same
        evidence read from the other side, live, finished or still queued."""
        manager = _manager()
        manager._agents[ORIGINAL] = SubagentInfo(
            id=ORIGINAL, task=TASK, done=True, error="turn_limit:100"
        )
        manager._agents[CONTINUATION] = SubagentInfo(
            id=CONTINUATION, task="carry on", conversation_key=f"subagent:{ORIGINAL}"
        )
        assert manager.continuation_of(ORIGINAL) == CONTINUATION
        del manager._agents[CONTINUATION]
        assert manager.continuation_of(ORIGINAL) == ""
        # A resume entry names a resident run, never a continuation; an
        # unstarted entry carrying the key does.
        manager._queue.append({"_resume_id": ORIGINAL, "_preassigned_id": ORIGINAL})
        assert manager.continuation_of(ORIGINAL) == ""
        manager._queue.append(
            {"task": "carry on", "conversation_key": f"subagent:{ORIGINAL}", "_preassigned_id": "q"}
        )
        assert manager.continuation_of(ORIGINAL) == "q"
        assert manager.continuation_of("nobody00deadbeef") == ""
