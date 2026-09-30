---
title: Crews on cloud — the Remote Crew panel splits into crews and mates
status: accepted
author: default
created: 2026-09-30
last-audited: 2026-09-30
audited-at: aec81410e2
doc-pr:
implementation-prs: [14953]
tracking-issues: []
supersedes: []
superseded-by: []
---

# RFC: Crews on cloud — the Remote Crew panel splits into crews and mates

- Status: accepted — the product-shape decision behind the Remote Crew panel
  rework. It lands ahead of its implementation because the First Principles
  review lane reads a product-shape decision off the base branch, and the
  change removes and replaces user-facing surfaces (see § 2). Every "exists
  today" claim was checked at `aec81410e2` (main, 2026-09-30); citations name
  symbols, not line numbers.
- Author: default
- Related: [rfc-crewmates-launch.md](rfc-crewmates-launch.md) (the crewmate /
  custom-agent vocabulary this panel adopts — a *mate* here is one crewmate put
  in the cloud), [rfc-remote-instance-on-fargate.md](rfc-remote-instance-on-fargate.md)
  (the Fargate launch path a mate lane runs on).

## 1. Summary

The Remote Crew panel listed *transports*, not the things being run. One list
held two different kinds of thing — a whole gateway serving a roster, and a
single agent in a task — and described neither well, because a card built to
name a transport cannot say what is actually running. This document records the
decision to reshape that panel:

1. **Two tabs, by what runs, not how it connects.** *Remote crews* lists
   **gateways**: a whole machine running `kirocrew gateway`, serving a whole
   roster, with a dashboard you switch to. *Remote mates* lists **mates**: one
   agent, a single Fargate task carrying one member's bundle, with no dashboard
   and no roster. A crew has no single face to draw and a mate has no dashboard
   to offer, so the two cannot share one list truthfully.
2. **Each launcher behind its own tab's footer button.** The EC2 crew launcher
   — an AWS probe, a size ladder, a subnet, an identity — is a long form, so it
   opens on request from the crews tab's footer rather than sitting above the
   answer to "which crews do I have". The mate picker opens from the mates
   tab's footer.
3. **Cards carry a lane chip and plain words; identifiers move to Details.**
   The SSH host and port, the ECS task target, the instance id and the
   transport acronym move behind the card's kebab (Details); the card face
   carries the crew's avatar, its name, one lane chip and one plain-words line
   about where it is.
4. **One Fargate mate lane, drawn at last.** The core has shipped the
   `aws_fargate` descriptor since `cloud.json` could configure it, but the
   frontend's built-in kind list named only `aws_ec2`, so the mate lane the API
   already accepted was absent from the dashboard. The mates tab draws it. A
   lane no provisioner backs is *not* drawn — an advertised lane that can launch
   nothing is worse than an absent one.

This document records those decisions so the pull request that implements them
can cite a decision rather than propose one.

## 2. Why a decision record

