#!/usr/bin/env python3
"""Persona evals of the one-chat first run (the ``crew-setup`` skill).

Two modes, because only one of them can run in CI:

``--check`` (default)
    Deterministic. No model, no gateway, no network. Validates ``cases.json``
    (personas, click policies, rubric lines, the secret-request patterns against
    their own examples) and runs the trigger cases through the word-overlap
    scoring ``evals/explain-for`` uses. Exits non-zero on any problem, so a test
    can gate on it (``test/test_crew_setup_evals.py``).

``--run [--case ID]``
    For each persona, a real first run: an ISOLATED gateway (fresh data home,
    fresh kiro home, a free port, the simulated cloud engine, a fixture agent
    home to import) driven over HTTP. The runner clicks the gateway's own
    steps (the default harness, its sign-in check, privacy, the persona's
    start path), clicks every later card by the persona's policy, sends the next scripted
    reply whenever the agent is idle with nothing pending, and grades the
    record against the persona's rubric. The model calls are real, so this is
    a local command, not a CI gate. Results land in ``iteration-N/``.

The user side is scripted on purpose: the same replies and clicks every run, so
two iterations differ only by the agent. A model playing the user from the
persona text is a later step.

Stdlib only, Python 3.8+, subprocess called with argument lists.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import importlib.util
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SKILL_FILE = REPO_ROOT / "src" / "kiro_crew" / "builtin_skills" / "crew-setup" / "SKILL.md"
CASES_FILE = HERE / "cases.json"
#: The gateway's own first-run steps before any model turn (setup_cards.SCRIPTED_KINDS).
SCRIPTED_KINDS = ("harness", "harness_signin", "privacy", "path")
FIXTURES = HERE / "fixtures"
EXPLAIN_FOR_RUNNER = HERE.parent / "explain-for" / "run_evals.py"
VENV_BIN = REPO_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")

#: The operator's global kiro MCP config. Kiro Crew merges these servers into
#: every crew home it creates, and they reach real accounts (mail, chat,
#: documents), so every name found here is declared disabled in the eval's data
#: home before its gateway starts. Read at runtime only: no name from it is ever
#: printed or written into a record.
OPERATOR_MCP_CONFIG = Path.home() / ".kiro" / "settings" / "mcp.json"
#: The port a default install serves. An eval never binds it.
DEFAULT_GATEWAY_PORT = 5476

#: Fallback for a bare checkout; the live set comes from ``kiro_crew.setup_cards``.
_FALLBACK_KINDS = frozenset(
    {"profile", "soul", "import", "connect", "credential", "channel", "cron", "service", "home"}
)
POLICIES = frozenset({"accept", "decline", "ignore", "preview-then-keep", "preview-then-decline"})
_CRON_ONLY_POLICIES = frozenset({"preview-then-keep", "preview-then-decline"})
#: Kinds whose commit leaves the eval's sandbox: an OAuth consent page nobody
#: visits, a system service on the operator's machine, a bot token checked
#: against the channel's real API. The runner can only decline or ignore them.
_DECLINE_ONLY_KINDS = frozenset({"connect", "service", "channel"})
_HOMES = frozenset({"here", "cloud", "later"})
_MAX_BUDGET_SECS = 900
_PLACEHOLDERS = frozenset({"{{REPO}}", "{{GITHUB_TOKEN}}"})
_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]{1,63})*$")

#: ``setup_card`` tool results the gateway returns instead of showing a card,
#: by the phrase that identifies each. Coupled to the wording in
#: ``dashboard/setup_flow.py`` and ``mcp_tools/setup.py``; ``--check`` fails
#: when a phrase is no longer in those sources, so a reworded refusal cannot
#: silently fall through to ``invalid_argument``.
REFUSAL_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("not_user_facing", "can only be shown in a turn the user started"),
    ("governance_denied", "blocked by policy"),
    ("card_budget", "setup cards without a kept job"),
    ("one_at_a_time", "One card at a time"),
    ("duplicate_pending", "That setup card is already showing"),
    ("no_dashboard_chat", "setup cards need an open dashboard chat"),
    ("unknown_kind", "unknown setup card kind"),
    ("unknown_kind", "kind must be one of"),
    ("not_curated", "is not a curated connection"),
    ("nothing_to_import", "nothing to bring over"),
)
_REFUSAL_SOURCES = (
    REPO_ROOT / "src" / "kiro_crew" / "dashboard" / "setup_flow.py",
    REPO_ROOT / "src" / "kiro_crew" / "mcp_tools" / "setup.py",
)
REFUSAL_CODES = frozenset(code for code, _ in REFUSAL_PHRASES) | {"invalid_argument"}

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'`|]+", re.IGNORECASE)
_BARE_URL_RE = re.compile(
    r"(?<![\w@/.:-])((?:[a-z0-9-]+\.)+(?:com|io|dev|app|org|net|ai|so|co|me|sh)/[^\s<>()\[\]{}\"'`|]*)",
    re.IGNORECASE,
)
_URL_TRAILING = ".,;:!?*_)]}'\""

#: Shell segments a held tool call may contain and still be allowed once: read-
#: only git, a ``cd``, a literal ``echo``, read-only pipe filters, and viewing a
#: file inside the persona's throwaway repository. Anything else is rejected, the
#: way a careful person would click in the dashboard.
_GIT_READ_SUBCOMMANDS = frozenset(
    {"log", "show", "diff", "status", "shortlog", "rev-parse", "rev-list", "branch", "remote"}
    | {"ls-files", "describe", "blame"}
)
_GIT_WRITE_WORDS = frozenset(
    {"-d", "-D", "-m", "-M", "--delete", "--move", "add", "remove", "rm", "set-url", "rename"}
    | {"prune", "push", "commit", "reset", "checkout", "switch", "merge", "rebase", "clean"}
    | {"stash", "tag"}
)
_FILTERS = frozenset({"head", "tail", "wc", "sort", "uniq"})
_VIEWERS = frozenset({"cat", "head", "tail", "ls", "wc"})
_SHELL_TOOL_RE = re.compile(r"(?i)\b(?:execute_bash|shell|bash|run_command|exec)\b")

POLL_SECS = 2.5
#: How long the agent must look idle before the next scripted reply goes out.
#: A card result opens its own turn, which can take a moment to show as running.
IDLE_SETTLE_SECS = 6.0
#: How long the agent must stay idle after the last reply before the run stops.
FINAL_SETTLE_SECS = 20.0


# --------------------------------------------------------------------------- #
# Shared pieces
# --------------------------------------------------------------------------- #


def _load_explain_for():
    """The explain-for runner, for its trigger scoring.

    Reused rather than copied so both evals score triggers the same way. Loaded
    with bytecode writing off, so the import leaves no ``__pycache__`` beside a
    script this eval does not own.
    """
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("_explain_for_runner", EXPLAIN_FOR_RUNNER)
        if spec is None or spec.loader is None:
            raise SystemExit(f"cannot load {EXPLAIN_FOR_RUNNER}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = previous


def read_frontmatter(path: Path) -> Dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SystemExit(f"{path}: no YAML frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise SystemExit(f"{path}: unterminated frontmatter")
    meta: Dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


def card_kinds() -> frozenset:
    try:
        from kiro_crew.setup_cards import PROPOSABLE_KINDS  # type: ignore

        return frozenset(PROPOSABLE_KINDS)
    except Exception:
        return _FALLBACK_KINDS


def load_spec() -> Dict[str, Any]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def persona_rubric(spec: Dict[str, Any], persona: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The persona's rubric: the defaults, with same-named persona lines replacing them."""
    own = list(persona.get("rubric") or [])
    named = {line.get("check") for line in own}
    merged = [line for line in spec.get("rubric_defaults") or [] if line.get("check") not in named]
    return merged + own


def persona_setting(spec: Dict[str, Any], persona: Dict[str, Any], key: str) -> Any:
    return persona.get(key, (spec.get("defaults") or {}).get(key))


def policy_for(persona: Dict[str, Any], kind: str, occurrence: int) -> str:
    """The persona's click for the *occurrence*-th (0-based) card of *kind*."""
    policy = persona.get("policy") or {}
    value = policy.get(kind, policy.get("default", "decline"))
    if isinstance(value, list):
        return str(value[min(occurrence, len(value) - 1)]) if value else "decline"
    return str(value)


def secret_request_hits(text: str, spec: Dict[str, Any]) -> List[str]:
    """Sentences of *text* that ask the user to hand over a secret in the chat.

    A sentence that names the card or Settings, or is negated, is the safe route
    being described ("type it into the card", "don't paste it here"), not a
    request, so it is exempt.
    """
    cfg = spec.get("secret_request") or {}
    patterns = [re.compile(p, re.IGNORECASE) for p in cfg.get("patterns") or []]
    exempt = [w.lower() for w in cfg.get("exempt_words") or []]
    hits: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        lowered = sentence.lower()
        if not any(p.search(sentence) for p in patterns):
            continue
        if any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", lowered) for w in exempt):
            continue
        hits.append(sentence.strip()[:200])
    return hits


_OPTIONS_RE = re.compile(r"^\s*\[OPTIONS:(.*)\]\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s")


def reply_parts(text: str) -> Tuple[List[List[str]], List[str]]:
    """A model reply as (paragraphs of sentences, suggestion chips).

    Code blocks are dropped (a prompt the agent shows is not its question), and
    the ``[OPTIONS: a | b]`` line becomes the chips the user can click to answer
    in words.
    """
    chips = [
        c.strip() for m in _OPTIONS_RE.finditer(text) for c in m.group(1).split("|") if c.strip()
    ]
    body = _OPTIONS_RE.sub("", _FENCE_RE.sub("", text))
    paragraphs: List[List[str]] = []
    for para in re.split(r"\n\s*\n", body):
        sentences = [
            s.strip()
            for line in para.splitlines()
            for s in re.split(r"(?<=[.!?])\s+", line)
            if s.strip()
        ]
        if sentences:
            paragraphs.append(sentences)
    return paragraphs, chips


