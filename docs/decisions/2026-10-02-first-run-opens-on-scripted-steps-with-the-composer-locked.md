# The first run opens on scripted steps in the chat, with the composer locked until the agent can answer

Decided by: Akim Akimov (owner of the one-chat first run), on group feedback; a maintainer restates it on the pull request that adds this entry (README rule 4)
Date: 2026-10-02

## Decision

Until a harness is installed and signed in, the first-run chat is a scripted
onboarding that the gateway runs itself, one message with its card (a tray
hint) per step, in this order: choose the agent engine (Kiro the default),
install and sign in to it, privacy, then get started with tips or a more
detailed setup. The chat's composer is locked with the step it waits on, and
the gateway refuses a message there, until the last step sends the agent its
first turn.

## Why

- Group feedback on the onboarding board: with no harness signed in there is no
  inference, so the first part of onboarding cannot be the agent. It has to work
  like a standard onboarding, rendered in the chat.
- The owner of the first run chose the board's order, with privacy after the
  sign-in. Nothing is sent before privacy is acknowledged, so the disclosure
  still comes before any data leaves the machine.
- The owner chose to keep these steps in the chat rather than in a row of
  modals (board question Q19), and accepted the recommended answers to the
  design's open questions: Continue is a click, lit when the status reads
  ready; after one failed sign-in check the owner may continue without it; the
  lock covers the first-run chat only; a harness other than Kiro is checked by
  a handshake with no prompt, and a first reply that cannot sign in brings the
  sign-in step back; on a fresh install `kirocrew start` leaves the harness to
  the chat.
- It replaces the privacy card as the first-run chat's first card, and the
  full-screen Kiro CLI setup page in front of that chat on a first run. Neither
  was a recorded decision, so no entry is superseded.

## Evidence

- Board cards UX.2 and UX.3 (`docs/kanban/one-chat-first-run/tasks/130-*`,
  `131-*`) and Q19 (`138-*`), from the group's onboarding board.
- `docs/request-for-change/rfc-one-chat-first-run.md` §5.1 step 5 and Q19.
- The pull request that adds this entry, and the maintainer comment on it that
  restates the decision (linked here before the entry merges).
