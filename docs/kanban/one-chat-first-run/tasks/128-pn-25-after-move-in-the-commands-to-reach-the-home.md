---
id: 128
title: 'PN.25 After Move in: the commands to reach the home next time'
status: review
priority: medium
created: 2026-09-30T15:17:54.061473344Z
updated: 2026-09-30T16:18:16.567378665Z
tags:
    - parallel-nest
    - backend
    - frontend
    - cli
parent: 58
class: standard
---

After a cloud home is built and Move in completes, show how to reach it again: kirocrew cloud connect with explicit --tag/--region (--profile unless default), pause/resume/status/list behind a disclosure, one line for another computer; the same line in kirocrew start. SSM only, no token/URL/credential in any command, card, chat line or log.

[[2026-09-30]] Wed 16:18
Done. Backend: cloud/reconnect.py (reconnect_commands/open_command/home_name; the four registration sites now share home_name). setup_move_in.reach_back adds home{name,tag,region,profile} + reconnect[] to the committed outcome (live and simulated) and the [Setup card result] gets the open command; simulated_result_detail says the command finds nothing. cli_start prints the home + open command after the running line. Frontend: HomeNextTime in HomeMovedDetail.tsx (open command + copy, others behind a disclosure, another-computer line, simulated note); CommandLine extracted to its own module; strings in all 12 catalogs + pseudolocale. Specs: first-run Moving in (Next time + auto-reconnect finding), cli.md cloud + start, RFC 5.7. Tests: test_cloud_reconnect.py (15), test_setup_move_in TestNextTime (+3), test_cli_start TestHomeHint (7), simulated setup_flow test, SetupCardHomeMoveIn Next time (+3). Gates green; two pre-existing unrelated flakes (MarkdownRenderer timing under coverage, bnStyle digit debt). Screenshots in /tmp/kirocrew-move-in-shots/. Connect/tag + auto-reconnect findings in the agent report.