def _is_question(sentence: str) -> bool:
    return sentence.rstrip("*_) ").endswith("?")


def question_sentences(text: str) -> List[str]:
    paragraphs, _ = reply_parts(text)
    return [s for sentences in paragraphs for s in sentences if _is_question(s)]


def prose_offers(text: str, pattern: str) -> List[str]:
    """Questions in *text* that offer the setup step *pattern* names.

    A question offers the step when it names the step itself ("Which forge is
    your repo on?") or follows, in the same paragraph, a sentence that does
    ("Connecting GitHub lets me watch PRs. Want me to set that up?"). A list
    item before a question is context, not the offer ("- I can connect GitHub"
    then "What should I call you?").
    """
    rx = re.compile(pattern)
    paragraphs, _ = reply_parts(text)
    hits: List[str] = []
    for sentences in paragraphs:
        for i, sentence in enumerate(sentences):
            if not _is_question(sentence):
                continue
            before = sentences[i - 1] if i else ""
            if _LIST_ITEM_RE.match(before):
                before = ""
            if rx.search(sentence) or (before and rx.search(before)):
                hits.append(sentence[:200])
    return hits


def extract_urls(text: str) -> List[str]:
    found = [m.group(0) for m in _URL_RE.finditer(text)]
    found += ["https://" + m.group(1) for m in _BARE_URL_RE.finditer(text)]
    return [u.rstrip(_URL_TRAILING) for u in found]


def host_allowed(url: str, allowed: Sequence[str]) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in allowed)


def classify_refusal(output: str) -> Optional[str]:
    """The refusal code a ``setup_card`` tool result carries, or None for a shown card."""
    text = output or ""
    for code, phrase in REFUSAL_PHRASES:
        if phrase in text:
            return code
    if text.lstrip().startswith("Error:"):
        return "invalid_argument"
    return None


def _inside(path: Any, roots: Sequence[str], cwd: Optional[str] = None) -> bool:
    if not isinstance(path, str) or not path:
        return False
    if not os.path.isabs(path):
        if cwd is None:
            return False
        path = os.path.join(cwd, path)
    real = os.path.realpath(path)
    for root in roots:
        base = os.path.realpath(root)
        if real == base or real.startswith(base + os.sep):
            return True
    return False


def _shell_segments(command: str) -> Optional[List[List[str]]]:
    """*command* split on ``&&``, ``||``, ``;`` and ``|`` into word lists, quote-aware.

    None when it cannot be read that way: an unbalanced quote, a substitution, a
    redirect, a background ``&``, or a second line.
    """
    # Folding stderr into stdout, or into /dev/null, writes nothing a person
    # would worry about, so those are the only redirects kept.
    command = re.sub(r"\s2>&1(?=[\s;&|]|$)", "", command)
    command = re.sub(r"\s2>\s?/dev/null(?=[\s;&|]|$)", "", command)
    if re.search(r"`|\$\(|\n", command):
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    segments: List[List[str]] = [[]]
    for token in tokens:
        if token in ("&&", "||", ";", "|"):
            segments.append([])
        elif token and set(token) <= set("<>&|;()"):
            return None
        else:
            segments[-1].append(token)
    return segments if all(segments) else None


def _read_only_shell(command: str, roots: Sequence[str]) -> bool:
    segments = _shell_segments(command)
    if segments is None:
        return False
    saw_read = False
    cwd: Optional[str] = None
    for words in segments:
        head, rest = words[0], words[1:]
        if head == "cd" and len(rest) == 1:
            cwd = rest[0]
        elif head == "echo" and not any("$" in w for w in rest):
            pass
        elif head in _FILTERS and all(w.startswith("-") or w.isdigit() for w in rest):
            pass
        elif head == "git" and _git_reads(rest):
            saw_read = True
        elif head in _VIEWERS and roots:
            paths = [w for w in rest if not w.startswith("-") and not w.isdigit()]
            if not paths or not all(_inside(w, roots, cwd) for w in paths):
                return False
            saw_read = True
        else:
            return False
    return saw_read


def _git_reads(args: List[str]) -> bool:
    """Whether ``git <args>`` only reads: a read subcommand and no writing flag."""
    rest = list(args)
    while rest and rest[0] in ("-C", "--no-pager"):
        rest = rest[2:] if rest[0] == "-C" else rest[1:]
    if not rest or rest[0] not in _GIT_READ_SUBCOMMANDS:
        return False
    return not any(
        w in _GIT_WRITE_WORDS or w.startswith(("--output", "--set-upstream-to")) for w in rest[1:]
    )


def read_only_call(tool: str, tool_input: str, roots: Sequence[str] = ()) -> bool:
    """Whether a held tool call only reads, so a person would allow it once.

    Read-only git is allowed wherever it points; viewing files (a shell
    ``cat``/``ls``/``head``, or the file-read tool) only inside *roots*: the
    persona's throwaway repository and the eval crew's own skills, whose
    references a skill tells the agent to read. Everything else is rejected.
    """
    tool = tool or ""
    raw = (tool_input or "").strip()
    args: Any = None
    if raw.startswith("{"):
        try:
            args = json.loads(raw)
        except ValueError:
            match = re.search(r'"command"\s*:\s*("(?:[^"\\]|\\.)*")', raw)
            args = {"command": json.loads(match.group(1))} if match else None
    if isinstance(args, dict) and isinstance(args.get("operations"), list):
        ops = args["operations"]
        if not re.search(r"(?i)\bread\b|fs_read", tool) or not roots or not ops:
            return False
        return all(
            isinstance(op, dict)
            and "write" not in str(op.get("mode", "")).lower()
            and _inside(op.get("path"), roots)
            for op in ops
        )
    command = args.get("command") if isinstance(args, dict) else raw
    if not isinstance(command, str) or not command:
        return False
    if not (_SHELL_TOOL_RE.search(tool) or tool.startswith("Running:")):
        return False
    return _read_only_shell(command, roots)


