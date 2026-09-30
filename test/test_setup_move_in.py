"""Moving the crew into a live home (``dashboard/setup_move_in.py``).

The tunnel manager, the registry and the session-transfer builder are fakes;
nothing reaches the network or AWS. Pins:

* the happy path: every step done, the moving schedules off here and named on the
  card, the command-running schedule still on here, the chat sent, the result
  telling the agent where the chat lives and which secrets to enter again;
* SC5 ordering: the chat goes first, then the moving schedules are switched off
  BEFORE the archive reaches the home, and back on when the home does not confirm it;
* a schedule owned by the moved chat reports to the chat's copy on the home: the
  archive names that copy's key, and the jobs here keep their own;
* a retry never sends the chat or the archive twice;
* secrets never travel: with the real export, the archive the home receives holds
  neither the vault nor ``.env`` nor a secret value.
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import email.policy
import io
import json
import zipfile
from types import SimpleNamespace
from typing import Any

import pytest

from kiro_crew import first_run
from kiro_crew import setup_cards as sc
from kiro_crew.config.paths import config_dir, data_home
from kiro_crew.dashboard import setup_flow, setup_move_in
from kiro_crew.instances.ssh_tunnel_manager import ProxyRequestError, TunnelState

SENTINEL_SECRET = "sk-move-in-sentinel-7d3b1c9e4a2f"
HOME_EC2_ID = "i-0abc1234def567890"
HOME_REG_ID = "home-1"
HOME_NAME = "Kiro Crew Cloud (kc-home)"

MESSAGE_JOB = {
    "id": "j-brief",
    "name": "Dev brief",
    "message": "Summarize my PRs",
    "schedule": {"kind": "cron", "cron_expr": "0 8 * * 1-5"},
    "enabled": True,
}
COMMAND_JOB = {
    "id": "j-backup",
    "name": "Backup notes",
    "message": "",
    "command": "tar czf notes.tgz notes",
    "schedule": {"kind": "every", "every_secs": 86400},
    "enabled": True,
}
PAUSED_JOB = {
    "id": "j-paused",
    "name": "Old digest",
    "message": "Digest",
    "schedule": {"kind": "every", "every_secs": 86400},
    "enabled": False,
    "user_paused": True,
}


def _archive(jobs: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("kirocrew-export-x/crons.json", json.dumps({"jobs": jobs}))
        zf.writestr("kirocrew-export-x/MANIFEST.json", json.dumps({"version": 1}))
    return buf.getvalue()


class FakeSlot:
    def __init__(self, key: str) -> None:
        self.key = key
        self.memory_mode = "persistent"
        self.running = False
        self.messages: list[Any] = []

    def append(self, *args, **kwargs):
        self.messages.append(args)


class FakeCrons:
    def __init__(self, log: list[tuple[str, str]]) -> None:
        self.log = log
        self.enabled = {j["id"]: j["enabled"] for j in (MESSAGE_JOB, COMMAND_JOB, PAUSED_JOB)}

    async def enable_job_async(self, job_id, enabled=True, **_):
        if job_id not in self.enabled:
            return False
        self.enabled[job_id] = enabled
        self.log.append(("on" if enabled else "off", job_id))
        return True


class _Content:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i : i + n]


class FakeManager:
    """The tunnel manager's three calls a move-in makes, recorded in *log*."""

    def __init__(self, log: list[tuple[str, str]]) -> None:
        self.log = log
        self.connected: list[str] = []
        self.imports: list[dict[str, Any]] = []
        self.sent: list[tuple[str, dict]] = []
        self.connect_state = TunnelState.CONNECTED
        self.import_status = 200
        self.import_reply: dict[str, Any] = {
            "ok": True,
            "summary": {"items": ["memory (merged)", "crons (merged)", "skills (merged)"]},
        }
        self.import_raises: Exception | None = None
        self.send_reply: tuple[bool, dict] = (
            True,
            {"key": "chat-9-1", "resume_mode": "session_load", "home_setup_version": 1},
        )
        self.arrivals: list[dict] = []
        self.arrival_status = 200

    async def connect(self, instance_id):
        self.connected.append(instance_id)
        return SimpleNamespace(state=self.connect_state, error="session-manager-plugin missing")

    @contextlib.asynccontextmanager
    async def proxy_request(
        self, instance_id, method, path, *, params=None, data=None, content_type=""
    ):
        if path == "api/setup/home-arrival":
            self.arrivals.append(json.loads(data))
            yield SimpleNamespace(
                status=self.arrival_status,
                content=_Content(json.dumps({"main_slot": "chat-9-1"}).encode()),
            )
            return
        self.log.append(("import", instance_id))
        self.imports.append(
            {
                "instance": instance_id,
                "method": method,
                "path": path,
                "params": params,
                "data": data,
                "content_type": content_type,
            }
        )
        if self.import_raises is not None:
            raise self.import_raises
        yield SimpleNamespace(
            status=self.import_status, content=_Content(json.dumps(self.import_reply).encode())
        )

    async def send_session_bundle(self, instance_id, bundle):
        self.log.append(("send", instance_id))
        self.sent.append((instance_id, bundle))
        return self.send_reply


