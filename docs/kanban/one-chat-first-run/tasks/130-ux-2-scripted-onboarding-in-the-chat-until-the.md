---
id: 130
title: UX.2 Scripted onboarding in the chat until the harness is ready (no inference)
status: todo
priority: high
created: 2026-10-02T14:32:54.534289088Z
updated: 2026-10-02T14:57:38.839348286Z
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
