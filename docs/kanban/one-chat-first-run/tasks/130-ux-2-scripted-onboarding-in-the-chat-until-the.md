---
id: 130
title: UX.2 Scripted onboarding in the chat until the harness is ready (no inference)
status: review
priority: high
created: 2026-10-02T14:32:54.534289088Z
updated: 2026-10-02T17:41:02.833465771Z
tags:
    - ux
    - design
    - frontend
    - backend
depends_on:
    - 129
class: standard
---

From the group's onboarding board (2026-10-02). Before a harness is installed and signed in there is no inference, so the first part of onboarding cannot be the agent. It works like a standard onboarding rendered in the chat: the gateway writes each step as a scripted message with its card (as a hint, P2.25), and the composer is locked with a plain explanation until the harness is ready. Steps: welcome; choose the harness (default Kiro; the selector itself is coming from another team, so build the seam and show the selectable backends); install and sign in to that harness in the chat (moves the KiroPrerequisiteGate install/sign-in/re-check into a card; another harness gets its own sign-in); privacy; then the start-path fork (UX.3). Only once the harness answers does the gateway send the [First run] kickoff and the agent take over. Must not assume kiro-cli: identity is positive per harness-parity, and the board asks what breaks when the harness is not Kiro. Related: Q2 (#5), #95, EG.8 (#123).

[[2026-10-02]] Fri 14:57
Design (read-only pass, harness-guide). Seams exist: gateway-only cards with no model (privacy, home choose) and inject rows with no injectKind (open no turn, reach the model as gateway breadcrumbs, not as its own words). New: three gateway-only kinds harness / harness_signin / path (proposable=False, reported=False, governed=False like privacy; harness commit re-validated against governance-narrowed selectable_backends() and writes agent.acp_backend, live), step rows as inject + meta.setupStep drawn by a SetupStepMessage renderer, a per-harness readiness verdict (Kiro: verified_ready, never spawn acp to probe; KAS: kas-login usable; others: install probe + no-prompt handshake, never read their credentials), a server-side composer lock in api_chat before secret capture (409 setup_step_pending, keyed on pending scripted cards in the first-run slot, fails open), ChatInput lockedReason, resume_scripted_onboarding on restart (never auto-dispatch the kickoff). Order: welcome+harness, harness_signin, privacy, path, kickoff (path commit is the first model call; kickoff facts carry engine and path). Today's first run BREAKS on a non-Kiro harness: KiroPrerequisiteGate wraps the app and never reads agent.acp_backend; the prerequisite service always probes kiro-cli; reject_if_kiro_unverified 503s regenerate/rewind on any harness; /api/models uses a negative identity test (H5); connections/mint.py spawns kiro-cli for the connect card; _mark_kiro_signed_out runs on any harness's auth failure; cli_start/setup.sh assume kiro-cli. New named set ACP_BACKENDS_KIRO_CLI_PREREQUISITE = {KIRO, KAS} (H5/H6/H8). Needs RFC §5.1 revision (privacy no longer first card) and a decision entry for the scripted order and composer lock. Sequence after #129.

Done (uncommitted):
- The first-run chat opens on the gateway's scripted steps (no model until the harness answers): welcome + harness choice, install and sign in, privacy, then the start path (UX.3). Each is a step message (inject row, meta.setupStep, catalog copy; replays as the gateway's breadcrumb, not the agent's words) plus its card as a tray hint.
- Kinds harness, harness_signin, path (gateway-only, governed=False like privacy; the harness list is selectable_backends() after governance, re-checked at commit, H3/H4). Readiness verdict in dashboard/harness_readiness.py: the Kiro prerequisite probe for kiro-cli harnesses (never an acp spawn), install probe + no-prompt handshake for any other; no credential files read. Check again until ready, then a lit Continue; after one failed check, Continue without checking.
- Composer lock: api_chat refuses setup_step_pending before the paste capture; ChatInput lockedReason (ChatPage, ChatPane). Restart resume in ensure_first_run_session; a finished script posts the kickoff notice, never a turn. An AcpAuthRequired before the first reply re-shows the sign-in step and the commit resends the kickoff.
- Non-Kiro breakages (fork): status applies/scripted_first_run + gate deferral; reject guard, /api/models, the signed-out latch by positive set (ACP_BACKENDS_KIRO_CLI_PREREQUISITE); kirocrew start skips the terminal sign-in on a fresh install; setup.sh wording. Mint unchanged (needs a per-harness mint; see report).
- Docs: first-run.md, RFC 5.1 + SC8 + Q2 + Q19, decision 2026-10-02-first-run-opens-on-scripted-steps-with-the-composer-locked.md, cli.md, learn-cron-dashboard.md, acp-client.md, harness-parity.md, install.md; crew-setup SKILL branches on the path; evals click the steps.
- Tests: test_setup_flow (TestScriptedSteps, restart, sign-in again), test_harness_readiness, test_first_run_composer_lock, ScriptedSteps.test.tsx, plus the fork's. Verified in the simulated demo: Kiro to the agent's hello, a restart mid-phase, DeepSeek to its sign-in step and the lock. Screenshots: temp-screenshots/ux2-scripted-first-run/.
