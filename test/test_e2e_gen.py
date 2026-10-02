"""scripts/e2e_gen/generate.py: from a decision-model run to a Playwright spec."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "e2e_gen" / "generate.py"
_spec = importlib.util.spec_from_file_location("e2e_gen_generate", _PATH)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _act(desc, kind="click", value=None):
    return {"kind": "act", "desc": desc, "action_kind": kind, "value": value}


def _verify(accepted=True, undone=False):
    return {"kind": "verify", "accepted": accepted, "undone": undone}


def test_an_action_description_yields_role_and_accessible_name():
    assert gen.parse_action('button "Dark" | not selected') == ("button", "Dark")
    assert gen.parse_action('option "Theme"') == ("option", "Theme")
    assert gen.parse_action("go back to the previous page") is None


def test_the_trajectory_keeps_accepted_steps_only_and_folds_a_repeated_click():
    events = [
        _act('button "Settings"'),
        _verify(),
        _act('button "Settings"'),
        _verify(),
        _act('button "Focus mode" | not selected'),
        _verify(accepted=False),
        _act('textbox "Search settings…"', kind="type", value="theme"),
        _verify(),
        _act('button "Display"'),
        _verify(undone=True),
        _act("go back to the previous page"),
        _verify(),
    ]
    assert gen.trajectory(events) == [
        {"kind": "click", "role": "button", "name": "Settings", "value": None},
        {"kind": "type", "role": "textbox", "name": "Search settings…", "value": "theme"},
        {"kind": "back"},
    ]


def test_the_start_path_drops_the_origin_and_the_token():
    assert gen.start_path("{BASE}/?token={TOKEN}") == "/"
    assert gen.start_path("{BASE}/settings/display/theme") == "/settings/display/theme"
    assert gen.start_path("{BASE}") == "/"


def test_a_spec_replays_the_steps_by_role_and_asserts_the_end_state():
    case = {
        "id": "settings-dark-mode",
        "goal": "Switch the Mode to Dark.",
        "expect": {"selected": "Dark"},
    }
    steps = [
        {"kind": "click", "role": "button", "name": "Settings", "value": None},
        {"kind": "type", "role": "textbox", "name": 'Say "hi"', "value": "a"},
        {"kind": "click", "role": "button", "name": "Dark", "value": None},
    ]
    spec = gen.render_spec(case, "/", steps)
    assert 'test("settings-dark-mode"' in spec
    assert 'await page.goto("/"' in spec
    assert 'page.getByRole("button", { name: "Settings", exact: true }).click()' in spec
    assert 'page.getByRole("textbox", { name: "Say \\"hi\\"", exact: true }).fill("a")' in spec
    assert spec.index('name: "Settings"') < spec.index('name: "Dark"')
    assert '.filter({ hasText: "Dark" })' in spec


def test_a_url_end_state_becomes_an_escaped_url_assertion():
    case = {"id": "x", "goal": "g", "expect": {"url_contains": "/settings/privacy"}}
    spec = gen.render_spec(case, "/", [])
    assert 'await expect(page).toHaveURL(new RegExp("/settings/privacy"))' in spec


def test_a_case_without_a_code_checked_end_state_is_refused(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps({"id": "a", "start": "{BASE}/", "goal": "g"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="code-checked"):
        gen._load_cases(path, [])


def test_a_keyboard_hint_is_dropped_from_the_locator_name():
    assert gen.accessible_name("Schedule Alt + S") == "Schedule"
    assert gen.accessible_name("Task Runner Alt + P") == "Task Runner"
    assert gen.accessible_name("Previous Ctrl + Shift + [") == "Previous"
    assert gen.accessible_name("Alt text") == "Alt text"
    spec = gen.render_spec(
        {"id": "x", "goal": "g", "expect": {"url_contains": "/schedule"}},
        "/",
        [{"kind": "click", "role": "button", "name": "Schedule Alt + S", "value": None}],
    )
    assert 'name: "Schedule", exact: true' in spec


def test_a_case_setup_request_runs_from_the_page_before_the_case_starts():
    setup = [{"method": "PUT", "path": "/api/config/theme", "json": {"mode": "light"}}]
    case = {"id": "x", "goal": "g", "expect": {"selected": "Dark"}, "setup": setup}
    spec = gen.render_spec(case, "/settings", [])
    assert '[{"method": "PUT", "path": "/api/config/theme", "json": {"mode": "light"}}]' in spec
    assert "expect(setup).toEqual([])" in spec
    # Setup runs on a loaded page, then the case's own start is a fresh load that reads it.
    assert spec.index("page.evaluate") < spec.index('page.goto("/settings"')
    assert "page.evaluate" not in gen.render_spec({**case, "setup": []}, "/", [])


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "A", "x" * 65, "-lead"])
def test_a_case_id_that_is_not_one_plain_path_segment_is_refused(tmp_path, bad):
    path = tmp_path / "cases.jsonl"
    case = {"id": bad, "start": "{BASE}/", "goal": "g", "expect": {"url_contains": "/"}}
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="case id"):
        gen._load_cases(path, [])


@pytest.mark.parametrize("brk", ["\n", "\r", "\u2028", "\u2029"])
def test_a_line_break_in_the_goal_cannot_end_its_comment(brk):
    case = {"id": "x", "goal": f"g{brk}process.exit(1)", "expect": {"url_contains": "/x"}}
    spec = gen.render_spec(case, "/", [])
    assert "  // g process.exit(1)\n" in spec
    assert f"{brk}process.exit" not in spec


def test_a_navigation_click_is_retried_until_what_it_leads_to_is_shown():
    case = {"id": "x", "goal": "g", "expect": {"url_contains": "/settings/privacy"}}
    steps = [
        {"kind": "click", "role": "button", "name": "Settings", "value": None},
        {"kind": "click", "role": "button", "name": "Privacy", "value": None},
    ]
    spec = gen.render_spec(case, "/", steps)
    first = spec.index('name: "Settings"')
    assert spec.count(".toPass(") == 2
    assert spec.index("await expect(async () => {") < first
    assert (
        'await expect(page.getByRole("button", { name: "Privacy", exact: true }))'
        ".toBeVisible({ timeout: 2000 })" in spec
    )
    assert 'toHaveURL(new RegExp("/settings/privacy"), { timeout: 2000 })' in spec


def test_a_last_toggle_click_is_not_retried():
    case = {"id": "x", "goal": "g", "expect": {"selected": "Dark"}}
    steps = [{"kind": "click", "role": "button", "name": "Dark", "value": None}]
    spec = gen.render_spec(case, "/", steps)
    assert ".toPass(" not in spec
    assert 'await page.getByRole("button", { name: "Dark", exact: true }).click()' in spec


def test_the_shortcut_hint_pattern_does_not_backtrack_on_a_long_run_of_pluses():
    import time

    start = time.monotonic()
    gen.accessible_name("x\tAlt+" + "!+" * 40 + "\t")
    assert time.monotonic() - start < 1.0


def test_a_spec_outside_the_playwright_tree_is_validated_from_a_staged_copy(tmp_path, monkeypatch):
    playwright = tmp_path / "website" / "playwright"
    playwright.mkdir(parents=True)
    monkeypatch.setattr(gen, "WEBSITE", tmp_path / "website")
    seen = {}

    def fake_run(rel, port, token, results):
        seen["rel"] = rel
        seen["text"] = (playwright / rel).read_text(encoding="utf-8")
        return True, ""

    monkeypatch.setattr(gen, "_run_playwright", fake_run)
    spec = tmp_path / "elsewhere" / "x.spec.ts"
    spec.parent.mkdir()
    spec.write_text("// spec", encoding="utf-8")
    assert gen._validate(spec, 1, "t", tmp_path / "r") == (True, "")
    assert seen["text"] == "// spec"
    assert seen["rel"].name == "x.spec.ts"
    assert list(playwright.iterdir()) == []


def test_setup_requests_go_only_to_the_local_gateway_api():
    with pytest.raises(SystemExit, match="local gateway"):
        gen._run_setup("file:///etc", "t", [{"method": "GET", "path": "/api/x"}])
    with pytest.raises(SystemExit, match="/api/"):
        gen._run_setup("http://localhost:1", "t", [{"method": "GET", "path": "/../x"}])
