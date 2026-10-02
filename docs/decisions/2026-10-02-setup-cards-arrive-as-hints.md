# Setup cards arrive in the tray as one-line hints that the owner opens to act

Decided by: Akim Akimov (owner of the one-chat first run), on group feedback; a maintainer restates it on the pull request that adds this entry (README rule 4)
Date: 2026-10-02

## Decision

In the one-chat first run every pending setup card arrives in the tray above
the composer as a one-line hint -- the kind's icon (the shield for a
high-stakes card), its title, a short summary from its payload or its live
state, "N more" when several wait, Not now when it may be declined, and Review
-- and opens only when the owner asks; its commit and every other decision stay
in the opened card.

## Why

- Group feedback on the first run: the onboarding cards got in the way of the
  chat. The owner of the first run chose to show them minimized, as hints that
  expand to act.
- It replaces opening a freshly proposed card in full (commit 4c9af006b). That
  behaviour was not a recorded decision, so no entry is superseded.
- Consent stays where it was: the owner always sees the details a click is
  bound to before making it. Not now is the one action on the hint because
  declining commits nothing.

## Evidence

- Commit 4c9af006b: the behaviour this replaces, a freshly proposed card opened
  in full in the tray.
- The pull request that adds this entry, and the maintainer comment on it that
  restates the decision (linked here before the entry merges).
