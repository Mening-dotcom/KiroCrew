---
id: 129
title: P2.25 Setup cards arrive as one-line hints that expand to act
status: review
priority: high
created: 2026-10-02T14:25:05.595077084Z
updated: 2026-10-02T15:28:44.306335442Z
tags:
    - phase-2
    - frontend
    - ux
    - skill
class: standard
---

User decision 2026-10-02 from group feedback: onboarding cards get in the way of the chat. Every pending card arrives in the tray as a one-line hint (kind icon or the high-stakes shield, title, a short payload summary, Review, and Not now when declinable), never auto-expanded; this replaces opening a fresh card in full (4c9af006b). Consent stays in the expanded card: nothing commits from the hint, decline is the one action there. A working/waiting card shows its state in the hint. Several cards: the hint names the first plus 'N more'. Keep Hide, the fold when the chat moves past an expanded card (unless engaged), the ref row opening the card, aria wiring, mobile, and the #125 follow fix. The crew-setup skill learns the card shows as a hint, so its line beside the card says in one sentence what it does and why now. Record the decision under docs/decisions/.

[[2026-10-02]] Fri 15:28
Done, not committed. Tray (PendingSetupCards.tsx): every pending card arrives as a one-line hint, never auto-expanded: kind icon (shield when high-stakes), title, per-kind summary or live state (registry 'hint' -> setupCardHints.ts), 'N more' (names the first), Not now when declinable (registry 'mandatory' for privacy), Review/Hide with aria-expanded+aria-controls. Nothing commits from the hint; Not now posts decline with the hash. Open tray folds on Hide, when the conversation moves after it was opened (conversationMoves), or on scroll-up, except while the owner is engaged with a card. The transcript ref row is now a button that opens the card in the tray and highlights it. Strings: components.setupCardHint.* in all 12 catalogs, 8 plural keys registered; removed the now-dead setupCardTray.show and setupCardTray.cards. Skill: crew-setup says a card shows as a hint and the line beside it says what it does and why now, without narrating buttons. Decision: docs/decisions/2026-10-02-setup-cards-arrive-as-hints.md (needs a maintainer restatement on the PR, rule 4). Spec: first-run.md Pieces row; RFC 6.3. Tests: PendingSetupCards.fold.test.tsx rewritten (22/23 fail on HEAD), setupCardHints.test.ts new, PendingSetupCards.test.tsx opens via Review. Gates: tsc, eslint, i18n:check (base origin/main) clean; 190/193 files green, the 3 failures pre-date this (bn digit ceiling, hi formality ceiling, HomeOffer copy assertion vs HEAD catalog). Evals --check + tests pass. WebKit lane not required (no useVirtualChat/FollowController change). Browser: stub harness /tmp/kc-demo/out-hint (hint, expanded, two cards, desktop+mobile; follow check streaming through Review/Hide ended 0px from bottom) and isolated demo /tmp/kc-demo/out-hint-demo (every kind's hint live).