Under [README.md § Status vocabulary](README.md#status-vocabulary), a change to
a user-facing default or the removal or replacement of a user-facing capability
must trace to a document here whose status is `accepted` or later, read off the
base commit. This change does several such things:

- It **removes** the old single "your crews" list and the setup *tab* that was
  the panel's whole second tab, replacing them with two tabs and two
  footer-button launchers.
- It **removes** the identifiers and the two acronyms from the card face,
  moving them into a Details disclosure.
- It **adds** a user-facing lane (Fargate mates) the dashboard previously
  filtered out, and **removes** a placeholder Coder lane chip that advertised a
  lane no provisioner backs.

Prose inside the change is a proposal, not a decision. This document is the
durable record of what was decided, so the implementation PR
([#14953](https://github.com/kirodotdev/KiroCrew/pull/14953)) records it rather
than argues it.

## 3. What exists today (base `aec81410e2`)

The reshape is not yet on main. Verified at `aec81410e2`:

- `website/src/pages/settings/RemoteCrewPanel.tsx` is the panel. It already
  carries the two-tab split and the two footer launchers on this branch's
  base only through the implementation PR — on main the tabs
  (`setTab('crews')` / `setTab('mates')`), the `deploy-crew-open` disclosure
  toggle and the `deploy-mate-open` button are what #14953 introduces.
- `website/src/pages/settings/remoteLane.ts` is the pure lane vocabulary
  (`RemoteLane`, `laneOf`, `laneLabel`, `isMate`, `captionWords`, `detailRows`,
  `detailNote`) — the words a card says, decided away from the JSX that draws
  them.
- `website/src/pages/settings/DeployMateDialog.tsx` is the mate picker and its
  one-sentence, one-checkbox confirmation.
- `website/src/components/remoteProvisionerRenderers.tsx` carries
  `canRenderRemoteProvisionerKind` and `BUILTIN_REMOTE_PROVISIONER_KINDS`, which
  now list `aws_fargate` beside `aws_ec2` so the mate lane is drawable.
- The backend seam is real and shipped: `src/kiro_crew/platform/defaults.py`
  `DefaultRemoteProvisionerProvider` returns the `aws_fargate` descriptor when
  `cloud.json` configures it, and `src/kiro_crew/cloud/fargate_engine.py`
  publishes `serves_mate` per lane.

## 4. Decisions

### 4.1 Two tabs, keyed on what runs

`isMate(inst)` is the single predicate that decides which tab a row lists in,
read off the transport today (`connection_method === 'fargate'`) because that
is the distinction: Fargate is the only connection that reaches a single-agent
task; every other row is a gateway. A second mate lane later teaches this one
function, not every list and card.

### 4.2 The crew card wears the machine glyph, the mate card its own face

A gateway that wore one member's avatar would be claiming to *be* that member,
so a crew keeps the machine glyph. A mate is one agent, so it draws that
member's ghost avatar. The lane chip beside either says which kind it is.

### 4.3 Launchers behind footer buttons

The EC2 crew launcher is a long form and opens from the crews tab footer
(`deploy-crew-open`, an `aria-expanded` / `aria-controls` disclosure over a
`role="region"` panel). The mate picker opens from the mates tab footer
(`deploy-mate-open`). Neither offers the other's lane: EC2 installs a gateway,
which cannot answer "which mate?", and a mate lane cannot create a crew.

### 4.4 Identifiers move to Details

The card face answers "which crew, and can I use it". An identifier answers
neither, so the SSH host/port, ECS task target, instance id and transport
acronym move into the kebab's Details as a verbatim, untruncated lookup table
(`detailRows`), with the long "Remove only unregisters / AWS keeps billing"
note beside them (`detailNote`).

### 4.5 One Fargate mate lane; no chip for a lane nothing backs

The mates tab draws the Fargate lane. A lane the gateway knows about but has
not configured (Fargate before a cluster is set up) gets a disabled chip with
the reason, because a silently absent lane is indistinguishable from one that
does not exist. A lane *no provisioner backs at all* is **not** drawn: an
advertised chip that can launch nothing is a dead end, so the earlier
placeholder Coder chip is removed until a Coder provisioner ships
([#10433](https://github.com/kirodotdev/KiroCrew/issues/10433)).

### 4.6 The mate confirmation names what it costs

A launch is money. The confirm step carries one sentence, one checkbox, and a
billing line that names an approximate hourly figure and links the AWS pricing
calculator, so a reader can tell what a click costs before making it.

## 5. Not a goal

- **Changing any server-side gate.** The credential-recipient confirmation is
  untouched: the client still sends `confirm_recipient` verbatim from the lane's
  own `confirm_before_launch`, and both the boundary and the engine still
  compare it.
- **Connect / Disconnect / Stop / Start / Delete semantics.** Unchanged; only
  where their labels and explanations sit has moved.
- **A Coder lane now.** No provisioner backs one, so none is drawn. If a Coder
  provisioner ships (Refs #10433), the lane returns with it.

## 6. Rollout

One implementation PR, [#14953](https://github.com/kirodotdev/KiroCrew/pull/14953),
carries the reshape, the vocabulary module, the mate picker, the Fargate-lane
renderer registration, the docs and the i18n. This document merges first so the
First Principles lane reads the decision off the base branch when #14953
rebases onto it.
