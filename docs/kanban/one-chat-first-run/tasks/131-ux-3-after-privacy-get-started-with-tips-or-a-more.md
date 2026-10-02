---
id: 131
title: 'UX.3 After privacy: get started with tips, or a more detailed setup'
status: review
priority: high
created: 2026-10-02T14:32:54.567505308Z
updated: 2026-10-02T17:41:02.86993248Z
tags:
    - ux
    - design
depends_on:
    - 129
class: standard
---

From the group's onboarding board: after the privacy preference the owner picks a path. 'Get started' goes straight to examples of things to do, with tips. 'Set me up' takes the detailed setup: import, theme, profile (name, language), connections (e.g. GitHub), then examples. A scripted card, the last step before inference (UX.2); its answer tells the agent which path to follow. The detailed path is today's flow plus a theme step (UX.4). Both paths must allow arriving in a blank-slate app (UX.6).

Done with UX.2 (uncommitted): the path card (Get started / Set me up) is the last scripted step; its commit sends the kickoff with the path as a fact, and the crew-setup skill's new 'The start path' section branches on it (tips: a hello and two or three things to try with their cards; detailed: today's order). The theme step is UX.4, not built; the detailed hint does not promise it. Pinned by test_setup_flow TestScriptedSteps (both paths named in the kickoff) and ScriptedSteps.test.tsx (the path card).
