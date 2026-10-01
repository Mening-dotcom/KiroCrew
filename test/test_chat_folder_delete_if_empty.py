"""``DELETE /api/chat/folders/{id}?if_empty=true`` deletes only an empty folder.

This is the mode the ``chat_folder_delete`` MCP tool sends. It must never unfile
a session or lift a subfolder: occupancy is decided in the same locked step that
removes the folder, so a session filed after the caller's own read is still seen.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_folder_app, _make_state

from kiro_crew.dashboard import chat_folders


async def _create(client: TestClient, name: str, parent_id: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if parent_id:
        body["parent_id"] = parent_id
    resp = await client.post("/api/chat/folders", json=body)
    assert resp.status in (200, 201), await resp.text()
    return await resp.json()


def _ids(state: Any) -> set[str]:
    return {f["id"] for f in state._folders}


class TestIfEmptyDelete:
    @pytest.mark.asyncio
    async def test_an_empty_folder_is_deleted(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Empty")
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 200
        assert folder["id"] not in _ids(state)

    @pytest.mark.asyncio
    async def test_a_live_session_keeps_the_folder(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Work")
            slot = state.get_or_create_slot("filed")
            slot.folder_id = folder["id"]
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 409
            body = await resp.json()
        assert body["code"] == "folder_not_empty"
        assert folder["id"] in _ids(state)
        assert slot.folder_id == folder["id"], "the empty-only delete unfiled a session"

    @pytest.mark.asyncio
    async def test_a_subfolder_keeps_the_folder(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            parent = await _create(client, "Parent")
            child = await _create(client, "Child", parent["id"])
            resp = await client.delete(f"/api/chat/folders/{parent['id']}?if_empty=true")
            assert resp.status == 409
        assert parent["id"] in _ids(state)
        kept = next(f for f in state._folders if f["id"] == child["id"])
        assert kept["parent_id"] == parent["id"], "the empty-only delete lifted a subfolder"

    @pytest.mark.asyncio
    async def test_an_archived_session_keeps_the_folder(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Old")
            monkeypatch.setattr(
                chat_folders, "_folder_history_counts", lambda _state: {folder["id"]: 1}
            )
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 409
            body = await resp.json()
        assert body["code"] == "folder_not_empty"
        assert "1" not in body["error"], "the refusal must not carry a count"
        assert folder["id"] in _ids(state)

    @pytest.mark.asyncio
    async def test_a_session_filed_during_the_archive_scan_is_still_seen(
        self, tmp_path, monkeypatch
    ) -> None:
        """The live-slot check runs after the scan's await, under the store lock.

        A pre-check before that await would pass here, and the delete would
        then unfile the session that landed.
        """
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Racy")
            slot = state.get_or_create_slot("late")

            def _scan_while_a_session_is_filed(_state: Any) -> dict[str, int]:
                slot.folder_id = folder["id"]
                return {}

            monkeypatch.setattr(
                chat_folders, "_folder_history_counts", _scan_while_a_session_is_filed
            )
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 409
        assert folder["id"] in _ids(state)
        assert slot.folder_id == folder["id"]

    @pytest.mark.asyncio
    async def test_a_folder_gone_before_the_lock_is_not_found(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Vanishing")

            def _scan_while_it_is_deleted(_state: Any) -> dict[str, int]:
                state._folders[:] = [f for f in state._folders if f["id"] != folder["id"]]
                return {}

            monkeypatch.setattr(chat_folders, "_folder_history_counts", _scan_while_it_is_deleted)
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_a_cron_job_reference_keeps_the_folder(self, tmp_path, monkeypatch) -> None:
        """A saved cron job files its future tab into the folder by id.

        The folder is empty by sessions and subfolders, but deleting it would
        strand the tab that job later mints as unfiled, so the empty-only
        delete must refuse.
        """
        from unittest.mock import AsyncMock

        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "CronTarget")
            state.crons.chat_folder_ids_async = AsyncMock(return_value={folder["id"]})
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 409
            body = await resp.json()
        assert body["code"] == "folder_not_empty"
        assert folder["id"] in _ids(state)

    @pytest.mark.asyncio
    async def test_a_channel_reference_keeps_the_folder(self, tmp_path, monkeypatch) -> None:
        """A channel files into the folder by name; deleting it would orphan that filing."""
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "ChannelTarget")
            # Match the name as the channel occupancy read reports it.
            from kiro_crew.dashboard.chat_folder_cleanup import _name_key

            monkeypatch.setattr(
                chat_folders,
                "_folder_history_counts",
                lambda _state: {},
            )
            monkeypatch.setattr(
                "kiro_crew.dashboard.chat_folder_cleanup.channel_folder_names",
                lambda: {_name_key("ChannelTarget")},
            )
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 409
            body = await resp.json()
        assert body["code"] == "folder_not_empty"
        assert folder["id"] in _ids(state)

    @pytest.mark.asyncio
    async def test_a_folder_carrying_saved_settings_keeps_itself(
        self, tmp_path, monkeypatch
    ) -> None:
        """A configured folder (project_dir/tags/color/icon/...) is not deleted empty.

        The person set those on the folder on purpose; deleting the row throws
        them away. This mirrors the bulk cleanup's _KEEP_FIELDS refusal via the
        shared _keeps_itself predicate.
        """
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Configured")
            row = next(f for f in state._folders if f["id"] == folder["id"])
            row["project_dir"] = "/home/me/project"
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 409
            body = await resp.json()
        assert body["code"] == "folder_not_empty"
        assert folder["id"] in _ids(state)

    @pytest.mark.asyncio
    async def test_a_channel_stamped_folder_keeps_itself(self, tmp_path, monkeypatch) -> None:
        """A folder carrying a ``channel`` stamp is kept even if its name drifted.

        The stamp is the identity ensure_channel_folder matches on; deleting the
        row destroys it. _keeps_itself refuses on the stamp field directly.
        """
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "WasAChannelFolder")
            row = next(f for f in state._folders if f["id"] == folder["id"])
            row["channel"] = "slack:C123"
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 409
            body = await resp.json()
        assert body["code"] == "folder_not_empty"
        assert folder["id"] in _ids(state)

    @pytest.mark.asyncio
    async def test_an_unreadable_cron_store_refuses_the_delete(self, tmp_path, monkeypatch) -> None:
        """A reference store that cannot be read is an unknown set, not an empty one.

        The empty-only delete fails closed (503) rather than deleting a folder
        whose cron references could not be read.
        """
        from unittest.mock import AsyncMock

        from kiro_crew.cron_service.store import CronStoreUnreadable

        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Unreadable")
            state.crons.chat_folder_ids_async = AsyncMock(side_effect=CronStoreUnreadable("boom"))
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 503
            body = await resp.json()
        assert body["code"] == "folder_refs_unreadable"
        assert folder["id"] in _ids(state)

    @pytest.mark.asyncio
    async def test_a_crew_member_cannot_delete_a_folder(self, tmp_path, monkeypatch) -> None:
        """Defense in depth: a member caller is refused in the endpoint, both modes.

        The auth middleware already admits only PATCH on this path for a member,
        but the endpoint refuses a member principal regardless so a later change
        to the admitted method set cannot let a member delete the person's folder.
        """
        from aiohttp import web

        from kiro_crew.dashboard.token_auth import MEMBER_CHAT_PRINCIPAL_KEY

        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        # Build the folder first through the ordinary (non-member) app.
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "PersonsFolder")

        # Now a member caller attempts the delete through a member-stamped app.
        app = _make_folder_app(state)

        @web.middleware
        async def _stamp_member(request: web.Request, handler: Any) -> Any:
            request[MEMBER_CHAT_PRINCIPAL_KEY] = "member:alice"
            return await handler(request)

        app.middlewares.append(_stamp_member)
        async with TestClient(TestServer(app)) as client:
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 403
            body = await resp.json()
            assert body["code"] == "folder_delete_forbidden"
            # Also refused in the full-delete mode.
            resp2 = await client.delete(f"/api/chat/folders/{folder['id']}")
            assert resp2.status == 403
        assert folder["id"] in _ids(state)

    @pytest.mark.asyncio
    async def test_duplicate_folder_ids_are_not_deleted_together(
        self, tmp_path, monkeypatch
    ) -> None:
        """A hand-corrupted store with duplicate ids must not lose every match.

        The empty-only mode removes by id with a filter; it refuses when more
        than one row carries the id rather than deleting them all.
        """
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Dup")
            # Simulate an externally edited store carrying a duplicate id.
            state._folders.append({"id": folder["id"], "name": "DupTwin", "parent_id": ""})
            resp = await client.delete(f"/api/chat/folders/{folder['id']}?if_empty=true")
            assert resp.status == 409
            body = await resp.json()
        assert body["code"] == "folder_not_empty"
        matching = [f for f in state._folders if f["id"] == folder["id"]]
        assert len(matching) == 2, "the empty-only delete removed a duplicate-id row"

    @pytest.mark.asyncio
    async def test_without_the_flag_a_full_folder_is_still_deleted(
        self, tmp_path, monkeypatch
    ) -> None:
        """The person's own sidebar delete keeps unfiling, as it always did."""
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        async with TestClient(TestServer(_make_folder_app(state))) as client:
            folder = await _create(client, "Full")
            slot = state.get_or_create_slot("filed")
            slot.folder_id = folder["id"]
            resp = await client.delete(f"/api/chat/folders/{folder['id']}")
            assert resp.status == 200
        assert folder["id"] not in _ids(state)
        assert slot.folder_id == ""


class TestChannelCallerRefused:
    """A ``channel:`` caller cannot delete the person's folders.

    A channel agent acts on thread text other people wrote; the sidebar is the
    person's. The gate resolves a channel caller as the unscoped person, so the
    tool refuses it at dispatch (the same split chat_session_pin uses), and the
    name is on the channel-agent blocklist for the permission prompt.
    """

    def test_a_channel_caller_is_refused_at_dispatch(self) -> None:
        from unittest.mock import patch

        from kiro_crew.mcp_dashboard import _call_tool_inner

        with (
            patch(
                "kiro_crew.mcp_dashboard._refuse_tree_shaping_if_unverifiable",
                return_value=("channel:slack:C1:1.0", "", None),
            ),
            patch("kiro_crew.mcp_dashboard._delete") as mock_delete,
        ):
            out = _call_tool_inner("chat_folder_delete", {"folder": "Anything"})
        mock_delete.assert_not_called()
        assert "not available to channel agents" in out

    def test_chat_folder_delete_is_on_the_channel_blocklist(self) -> None:
        from kiro_crew.channel import CHANNEL_AGENT_BLOCKED_TOOLS

        assert "chat_folder_delete" in CHANNEL_AGENT_BLOCKED_TOOLS
