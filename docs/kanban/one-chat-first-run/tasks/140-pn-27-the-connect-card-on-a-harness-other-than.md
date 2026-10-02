---
id: 140
title: PN.27 The connect card on a harness other than Kiro
status: todo
priority: medium
created: 2026-10-02T18:20:34.631084459Z
updated: 2026-10-02T18:20:34.631084459Z
tags:
    - phase-2
    - backend
    - harness-parity
    - open-question
class: standard
---

Found building UX.2: the connect card's OAuth mint (connections/mint.py) builds its AcpClient with no acp_backend, so it always spawns kiro-cli, writes a Kiro agent spec, and the grant lands in kiro-cli's store, whatever harness the owner chose. On a non-Kiro first run without kiro-cli the connect card fails; with kiro-cli present it mints a grant another harness may not be able to use (unverified). Options: a per-harness mint, or refuse the connect card off kiro-cli with plain copy (new string) and let the agent skip the step. Product call needed before code.
