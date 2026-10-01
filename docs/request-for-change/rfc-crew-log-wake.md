---
title: Crew log wake -- a worker's write pulls the conductor's tick forward
status: draft
author: Raymond Chen, with kirocrew-lead
created: 2026-10-01
last-audited: 2026-10-01
audited-at: 321fd996a2
doc-pr: 15671
implementation-prs: []
tracking-issues: []
supersedes: []
superseded-by: []
---

# RFC: Crew log wake -- a worker's write pulls the conductor's tick forward

- Status: draft.
- Builds on [`rfc-conductor-work-ledger`](rfc-conductor-work-ledger.md) Phase 3
  (the `work-ledger` probe, in flight on
  [#12781](https://github.com/kirodotdev/KiroCrew/pull/12781)) and on
  [`rfc-append-only-ledger`](rfc-append-only-ledger.md) (the crew log). It adds
  no delivery path and no second loop: it moves one deadline.
- Terminology. An earlier draft of this design (2026-09-22, never in this tree)
  said "the ledger is the channel" and hooked `session_ledger_record`. The word
  has since moved. The per-unit append-only file is the **crew log**
  (`kiro_crew.crew_log`, recorded by default since
  [#14295](https://github.com/kirodotdev/KiroCrew/pull/14295)). The **session
  ledger** (`session_ledger.py`, fold `ledger`) and the **work ledger**
  (`work_ledger.py`, entry type `WORK_ENTRY_TYPE`, rebuilt by
  `rebuild_from_crew_log`) are two folds of that log. So the channel the draft
  wanted already exists: every report a worker makes lands in the crew log
  first. This document hooks the log, not a fold.

## 1. Problem

A conductor learns that a worker finished, got stuck, asked a question or died
by ticking. Phase 3 makes a tick that finds nothing cost no turn, which fixed
the price; it did not fix the delay. The gate runs on the loop's cadence
(`idle_secs`, 300 s by default), so a `question` written one second after a
tick waits 299 s, and a worker whose session was closed with its item open is
noticed only when the staleness window has also elapsed on a later tick.

The eager fold already sits on the crew-log append path and names this gap:
`crew_log/eager.py` folds a slot's board the moment its entry lands, and its
header says "WHAT IT DOES NOT DO: push ... there is no consumer yet to need it."
The conductor's armed gate is that consumer.

The team discussion of 2026-09-30 reached the same place from the product side:
the conductor "does not know when a session it opened ended", because "the
ledger does not trigger a wake". Two routes were weighed. Direct worker to
conductor messaging over `session_send` has the infrastructure but is withheld
for the reason `rfc-conductor-work-ledger` gives (nothing bounds WHAT is sent).
The crew log route needs one more piece, and this RFC is that piece.

## 2. Goals and non-goals

Goals:

1. A worker's actionable report reaches its conductor within seconds of the
   write, not within one cadence.
2. A worker session that is closed with its item still open wakes its conductor
   without waiting for a staleness window.
3. No new delivery path, no new decision path. The push moves a deadline; the
   existing gate and budget decide whether a turn is spent.
4. Loss is harmless. A dropped push is caught by the next scheduled tick, so no
   delivered-map, replay sweep or durable subscription is needed.

Non-goals:

- Lifting the upward `session_send` refusal. The worker still gains no handle on
  its conductor.
- A general event bus, a UI push, or a per-(slot, fold) revision; `eager.py`
  records why that is a separate contract.
- Replacing the periodic tick. It stays as the liveness fallback at whatever
  cadence the conductor already set.
- A configuration switch. Phase 3 decided the gate has none and recorded why.

## 3. Design

```
worker turn --work_report--> work_ledger.apply_worker_report
                                  |  durable write + crew-log append
                                  v
                      crew_log eager queue (already on the append path)
                                  |  worker thread, not the writer
                                  v
            read_binding(worker slot) -> conductor slot -> its armed work-ledger loop
                                  |
                                  v
                      AutoNudgeService.fire_now(loop_id)      <-- the whole change
                                  |
                                  v
                  ordinary _timer body: stop sentinel, caps, probe gate
                     quiet (progress) -> re-arm, no turn
                     wake  (done / blocked / question / stale) -> one turn
```

### 3.1 Trigger one: a worker's write

`apply_worker_report` already appends a `WORK_ENTRY_TYPE` entry to the worker's
crew log through the emitter, and `eager.py` already receives every append on a
queue it drains off the writer's thread. The hook is a second consumer on that
drain: when the entry is a `work/*` entry written from a worker unit, resolve
the binding (`read_binding(worker_slot_key)` gives the conductor slot and item),
find the conductor slot's armed `work-ledger` loop, and call `fire_now`.

`fire_now` is the right seam and the only one touched. Its own docstring states
the contract this design relies on: "it does not deliver the nudge itself. It
re-arms through `_arm_timer`, so the cycle runs inside the ordinary `_timer`
body -- the stop sentinel, the cycle cap, the wall-clock budget, the
approval-stall stop and the probe gate all apply exactly as they do on a
scheduled tick." So a `progress` report pulls the tick forward and the probe
answers quiet, which re-arms and spends nothing; a `done`, `blocked` or
`question` pulls it forward and the probe answers wake. The actionable set,
the fingerprint and `MAX_WAKES_PER_ITEM_PER_HOUR` are Phase 3's and are not
restated here.

The session-ledger fold (`ledger/recorded` entries) is deliberately not a
trigger. A worker's `session_ledger_record` is its own working memory and has
no binding to a conductor; the work ledger is the reporting channel, and Phase
2 made that the only one a worker can write toward its conductor.

### 3.2 Trigger two: a worker session closes

`chat_handlers.close_slot` is the one path a dashboard session is closed
through. After the slot is gone, the same lookup runs: binding, conductor slot,
armed loop, `fire_now`.

Phase 3's liveness rule is a conjunction: quiet past the window, AND the worker
not running, AND the next move still the worker's. "Not running" is true of an
idle worker between turns as well as of a closed one, which is why the window
exists at all: it separates a worker that is thinking from a worker that is
gone. A close is not ambiguous. So the probe gains one input beside
`worker_running`: `worker_closed`. An open item whose worker is closed and whose
next move is the worker's is stale at once, window or not. An item whose move
belongs to the conductor (a `done` awaiting verification) is not woken by the
close, for the reason Phase 3 gives: silence there is the expected end of the
work.

### 3.3 The tick stays, as the fallback

A push can be dropped. The eager queue drops under pressure by design (`eager.py`:
"a slow consumer must cost currency, never turn latency"), the process can
restart between the append and the drain, and `fire_now` refuses when the loop
is mid-fire. None of that needs recovery machinery, because the scheduled tick
runs the identical gate over the identical store a cadence later and sees the
same fingerprint move. The 2026-09-22 draft's `delivered` map, replay sweep and
one-shot scheduler deadline were there to make push the only path; with the
tick kept, they are not needed and are not built.

What changes for the conductor is only how long it waits for the fallback, so
the `goal-conductor` skill can lengthen the patrol cadence once this lands:
the tick is for silence, and silence is measured in hours.

### 3.4 Coalescing

Several workers reporting inside one cadence produce several `fire_now` calls
on the same loop. `_arm_timer` cancels the previous timer and arms a new one at
delay zero, so the loop ticks once and the probe reads every item's newest
event in that one tick; this is the coalescing the draft asked for, obtained
from the existing timer rather than from a queue of envelopes. A `fire_now`
that arrives while the loop is in `_run_fire_cycle` is refused, and the
re-arm at the end of that cycle covers the report that caused it.

## 4. Cost

| | Phase 3 alone | with this RFC |
|---|---|---|
| conductor turns per worker report | gate decides | gate decides (unchanged) |
| delay from `question` to conductor turn | up to `idle_secs` | seconds |
| delay from worker close to conductor turn | `idle_secs` + staleness window | seconds |
| patrol cadence the skill can set | minutes | hours |
| new timers, stores, maps | none | none |

## 5. Security

- The worker gains no handle on its conductor. The push carries no payload: it
  moves a deadline on a loop the conductor armed, and the probe then reads the
  store under the conductor's own identity, exactly as on a scheduled tick.
- A worker cannot spend the conductor's budget faster than Phase 3 allows.
  Every pull-forward runs the same `MAX_WAKES_PER_ITEM_PER_HOUR` cap, cycle cap
  and wall-clock budget; a report storm collapses into one tick per cycle.
- The hook runs on the eager drain thread, never on the writer's thread, so a
  slow or failing lookup cannot delay a worker's append; the drop counter
  `eager_dropped` already measures back-pressure.
- `read_binding` is the Phase 2 resolver and is read with `strict=False`: an
  unreadable binding means no push, and the tick covers it.

## 6. Alternatives considered

- **Direct upward `session_send`.** Infrastructure exists and the refusal is a
  prompt-level and authz-level choice. Rejected again for the reason the work
  ledger RFC records: nothing bounds what is sent, and the receiving conductor
  cannot tell a report from an instruction.
- **A new delivery path for a `[ledger wake]` envelope** (the 2026-09-22 draft).
  Rejected: the gate already delivers a turn with the ledger snapshot, and a
  second path would need its own caps, stop sentinel and approval-stall rule.
- **Hook `session_ledger_record` instead of the crew log.** Rejected: that fold
  has no conductor binding, and it would miss `work_report`.
- **Push from the eager fold to the UI as well.** Deferred; `eager.py` states the
  revision contract it needs, and this RFC adds no reader.

## 7. Open questions

1. Should a worker's `blocked` close pull the tick forward at all, or only
   closes with an open item? Proposal: only open items; a closed item has
   nothing left for the conductor to do about the worker.
2. Does a `progress` push that finds the loop mid-fire need any record? Proposal:
   no; the end-of-cycle re-arm and the fingerprint make it visible next tick.

## 8. Rollout

1. This document.
2. After #12781 merges: the eager-drain consumer, the `close_slot` hook, the
   `worker_closed` probe input, and tests pinning: a `question` append fires
   `fire_now` on the bound conductor's loop and nothing on an unbound slot; a
   `progress` append fires `fire_now` and the tick answers quiet; a close with
   an open worker-move item wakes inside the window; a close with a `done`
   item does not; a dropped push is observed by the next tick.
3. `goal-conductor` skill: lengthen the patrol cadence and name the tick as the
   liveness fallback.