class FakeRegistry:
    def list(self):
        return [
            SimpleNamespace(
                id="other",
                name="Dev box",
                ssm_target="",
                ssh_host="devbox",
                connection_method="ssh",
            ),
            SimpleNamespace(
                id=HOME_REG_ID,
                name=HOME_NAME,
                ssm_target=HOME_EC2_ID,
                ssh_host="",
                connection_method="ssm",
            ),
        ]


class FakeState:
    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []
        self.slots = {"chat-1-1": FakeSlot("chat-1-1")}
        self.events: list[tuple[str, Any]] = []
        self.crons = FakeCrons(self.log)
        self.instances_manager = FakeManager(self.log)
        self.instances_registry = FakeRegistry()
        self.conversation_log = None

    def get_slot(self, key):
        return self.slots.get(key)

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))

    def push_slots_update(self, **_):
        pass


@pytest.fixture
def state():
    return FakeState()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Governance permits; the instances feature is on; no host grant cache is read."""
    from kiro_crew import mcp_grant
    from kiro_crew.dashboard import chat_utils, session_transfer

    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
    monkeypatch.setattr(mcp_grant, "grant_presence", lambda url, **_: False)
    monkeypatch.setattr(chat_utils, "slot_history_key", lambda slot: f"dashboard:{slot.key}")

    async def _bundle(state, slot, *, origin="", **_):
        return {"bundle_version": 2, "messages": [{"role": "user", "content": "hi"}]}

    monkeypatch.setattr(session_transfer, "build_transfer_bundle_async", _bundle)
    data_home().mkdir(parents=True, exist_ok=True)
    (data_home() / "config.json").write_text(json.dumps({"instances": {"enabled": True}}))
    first_run.record_slot("chat-1-1")


@pytest.fixture
def exported(monkeypatch):
    """The export, faked: the archive carries the three jobs above."""
    from kiro_crew import portability

    calls: list[int] = []

    def _export():
        calls.append(1)
        return _archive([MESSAGE_JOB, COMMAND_JOB, PAUSED_JOB]), {"version": 1}

    monkeypatch.setattr(portability, "create_export_zip", _export)
    return calls


@pytest.fixture
def dispatched(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    async def _fake(state, slot, text, inject_kind):
        calls.append((slot.key, inject_kind, text))

    monkeypatch.setattr(setup_flow, "_dispatch_envelope_turn", _fake)
    return calls


#: The launch job a card build records (``private.job_id``); the store's id shape.
LAUNCH_JOB_ID = "0123456789ab"
HOME_TAG = "kc-4d5e6f"


def _saved_launch(tag: str = HOME_TAG, region: str = "us-east-1", profile: str = "") -> None:
    """The launch record the card's build left, as ``handlers_cloud``'s store keeps it."""
    from kiro_crew.cloud.launch_job import LaunchJob, LaunchJobStore

    LaunchJobStore().save(
        LaunchJob(id=LAUNCH_JOB_ID, profile=profile, region=region, size_key="light", tag=tag)
    )


def _ready_home_card(job_id: str = "lj-1") -> sc.SetupCard:
    card = sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_HOME,
        payload={"simulated": False, "region": "us-east-1"},
        private={"phase": "move", "instance_id": HOME_EC2_ID, "job_id": job_id},
    )

    def _ready(c: sc.SetupCard) -> None:
        c.outcome = {"ready": True, "steps": []}

    return sc.update_card(card.id, _ready)


async def _move(state, card: sc.SetupCard) -> sc.SetupCard:
    return await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})


def _steps(card: sc.SetupCard) -> dict[str, str]:
    return {s["key"]: s["state"] for s in card.outcome["move_steps"]}


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_the_crew_moves_and_the_card_says_what_moved(self, state, exported, dispatched):
        card = _ready_home_card()
        moved = await _move(state, card)

        assert moved.status == sc.STATUS_COMMITTED, moved.error
        assert _steps(moved) == {"reach": "done", "pack": "done", "chat": "done", "carry": "done"}
        assert [s["key"] for s in moved.outcome["move_steps"]] == ["reach", "pack", "chat", "carry"]
        assert moved.outcome["moved"] is True and not moved.outcome.get("simulated")
        assert moved.outcome["home"] == {
            "instance_id": HOME_REG_ID,
            "name": HOME_NAME,
            "remote_key": "chat-9-1",
            "messages": 1,
            "resume_mode": "session_load",
            "home_setup_version": 1,
        }
        assert moved.outcome["jobs_moved"] == [{"id": "j-brief", "name": "Dev brief"}]
        assert moved.outcome["jobs_kept_here"] == [
            {"name": "Backup notes", "reason": setup_move_in.KEPT_RUNS_HERE}
        ]
        # SC5: the moving job is off here; the host-bound one keeps running here;
        # the job that was already paused is left alone.
        assert state.crons.enabled == {"j-brief": False, "j-backup": True, "j-paused": False}
        mgr = state.instances_manager
        assert mgr.connected == [HOME_REG_ID]
        assert [s[0] for s in mgr.sent] == [HOME_REG_ID]
        assert "stay_on" in first_run.done_stages()

    @pytest.mark.asyncio
    async def test_the_archive_reaches_the_home_importer_as_one_file_part(
        self, state, exported, dispatched
    ):
        await _move(state, _ready_home_card())
        (req,) = state.instances_manager.imports
        assert (req["method"], req["path"], req["params"]) == (
            "POST",
            setup_move_in.IMPORT_PATH,
            {"mode": "merge"},
        )
        message = email.message_from_bytes(
            b"Content-Type: " + req["content_type"].encode() + b"\r\n\r\n" + req["data"],
            policy=email.policy.HTTP,
        )
        (part,) = list(message.iter_parts())
        assert part.get_param("name", header="content-disposition") == "file"
        assert part.get_content_type() == "application/zip"
        assert part.get_payload(decode=True) == _archive([MESSAGE_JOB, COMMAND_JOB, PAUSED_JOB])

    @pytest.mark.asyncio
    async def test_sc5_the_moving_job_is_off_here_before_the_archive_lands(
        self, state, exported, dispatched
    ):
        await _move(state, _ready_home_card())
        assert state.log == [("send", HOME_REG_ID), ("off", "j-brief"), ("import", HOME_REG_ID)]

    @pytest.mark.asyncio
    async def test_the_result_names_the_home_the_schedules_and_what_to_enter_again(
        self, state, exported, dispatched
    ):
        from kiro_crew.secrets.vault import SecretVault

        await SecretVault(config_dir()).set("GITHUB_TOKEN", SENTINEL_SECRET)
        (config_dir() / ".env").write_text(f"TELEGRAM_BOT_TOKEN={SENTINEL_SECRET}\n")
        moved = await _move(state, _ready_home_card())

        assert moved.outcome["reenter"]["secrets"] == ["GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN"]
        assert dispatched == []  # The local agent must not restart after its snapshot moved.
        text = setup_move_in.result_detail(moved.outcome)
        assert HOME_NAME in text
        assert "Your crews" in text
        assert "switched off here: Dev brief" in text
        assert "Backup notes" in text
        assert "GITHUB_TOKEN" in text and "TELEGRAM_BOT_TOKEN" in text
        assert moved.outcome["settings_moved"] is True
        assert "kept its own settings" not in text
        store = (data_home() / "setup" / sc.CARDS_FILE).read_text()
        for where in (text, store, json.dumps(state.events)):
            assert SENTINEL_SECRET not in where


class TestNextTime:
    """The committed move names the commands that reach the home again."""

    @pytest.mark.asyncio
    async def test_the_outcome_carries_the_commands_and_the_agent_gets_the_open_one(
        self, state, exported, dispatched
    ):
        _saved_launch()
        moved = await _move(state, _ready_home_card(LAUNCH_JOB_ID))
        assert moved.status == sc.STATUS_COMMITTED, moved.error

        where = f"--tag {HOME_TAG} --region us-east-1"
        assert moved.outcome["reconnect"] == [
            {"purpose": "open", "command": f"kirocrew cloud connect {where}"},
            {"purpose": "stop", "command": f"kirocrew cloud stop {where}"},
            {"purpose": "start", "command": f"kirocrew cloud start {where}"},
            {"purpose": "status", "command": f"kirocrew cloud status {where}"},
            {"purpose": "list", "command": "kirocrew cloud list --region us-east-1"},
        ]
        home = moved.outcome["home"]
        assert (home["tag"], home["region"], home["profile"]) == (HOME_TAG, "us-east-1", "")
        assert home["name"] == HOME_NAME and home["instance_id"] == HOME_REG_ID
        # A real move-in switches the window to the home's chat, so no result
        # turn is posted here; the open line rides the result text that travels
        # to the home instead.
        text = setup_move_in.result_detail(moved.outcome)
        assert f"`kirocrew cloud connect {where}`" in text
        assert "in one line" in text
        assert "token" not in json.dumps(moved.outcome["reconnect"])

    @pytest.mark.asyncio
    async def test_the_homes_copy_of_the_card_carries_them_too(self, state, exported, dispatched):
        # After the move the owner is switched to the chat's copy on the home, so
        # the card they see there is the receipt the arrival completes.
        _saved_launch()
        moved = await _move(state, _ready_home_card(LAUNCH_JOB_ID))
        assert moved.status == sc.STATUS_COMMITTED, moved.error
        arrival = state.instances_manager.arrivals[-1]["outcome"]
        assert arrival["reconnect"] == moved.outcome["reconnect"]
        assert (arrival["home"]["tag"], arrival["home"]["region"]) == (HOME_TAG, "us-east-1")

    @pytest.mark.asyncio
    async def test_a_named_profile_rides_every_command(self, state, exported, dispatched):
        _saved_launch(profile="work")
        moved = await _move(state, _ready_home_card(LAUNCH_JOB_ID))
        assert all(c["command"].endswith("--profile work") for c in moved.outcome["reconnect"])

    @pytest.mark.asyncio
    async def test_a_launch_record_that_no_longer_reads_gives_no_command(
        self, state, exported, dispatched
    ):
        _saved_launch(tag="kc bad;tag")
        moved = await _move(state, _ready_home_card(LAUNCH_JOB_ID))
        assert moved.status == sc.STATUS_COMMITTED
        assert moved.outcome["reconnect"] == []
        assert "To open the home again" not in setup_move_in.result_detail(moved.outcome)


class TestCarryFailure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status, reply, raises, code",
        [
            (500, {"ok": False, "code": "import_failed"}, None, "move_in_carry_refused"),
            (409, {"ok": False, "code": "named_store_in_use"}, None, "move_in_carry_refused"),
            (
                200,
                {},
                ProxyRequestError("proxy_peer_unreachable", "peer did not answer"),
                "move_in_carry_failed",
            ),
        ],
    )
    async def test_a_carry_the_home_does_not_confirm_stops_and_turns_the_jobs_back_on(
        self, state, exported, dispatched, status, reply, raises, code
    ):
        mgr = state.instances_manager
        mgr.import_status, mgr.import_reply, mgr.import_raises = status, reply, raises
        card = _ready_home_card()
        failed = await _move(state, card)

        assert failed.status == sc.STATUS_PENDING
        assert failed.error["code"] == code
        assert _steps(failed) == {
            "reach": "done",
            "pack": "done",
            "chat": "done",
            "carry": "failed",
        }
        assert state.crons.enabled["j-brief"] is True
        assert state.log == [
            ("send", HOME_REG_ID),
            ("off", "j-brief"),
            ("import", HOME_REG_ID),
            ("on", "j-brief"),
        ]
        assert dispatched == []
        record = sc.get_card(card.id).private["move"]
        assert record["chat"]["remote_key"] == "chat-9-1" and not record.get("carried")
        # The peer's free text never reaches the card; only its code, when well formed.
        assert "peer did not answer" not in json.dumps(failed.outcome)

    @pytest.mark.asyncio
    async def test_a_job_the_home_rejects_comes_back_on_here(self, state, exported, dispatched):
        state.instances_manager.import_reply = {
            "ok": True,
            "summary": {"items": ["crons (merged)"], "rejected_crons": ["Dev brief"]},
        }
        moved = await _move(state, _ready_home_card())
        assert moved.status == sc.STATUS_COMMITTED
        assert state.crons.enabled["j-brief"] is True
        assert moved.outcome["jobs_moved"] == []
        assert {"name": "Dev brief", "reason": setup_move_in.KEPT_HOME_REFUSED} in (
            moved.outcome["jobs_kept_here"]
        )

    @pytest.mark.asyncio
    async def test_a_home_that_cannot_read_its_schedules_keeps_them_all_here(
        self, state, exported, dispatched
    ):
        state.instances_manager.import_reply = {
            "ok": True,
            "summary": {"items": ["memory (merged)"], "refused_merges": ["crons"]},
        }
        moved = await _move(state, _ready_home_card())
        assert moved.status == sc.STATUS_COMMITTED
        assert state.crons.enabled["j-brief"] is True
        assert {"name": "Dev brief", "reason": setup_move_in.KEPT_HOME_NO_STORE} in (
            moved.outcome["jobs_kept_here"]
        )


class TestInterrupted:
    @pytest.mark.asyncio
    async def test_a_gateway_stopping_mid_carry_turns_the_jobs_back_on(
        self, state, exported, dispatched
    ):
        import asyncio

        state.instances_manager.import_raises = asyncio.CancelledError()
        card = _ready_home_card()
        with pytest.raises(asyncio.CancelledError):
            await _move(state, card)
        assert state.crons.enabled["j-brief"] is True
        stored = sc.get_card(card.id)
        assert stored.status == sc.STATUS_PENDING
        assert stored.error["code"] == "move_in_interrupted"
        assert "still run here" in stored.error["message"]


class TestRetry:
    @pytest.mark.asyncio
    async def test_a_failed_chat_step_moves_nothing_else(self, state, exported, dispatched):
        mgr = state.instances_manager
        mgr.send_reply = (False, {"error": "boom", "code": "transfer_unreachable"})
        card = _ready_home_card()
        failed = await _move(state, card)

        assert failed.status == sc.STATUS_PENDING
        assert failed.error["code"] == "move_in_chat_failed"
        assert "transfer_unreachable" in failed.error["message"]
        assert _steps(failed) == {
            "reach": "done",
            "pack": "done",
            "chat": "failed",
            "carry": "pending",
        }
        assert state.log == [("send", HOME_REG_ID)]
        assert state.crons.enabled["j-brief"] is True
        assert mgr.imports == [] and dispatched == []

        mgr.send_reply = (True, {"key": "chat-9-1", "home_setup_version": 1})
        moved = await _move(state, sc.get_card(card.id))
        assert moved.status == sc.STATUS_COMMITTED
        assert len(mgr.sent) == 2 and len(mgr.imports) == 1
        assert state.crons.enabled["j-brief"] is False

    @pytest.mark.asyncio
    async def test_a_retry_after_a_failed_carry_does_not_send_the_chat_twice(
        self, state, exported, dispatched, monkeypatch
    ):
        from kiro_crew import portability

        monkeypatch.setattr(
            portability,
            "create_export_zip",
            lambda: (_archive([{**MESSAGE_JOB, "session_key": "dashboard:chat-1-1"}]), {}),
        )
        mgr = state.instances_manager
        mgr.import_status, mgr.import_reply = 500, {"ok": False, "code": "import_failed"}
        card = _ready_home_card()
        failed = await _move(state, card)
        assert failed.error["code"] == "move_in_carry_refused"
        assert state.crons.enabled["j-brief"] is True

        mgr.import_status, mgr.import_reply = 200, {"ok": True, "summary": {"items": []}}
        moved = await _move(state, sc.get_card(card.id))
        assert moved.status == sc.STATUS_COMMITTED, moved.error
        assert len(mgr.sent) == 1 and len(mgr.imports) == 2
        assert _steps(moved)["chat"] == "done"
        detail = next(s for s in moved.outcome["move_steps"] if s["key"] == "chat")["detail"]
        assert detail == "Done on the last try."
        # The retry's fresh archive still points the chat's job at the copy sent
        # on the first try.
        assert _posted_jobs(mgr.imports[-1])[0]["session_key"] == "dashboard:chat-9-1"
        assert moved.outcome["home"]["remote_key"] == "chat-9-1"
        assert state.crons.enabled["j-brief"] is False


def _posted_jobs(request: dict[str, Any]) -> list[dict[str, Any]]:
    """The jobs in the archive a fake import request carried."""
    data = request["data"]
    archive = data[data.index(b"PK\x03\x04") : data.rindex(b"\r\n--")]
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        member = next(n for n in zf.namelist() if n.endswith("/crons.json"))
        return json.loads(zf.read(member))["jobs"]


class TestJobsFollowTheChat:
    @pytest.fixture
    def bound(self, monkeypatch):
        """An export whose brief was made in this chat and whose digest in another."""
        from kiro_crew import portability

        jobs = [
            {**MESSAGE_JOB, "session_key": "dashboard:chat-1-1"},
            {**PAUSED_JOB, "session_key": "dashboard:chat-1-1"},
            {**COMMAND_JOB, "session_key": "dashboard:chat-2-2"},
        ]
        archive = _archive(jobs)
        monkeypatch.setattr(portability, "create_export_zip", lambda: (archive, {}))
        return archive

    @pytest.mark.asyncio
    async def test_a_job_made_in_this_chat_reports_to_its_copy_on_the_home(
        self, state, bound, dispatched
    ):
        moved = await _move(state, _ready_home_card())
        assert moved.status == sc.STATUS_COMMITTED, moved.error
        keys = {
            j["id"]: j.get("session_key") for j in _posted_jobs(state.instances_manager.imports[0])
        }
        assert keys == {
            "j-brief": "dashboard:chat-9-1",
            "j-paused": "dashboard:chat-9-1",
            "j-backup": "dashboard:chat-2-2",
        }
        assert moved.outcome["jobs_follow_chat"] == ["Dev brief", "Old digest"]
        assert (
            "now report to its copy on the home: Dev brief, Old digest"
            in setup_move_in.result_detail(moved.outcome)
        )

    @pytest.mark.asyncio
    async def test_an_unknown_copy_key_stops_before_moving_schedules(
        self, state, bound, dispatched
    ):
        state.instances_manager.send_reply = (True, {"resume_mode": "full"})
        moved = await _move(state, _ready_home_card())
        assert moved.status == sc.STATUS_PENDING
        assert moved.error["code"] == "move_in_chat_failed"
        assert state.instances_manager.imports == []
        assert state.crons.enabled["j-brief"] is True

    def test_only_crons_json_is_rewritten(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "kirocrew-export-x/crons.json",
                json.dumps(
                    {"jobs": [{**MESSAGE_JOB, "name": "Brief ☕", "session_key": "dashboard:a"}]}
                ),
            )
            zf.writestr("kirocrew-export-x/workspace/notes.md", "# notes\n" * 100)
            zf.writestr("kirocrew-export-x/memory.db", b"\x00SQLite" * 50)
        out, names = setup_move_in.rebind_jobs_to_chat(
            buf.getvalue(), frozenset({"dashboard:a"}), "dashboard:b"
        )
        assert names == ["Brief ☕"]
        with (
            zipfile.ZipFile(io.BytesIO(buf.getvalue())) as before,
            zipfile.ZipFile(io.BytesIO(out)) as after,
        ):
            assert after.namelist() == before.namelist()
            for name in before.namelist():
                if not name.endswith("crons.json"):
                    assert after.read(name) == before.read(name)
            jobs = json.loads(after.read("kirocrew-export-x/crons.json").decode("utf-8"))["jobs"]
        assert jobs[0]["session_key"] == "dashboard:b" and jobs[0]["name"] == "Brief ☕"
        assert setup_move_in.rebind_jobs_to_chat(
            out, frozenset({"dashboard:z"}), "dashboard:b"
        ) == (
            out,
            [],
        )


class TestReach:
    @pytest.mark.asyncio
    async def test_remote_crew_off_is_turned_on_and_restarts_before_anything_is_packed(
        self, state, exported, dispatched, monkeypatch
    ):
        from kiro_crew.dashboard import setup_move_in
        from kiro_crew.dashboard.handlers import updates

        (data_home() / "config.json").write_text(json.dumps({"instances": {"enabled": False}}))
        restarts: list = []

        async def _fake_restart(st, **_):
            restarts.append(st)
            return True

        monkeypatch.setattr(updates, "_restart_gateway", _fake_restart)
        monkeypatch.setattr(setup_move_in, "_RESTART_DELAY_SECS", 0)
        failed = await _move(state, _ready_home_card())
        assert failed.status == sc.STATUS_PENDING
        assert failed.error["code"] == "move_in_restarting"
        assert exported == [] and state.log == []
        cfg = json.loads((data_home() / "config.json").read_text())
        assert cfg["instances"]["enabled"] is True
        for _ in range(50):
            if restarts:
                break
            await asyncio.sleep(0.01)
        assert restarts == [state]

    @pytest.mark.asyncio
    async def test_an_unreachable_home_stops_before_anything_is_packed(
        self, state, exported, dispatched
    ):
        state.instances_manager.connect_state = TunnelState.ERROR
        failed = await _move(state, _ready_home_card())
        assert failed.error["code"] == "move_in_unreachable"
        assert "session-manager-plugin missing" in failed.error["message"]
        assert _steps(failed)["reach"] == "failed"
        assert exported == [] and state.log == []

    @pytest.mark.asyncio
    async def test_a_home_missing_from_your_crews_is_named(self, state, exported, dispatched):
        card = _ready_home_card()

        def _elsewhere(c: sc.SetupCard) -> None:
            c.private["instance_id"] = "i-notregistered"

        card = sc.update_card(card.id, _elsewhere)
        failed = await _move(state, card)
        assert failed.error["code"] == "move_in_home_not_registered"
        assert state.instances_manager.connected == []


class TestSecretsNeverTravel:
    @pytest.mark.asyncio
    async def test_the_real_export_the_home_receives_holds_no_secret(self, state, dispatched):
        from kiro_crew.secrets.vault import SecretVault

        await SecretVault(config_dir()).set("GITHUB_TOKEN", SENTINEL_SECRET)
        (config_dir() / ".env").write_text(f"TELEGRAM_BOT_TOKEN={SENTINEL_SECRET}\n")
        (config_dir() / "crons.json").write_text(
            json.dumps({"jobs": [{**MESSAGE_JOB, "session_key": "dashboard:chat-1-1"}]})
        )
        sc.persona_path("SOUL").parent.mkdir(parents=True, exist_ok=True)
        sc.persona_path("SOUL").write_text("Be brief.", encoding="utf-8")
        moved = await _move(state, _ready_home_card())
        assert moved.status == sc.STATUS_COMMITTED, moved.error

        (req,) = state.instances_manager.imports
        assert SENTINEL_SECRET.encode() not in req["data"]
        archive = req["data"][req["data"].index(b"PK\x03\x04") :]
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = zf.namelist()
        assert not any(".vault" in n or n.endswith(".env") for n in names), names
        assert any(n.endswith("/persona/SOUL.md") for n in names), names
        assert any(n.endswith("/crons.json") for n in names), names
        assert _posted_jobs(req)[0]["session_key"] == "dashboard:chat-9-1"
        # Only the archive names the copy; the job here keeps this chat's key.
        local = json.loads((config_dir() / "crons.json").read_text(encoding="utf-8"))
        assert local["jobs"][0]["session_key"] == "dashboard:chat-1-1"
        assert state.log[:2] == [("send", HOME_REG_ID), ("off", "j-brief")]


class TestJobsInArchive:
    def test_only_enabled_message_jobs_move(self):
        moving, staying = setup_move_in.jobs_in_archive(
            _archive([MESSAGE_JOB, COMMAND_JOB, PAUSED_JOB, {"id": 3, "name": "bad"}])
        )
        assert [j.id for j in moving] == ["j-brief"]
        assert [j.id for j in staying] == ["j-backup"]

    def test_an_archive_without_a_schedule_list_moves_nothing(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("kirocrew-export-x/MANIFEST.json", "{}")
        assert setup_move_in.jobs_in_archive(buf.getvalue()) == ([], [])


@pytest.mark.asyncio
async def test_adoption_carries_title_and_progress_and_retry_reuses_chat(
    state, exported, dispatched
):
    state.slots["chat-1-1"].title = "Sam's crew"
    first_run.mark_stage("hello")
    first_run.mark_stage("connect")
    mgr = state.instances_manager
    mgr.arrival_status = 404
    card = _ready_home_card()
    failed = await _move(state, card)
    assert failed.status == sc.STATUS_PENDING
    assert failed.error["code"] == "move_in_adopt_failed"
    assert state.crons.enabled["j-brief"] is False
    mgr.arrival_status = 200
    moved = await _move(state, failed)
    assert moved.status == sc.STATUS_COMMITTED
    assert len(mgr.sent) == len(mgr.imports) == 1
    assert len(mgr.arrivals) == 2
    for arrival in mgr.arrivals:
        assert {k: arrival[k] for k in ("slot", "title", "stages")} == {
            "slot": "chat-9-1",
            "title": "Sam's crew",
            "stages": ["hello", "connect"],
        }
        assert arrival["preferences"] == {"fields": {}, "persona": {}}
        assert arrival["outcome"]["home"]["remote_key"] == "chat-9-1"
    assert sc.get_card(card.id).private["move"]["adopted"] is True
