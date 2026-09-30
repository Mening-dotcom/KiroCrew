"""The commands that reach a home in the cloud again, from this or any computer.

After a crew moves into its home, the owner needs one line to open it next time.
``kirocrew cloud connect`` is that line: it mints a fresh dashboard token over SSM
and opens an SSM port-forward each time, so the command itself carries no token,
no URL and no credential, and the home needs no inbound port.

Every command names the home explicitly (``--tag`` and ``--region``, and
``--profile`` unless it is the AWS CLI's default), because a bare ``kirocrew
cloud connect`` resolves the tag this computer's own ``cloud launch`` recorded,
which a home built from the setup card is not, and another computer has none.
The tag is the card build's launch tag, and ``ec2.stack_name(tag)`` is the stack
both the card's build and ``cloud connect`` address.
"""

from __future__ import annotations

import shlex

from kiro_crew.cloud import ec2
from kiro_crew.validation import ValidationError

#: What each command is for, in the order they are shown.
PURPOSE_OPEN = "open"
PURPOSE_STOP = "stop"
PURPOSE_START = "start"
PURPOSE_STATUS = "status"
PURPOSE_LIST = "list"

#: ``(purpose, kirocrew cloud verb, whether the verb names one home)``.
_COMMANDS: tuple[tuple[str, str, bool], ...] = (
    (PURPOSE_OPEN, "connect", True),
    (PURPOSE_STOP, "stop", True),
    (PURPOSE_START, "start", True),
    (PURPOSE_STATUS, "status", True),
    (PURPOSE_LIST, "list", False),
)
#: Profile names that mean the AWS CLI's own default, so no ``--profile`` is passed.
_DEFAULT_PROFILES = frozenset({"", "default"})


def home_name(tag: str) -> str:
    """The name a home built under launch tag *tag* carries in Your crews."""
    return f"Kiro Crew Cloud ({tag})"


def reconnect_commands(tag: str, region: str, profile: str = "") -> list[dict[str, str]]:
    """``[{purpose, command}]`` for the home built under *tag* in *region*.

    Raises :class:`~kiro_crew.validation.ValidationError` for a tag, region or
    profile the cloud commands would refuse, so a malformed value never becomes a
    command anyone copies. Every argument is shell-quoted.
    """
    tag = ec2.validate_tag(tag)
    region = ec2.validate_region(region)
    if not region:
        raise ValidationError("region", "required")
    profile = ec2.validate_profile(profile)
    creds = ["--region", region]
    if profile not in _DEFAULT_PROFILES:
        creds += ["--profile", profile]
    return [
        {
            "purpose": purpose,
            "command": shlex.join(
                ["kirocrew", "cloud", verb, *(["--tag", tag] if one_home else []), *creds]
            ),
        }
        for purpose, verb, one_home in _COMMANDS
    ]


def commands_for_home(home: object) -> list[dict[str, str]]:
    """The commands for a stored ``outcome.home``, rebuilt; ``[]`` when it does not validate.

    The card store is written by in-sandbox code, so a stored command string is
    never served or printed: only the home's tag, region and profile are read
    back, and each passes the cloud commands' own validators again. A planted
    string cannot become a line the owner copies into a terminal.
    """
    if not isinstance(home, dict):
        return []
    tag, region, profile = (str(home.get(key) or "") for key in ("tag", "region", "profile"))
    try:
        return reconnect_commands(tag, region, profile)
    except ValidationError:
        return []


def open_command(commands: list[dict[str, str]]) -> str:
    """The command that opens the home, from :func:`reconnect_commands`'s list."""
    return next((c["command"] for c in commands if c.get("purpose") == PURPOSE_OPEN), "")