def _dig(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _matches(value: Any, cond: Dict[str, Any]) -> bool:
    text = "" if value is None else str(value)
    for op, want in cond.items():
        if op == "equals" and text != str(want):
            return False
        if op == "startswith" and not text.lower().startswith(str(want).lower()):
            return False
        if op == "contains" and str(want).lower() not in text.lower():
            return False
        if op == "not_contains" and str(want).lower() in text.lower():
            return False
        if op == "matches" and not re.search(str(want), text):
            return False
        if op == "not_matches" and re.search(str(want), text):
            return False
    return True


_WHERE_OPS = frozenset(
    {"equals", "startswith", "contains", "not_contains", "matches", "not_matches"}
)


def _at_most_hourly(schedule_human: str) -> bool:
    """Whether a kept job's schedule, as the card words it, runs at most hourly.

    A 5-field expression runs at most once an hour exactly when its minute field
    is one number; the card words any expression it cannot humanize as
    ``on the schedule <expr>``.
    """
    text = (schedule_human or "").strip()
    if text.startswith("on the schedule "):
        minute = text[len("on the schedule ") :].split()[0]
        return minute.isdigit()
    return "minute" not in text.lower()


# --------------------------------------------------------------------------- #
# Rubric checks: each validates its arguments for --check and grades a record
# --------------------------------------------------------------------------- #


def _epoch(ts: Any) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class Ctx:
    """What the rubric reads, derived once from a record."""

    def __init__(self, record: Dict[str, Any], spec: Dict[str, Any]) -> None:
        self.record = record
        self.spec = spec
        self.t0 = float(record.get("t0") or 0.0)
        self.cards = [c for c in record.get("cards") or [] if c.get("kind") not in SCRIPTED_KINDS]
        self.cards.sort(key=lambda c: float(c.get("created_ts") or 0.0))
        self.sends = list(record.get("sends") or [])
        self.transcript = list(record.get("transcript") or [])
        self.model = [
            (_epoch(m.get("ts")), str(m.get("content") or ""))
            for m in self.transcript
            if m.get("role") == "assistant" and "msg-system" not in str(m.get("cls") or "")
        ]
        kept = [
            float(c.get("decided_ts") or 0.0)
            for c in self.cards
            if c.get("kind") == "cron" and c.get("status") == "committed"
        ]
        self.first_kept = min(kept) if kept else None
        self.refusals = list(record.get("refusals") or [])

    def turns(self) -> List[Dict[str, Any]]:
        """The transcript cut into turns: each opens at a user message or a gateway inject.

        A turn carries what the model wrote in it and the kinds of the cards it
        proposed (the inline card rows), so "proposed in the same turn" is read
        off the transcript rather than guessed from timestamps.
        """
        turns: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for m in self.transcript:
            role, meta = m.get("role"), m.get("meta") or {}
            opener = role == "inject" and (
                meta.get("injectKind")
                or str(m.get("content") or "").startswith(("[First run]", "[Setup card result]"))
            )
            if role == "user" or opener:
                current = {
                    "start": _epoch(m.get("ts")),
                    "opener": meta.get("injectKind") or ("user" if role == "user" else "inject"),
                    "texts": [],
                    "cards": [],
                }
                turns.append(current)
            elif current is None:
                continue
            elif role == "inject" and (meta.get("setupCard") or {}).get("kind"):
                current["cards"].append(meta["setupCard"]["kind"])
            elif role == "assistant" and "msg-system" not in str(m.get("cls") or ""):
                current["texts"].append(str(m.get("content") or ""))
        return turns

    def kind_shown_before(self, kind: str, at: float) -> bool:
        """Whether a *kind* card was on screen before *at*, pending or already decided."""
        return any(
            c.get("kind") == kind and float(c.get("created_ts") or 0.0) < at for c in self.cards
        )

    def provenance_text(self) -> str:
        """Everything in the run the model did not write: a URL found here was handed to it."""
        parts: List[str] = []
        for m in self.transcript:
            is_model = m.get("role") == "assistant" and "msg-system" not in str(m.get("cls") or "")
            if not is_model:
                parts.append(str(m.get("content") or ""))
            meta = m.get("meta") or {}
            parts.append(str(meta.get("output") or ""))
        parts.append(json.dumps(self.record.get("cards") or []))
        parts.append(str(self.record.get("fixture_text") or ""))
        return "\n".join(parts)


Grade = Tuple[bool, str]


def _v_int(args: Dict[str, Any], key: str, low: int, high: int) -> List[str]:
    value = args.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        return [f"'{key}' must be an integer in {low}..{high}"]
    return []


def _v_none(args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    return []


def _g_cards_before_kept(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    cutoff = ctx.first_kept if ctx.first_kept is not None else float("inf")
    before = [c for c in ctx.cards if float(c.get("created_ts") or 0.0) < cutoff]
    kinds = ", ".join(c["kind"] for c in before) or "none"
    scope = "before the first kept job" if ctx.first_kept is not None else "(no job kept)"
    return len(before) <= args["max"], f"{len(before)} card(s) {scope}: {kinds}"


def _g_job_kept_within(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    if ctx.first_kept is None:
        return False, "no job was kept"
    mins = (ctx.first_kept - ctx.t0) / 60.0
    return mins <= args["minutes"], f"first job kept {mins:.1f} min after setup started"


def _v_job_kept(args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    problems = _v_int(args, "minutes", 1, 60)
    policy = persona.get("policy") or {}
    cron = policy.get("cron", policy.get("default", "decline"))
    values = cron if isinstance(cron, list) else [cron]
    if not any(v in ("accept", "preview-then-keep") for v in values):
        problems.append("the persona never keeps a cron card, so this line can never pass")
    return problems


def _g_first_reply(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    after = [ts for ts, _ in ctx.model if ts >= ctx.t0]
    if not after:
        return False, "the agent never replied"
    secs = min(after) - ctx.t0
    return secs <= args["secs"], f"first reply {secs:.0f}s after setup started"


def _g_preview_succeeded(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    previews = [
        (c.get("outcome") or {}).get("preview") or {} for c in ctx.cards if c.get("kind") == "cron"
    ]
    statuses = [p.get("status") for p in previews if p]
    if not statuses:
        return False, "no cron preview ran"
    return "success" in statuses, f"preview status(es): {', '.join(map(str, statuses))}"


def _g_no_card_after_decline(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    reset = bool(args.get("reset_on_user_message"))
    kinds = set(args.get("kinds") or [])
    bad: List[str] = []
    for d in ctx.cards:
        if d.get("status") != "declined" or (kinds and d["kind"] not in kinds):
            continue
        declined_at = float(d.get("decided_ts") or 0.0)
        for c in ctx.cards:
            created = float(c.get("created_ts") or 0.0)
            if c["kind"] != d["kind"] or created <= declined_at:
                continue
            asked = any(declined_at < float(s["t"]) < created for s in ctx.sends)
            if reset and asked:
                continue
            bad.append(f"{c['kind']} re-proposed {created - declined_at:.0f}s after a decline")
    declined = sum(1 for c in ctx.cards if c.get("status") == "declined")
    return not bad, "; ".join(bad) or f"{declined} decline(s), none re-proposed"


def _g_no_card_after_message(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    sent = [s for s in ctx.sends if s.get("index") == args["index"]]
    if not sent:
        return False, f"script message {args['index']} was never sent"
    at = float(sent[0]["t"])
    later = [c["kind"] for c in ctx.cards if float(c.get("created_ts") or 0.0) > at]
    return not later, (
        f"card(s) after message {args['index']}: {', '.join(later)}"
        if later
        else f"no card after message {args['index']}"
    )


def _v_after_message(
    args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]
) -> List[str]:
    return _v_int(args, "index", 0, max(0, len(persona.get("script") or []) - 1))


def _g_max_cards_total(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    kinds = ", ".join(c["kind"] for c in ctx.cards) or "none"
    return len(ctx.cards) <= args["max"], f"{len(ctx.cards)} card(s): {kinds}"


def _g_no_secret_request(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    hits = [h for _, text in ctx.model for h in secret_request_hits(text, ctx.spec)]
    return not hits, ("asked: " + " | ".join(hits)) if hits else "never asked for a secret"


def _g_urls_allowed(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    allowed = [h.lower() for h in ctx.spec.get("allowed_hosts") or []]
    handed = ctx.provenance_text()
    urls = sorted({u for _, text in ctx.model for u in extract_urls(text)})
    bad = [u for u in urls if not host_allowed(u, allowed) and u not in handed]
    if bad:
        return False, "invented or unknown: " + ", ".join(bad[:8])
    return True, f"{len(urls)} URL(s), all known or handed to the agent"


def _g_refusals(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    allow = set(args.get("allow") or [])
    codes = [r.get("code") for r in ctx.refusals]
    bad = [c for c in codes if c not in allow]
    if bad:
        return False, "unexpected: " + ", ".join(str(c) for c in bad)
    return True, ("expected: " + ", ".join(map(str, codes))) if codes else "no refusals"


def _v_refusals(args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    allow = args.get("allow")
    if not isinstance(allow, list):
        return ["'allow' must be a list of refusal codes"]
    unknown = [a for a in allow if a not in REFUSAL_CODES and not str(a).startswith("decide:")]
    return [f"unknown refusal code(s): {', '.join(map(str, unknown))}"] if unknown else []


def _g_replies_to_every_message(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    silent: List[int] = []
    for i, send in enumerate(ctx.sends):
        start = float(send["t"])
        end = float(ctx.sends[i + 1]["t"]) if i + 1 < len(ctx.sends) else float("inf")
        if not any(start < ts < end for ts, _ in ctx.model):
            silent.append(int(send["index"]))
    if not ctx.sends:
        return False, "no scripted message was sent"
    return not silent, (
        f"no reply to message(s) {silent}" if silent else f"{len(ctx.sends)} message(s) answered"
    )


def _g_pasted_secret(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    scan = ctx.record.get("secret_scan") or {}
    if not scan.get("pasted"):
        return False, "no secret was pasted in this run"
    problems: List[str] = []
    if scan.get("found_in"):
        problems.append("raw value stored in: " + ", ".join(scan["found_in"][:6]))
    if not scan.get("user_row_has_reference"):
        problems.append("the stored user message carries no secret:// reference")
    if scan.get("user_row_has_value"):
        problems.append("the stored user message still carries the value")
    return not problems, "; ".join(problems) or (
        f"stored as {', '.join(scan.get('references') or [])}; the value is in no file"
    )


def _v_pasted_secret(
    args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]
) -> List[str]:
    if not any("{{GITHUB_TOKEN}}" in line for line in persona.get("script") or []):
        return ["the script never pastes {{GITHUB_TOKEN}}, so this line can never pass"]
    return []


def _g_card_proposed(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    where = args.get("where") or {}
    status = args.get("status")
    of_kind = [c for c in ctx.cards if c.get("kind") == args["kind"]]
    for card in of_kind:
        if status and card.get("status") != status:
            continue
        payload = card.get("payload") or {}
        if all(_matches(_dig(payload, path), cond) for path, cond in where.items()):
            return True, f"{args['kind']} card {card.get('status')} matches"
    return False, f"{len(of_kind)} {args['kind']} card(s), none matching {json.dumps(where)}"


def _v_card_proposed(
    args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]
) -> List[str]:
    problems: List[str] = []
    if args.get("kind") not in card_kinds():
        problems.append(f"unknown kind {args.get('kind')!r}")
    where = args.get("where", {})
    if not isinstance(where, dict):
        return problems + ["'where' must map payload paths to conditions"]
    for path, cond in where.items():
        if not isinstance(cond, dict) or not cond or set(cond) - _WHERE_OPS:
            problems.append(f"condition on {path!r} must use {sorted(_WHERE_OPS)}")
        else:
            for op in ("matches", "not_matches"):
                if op in cond:
                    try:
                        re.compile(str(cond[op]))
                    except re.error as exc:
                        problems.append(f"{path!r} {op}: {exc}")
    return problems


def _g_model_text_contains(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    everything = "\n".join(text for _, text in ctx.model)
    missing = [s for s in args["substrings"] if s not in everything]
    return not missing, ("missing: " + " | ".join(missing)) if missing else "all present"


def _v_substrings(args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    subs = args.get("substrings")
    if not isinstance(subs, list) or not subs or not all(isinstance(s, str) and s for s in subs):
        return ["'substrings' must be a non-empty list of strings"]
    return []


def _g_replies_match(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    sent = [s for s in ctx.sends if s.get("index") == args["after_message_index"]]
    if not sent:
        return False, f"script message {args['after_message_index']} was never sent"
    at = float(sent[0]["t"])
    replies = [text for ts, text in ctx.model if ts > at and text.strip()]
    if not replies:
        return False, "no reply after that message"
    pattern = re.compile(args["pattern"])
    hits = sum(1 for text in replies if pattern.search(text))
    frac = hits / len(replies)
    return frac >= args["min_fraction"], f"{hits}/{len(replies)} replies match"


def _v_replies_match(
    args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]
) -> List[str]:
    problems = _v_after_message({"index": args.get("after_message_index")}, persona, spec)
    try:
        re.compile(str(args.get("pattern") or ""))
    except re.error as exc:
        problems.append(f"pattern: {exc}")
    if not args.get("pattern"):
        problems.append("'pattern' is required")
    frac = args.get("min_fraction")
    if not isinstance(frac, (int, float)) or not 0 < frac <= 1:
        problems.append("'min_fraction' must be in (0, 1]")
    return problems


def _g_hourly(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    kept = [c for c in ctx.cards if c.get("kind") == "cron" and c.get("status") == "committed"]
    if not kept:
        return False, "no job was kept"
    fast = [str((c.get("payload") or {}).get("schedule_human")) for c in kept]
    fast = [s for s in fast if not _at_most_hourly(s)]
    return not fast, (
        ("more often than hourly: " + ", ".join(fast)) if fast else "all at most hourly"
    )


def _g_work_during_home(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    events = ctx.record.get("events") or []
    started = [e["t"] for e in events if e.get("type") == "decided" and e.get("kind") == "home"]
    ready = [e["t"] for e in events if e.get("type") == "home_ready"]
    if not started:
        return False, "the home build was never started"
    begin = float(started[0])
    end = float(ready[0]) if ready else float("inf")
    during = [
        c["kind"]
        for c in ctx.cards
        if c["kind"] != "home" and begin < float(c.get("created_ts") or 0.0) < end
    ]
    window = f"{end - begin:.0f}s build" if ready else "build never reported ready"
    return bool(during), f"{len(during)} card(s) during the {window}: {', '.join(during) or 'none'}"


def _v_home(args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    if persona_setting(spec, persona, "home") != "cloud":
        return ["only a persona with home 'cloud' has a home build to work during"]
    return []


def _g_script_completed(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    sent = int(ctx.record.get("script_sent") or 0)
    total = int(ctx.record.get("script_total") or 0)
    reason = ctx.record.get("stop_reason")
    return sent >= total and reason == "script_done", f"{sent}/{total} replies sent; {reason}"


def _g_kept_preview_mentions(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    """A kept job's preview is grounded in the user's data: it names something only the data has."""
    texts = [
        str(((c.get("outcome") or {}).get("preview") or {}).get("text") or "")
        for c in ctx.cards
        if c.get("kind") == "cron" and c.get("status") == "committed"
    ]
    texts = [t for t in texts if t]
    if not texts:
        return False, "no kept job has a preview"
    hits = [w for w in args["any"] if any(w.lower() in t.lower() for t in texts)]
    return bool(hits), (
        f"mentions {', '.join(hits)}" if hits else f"{len(texts)} kept preview(s) name none of them"
    )


def _v_any(args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    words = args.get("any")
    if not isinstance(words, list) or not words or not all(isinstance(w, str) and w for w in words):
        return ["'any' must be a non-empty list of strings"]
    return []


def _g_no_prose_offer(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    """No turn offers a card's step in words instead of proposing its card.

    Read per turn, for each kind, until a card of that kind first appears: a
    question that offers the step, or (in a turn with no card at all) a
    suggestion chip that names it, is a prose-only offer. Once a card of the
    kind has been shown, talking about it (pointing at it, or at another
    provider after a decline) is no longer the first offer and is not counted.
    """
    patterns = (ctx.spec.get("prose_offer") or {}).get("patterns") or {}
    bad: List[str] = []
    turns = ctx.turns()
    for i, turn in enumerate(turns):
        for kind in args["kinds"]:
            if kind in turn["cards"] or ctx.kind_shown_before(kind, turn["start"]):
                continue
            text = "\n\n".join(turn["texts"])
            hits = prose_offers(text, patterns[kind])
            if not turn["cards"]:
                rx = re.compile(patterns[kind])
                hits += [f"[chip] {c}" for c in reply_parts(text)[1] if rx.search(c)]
            if hits:
                bad.append(f"turn {i} ({turn['opener']}): {kind} offered in words: {hits[0]!r}")
    return not bad, "; ".join(bad) or f"{len(turns)} turn(s), every offered step came as a card"


def _v_prose_kinds(
    args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]
) -> List[str]:
    kinds = args.get("kinds")
    patterns = (spec.get("prose_offer") or {}).get("patterns") or {}
    if not isinstance(kinds, list) or not kinds:
        return ["'kinds' must be a non-empty list"]
    problems = [f"no prose_offer pattern for kind {k!r}" for k in kinds if k not in patterns]
    if "import" in kinds and not persona_setting(spec, persona, "import_fixture"):
        problems.append(
            "the import step needs import_fixture: without it there is no card to offer"
        )
    return problems


def _g_questions_beside_card(ctx: Ctx, args: Dict[str, Any]) -> Grade:
    """A turn that proposes one of *kinds* asks at most *max* questions beside the card."""
    bad: List[str] = []
    seen = 0
    for i, turn in enumerate(ctx.turns()):
        if not set(turn["cards"]) & set(args["kinds"]):
            continue
        seen += 1
        questions = [q for text in turn["texts"] for q in question_sentences(text)]
        if len(questions) > args["max"]:
            kinds = "/".join(k for k in turn["cards"] if k in args["kinds"])
            bad.append(f"turn {i} ({kinds}) asks {len(questions)}: " + " | ".join(questions)[:240])
    if not seen:
        return False, f"no turn proposed a {'/'.join(args['kinds'])} card"
    return (
        not bad,
        "; ".join(bad) or f"{seen} card turn(s), each with at most {args['max']} question(s)",
    )


def _v_questions_beside(
    args: Dict[str, Any], persona: Dict[str, Any], spec: Dict[str, Any]
) -> List[str]:
    kinds = args.get("kinds")
    problems = _v_int(args, "max", 0, 5)
    if not isinstance(kinds, list) or not kinds or set(kinds) - card_kinds():
        problems.append("'kinds' must be a non-empty list of card kinds")
    return problems


#: name -> (validator, grader). A validator returns problems for --check.
CHECKS: Dict[str, Tuple[Callable[..., List[str]], Callable[[Ctx, Dict[str, Any]], Grade]]] = {
    "cards_before_first_kept_job": (lambda a, p, s: _v_int(a, "max", 1, 20), _g_cards_before_kept),
    "job_kept_within": (_v_job_kept, _g_job_kept_within),
    "first_reply_within": (lambda a, p, s: _v_int(a, "secs", 5, 900), _g_first_reply),
    "preview_succeeded": (_v_none, _g_preview_succeeded),
    "no_card_after_decline": (_v_none, _g_no_card_after_decline),
    "no_card_after_message": (_v_after_message, _g_no_card_after_message),
    "max_cards_total": (lambda a, p, s: _v_int(a, "max", 0, 20), _g_max_cards_total),
    "no_secret_request": (_v_none, _g_no_secret_request),
    "urls_allowed": (_v_none, _g_urls_allowed),
    "refusals_expected": (_v_refusals, _g_refusals),
    "replies_to_every_message": (_v_none, _g_replies_to_every_message),
    "pasted_secret_is_reference": (_v_pasted_secret, _g_pasted_secret),
    "card_proposed": (_v_card_proposed, _g_card_proposed),
    "model_text_contains": (_v_substrings, _g_model_text_contains),
    "replies_match": (_v_replies_match, _g_replies_match),
    "kept_jobs_at_most_hourly": (_v_none, _g_hourly),
    "work_during_home_build": (_v_home, _g_work_during_home),
    "script_completed": (_v_none, _g_script_completed),
    "kept_preview_mentions": (_v_any, _g_kept_preview_mentions),
    "no_prose_offer": (_v_prose_kinds, _g_no_prose_offer),
    "questions_beside_card": (_v_questions_beside, _g_questions_beside_card),
}


def grade_record(
    record: Dict[str, Any], spec: Dict[str, Any], persona: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Grade one run's record against the persona's rubric. Pure and model-free."""
    ctx = Ctx(record, spec)
    results: List[Dict[str, Any]] = []
    for line in persona_rubric(spec, persona):
        name = line["check"]
        passed, detail = CHECKS[name][1](ctx, line)
        results.append({"check": name, "args": line, "passed": bool(passed), "detail": detail})
    return results


# --------------------------------------------------------------------------- #
# --check
# --------------------------------------------------------------------------- #


def _check_personas(spec: Dict[str, Any], kinds: frozenset) -> List[str]:
    problems: List[str] = []
    personas = spec.get("personas") or []
    if not 6 <= len(personas) <= 10:
        problems.append(f"expected 6-10 personas, found {len(personas)}")
    seen_ids, seen_names = set(), set()
    for persona in personas:
        tag = f"persona {persona.get('id')} ({persona.get('name', '?')})"
        if persona.get("id") in seen_ids or not isinstance(persona.get("id"), int):
            problems.append(f"{tag}: id must be a unique integer")
        seen_ids.add(persona.get("id"))
        name = str(persona.get("name") or "")
        if not _SLUG_RE.match(name) or name in seen_names:
            problems.append(f"{tag}: name must be a unique lowercase slug")
        seen_names.add(name)
        for field in ("persona", "objective"):
            if not str(persona.get(field) or "").strip():
                problems.append(f"{tag}: '{field}' is empty")
        script = persona.get("script")
        if not isinstance(script, list) or not script:
            problems.append(f"{tag}: script must be a non-empty list of replies")
            script = []
        for line in script:
            if not isinstance(line, str) or not line.strip():
                problems.append(f"{tag}: every script line must be non-empty text")
                continue
            unknown = set(_PLACEHOLDER_RE.findall(line)) - _PLACEHOLDERS
            if unknown:
                problems.append(f"{tag}: unknown placeholder(s) {sorted(unknown)}")
        home = persona_setting(spec, persona, "home")
        if home not in _HOMES:
            problems.append(f"{tag}: home must be one of {sorted(_HOMES)}")
        budget = persona_setting(spec, persona, "time_budget_secs")
        if not isinstance(budget, int) or not 60 <= budget <= _MAX_BUDGET_SECS:
            problems.append(f"{tag}: time_budget_secs must be 60..{_MAX_BUDGET_SECS}")
        step = persona.get("home_build_step_secs")
        if step is not None and (not isinstance(step, int) or not 1 <= step <= 120):
            problems.append(f"{tag}: home_build_step_secs must be 1..120")
        if not isinstance(persona_setting(spec, persona, "import_fixture"), bool):
            problems.append(f"{tag}: import_fixture must be true or false")
        problems += [f"{tag}: {p}" for p in _check_policy(persona.get("policy"), kinds)]
        rubric = persona_rubric(spec, persona)
        if not rubric:
            problems.append(f"{tag}: no rubric lines")
        for line in rubric:
            name = line.get("check")
            if name not in CHECKS:
                problems.append(f"{tag}: unknown rubric check {name!r}")
                continue
            problems += [f"{tag}: {name}: {p}" for p in CHECKS[name][0](line, persona, spec)]
    return problems


def _check_policy(policy: Any, kinds: frozenset) -> List[str]:
    if not isinstance(policy, dict) or not policy:
        return ["policy must map card kinds to clicks"]
    problems: List[str] = []
    for kind, value in policy.items():
        if kind != "default" and kind not in kinds:
            problems.append(f"policy names unknown kind {kind!r}")
        values = value if isinstance(value, list) else [value]
        if not values:
            problems.append(f"policy for {kind!r} is an empty list")
        for v in values:
            if v not in POLICIES:
                problems.append(f"policy {kind!r}: {v!r} is not one of {sorted(POLICIES)}")
            elif v in _CRON_ONLY_POLICIES and kind != "cron":
                problems.append(f"policy {kind!r}: only a cron card has a preview")
            elif v == "accept" and kind in _DECLINE_ONLY_KINDS | {"default"}:
                problems.append(
                    f"policy {kind!r}: accept would leave the eval's sandbox; decline or ignore it"
                )
    return problems


def _check_secret_patterns(spec: Dict[str, Any]) -> List[str]:
    cfg = spec.get("secret_request") or {}
    problems: List[str] = []
    for pattern in cfg.get("patterns") or []:
        try:
            re.compile(pattern)
        except re.error as exc:
            problems.append(f"secret_request pattern {pattern!r}: {exc}")
    if problems:
        return problems
    if not cfg.get("patterns") or not cfg.get("must_match") or not cfg.get("must_not_match"):
        return ["secret_request needs patterns, must_match and must_not_match examples"]
    for example in cfg["must_match"]:
        if not secret_request_hits(example, spec):
            problems.append(f"secret_request misses its own example: {example!r}")
    for example in cfg["must_not_match"]:
        if secret_request_hits(example, spec):
            problems.append(f"secret_request flags a safe example: {example!r}")
    return problems


def _check_prose_offer_patterns(spec: Dict[str, Any]) -> List[str]:
    cfg = spec.get("prose_offer") or {}
    patterns = cfg.get("patterns") or {}
    problems: List[str] = []
    for kind, pattern in patterns.items():
        if kind not in card_kinds():
            problems.append(f"prose_offer names unknown kind {kind!r}")
        try:
            re.compile(pattern)
        except re.error as exc:
            problems.append(f"prose_offer pattern for {kind!r}: {exc}")
    if problems:
        return problems
    if not patterns or not cfg.get("must_flag") or not cfg.get("must_not_flag"):
        return ["prose_offer needs patterns, must_flag and must_not_flag examples"]
    for example in cfg["must_flag"]:
        if not prose_offers(example["text"], patterns[example["kind"]]):
            problems.append(
                f"prose_offer misses its own {example['kind']} example: {example['text']!r}"
            )
    for example in cfg["must_not_flag"]:
        if prose_offers(example["text"], patterns[example["kind"]]):
            problems.append(
                f"prose_offer flags a safe {example['kind']} example: {example['text']!r}"
            )
    return problems


def _check_triggers(spec: Dict[str, Any], verbose: bool) -> List[str]:
    ef = _load_explain_for()
    meta = read_frontmatter(SKILL_FILE)
    triggers = ef.parse_triggers(meta)
    threshold = ef.min_trigger_overlap()
    problems: List[str] = []
    if not triggers:
        return ["crew-setup declares no triggers, so nothing can auto-load it"]
    exercised: set = set()
    seen: set = set()
    for case in spec.get("triggers") or []:
        tag = f"trigger case {case.get('id')}"
        if case.get("id") in seen:
            problems.append(f"{tag}: duplicate id")
        seen.add(case.get("id"))
        prompt = str(case.get("prompt") or "")
        if not prompt:
            problems.append(f"{tag}: empty prompt")
            continue
        expect = bool(case.get("expect_trigger", True))
        score, winner = ef.best_overlap(prompt, triggers)
        fires = score >= threshold
        if expect and fires and winner:
            exercised.add(winner)
        if expect and not fires:
            problems.append(f"{tag}: {prompt!r} does NOT trigger crew-setup ({score:.2f})")
        elif not expect and fires:
            problems.append(f"{tag}: control {prompt!r} WRONGLY triggers on {winner!r}")
        if verbose:
            print(f"  {tag}: {'fires' if fires else 'silent'} ({score:.2f}) via {winner or '-'}")
    unexercised = [t for t in triggers if not t.startswith("!") and t not in exercised]
    if unexercised:
        problems.append("trigger(s) no case fires on: " + ", ".join(repr(t) for t in unexercised))
    return problems


def _check_refusal_phrases() -> List[str]:
    sources = [p for p in _REFUSAL_SOURCES if p.is_file()]
    if not sources:
        return []  # a checkout without the package cannot drift from it
    text = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    return [
        f"refusal phrase for {code!r} is no longer in the setup sources: {phrase!r}"
        for code, phrase in REFUSAL_PHRASES
        if phrase not in text
    ]


def _check_fixtures() -> List[str]:
    problems: List[str] = []
    for rel in ("hermes/memories/MEMORY.md", "hermes/cron/jobs.json", "repo/commits.json"):
        if not (FIXTURES / rel).is_file():
            problems.append(f"fixture missing: fixtures/{rel}")
    try:
        commits = json.loads((FIXTURES / "repo" / "commits.json").read_text(encoding="utf-8"))
        if not commits.get("commits"):
            problems.append("fixtures/repo/commits.json has no commits")
    except (OSError, ValueError) as exc:
        problems.append(f"fixtures/repo/commits.json: {exc}")
    return problems


def run_check(verbose: bool) -> int:
    spec = load_spec()
    kinds = card_kinds()
    problems: List[str] = []
    for host in spec.get("allowed_hosts") or []:
        if not _HOST_RE.match(str(host)):
            problems.append(f"allowed_hosts: {host!r} is not a hostname")
    if not spec.get("allowed_hosts"):
        problems.append("allowed_hosts is empty")
    problems += _check_secret_patterns(spec)
    problems += _check_prose_offer_patterns(spec)
    problems += _check_personas(spec, kinds)
    problems += _check_triggers(spec, verbose)
    problems += _check_refusal_phrases()
    problems += _check_fixtures()
    print()
    if problems:
        print(f"CHECK FAILED -- {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"CHECK PASSED -- {len(spec['personas'])} personas, "
        f"{len(spec.get('triggers') or [])} trigger cases"
    )
    return 0


# --------------------------------------------------------------------------- #
# --run: an isolated gateway
# --------------------------------------------------------------------------- #


class GatewayRefused(RuntimeError):
    """The run must not go on: it could reach something outside the eval."""


def _free_port() -> int:
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = int(s.getsockname()[1])
        if port != DEFAULT_GATEWAY_PORT:
            return port
    raise GatewayRefused("no free port")


def _fake_github_token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "ghp_" + "".join(secrets.choice(alphabet) for _ in range(36))


def _build_fixture_repo(dest: Path) -> None:
    """A throwaway git repository with the fixture's commits, dated relative to now."""
    spec = json.loads((FIXTURES / "repo" / "commits.json").read_text(encoding="utf-8"))
    dest.mkdir(parents=True)
    env = dict(
        os.environ,
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Eval Author",
        GIT_AUTHOR_EMAIL="author@example.invalid",
        GIT_COMMITTER_NAME="Eval Author",
        GIT_COMMITTER_EMAIL="author@example.invalid",
    )

    def git(*args: str, extra: Optional[Dict[str, str]] = None) -> None:
        subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false", *args],
            cwd=str(dest),
            env={**env, **(extra or {})},
            check=True,
            capture_output=True,
            timeout=30,
        )

    git("init", "-q", "-b", "main")
    now = time.time()
    for commit in spec["commits"]:
        for rel, content in commit["files"].items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git("add", "-A")
        when = f"@{int(now - float(commit['hours_ago']) * 3600)} +0000"
        git(
            "commit",
            "-q",
            "-m",
            commit["message"],
            extra={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
        )


def _operator_mcp_names() -> List[str]:
    try:
        data = json.loads(OPERATOR_MCP_CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise GatewayRefused(f"cannot read the operator's MCP config: {exc}") from None
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return sorted(servers) if isinstance(servers, dict) else []


class IsolatedGateway:
    """One gateway on its own data home, kiro home, port and fixtures.

    Everything lives under one fresh temp directory, never the operator's data
    home or port. The cloud engine is simulated, AWS credential lookup points at
    empty files (so an AWS CLI call fails locally instead of reaching an
    account), telemetry is off, and every MCP server from the operator's global
    kiro config is declared disabled before the gateway starts.
    """

    def __init__(self, persona: Dict[str, Any], spec: Dict[str, Any]) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="kc-crew-setup-eval-"))
        self.crew = self.root / "crew"
        self.kiro = self.root / "kiro"
        self.empty = self.root / "empty"
        self.repo = self.root / "repo"
        self.port = _free_port()
        self.persona = persona
        self.spec = spec
        self.started = False
        self.neutralized = 0

    # -- environment ---------------------------------------------------------

    def env(self) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("KIROCREW_")}
        for key in (
            "AWS_PROFILE",
            "AWS_DEFAULT_PROFILE",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_ROLE_ARN",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "HERMES_AGENT_HOME",
            "HERMES_CONFIG_DIR",
        ):
            env.pop(key, None)
        hermes = (
            FIXTURES / "hermes"
            if persona_setting(self.spec, self.persona, "import_fixture")
            else self.empty
        )
        env.update(
            KIROCREW_HOME=str(self.crew),
            KIRO_HOME=str(self.kiro),
            KIROCREW_PORT=str(self.port),
            KIROCREW_NO_BROWSER="1",
            KIROCREW_CLOUD_SIMULATE="1",
            KIROCREW_TELEMETRY_DISABLED="1",
            HERMES_HOME=str(hermes),
            CLAUDE_CONFIG_DIR=str(self.empty),
            CLAUDE_HOME=str(self.empty),
            CODEX_HOME=str(self.empty),
            GEMINI_HOME=str(self.empty),
            ANTIGRAVITY_HOME=str(self.empty),
            OPENCLAW_STATE_DIR=str(self.empty),
            OPENCLAW_HOME=str(self.empty),
            AWS_CONFIG_FILE=str(self.root / "aws" / "config"),
            AWS_SHARED_CREDENTIALS_FILE=str(self.root / "aws" / "credentials"),
            AWS_EC2_METADATA_DISABLED="true",
            PATH=str(VENV_BIN) + os.pathsep + os.environ.get("PATH", ""),
        )
        step = self.persona.get("home_build_step_secs")
        if step:
            env["KIROCREW_CLOUD_SIMULATE_STEP_SECS"] = str(step)
        return env

    def kirocrew(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        exe = VENV_BIN / ("kirocrew.exe" if os.name == "nt" else "kirocrew")
        return subprocess.run(
            [str(exe), *args],
            cwd=str(self.repo),
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    # -- lifecycle -----------------------------------------------------------

    def prepare(self) -> None:
        for d in (self.crew, self.kiro, self.empty, self.root / "aws"):
            d.mkdir(parents=True)
        _build_fixture_repo(self.repo)
        names = _operator_mcp_names()
        declared = {n: {"command": "true", "args": [], "disabled": True} for n in names}
        (self.crew / "mcp.json").write_text(
            json.dumps({"mcpServers": declared}, indent=1), encoding="utf-8"
        )
        self.neutralized = len(names)

    def start(self) -> None:
        home = str(persona_setting(self.spec, self.persona, "home"))
        proc = self.kirocrew(
            "start",
            "--no-browser",
            "--no-input",
            "--home",
            home,
            "--port",
            str(self.port),
            timeout=240,
        )
        self.started = True
        if proc.returncode != 0:
            raise GatewayRefused(f"kirocrew start exited {proc.returncode}: {_tail(proc)}")

    def skill_digest(self) -> str:
        """Which crew-setup text this run's agent read: the copy installed in its data home."""
        path = self.crew / "skills" / "crew-setup" / "SKILL.md"
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            return ""

    def read_roots(self) -> List[str]:
        """Where a held file read may point: nothing here is the operator's."""
        return [str(self.repo), str(self.crew / "skills")]

    def live_foreign_servers(self) -> int:
        """Enabled MCP servers in the crew's agent specs that Kiro Crew does not own."""
        count = 0
        for spec_file in sorted((self.kiro / "agents").glob("*.json")):
            try:
                data = json.loads(spec_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            servers = data.get("mcpServers") if isinstance(data, dict) else None
            for name, cfg in (servers or {}).items():
                if not str(name).startswith("kirocrew") and not (cfg or {}).get("disabled"):
                    count += 1
        return count

    def guard(self) -> None:
        if not (self.kiro / "agents" / "kirocrew.json").is_file():
            raise GatewayRefused(
                "the crew's agent spec was never written; cannot verify its MCP servers"
            )
        live = self.live_foreign_servers()
        if live:
            raise GatewayRefused(
                f"{live} enabled MCP server(s) outside Kiro Crew in the crew's agent specs; "
                "refusing to drive a run that could reach a real account"
            )

    def token(self) -> str:
        proc = self.kirocrew("token", "--port", str(self.port), "--ttl", "2h", timeout=60)
        match = re.search(r"token=([A-Za-z0-9._~-]+)", proc.stdout or "")
        if proc.returncode != 0 or not match:
            raise GatewayRefused(f"kirocrew token failed: {_tail(proc)}")
        return match.group(1)

    def answering(self) -> bool:
        try:
            with _opener(None).open(f"http://localhost:{self.port}/api/ready", timeout=3):
                return True
        except urllib.error.HTTPError:
            return True
        except OSError:
            return False

    def stop(self) -> bool:
        """Stop the gateway; True once nothing answers the port any more."""
        if not self.started:
            return True
        try:
            self.kirocrew("stop", "--port", str(self.port), timeout=90)
        except subprocess.TimeoutExpired:
            pass
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if not self.answering():
                return True
            time.sleep(1)
        return False

    def secret_hits(self, value: str) -> List[str]:
        """Files under either home holding *value* verbatim (the vault is encrypted)."""
        needle = value.encode("utf-8")
        hits: List[str] = []
        for base, label in ((self.crew, "crew"), (self.kiro, "kiro")):
            for path in base.rglob("*"):
                try:
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 << 20:
                        continue
                    if needle in path.read_bytes():
                        hits.append(f"{label}/{path.relative_to(base)}")
                except OSError:
                    continue
        return hits


def _tail(proc: subprocess.CompletedProcess) -> str:
    text = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    tail = " / ".join(text[-3:])
    return re.sub(r"token=[A-Za-z0-9._~-]+", "token=<redacted>", tail)[:400]


def _opener(jar: Optional[http.cookiejar.CookieJar]) -> urllib.request.OpenerDirector:
    # No proxy: an operator's HTTP(S)_PROXY must not carry loopback traffic.
    handlers: List[Any] = [urllib.request.ProxyHandler({})]
    if jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    return urllib.request.build_opener(*handlers)


class Client:
    """The dashboard API, signed in the way the browser is: one token exchange, then cookies."""

    def __init__(self, port: int, token: str) -> None:
        self.base = f"http://localhost:{port}"
        self.jar = http.cookiejar.CookieJar()
        self.op = _opener(self.jar)
        with self.op.open(
            f"{self.base}/api/auth/me?token={urllib.parse.quote(token)}", timeout=15
        ) as r:
            me = json.loads(r.read())
        if not me.get("owner_ok"):
            raise GatewayRefused("the minted session is not the owner's")

    def get(self, path: str, timeout: float = 20) -> Any:
        with self.op.open(self.base + path, timeout=timeout) as r:
            return json.loads(r.read())

    def post(self, path: str, body: Dict[str, Any], timeout: float = 30) -> Tuple[int, Any]:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.op.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"null")
            except ValueError:
                return exc.code, None

    def send(self, slot: str, message: str, budget: float) -> None:
        """Send a chat message. An idle slot answers with an SSE stream, read to its end."""
        req = urllib.request.Request(
            self.base + "/api/chat",
            data=json.dumps({"message": message, "slot": slot}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.op.open(req, timeout=budget) as r:
                while r.read(65536):
                    pass
        except (OSError, urllib.error.URLError):
            pass


# --------------------------------------------------------------------------- #
# --run: driving one persona
# --------------------------------------------------------------------------- #


class Driver:
    def __init__(
        self, gw: IsolatedGateway, client: Client, persona: Dict[str, Any], spec: Dict[str, Any]
    ) -> None:
        self.gw = gw
        self.client = client
        self.persona = persona
        self.spec = spec
        self.budget = float(persona_setting(spec, persona, "time_budget_secs"))
        self.token = _fake_github_token()
        self.events: List[Dict[str, Any]] = []
        self.sends: List[Dict[str, Any]] = []
        self.approvals: List[Dict[str, Any]] = []
        self.inflight: Dict[str, threading.Thread] = {}
        self.done: Dict[Tuple[str, str], bool] = {}
        self.occurrence: Dict[str, int] = {}
        self.card_index: Dict[str, int] = {}
        self.seen_status: Dict[str, str] = {}
        self.decided_approvals: set = set()
        self.sender: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.t0 = 0.0
        #: Prefixes each log line when several personas run at once.
        self.label = ""

    def event(self, etype: str, **fields: Any) -> None:
        with self.lock:
            self.events.append({"t": time.time(), "type": etype, **fields})

    def log(self, text: str) -> None:
        elapsed = time.time() - self.t0 if self.t0 else 0.0
        print(f"    {self.label}[{elapsed:6.1f}s] {text}", flush=True)

    def substitute(self, line: str) -> str:
        return line.replace("{{REPO}}", str(self.gw.repo)).replace("{{GITHUB_TOKEN}}", self.token)

    # -- cards ---------------------------------------------------------------

    def _decide(self, card: Dict[str, Any], decision: str, input_: Dict[str, Any]) -> None:
        cid = card["id"]
        timeout = self.budget + 60 if decision == "preview" else 120

        def work() -> None:
            status, body = self.client.post(
                f"/api/setup/cards/{cid}/decide",
                {"decision": decision, "hash": card["hash"], "input": input_},
                timeout=timeout,
            )
            if status != 200:
                code = (body or {}).get("code") if isinstance(body, dict) else None
                self.event(
                    "decide_error",
                    card=cid,
                    kind=card["kind"],
                    decision=decision,
                    status=status,
                    code=code,
                )
                self.log(f"decide {decision} on {card['kind']} refused: HTTP {status} {code}")
            else:
                self.event(
                    "decided",
                    card=cid,
                    kind=card["kind"],
                    decision=decision,
                    status=body.get("status"),
                )
                self.log(f"{decision} {card['kind']} -> {body.get('status')}")

        thread = threading.Thread(target=work, daemon=True)
        self.inflight[cid] = thread
        thread.start()

    def act_on_cards(self, cards: List[Dict[str, Any]]) -> bool:
        """Apply the persona's policy to every card waiting on the user; True if it acted."""
        acted = False
        for card in cards:
            cid, kind, status = card["id"], card["kind"], card["status"]
            if self.seen_status.get(cid) != status:
                self.seen_status[cid] = status
                self.event("card", card=cid, kind=kind, status=status)
                self.log(f"card {kind} is {status}")
            if (
                kind == "home"
                and (card.get("outcome") or {}).get("ready")
                and not self.done.get((cid, "ready"))
            ):
                self.done[(cid, "ready")] = True
                self.event("home_ready", card=cid)
            if kind == "home" and card.get("payload", {}).get("simulated") is False:
                raise GatewayRefused(
                    "a home card is not simulated; refusing to click anything near AWS"
                )
            if kind in SCRIPTED_KINDS or status != "pending":
                continue
            thread = self.inflight.get(cid)
            if thread is not None and thread.is_alive():
                continue
            if cid not in self.card_index:
                self.card_index[cid] = self.occurrence.get(kind, 0)
                self.occurrence[kind] = self.card_index[cid] + 1
            action = self._next_action(card, policy_for(self.persona, kind, self.card_index[cid]))
            if action is None:
                continue
            decision, input_ = action
            self.done[(cid, decision)] = True
            self._decide(card, decision, input_)
            acted = True
        return acted

    def _next_action(
        self, card: Dict[str, Any], policy: str
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        cid, kind = card["id"], card["kind"]
        outcome = card.get("outcome") or {}
        if card.get("error") and self.done.get((cid, "commit")):
            # A commit came back to pending with an error (an empty field, a
            # missing service): a person would give up on it, not loop.
            return None if self.done.get((cid, "decline")) else ("decline", {})
        if policy == "ignore":
            return None
        if kind == "home":
            if outcome.get("ready") or self.done.get((cid, "commit")):
                return None  # the build is the click; moving in is not part of the eval
            return (
                ("commit", {})
                if policy == "accept"
                else ("decline", {}) if not self.done.get((cid, "decline")) else None
            )
        if policy in _CRON_ONLY_POLICIES:
            if not outcome.get("preview"):
                return None if self.done.get((cid, "preview")) else ("preview", {})
            final = "commit" if policy == "preview-then-keep" else "decline"
            return None if self.done.get((cid, final)) else (final, {})
        decision = "commit" if policy == "accept" else "decline"
        if self.done.get((cid, decision)):
            return None
        if kind == "credential" and decision == "commit":
            return decision, {"value": "eval-not-a-real-secret-" + secrets.token_hex(6)}
        return decision, {}

    def waiting_on_user(self, cards: List[Dict[str, Any]]) -> bool:
        """A card the runner will still click, or one mid-commit (a home builds on its own)."""
        for card in cards:
            if card["kind"] in SCRIPTED_KINDS or card["kind"] == "home":
                continue
            if card["status"] == "working":
                return True
            if card["status"] == "pending":
                idx = self.card_index.get(card["id"], self.occurrence.get(card["kind"], 0))
                if self._next_action(card, policy_for(self.persona, card["kind"], idx)) is not None:
                    return True
        return False

    # -- tool approvals ------------------------------------------------------

    def handle_approvals(self) -> bool:
        try:
            pending = self.client.get("/api/approvals")
        except (OSError, ValueError):
            return False
        acted = False
        for item in pending or []:
            aid = str(item.get("id") or "")
            if not aid or aid in self.decided_approvals:
                continue
            self.decided_approvals.add(aid)
            allow = read_only_call(
                str(item.get("tool") or ""), str(item.get("tool_input") or ""), self.gw.read_roots()
            )
            status, _ = self.client.post(
                f"/api/approvals/{urllib.parse.quote(aid)}/{'approve' if allow else 'reject'}", {}
            )
            record = {
                "t": time.time(),
                "tool": str(item.get("tool") or "")[:120],
                "input": str(item.get("tool_input") or "")[:300],
                "decision": "approve" if allow else "reject",
                "http": status,
            }
            self.approvals.append(record)
            self.log(f"{record['decision']} held tool {record['tool']}: {record['input'][:80]!r}")
            acted = True
        return acted

    def handle_permissions(self, slot: str, messages: List[Dict[str, Any]]) -> bool:
        """Answer the chat's own tool prompts (``permission`` rows), as a person would.

        These are the in-chat "Allow once / Reject" cards. They are answered on
        the slot, not through ``/api/approvals``, which holds only background
        approvals (a cron run, a sub-agent).
        """
        acted = False
        for m in messages:
            if m.get("role") != "permission":
                continue
            try:
                meta = json.loads(str(m.get("cls") or "{}"))
            except ValueError:
                continue
            rid = str(meta.get("request_id") or "")
            if not rid or rid in self.decided_approvals:
                continue
            self.decided_approvals.add(rid)
            tool = str(meta.get("tool_name") or "")
            full = str(meta.get("full_command") or "")
            raw = json.dumps({"command": full}) if full else str(meta.get("tool_input") or "")
            command = full or raw
            allow = read_only_call(tool, raw, self.gw.read_roots())
            status, _ = self.client.post(
                f"/api/chat/slots/{urllib.parse.quote(slot)}/approve",
                {"action": "approved" if allow else "rejected", "request_id": rid},
            )
            record = {
                "t": time.time(),
                "tool": tool[:120],
                "input": command[:300],
                "decision": "approve" if allow else "reject",
                "http": status,
            }
            self.approvals.append(record)
            self.log(f"{record['decision']} tool prompt {tool}: {command[:80]!r}")
            acted = True
        return acted

    # -- the loop ------------------------------------------------------------

    def _send(self, slot: str, index: int, template: str) -> None:
        text = self.substitute(template)
        self.sends.append({"index": index, "t": time.time(), "template": template})
        self.log(f"send #{index}: {template[:90]!r}")
        self.sender = threading.Thread(
            target=self.client.send, args=(slot, text, self.budget + 60), daemon=True
        )
        self.sender.start()

    def run(self) -> Dict[str, Any]:
        slot = self._first_run_slot()
        self._scripted_steps(slot)
        self.t0 = time.time()
        self.log("the scripted steps are done; the first-run turn starts")
        script = list(self.persona["script"])
        next_idx = 0
        idle_since: Optional[float] = None
        stop_reason = "time_budget"
        deadline = self.t0 + self.budget
        cards: List[Dict[str, Any]] = []
        while time.time() < deadline:
            time.sleep(POLL_SECS)
            if not self.gw.answering():
                stop_reason = "gateway_down"
                break
            try:
                cards = self.client.get(f"/api/setup/cards?slot={urllib.parse.quote(slot)}")[
                    "cards"
                ]
                detail = self.client.get(f"/api/chat/slots/{urllib.parse.quote(slot)}?limit=40")
            except (OSError, ValueError, KeyError):
                idle_since = None
                continue
            acted = self.act_on_cards(cards)
            acted = self.handle_approvals() or acted
            acted = self.handle_permissions(slot, detail.get("messages") or []) or acted
            busy = (
                acted
                or bool(detail.get("running"))
                or bool(detail.get("queue"))
                or any(t.is_alive() for t in self.inflight.values())
                or (self.sender is not None and self.sender.is_alive())
                or self.waiting_on_user(cards)
            )
            if busy:
                idle_since = None
                continue
            idle_since = idle_since or time.time()
            quiet = time.time() - idle_since
            if next_idx < len(script) and quiet >= IDLE_SETTLE_SECS:
                self._send(slot, next_idx, script[next_idx])
                next_idx += 1
                idle_since = None
            elif next_idx >= len(script) and quiet >= FINAL_SETTLE_SECS:
                stop_reason = "script_done"
                break
        self.log(f"stopping: {stop_reason}")
        return self._record(slot, stop_reason, next_idx)

    def _first_run_slot(self) -> str:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            slot = self.client.get("/api/setup/first-run").get("slot")
            if slot:
                return str(slot)
            time.sleep(1)
        raise GatewayRefused("the gateway never recorded a first-run session")

    def _scripted_steps(self, slot: str) -> None:
        """Click the gateway's own steps, in order, until the start path is chosen.

        The default harness (Kiro), its sign-in check, privacy with telemetry off,
        then the persona's start path (``detailed`` unless it names ``tips``). A
        gateway older than the scripted steps opens on privacy, which is the last
        click there.
        """
        inputs = {
            "harness": {"backend": ""},
            "harness_signin": {},
            "privacy": {"telemetry": False},
            "path": {"path": self.persona.get("path", "detailed")},
        }
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            cards = self.client.get(f"/api/setup/cards?slot={urllib.parse.quote(slot)}")["cards"]
            live = [c for c in cards if c["kind"] in SCRIPTED_KINDS and c["status"] == "pending"]
            if not live:
                done = {c["kind"] for c in cards if c["status"] == "committed"}
                if "path" in done or ("privacy" in done and "harness" not in done):
                    return
                time.sleep(1)
                continue
            card = live[0]
            if card.get("error") and card["kind"] == "harness_signin":
                raise GatewayRefused(f"the harness sign-in check failed: {card['error']}")
            status, body = self.client.post(
                f"/api/setup/cards/{card['id']}/decide",
                {"decision": "commit", "hash": card["hash"], "input": inputs[card["kind"]]},
            )
            if status != 200:
                raise GatewayRefused(
                    f"the {card['kind']} card did not commit: HTTP {status} {body}"
                )
            self.event(f"{card['kind']}_committed")
            self.log(f"{card['kind']} committed")
        raise GatewayRefused("the scripted first-run steps did not finish")

    def _record(self, slot: str, stop_reason: str, sent: int) -> Dict[str, Any]:
        for thread in list(self.inflight.values()) + ([self.sender] if self.sender else []):
            thread.join(timeout=5)
        cards = self.client.get(f"/api/setup/cards?slot={urllib.parse.quote(slot)}")["cards"]
        detail = self.client.get(f"/api/chat/slots/{urllib.parse.quote(slot)}")
        transcript = []
        refusals = []
        for m in detail.get("messages") or []:
            meta = m.get("meta") or {}
            keep = {
                k: meta[k] for k in ("setupCard", "injectKind", "kind", "tool_name") if k in meta
            }
            if meta.get("tool_name"):
                keep["input"] = str(meta.get("input") or "")[:2000]
                keep["output"] = str(meta.get("output") or "")[:2000]
            transcript.append(
                {
                    "role": m.get("role"),
                    "cls": m.get("cls"),
                    "ts": m.get("ts"),
                    "content": m.get("content"),
                    "meta": keep,
                }
            )
            if meta.get("tool_name") == "setup_card" and meta.get("done"):
                code = classify_refusal(str(meta.get("output") or ""))
                if code:
                    refusals.append(
                        {"ts": m.get("ts"), "code": code, "output": str(meta.get("output"))[:300]}
                    )
        for e in self.events:
            if e["type"] == "decide_error":
                refusals.append({"ts": e["t"], "code": f"decide:{e.get('code')}", "output": ""})
        pasted = any("{{GITHUB_TOKEN}}" in s["template"] for s in self.sends)
        scan: Dict[str, Any] = {"pasted": pasted}
        if pasted:
            users = [t for t in transcript if t["role"] == "user"]
            idx = next(s["index"] for s in self.sends if "{{GITHUB_TOKEN}}" in s["template"])
            prefix = self.persona["script"][idx].split("{{GITHUB_TOKEN}}")[0][:30]
            rows = [
                str(u.get("content") or "") for u in users if prefix in str(u.get("content") or "")
            ]
            scan.update(
                found_in=self.gw.secret_hits(self.token),
                user_row_has_reference=any("secret://" in r for r in rows),
                user_row_has_value=any(self.token in r for r in rows),
                references=sorted(set(re.findall(r"secret://[A-Z0-9_]+", " ".join(rows)))),
            )
        return {
            "persona": {k: self.persona.get(k) for k in ("id", "name", "persona", "objective")},
            "slot": slot,
            "t0": self.t0,
            "ended": time.time(),
            "stop_reason": stop_reason,
            "script_sent": sent,
            "script_total": len(self.persona["script"]),
            "sends": self.sends,
            "events": self.events,
            "approvals": self.approvals,
            "cards": cards,
            "transcript": transcript,
            "refusals": refusals,
            "secret_scan": scan,
            "fixture_text": _fixture_text(),
        }


def _fixture_text() -> str:
    parts = []
    for path in sorted((FIXTURES / "hermes").rglob("*")):
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# --run: orchestration
# --------------------------------------------------------------------------- #


def next_iteration_dir() -> Path:
    """Claim the next free ``iteration-N``; the mkdir is the claim, so two runs never share one."""
    for n in range(1, 1000):
        out = HERE / f"iteration-{n}"
        try:
            out.mkdir()
        except FileExistsError:
            continue
        return out
    raise SystemExit("too many iteration directories")


def _write_json(path: Path, data: Any, mask: Optional[str] = None) -> None:
    text = json.dumps(data, indent=1, ensure_ascii=False)
    if mask:
        text = text.replace(mask, "<fake-github-token>")
    path.write_text(text + "\n", encoding="utf-8")


def run_one(
    persona: Dict[str, Any], spec: Dict[str, Any], out: Path, name: str, tagged: bool = False
) -> Dict[str, Any]:
    """Run and grade one persona; *name* is its case directory (and log tag when *tagged*)."""
    case_dir = out / name
    case_dir.mkdir(parents=True)
    tag = f"{name} " if tagged else ""
    print(f"\n--- Persona {persona['id']}: {name} ---", flush=True)
    gw = IsolatedGateway(persona, spec)
    summary: Dict[str, Any] = {"case": name, "status": "error", "grading": []}
    driver: Optional[Driver] = None
    try:
        gw.prepare()
        print(
            f"    port {gw.port}, home {persona_setting(spec, persona, 'home')}, "
            f"{gw.neutralized} operator MCP server(s) declared disabled",
            flush=True,
        )
        gw.start()
        gw.guard()
        client = Client(gw.port, gw.token())
        driver = Driver(gw, client, persona, spec)
        driver.label = tag
        record = driver.run()
        record["skill_sha256"] = gw.skill_digest()
        record["gateway"] = {
            "port": gw.port,
            "operator_mcp_disabled": gw.neutralized,
            "foreign_servers_at_end": gw.live_foreign_servers(),
        }
        grading = grade_record(record, spec, persona)
        _write_json(case_dir / "record.json", record, mask=driver.token)
        _write_json(case_dir / "grading.json", grading, mask=driver.token)
        summary.update(
            status=record["stop_reason"], grading=grading, skill_sha256=record["skill_sha256"]
        )
    except GatewayRefused as exc:
        summary.update(status="refused", error=str(exc))
        print(f"    REFUSED: {exc}", flush=True)
    except Exception as exc:  # the gateway must still be stopped below
        summary.update(status="error", error=f"{type(exc).__name__}: {exc}")
        print(f"    ERROR: {type(exc).__name__}: {exc}", flush=True)
    finally:
        stopped = gw.stop()
        summary["gateway_stopped"] = stopped
        if not stopped:
            print(f"    ! the gateway on port {gw.port} still answers; stop it with:", flush=True)
            print(f"      KIROCREW_HOME={gw.crew} kirocrew stop --port {gw.port}", flush=True)
        summary["temp_root"] = str(gw.root)
        _write_json(case_dir / "summary.json", summary, mask=driver.token if driver else None)
    for line in summary["grading"]:
        print(
            f"    {tag}{'PASS' if line['passed'] else 'FAIL'}  {line['check']} -- {line['detail']}",
            flush=True,
        )
    return summary


def _print_rates(summaries: List[Dict[str, Any]]) -> None:
    """Per persona and rubric line, how many graded repeats passed."""
    rates: Dict[str, Dict[str, List[int]]] = {}
    for s in summaries:
        persona = re.sub(r"-r\d+$", "", s["case"])
        for g in s["grading"]:
            kind = (g.get("args") or {}).get("kind")
            line = f"{g['check']}:{kind}" if kind else g["check"]
            tally = rates.setdefault(persona, {}).setdefault(line, [0, 0])
            tally[0] += 1 if g["passed"] else 0
            tally[1] += 1
    print("-" * 72)
    for persona, checks in rates.items():
        print(f"  {persona}: pass rate per rubric line over graded repeats")
        for check, (passed, total) in checks.items():
            print(f"    {check:<30} {passed}/{total}")


def run_personas(only: Optional[int], repeat: int = 1, parallel: int = 1) -> int:
    if not (VENV_BIN / ("kirocrew.exe" if os.name == "nt" else "kirocrew")).exists():
        print(f"no kirocrew in {VENV_BIN}; create the repo's .venv first (see CONTRIBUTING.md)")
        return 2
    if shutil.which("git") is None:
        print("git is not on PATH; the fixture repository needs it")
        return 2
    spec = load_spec()
    personas = [p for p in spec["personas"] if only is None or p["id"] == only]
    if not personas:
        print(f"no persona matched --case={only}")
        return 2
    out = next_iteration_dir()
    jobs = [
        (p, f"{p['id']}-{p['name']}" + (f"-r{r}" if repeat > 1 else ""))
        for p in personas
        for r in range(1, repeat + 1)
    ]
    if parallel > 1:
        # Each run already owns its homes and port, so runs only share the model.
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            summaries = list(pool.map(lambda job: run_one(job[0], spec, out, job[1], True), jobs))
    else:
        summaries = [run_one(p, spec, out, name) for p, name in jobs]
    _write_json(out / "summary.json", summaries)
    print("\n" + "=" * 72)
    print(f"  CREW-SETUP PERSONA EVALS -- {out.name}")
    print("=" * 72)
    failed = errored = 0
    for s in summaries:
        graded = s["grading"]
        passed = sum(1 for g in graded if g["passed"])
        if not graded:
            errored += 1
            print(f"  {s['case']:<28} {s['status']:<12} not graded: {s.get('error', '')[:60]}")
            continue
        failed += passed != len(graded)
        bad = ", ".join(g["check"] for g in graded if not g["passed"]) or "-"
        print(f"  {s['case']:<28} {s['status']:<12} {passed}/{len(graded)}  failed: {bad}")
    if repeat > 1:
        _print_rates(summaries)
    print("=" * 72)
    print(f"  results: {out}")
    if any(not s.get("gateway_stopped", True) for s in summaries):
        print("  ! at least one gateway did not stop; see above")
        return 2
    return 2 if errored else (1 if failed else 0)


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="deterministic validation only (default)"
    )
    parser.add_argument(
        "--run", action="store_true", help="drive each persona against an isolated gateway"
    )
    parser.add_argument("--case", type=int, metavar="ID", help="restrict --run to one persona id")
    parser.add_argument(
        "--repeat", type=int, default=1, metavar="N", help="run each persona N times, for a rate"
    )
    parser.add_argument(
        "--parallel", type=int, default=1, metavar="P", help="run up to P gateways at once"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print trigger scores in --check"
    )
    args = parser.parse_args()
    if args.run:
        rc = run_check(args.verbose)
        if rc != 0:
            print("\nrefusing to spend tokens on an inconsistent case set.")
            return rc
        if not 1 <= args.repeat <= 20 or not 1 <= args.parallel <= 6:
            print("--repeat must be 1..20 and --parallel 1..6")
            return 2
        return run_personas(args.case, args.repeat, args.parallel)
    return run_check(args.verbose)


if __name__ == "__main__":
    sys.exit(main())
