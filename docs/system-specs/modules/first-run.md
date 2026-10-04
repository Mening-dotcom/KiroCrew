# First run and setup cards

The one-chat first run gives a fresh install a single pinned chat in which the
agent sets Kiro Crew up with the user. Every change the agent wants is a
**setup card**: a server-side pending action rendered inline in the chat, which
commits only when the owner clicks it. The chat can also build a **home** in the
owner's own AWS account in the background and move the crew into it. When the
home choice is settled, the chat becomes the **main chat**, where the user works from
then on. The design and its rationale are in
[rfc-one-chat-first-run.md](../../request-for-change/rfc-one-chat-first-run.md);
this spec is the contract the code keeps.

## Pieces

| Piece | Where | Role |
|---|---|---|
| One-command start | `src/kiro_crew/cli_start.py`, `start.sh`, `start.ps1`, `setup.sh` | `kirocrew start` checks the harness (on a terminal a missing kiro-cli is left to the browser), starts or reuses the gateway and opens the first-run or main chat, asking nothing; the three scripts install and then run it. The contract is in [cli](cli.md#start-command). |
| First-run state | `src/kiro_crew/first_run.py` | `data_home()/setup/first-run.json`: the first-run slot key, the stages done, the main chat, first-week tip state, held hand-off notices, and a home answer `kirocrew start --home` recorded for a script. Every change to one key goes through `update_state`, which serializes read-change-write in the gateway process so two writers cannot drop each other's keys. Presentation only. |
| Card store | `src/kiro_crew/setup_cards.py` | The `SetupCard` record, the durable store `data_home()/setup/cards.json`, the kinds it accepts (`CARD_KINDS`), payload hashing, per-kind argument validation, the home's size and price data, the persona files. |
| Setup actions | `src/kiro_crew/setup_actions/` | One module per kind, each a `SetupAction`: its tool arguments and their MCP-side check, its builder, committer, extra decisions, the model-facing title and result sentence, and the flags the flow reads instead of branching on the kind (see [Adding a setup action](#adding-a-setup-action)). |
| Flow | `src/kiro_crew/dashboard/setup_flow.py` | `propose` (the directive applier), `decide` (the owner's click), both dispatching through the setup actions; the committers and watchers the actions name, `ensure_first_run_session`, `start_first_run_turn`, graduation and the crew overview. |
| HTTP | `src/kiro_crew/dashboard/handlers/setup_cards.py` | `GET /api/setup/first-run`, `POST /api/setup/first-run/retry`, `GET /api/setup/cards?slot=`, `GET /api/setup/cards/{id}`, `GET /api/setup/cards/{id}/approvals`, `GET /api/setup/cards/{id}/signin-status`, `POST /api/setup/cards/{id}/decide`, `POST /api/setup/main-chat`. All owner-only. |
| Harness check | `src/kiro_crew/dashboard/harness_readiness.py` | Whether the chosen harness answers, asked by the sign-in step's Continue: the Kiro prerequisite probe for the kiro-cli harnesses, the install probe and a no-prompt handshake, built by the session factory, for any other; and whether a harness says it is signed in, asked of its own status command. Never by reading its credential files (see [The scripted steps](#the-scripted-steps)). |
| Composer lock | `setup_flow.scripted_lock`, `dashboard/chat_handlers.py` (`api_chat`), `website/src/components/setup/scriptedLock.ts` | The first-run chat refuses a message, and its composer is disabled with the step's reason, while a scripted step waits. |
| Guardrails | `src/kiro_crew/dashboard/setup_guardrails.py` | The stall watchdog, the kickoff notice and the quota pause (see [Guardrails](#guardrails)). |
| MCP tools | `src/kiro_crew/mcp_tools/setup.py` | `setup_card` (a session directive) and `setup_status` (read-only). The `setup_card` kind enum, argument properties and description are generated from the proposable setup actions. |
| Card faces | `website/src/components/setup/` | `setupCardRegistry.tsx`: kind → body, title key and flags (`draft`, `refreshesBoot`); a kind with no entry draws `FallbackBody`: the generic title, Approve and Not now, nothing from the payload. `SetupCard.tsx` draws a card, `SetupCardBodies.tsx` holds the bodies (the scripted steps' are in `ScriptedStepBodies.tsx`, the harness picker seam in `HarnessPicker.tsx`, the step messages in `SetupStepMessage.tsx`), and `PendingSetupCards.tsx` is the tray above the composer that keeps every live card of the chat in view while its transcript row folds to a one-line pointer. Every pending card arrives there as a one-line hint, never open ([decision](../../decisions/2026-10-02-setup-cards-arrive-as-hints.md)): the kind's icon (the shield on a high-stakes card), the title, a short summary from the payload or the card's live state (each kind's `hint` in the registry, `setupCardHints.ts`; a kind without one shows its title alone), "N more" when several wait (the hint names the card the latest turn proposed, `setupCardTray.ts` `latestProposedCard`: the newest card row in the transcript still live, else the newest live card), Not now when the card may be declined, and Review. Nothing commits from the hint: Not now is its one decision, and every other decision, the commit included, is in the opened card. Review opens that one card in place, capped at a third of the chat pane and scrolling inside; every other live card stays a one-line hint pinned at the foot of the open tray, with its own Not now and its own Review, which opens it instead (one card open at a time, every card staying mounted so a half-filled one keeps its input); Hide folds it back. An open card also folds once the conversation moves on after it was opened (a user message, a later turn or a notice: `setupCardTray.ts` `conversationMoves`) or when the user scrolls up to read, except while the owner is using a card (a pointer press, a key, or focus inside it), which holds until that card is decided, they hide it, or they scroll up themselves. A folded card stays mounted but inert. The hint's title scrolls the transcript to the card's row (`SetupCardRow.tsx`), which never folds into a turn's "Worked through N steps" (`TurnBlock.tsx` renders a card row in place, so a decided card's result line, a moved-in home's Next time among it, stays on screen); in the transcript the card animates only its own changes (`layoutDependency`), never each re-render of the rows around it; that row, "waiting for your decision below", opens the card in the tray and highlights it. |
| Channel pairing | `src/kiro_crew/dashboard/setup_channel.py` | The `channel` card's commit (the bot token) and its `/pair` code, which lives in process memory only. |
| Job preview | `src/kiro_crew/dashboard/setup_preview.py` | The `cron` card's preview run: the approvals it waits on, shown on the card, and a verdict that counts how they ended (see [Job previews](#job-previews)). |
| The account a home lives in | `src/kiro_crew/cloud/local_signin.py`, `src/kiro_crew/cloud/sizes.py` | Read-only AWS facts (who the CLI signs in as, the account's region, its plan, its vCPU quota), the sign-up links, and the size tiers with their per-region prices (see [The home](#the-home)). |
| AWS sign-in | `src/kiro_crew/dashboard/setup_aws_signin.py` | The `home` card's Sign in to AWS: runs the AWS CLI's own `aws login` from the card and watches for it to land (see [Signing in to AWS](#signing-in-to-aws)). |
| Home sign-in | `src/kiro_crew/dashboard/home_signin.py` | While the home's build waits on its own Kiro sign-in: opens the sign-in page once in the owner's browser when they are on this machine, and posts the one `home_signin` notice (see [The home's Kiro sign-in](#the-homes-kiro-sign-in)). |
| Moving in | `src/kiro_crew/dashboard/setup_move_in.py` | The `home` card's Move in for a live home: reach it over the Instances tunnel, export, send the chat, carry the archive (see [Moving in](#moving-in)). |
| Simulated home | `src/kiro_crew/cloud/simulated_engine.py` | With `KIROCREW_CLOUD_SIMULATE=1`, a launch engine that walks the same steps without AWS, labelled simulated on every card. The demo and the persona evals run on it. |
| Hand-off notices | `src/kiro_crew/dashboard/handoff_notice.py` | The main chat's `handoff_done` notice when a chat it handed work to finishes (see [The main chat](#the-main-chat)). |
| First week | `src/kiro_crew/dashboard/first_week.py` | At most one fixed tip a day in the main chat for seven days (see [The first week](#the-first-week)). |
| Pasted secrets | `src/kiro_crew/dashboard/secret_capture.py` | Moves credentials a user pasted into chat into the vault before the message is stored or sent. |
| Skill | `src/kiro_crew/builtin_skills/crew-setup/SKILL.md` | How the agent runs the first run and any later setup request. |
| Persona context | `src/kiro_crew/context.py` `_build_persona_files_section` | `[AGENT PERSONA]` / `[USER NOTES]` from `data_home()/persona/SOUL.md` and `USER.md`, for the primary agents only. |
| Persona evals | `evals/crew-setup/` | Scripted personas run whole first runs against an isolated gateway on the simulated home; `--check` is the CI half (`test/test_crew_setup_evals.py`). |

## The first-run session

`dashboard/server.py` calls `setup_flow.ensure_first_run_session` after the
session restore. It creates one pinned slot titled for the first run
(`FIRST_RUN_TITLE`), on the `kirocrew-main` agent spec (see
[The main chat](#the-main-chat)), records it in the state file, and opens it on
the first scripted step, the welcome with the harness choice (see
[The scripted steps](#the-scripted-steps)) — only when the install is not
onboarded, the privacy flag is unset, no slot is live and no session exists on
disk. It is idempotent across restarts, and on a restart it picks the scripted
steps up where they stopped. `_theme_payload` reports `first_run_slot`, which the SPA uses to keep
the classic chapters from opening by themselves; `/onboarding` still opens them.
`kirocrew start` lands on the main chat when there is one, else on this chat.

On a fresh install the harness's install and sign-in are steps in this chat,
not a screen in front of it: `kirocrew start` on a terminal skips its own
harness check and opens the browser ([cli](cli.md#start-command)), and the
dashboard's Kiro prerequisite gate leaves its first-run install and sign-in
screen to the chat while a scripted first run is under way
(`scripted_first_run`, [learn-cron-dashboard](learn-cron-dashboard.md)). The
chat's own transcript does not count as an established install until a main chat
is recorded. Installing a harness for the user is RFC Q2, still open.

Before `setup.sh` launches the gateway on WSL, it queries the systemd user
manager with a five-second timeout. A missing, unreachable or unresponsive
manager triggers a visible warning and clears `XDG_RUNTIME_DIR` and
`DBUS_SESSION_BUS_ADDRESS` only in the stop/start subshell. This selects the
gateway's existing no-user-session fallback: filesystem sandboxing remains,
but cgroup memory and process limits are unavailable. A working WSL manager
and other operating systems retain their environment. `--no-start` skips the
probe. Setup neither repairs OS mounts nor changes services or the caller's
environment; an installed service keeps its own environment. The warning
names the user-bus check and the temporary command for later manual starts.
The script preserves the gateway start command's exit status.

On a desktop-width page load that opens on the first-run chat before
graduation, the dashboard starts with the nav rail collapsed to its icons and
the session list hidden (`hooks/useFirstRunLayout.ts`). The rule is decided once
per load and never persisted: the rail and sessions toggles write `mc-nav` and
`mc-sidebar-pinned` as they always do, and a stored value wins. Any other load,
including the main chat after graduation, keeps the stored or default layout.

While the first run is under way (first-run slot known, no main chat yet) the
dashboard holds its generic feature tips in every chat, fetching and spending
nothing, so the setup cards and notices are the only guidance. Tips resume on
their own cadence once graduation sets the main chat, and the user's tips
opt-out still wins.

Committing the last scripted step, the start path, dispatches the `[First run]`
kickoff turn (`FIRST_RUN_PREFIX` in `dashboard/state.py`, `injectKind:
"first_run"`), whose text carries facts the gateway gathered (the agent engine
the owner chose and whether its sign-in check passed, the start path, other
agents detected, curated connections, whether the service is installed, and a
`--home cloud` answer) and the `$crew-setup` token, so the skill body is
expanded into that turn. A first-run chat created before the scripted steps
opens on the privacy card, and committing that card dispatches the kickoff as it
always did. Only a `kirocrew start --home cloud` answer puts a home card on
screen at this point; every other first run asks where the crew lives after
scheduling is kept or skipped (see [The home step](#the-home-step)).

### The scripted steps

Until a harness is installed and signed in no model can answer, so the first
part of the first run is scripted: the gateway shows each step itself, as a
message with its card (a tray hint, like every card), and advances on the
owner's click alone. The order (`setup_cards.SCRIPTED_KINDS`):

1. **Welcome, and the harness** (`harness`). The card offers
   `selectable_backends()` after governance narrowed it, Kiro first and badged as
   the default (harness-parity H1), with whether each is installed here. Its
   picker (`components/setup/HarnessPicker.tsx`) is a seam the full harness
   selector replaces by keeping its props. The commit re-checks the choice
   against the card's options and the live selectable set (H3, H4), then writes
   `agent.acp_backend` (a live key: new sessions use it).
2. **Install and sign in** (`harness_signin`). For the harnesses that run
   kiro-cli (`ACP_BACKENDS_KIRO_CLI_PREREQUISITE`: Kiro, KAS) the body reads the
   live Kiro CLI status (`?refresh=auto`, every 5 s): Kiro's install command for
   the host's platform, then its sign-in commands; a desktop app's bundled copy
   skips the install. Any other harness shows its install command, then what
   the harness itself says about its sign-in: a harness whose `host_auth`
   declaration names a `sign_in_status_command` (Claude Code's
   `claude auth status --json`, Codex's `codex login status`) is asked through
   `GET /api/setup/cards/{id}/signin-status` (`harness_readiness.signed_in`,
   one answer per 8 s however many tabs ask; asked again every 10 s while it says
   signed out). Signed in reads "Signed in to …" with Continue lit; signed out
   names the declared `sign_in_command`; anything else (no command, not
   installed, an older version without it, no answer in time) is unknown and
   the card shows the declared `sign_in_remedy`, verbatim, as before. Continue is
   the check (`dashboard/harness_readiness.py`): the Kiro prerequisite service's
   forced probe for the kiro-cli harnesses (never an `acp` spawn, which signed
   out opens a browser sign-in; KAS also counts Crew's own vault identity), and
   for any other harness its install probe, then a handshake with no prompt:
   the provider a chat gets, built by `build_provider_factory` from the loaded
   config (so its `agent.sandbox` mode, wrap and credential mask are the first
   turn's), started (spawn, `initialize`, `session/new`) and shut down. The status
   answer only words the card; Continue still starts the harness, because a
   harness can be signed in and still unable to start. The button reads Check
   again until the live status says ready, then lights up as Continue. A failed
   check returns the card with its reason; after one, Continue without checking
   commits it unverified.

   **Kiro Crew never reads another harness's credentials.** It asks the harness:
   by its own status command, run in the sandbox that harness's session gets
   (`acp.client.run_sign_in_status_command`: the same preflight, mask and env
   scrub as the session spawn), or by starting it. The command's output can name
   an account, so it is parsed for one answer and never shown or logged.

   **A sandbox that refuses there** is classified, never retried and never
   downgraded (security, "A sandbox that refuses to initialize"). An adapter
   that runs the agent CLI as its own child (claude-agent-acp) answers
   `session/new` with the child's stderr in the error's `data.details`, so the
   signature is read off that too (`acp.client.sandbox_init_failure_from_error`),
   with the layer taken from the argv Crew built. The card names the layer in
   plain words (`harness_sandbox_nested` on macOS when the harness's own sandbox
   refused inside Crew's, `harness_sandbox_crew`, `harness_sandbox_harness`) and
   keeps the classified error's own message, remedy included, behind a Details
   disclosure. No switch that turns a sandbox off is shown unless that message
   carries one, which only a corroborated Crew-layer refusal does.
3. **Privacy** (`privacy`), the existing disclosure. The first heartbeat waits
   on `privacy_acked` whatever the order, so nothing is sent before it.
4. **How to start** (`path`): get started with tips, or a more detailed setup
   (UX.3). Its commit sends the kickoff with the path as a fact, and the
   crew-setup skill branches on it.

**Choose a different engine.** Every step after the harness card offers it
(`payload.change_engine`, hash-bound, so only the gateway sets it). It is the
`change_engine` decision, claimed against the posted hash like a commit
(`setup_flow.change_engine`): the step ends `declined` with
`outcome.change_engine`, nothing is written (the configured engine stays the old
one until the new harness card commits), and a fresh harness card shows. A
sign-in passed before a newer harness card no longer counts, so it is asked again
for the new engine; a privacy answer stands; a start path left through it is
asked again. The lock holds through the gap: a step ended this way with no newer
harness card reads as the harness step (`scripted_lock`, and the dashboard's
`scriptedLock.ts`), and a restart inside that gap shows the harness card.

**Errors before the first turn.** No agent can answer yet and the composer is
locked, so the scripted cards' error notices (and the kickoff notice's retry
error) offer no "Ask the agent". Their words lead with the translated sentence
for the code, and the server's own detail sits behind a Details disclosure.

Each step opens with the gateway's message: an `inject` row with no
`injectKind` (it opens no turn) whose `meta.setupStep` names the step
(`welcome`, `harness`, `signin`, `signin_again`, `privacy`, `path`) and, for
the sign-in steps, the engine's label. The dashboard draws its words from the
catalog by step name (`components/setup/SetupStepMessage.tsx`); the row's
content is an English breadcrumb for the model. The first turn is a cold start,
so its replay shows these rows as `Inject:` lines, the gateway's, never as
`Assistant:` lines the agent would read as its own words.

**The composer lock.** While a scripted card is live in the first-run chat,
`api_chat` refuses a message there with `409 setup_step_pending` (and the step),
before the pasted-secret capture, so a refused message is stored nowhere; the
first-run retry route refuses the same way. The dashboard disables that chat's
composer with the step's reason (`components/setup/scriptedLock.ts`, read from
the tray's own card list). Both read the card store (`setup_flow.scripted_lock`),
never a readiness latch, and only the first-run chat: a lost card store unlocks
it, and every other chat sends as before.

**After a restart.** `ensure_first_run_session` re-runs the advance with no
click: a step left `working` returns to `pending` (`step_interrupted`), a step
whose successor was never shown is shown (the harness card after a step ended by
Choose a different engine), and a finished script whose kickoff
got no reply posts the kickoff notice with Try again once, never a turn of its
own (SC8).

**When the first reply cannot sign in.** A harness whose sign-in fails only on
its first prompt is caught by that turn: on `AcpAuthRequired` in the first-run
chat before the agent has answered, `reopen_signin_after_auth_failure` shows the
sign-in step again (`signin_again`), the composer locks, the kickoff notice is
withheld, and committing the new card sends the kickoff again.

## Lifecycle of a card

1. The agent calls `setup_card(kind=…)`. The tool validates the arguments and
   returns a `setup_card` session directive; it changes nothing.
2. The session's consumer applies it through `apply_session_directive`
   (`dashboard/session_directive_apply.py`). `setup_card` is a dashboard-only
   directive, so a slot-less or tabless caller is refused there.
3. `setup_flow.propose` refuses a turn no person started (SC8), a governance
   denial, a kind the model may not propose (`privacy`, answered as unknown), a
   proposal while the chat's quota is paused, a proposal past the card budget, a
   duplicate of a pending card, and a proposal while another card still waits
   for the owner (one decision at a time; see the `stack_exempt` flag).
   The tool answered the model before this step ran ("requested", never
   "shown"), so every refusal is logged to `gateway.log`, audited
   (`setup_card.propose`, `refused` or `denied`, with its code), and, for a
   proposal a person's turn made, reported to the agent as one
   `[Setup card result] <kind> card not shown: <reason>` envelope turn in its
   chat (`setup_flow._refused`), with the user provenance a result turn carries.
   The transcript folds it to its own note, "A setup card couldn’t be shown"
   (`RecoveryCard.tsx` `isSetupCardRefusal`, the same first-line rule), never the
   "Setup step answered" note a decided card gets.
   At most one is queued per turn, and a chain of them stops at
   `_REFUSAL_TURNS_PER_CHAIN` (2) until a person types, the kickoff runs or a
   card is decided. No turn is sent for a proposal no person caused, a kind the
   tool would not emit, a paused quota (a turn would meet the same limit), or a
   duplicate of a card that is showing (the agent saying so is true).
   An import card's `source_ids` take the source ids the kickoff facts name
   (`Claude Code (source id: claude_code; …)`); a display name or an obvious
   alias ("Claude Code", "claude-code", "claude") resolves to that id, case- and
   punctuation-insensitively, and a name that matches no detected source, or
   more than one, is refused with the ids that exist.
   Otherwise it builds the payload the owner will see (an import preview, a
   provider lookup, the service platform, the current persona file, the home's
   account facts), stores the card, appends an `inject` row whose meta is
   `{"setupCard": {"id", "kind"}}` (no `injectKind`, so it opens no turn), and
   sends the owner-only `setup_card_update` event.
4. The browser renders the card from `GET /api/setup/cards/{id}`, never from the
   row's text.
5. The owner's click posts `{decision, hash, input}` to `/decide`.
   `setup_cards.claim_pending` moves a `pending` card whose stored hash equals
   the posted hash to `working`, atomically, under the store lock; anything else
   is refused (`card_not_pending`, `card_hash_mismatch`). Governance is checked
   again. The committer runs.
6. A recoverable failure (an empty credential, a service not installed yet)
   returns the card to `pending` with `error` set. A terminal status
   (`committed`, `declined`, `failed`, `expired`) is reported to the agent as a
   `[Setup card result]` envelope turn in the card's chat.

Statuses: `pending`, `working`, `waiting` (outside action such as an OAuth
consent page or a build), `committed`, `declined`, `failed`, `expired`. A stored
record whose payload no longer hashes to its `payload_hash` is read back
`expired` with `error.code == "card_tampered"` and can never be claimed. The one
sanctioned payload change is `setup_cards.replace_payload`, for a `pending` card
only, which re-issues it under a new hash (the home card once AWS answers, and
for a picked region), so a click carrying the old hash is refused.

## Kinds

| Kind | Proposed by | Payload shown | Commit does |
|---|---|---|---|
| `harness` | the gateway only, as the first scripted step | the selectable harnesses (`id`, `label`), the current one and the default | re-checks the choice against the options and `selectable_backends()`, writes `agent.acp_backend`, then shows the sign-in step |
| `harness_signin` | the gateway only, after the harness and again when the first reply cannot sign in | the engine (`backend`, `label`) and its flow: `kiro_cli`, or `own` with its install command and `sign_in` remedy | asks the harness whether it answers (`harness_readiness.check`); no: back to pending with the reason; after one failed check `input.skip` commits it unverified. Then the next step, or the kickoff again when the rest is done |
| `privacy` | the gateway only | the privacy disclosure (frontend strings) | sets `dashboard.privacy_acked`; `telemetry.beacon_enabled = false` when the owner turned telemetry off; then the start path in a scripted first run, or, in a first-run chat from before the scripted steps, the home card a `--home cloud` answer asked for and the first model turn |
| `path` | the gateway only, as the last scripted step | `options`: `tips`, `detailed` | records `input.path`, shows the home card a `--home cloud` answer asked for, and starts the first model turn, whose facts name the path |
| `profile` | agent | `fields`: bot_name, language, timezone, technical_level, role | writes those config keys through `update_config_locked` under `run_config_write`, then a hot apply |
| `soul` | agent | `file` (`SOUL`/`USER`), `content` (≤ `SOUL_MAX_CHARS`, 3000), `previous` | writes `data_home()/persona/<file>.md` |
| `import` | agent | detected sources and categories with counts | `onboarding_import.run_import_apply` — the same lock order and re-scan as the Import chapter; imported jobs arrive disabled, and the result names them (name, schedule, a prompt excerpt) because the chat's session-scoped job tools do not list jobs the import created |
| `connect` | agent | a curated provider (`registry.json`), whether it needs an operator OAuth client | writes the remote MCP entry (`mcp_custom.ensure_remote_server`), starts the mint (`connections.start_provider_mint`), goes `waiting` with the consent URL, and a watcher follows `pending_mint_for` to `committed`/`failed`/`expired` |
| `credential` | agent | name, purpose, hosts, whether the name exists | stores the typed value in the vault; the outcome is only `secret://NAME` |
| `channel` | agent | the channel (Telegram, the one in `setup_cards.CHANNELS`) | stores the bot token typed into the card and turns the channel on, then goes `waiting` with a one-time pairing code; a `/pair <code>` DM to the bot allowlists that sender and commits. See [Channel pairing](#channel-pairing) |
| `cron` | agent | name, prompt summary, schedule in words, timezone (the full prompt is private); at most hourly (`CRON_MIN_EVERY_SECS`) | `preview`: creates the job disabled and silent, runs it once and shows the output, then returns to `pending`. `commit` (Keep it): makes it non-silent and enables it, and lifts the card budget. `decline`: removes the preview job. See [Job previews](#job-previews) |
| `service` | agent | platform, the command, whether a terminal is needed, installed | macOS: installs the launchd agent. Linux: verifies the unit exists (the owner runs `kirocrew stop && kirocrew service install`, which needs sudo) |
| `home` | the gateway, as the first run's home step after a kept or declined job and for `kirocrew start --home cloud`; the agent, on request | while it asks where the crew lives (`step: "choose"`): the region and the price floor (`from_usd`) only; then provider, region, AWS profile, whether AWS is signed in and the account's last four digits, the account's plan, the size options, estimated monthly cost, who bills it, whether the run is simulated; the sign-up links when not signed in; the region picker when no region answers | one card for the whole journey, each step a decision on it: `choose` ([The home step](#the-home-step)), `aws_signin` ([Signing in to AWS](#signing-in-to-aws)), `region` ([Asking for the region](#asking-for-the-region)), then `commit` by phase: Build ([Building the home](#building-the-home)), Sign the home in to Kiro ([The home's Kiro sign-in](#the-homes-kiro-sign-in)), Move in ([Moving in](#moving-in)). Commits with `moved: true`, or with `stayed: true` when the owner keeps the crew on this machine; decline ("Not now") answers nothing. A failed card whose build a restart cut short offers `remove` ([What a restart leaves in AWS](#what-a-restart-leaves-in-aws)) |

## Invariants

| Id | Rule | Pinned by |
|---|---|---|
| SC1 | No card commits without an owner decision carrying the payload hash the owner was shown. | `test_setup_flow.py::TestDecide::test_s1_a_wrong_hash_commits_nothing`, `test_setup_cards.py::TestStore`, and for every registered kind `test_setup_actions.py` |
| SC2 | A credential typed into a card never appears in the card, the store, the transcript, an event or a log record. | `test_setup_flow.py::TestDecide::test_s2_a_credential_reaches_the_vault_and_nowhere_else`, `test_setup_channel.py::TestCommit::test_s2_the_bot_token_reaches_the_credential_file_and_nowhere_else` |
| SC3 | No committer writes a governance keystone file or the sandbox/approval mode. | `test_setup_flow.py::TestDecide::test_s3_no_committer_writes_a_keystone_file` |
| SC4 | Kiro Crew never reads or stores an AWS credential: the AWS CLI resolves and caches its own. The card's `aws login` child has every standard stream closed, and a card keeps at most the account's last four digits. | `test_setup_aws_signin.py::TestNoCredentials` |
| SC5 | A schedule runs on exactly one crew during a move-in: the local copies are off before the archive reaches the home, back on when the home does not confirm it, and stay off once it has. | `test_setup_move_in.py::TestHappyPath::test_sc5_the_moving_job_is_off_here_before_the_archive_lands`, `TestCarryFailure`, `TestRetry` |
| SC6 | The first-run state file admits nothing. | `test_setup_flow.py::TestPropose::test_s6_the_first_run_state_file_admits_nothing` |
| SC9 | No model turn runs in the first-run chat before its scripted steps are done: a typed message is refused (`setup_step_pending`) before anything stores it, and the kickoff starts only from the start path's click (or the sign-in step's, after the first reply could not sign in), never from a restart. | `test_setup_flow.py::TestScriptedSteps::test_no_model_turn_runs_before_the_start_path`, `TestScriptedStepsAfterARestart::test_a_finished_script_never_starts_a_turn_on_its_own`, `test_first_run_composer_lock.py` |
| SC8 | A card is raised only in a turn a person started: a typed message, or a turn that exists because the owner clicked a card (the first-run kickoff and every `[Setup card result]` turn carry user provenance for that reason). A `[Setup card result]` saying a card was not shown carries it too: it exists because a person's turn proposed the card, it is never sent for a proposal no person caused, and a chain of them is capped. | `test_setup_flow.py::TestPropose::test_s8_a_turn_no_person_started_shows_nothing` |

## Governance

`capabilities.setup` (`platform/governance.py` `SCOPE_CATALOG`, default on) gates
every proposal and every commit of a governed kind; its inner `kinds` ruleset
checks the card kind as the item, so a fleet can keep cards while refusing, say,
`service`. The scripted steps (`harness`, `harness_signin`, `privacy`, `path`)
are the kinds a policy may not refuse (`governed=False`): nothing else runs
without them. The policy control over the harness is the harness list itself:
the card offers `selectable_backends()` after the agent-backend governance
narrowed it, and the commit re-checks the choice against that live set. Cron cards additionally pass
`capabilities.cron` (`mcp_cron._vet_cron_capability_governance`). The core MCP
server is auto-approved, so the card is the consent step and these checks run
inside the flow, not at the permission gate.

## Guardrails

Four guardrails keep the first run from running away or going quiet, plus the
rule of one decision at a time. Each one that speaks posts one deterministic
system notice in the first-run chat. The notice's English content is the
fallback text; the dashboard draws localized copy keyed on `meta.kind` and
`meta.reason` (`components/setup/SetupGuardrailNotice.tsx`), and every notice
offers classic setup (`/onboarding`).

| Guardrail | Trigger | What the user sees | Then |
|---|---|---|---|
| Card budget | `CARD_BUDGET_BEFORE_FIRST_JOB` (8) proposals without a kept job; the gateway's own cards (privacy, the home question) do not count | nothing; `propose` tells the model to stop proposing | the budget is lifted by the first kept job |
| One at a time | a card of a kind that is not `stack_exempt` is still `pending` | the agent's one line from its `[Setup card result] … not shown` turn | the owner decides the waiting card, and its result turn re-proposes; the home card is exempt both ways, since its build runs in the background |
| Stall | a first-run turn whose progress markers have not moved for `FIRST_RUN_STALL_SECS` (90 s) with nothing to wait on | `setup_stalled`, `reason: no_output`: stop the reply and send again, or use classic setup | at most one per turn |
| Kickoff | the `[First run]` kickoff ends with no reply (and no retry or queued turn follows it), or cannot be dispatched | `setup_stalled`, `reason: kickoff_failed`, with Try again | Try again posts `POST /api/setup/first-run/retry` |
| Quota | a first-run turn whose last word is the `usage_limit` error row | `setup_quota`: the allowance ran out; cards already shown and classic setup still work; the chat keeps its place | `propose` refuses new cards until a turn in that chat lands a reply |

The stall verdict is the session-health classifier's
(`dashboard/session_health.py`: `snapshot_state` and
`SessionHealthMonitor.classify_slot`, run with a private monitor on the shorter
window). It uses the same progress markers and wait reasons
`GET /api/sessions/health` reports, so an open approval, a pending question, a
running child, a parked `wait` or a recovery in flight is never called a stall.
The watch is armed once per top-level turn at the top of `chat_runner._run_chat`.
It returns at once for any chat that `setup_flow` did not record as the
first-run chat (`setup_guardrails.track`, weakly keyed by the gateway state). It
stops once the chat becomes the main chat. It samples every `_WATCH_POLL_SECS`
and judges the turn's rows when the turn's task ends. The ACP layer's own
stale-turn cutoff applies only after text has streamed, and its tool-stall cutoff
only while a tool call is open, so a turn that has produced nothing at all is
otherwise bounded only by the hours-long turn ceiling. That silence is the case
this guardrail covers.

The quota verdict comes from the row kind `chat_runner._terminal_error_meta`
sets from the provider's raw frame (`AcpError.usage_limit`), never from prose. A
turn whose model fallback answered after the limit is not an episode, and a
second failing turn in the same episode posts nothing more. The pause lives in
memory and can only make a proposal refuse. The retry route refuses with
`slot_not_found`, `privacy_not_acked`, `setup_step_pending` (a scripted step is
still live), `turn_running` or `kickoff_answered` (an assistant reply after the
last `first_run` inject row). A restart forgets the open kickoff, so a finished
script whose kickoff got no reply posts the kickoff notice once on the next start. The retried kickoff
carries user provenance for the same reason the first one does (SC8). None of
these reads or writes a keystone file (SC3), and the first-run state file only
picks which chat is watched (SC6).

## Adding a setup action

A kind is one module plus copy. The steps, in order:

1. **The store.** A `KIND_*` constant in `setup_cards.py`, added to `CARD_KINDS`
   (the store drops a record of any other kind), and to `PROPOSABLE_KINDS` unless
   the gateway alone shows it. Pure argument validation goes here too
   (`build_<kind>`), so the MCP server and the gateway share it.
2. **The action.** `setup_actions/<kind>.py` defines `ACTION = SetupAction(...)`,
   listed in `ACTIONS` in `setup_actions/__init__.py`. It declares:
   - `summary` and `arguments`: its clause of the `setup_card` description and
     its JSON-schema properties, whose `description` is its own text; a property
     two kinds read is merged as `kind: text; kind: text`. `validate` checks a
     proposal in the MCP server; `build(args)` makes `(payload, private)` in the
     gateway.
   - `commit`, and any `decisions` beside commit and decline. A `Decision` with
     `claimed=True` runs exactly like a commit (the cron card's `preview`);
     `claimed=False` is the whole decision and does its own governance check and
     hash-bound claim (the home card's `aws_signin`, `region` and `remove`). Each has the
     `refusal` a card of another kind gets (`invalid_decision`), and its name
     goes in `setup_cards.DECISIONS`.
   - `title` and `result_detail`: what the model reads in the card's row and its
     `[Setup card result]` turn.
   - The flags, all off by default: `proposable=False` (gateway-only),
     `stack_exempt` (a pending card holds no other proposal back, nor is held
     back), `lifts_budget` (a committed card lifts the card budget),
     `gateway_card` (which of its cards are the gateway's own step, outside the
     budget and the one-at-a-time rule), `on_claim`, `on_decline` (may return an
     updated card with the next-step outcome), `after_report` (runs after the
     `[Setup card result]` turn for both commits and declines), and `scopes`
     plus `vet` for a governance scope beyond `capabilities.setup`.
     `governed=False` and `reported=False` are for a gateway-only kind only.

   Keep the heavy flow in `dashboard/` and name it from the action through a
   lazy import: the MCP server imports the registry.
3. **The card face.** A body in `SetupCardBodies.tsx`, an entry in
   `SETUP_CARD_KINDS` and a title key in `SETUP_CARD_TITLE_KEY`
   (`setupCardRegistry.tsx`), the kind in the `SetupCardKind` union
   (`api/setupCards.ts`), and the copy in `en.manual.json` and every translation.
4. **The skill.** A row in the `crew-setup` skill's tool table for an
   agent-proposable kind.

`test/test_setup_action_parity.py` fails, naming the kind and the file, when a
registered kind is missing from `CARD_KINDS` or `PROPOSABLE_KINDS`, has a scope
that is not a `SCOPE_CATALOG` row, has no `SETUP_CARD_KINDS` entry or title key,
has a title key `en.manual.json` lacks, or has no skill row; and in reverse, when
one of those names a kind no action registers. `test/test_setup_actions.py` pins
what the registry cannot loosen: a gateway-only kind is absent from the tool's
schema and refused by the tool and by `propose` as an unknown kind, and for every
registered kind the commit and every claimed decision run only with the shown
hash (SC1) and never past a governance denial, while an unclaimed decision still
refuses both. It also pins the generated schema against the one the tool
declared by hand. The checks every card passes (SC8 provenance, governance on
`capabilities.setup`, the hash-bound claim) live in `setup_flow`; an action can
add a check (`vet`) and cannot remove one.

## Channel pairing

RFC §5.3 connects one channel through a card for the bot token plus a
`/pair 4821` message that allowlists the user's own ID without asking them to
look it up. Telegram is the one channel wired (`setup_cards.CHANNELS`).

- **The token.** The card's secret field posts `input.token`. The commit
  shape-checks it (`messaging.clean_telegram_token`), verifies it with `getMe`
  (a rejection returns the card to `pending`; offline stores it unverified),
  and stores it with `messaging.commit_telegram_writes`, the Settings save's
  Phase 2: `config.json` first (`telegram.enabled = true`, the legacy
  `telegram.bot_token` purged), then `TELEGRAM_BOT_TOKEN` in `.env`. The
  Telegram channel reads the literal `.env` value and does not resolve
  `secret://` references, so the token is not put in the vault; `.env` is hidden
  from the agent in every sandbox mode. Enabling the channel or purging the
  legacy token is a boot-key change the config watcher answers by reconnecting
  the channel; a token swapped under an already enabled channel asks the gateway
  through `DashboardState.restart_channel` instead.
- **The code.** Four digits from `secrets`, live for ten minutes, one-time,
  one per channel (a newer card's code expires the older card with
  `pair_superseded`). It is held in `setup_channel`'s process memory and
  checked against memory only: the card store is readable and writable from
  the agent's sandbox, and the model reads cards through `setup_status`, so a
  code on disk could be relayed by a steered agent and a code checked against
  disk could be planted by one. The owner sees it through the decide response,
  the owner-only card event, and `GET /api/setup/cards[/{id}]`
  (`setup_channel.owner_view`). A gateway restart drops a live code.
- **The message.** `TelegramTransport` reads a private `/pair <code>` before
  `authorize` (see [messaging](messaging.md), Telegram `/pair`). A match adds
  the sender's numeric id to `telegram.allowed_user_ids` through
  `messaging.add_telegram_allowed_user` (appended inside the sidecar lock, then
  hot-applied so the live transport admits the sender before the reply) and
  commits the card with `{channel, paired, username}`, the sender's prompt-safe
  `@handle`. Each wrong code, from anyone, costs one of five attempts; the fifth
  fails the card with `pair_attempts`. Ten minutes unpaired expire it with
  `pair_timeout`.

## Job previews

A job's tool call that needs a person is a background approval (the gateway's
`_interactive_approval("cron")` callback): it names no slot, because an
unattended job is not a chat and no chat's trust may speak for it, and it is
declined when nobody answers within the unattended window
(`DashboardState._BACKGROUND_APPROVAL_TIMEOUT_SECS`). The gateway writes the run's
session key (`cron:<job id>`, or `cron:<job id>:<agent>` in an agent sequence) on
the approval record as `run_session`: provenance only, never a slot or a trust
lookup.

- **On the card.** `_preview_cron` starts a `setup_preview.ApprovalWatch` for the
  run. `GET /api/setup/cards/{id}/approvals` returns the pending approvals whose
  `run_session` names the job of THAT card's running preview, and nothing when
  none runs. Which job is held in process memory for the run, not read from the
  card store, which the agent's sandbox can write. The card polls it while the
  card is `working` and answers through `POST /api/approvals/{id}/{action}`
  (`approve`, `reject`), the one-shot path Notifications uses; nothing on the card
  records a standing grant. A request stays in Notifications too.
- **The verdict.** The preview's status is `success`, `failure` or `timeout`,
  mapped from the cron run's own status. The cron service records a run whose
  approval was refused or expired as `ok` (only a security block counts against
  a run there), so the watch counts how each of the run's approvals ended, and a
  `success` with any approval not given becomes `failure` with
  `reason: approval_not_given`. A preview that asked carries
  `approvals: {asked, allowed, rejected, unanswered, wait_secs}`. A failed preview
  keeps "Run a preview now" as the primary action.
- **Keeping it.** Nothing is auto-approved. The card says the job will ask again on
  every run, that the requests appear in Notifications, and how long one waits
  before it is declined. The only standing grants for a job are the operator's:
  the job's own `approval_mode: "auto"` (every tool, that job) and
  `hooks.auto_approve_sources` (every job).

## The home

A home is a crew in the owner's own AWS account, built by the existing launch
engine (`handlers_cloud.start_launch_job`, the EC2 template) while setup carries
on locally. One `home` card carries the whole journey, in this order: where the
crew lives (the first run's own card), an AWS sign-in if needed, the region if AWS
names none, the size and Build, the home's own Kiro sign-in, then an automatic move. A simulated home
([Pieces](#pieces)) walks the same card without AWS. The card's facts come from
`_home_payload`, which reads them read-only and side by side through
`aws.run_aws` and never changes anything in the account.

### The home step

Where the crew lives is the final setup choice, asked in the chat after the
job step is kept **or skipped**. Declining scheduling ends only that step.
`prepare_home_after_job` marks the home question due (`outcome.home_choice`)
on a first-run cron card that is committed or declined, and its `after_report`
(`offer_home_after_job`) shows the question after the result turn starts, so the
tray opens it expanded. A declined preview is removed and never enabled.

A spoken refusal such as "no scheduled job" needs no cron card: `crew-setup`
and the kickoff facts direct the agent to call
`setup_card(kind="home", step="choose")` in that turn. The home action preserves
and validates `step` through the MCP directive and builds the same choice card,
without reading AWS. `setup_status` reports this next step until a home card or
a scripted answer exists. This final choice is exempt from the optional-card
budget; user provenance and governance still apply. An existing home card is
reused, including one already declined, rather than proposed again.

The choice card carries `offer: true` (`setup_actions.home.HOME_STEP_KEY`) and
`step: "choose"` (`HOME_PHASE_KEY`, `HOME_CHOICE_STEP`; private `phase: "choose"`).
Its payload (`_choice_payload`) contains the region (the profile's, else
`HOME_DEFAULT_REGION`) and `from_usd`, the cheapest monthly estimate offered in
that region. It offers "This machine: free · runs while it's on" and "In the
cloud: always on · from $14/mo", with **Continue** disabled until a row is picked,
and **Not now**. The copy applies whether or not the user kept a scheduled job.

Continue is the `choose` decision (`input.where`), a claimed decision on the home
action (`setup_actions/home.py`), so it is hash-bound and governed like a commit
(`_choose_home`). `here` commits the card with `outcome: {stayed: true}`: its
result line reads "Staying on this machine", and its `[Setup card result]` asks
the agent to offer the keep-running service card. `cloud` moves the SAME card on:
it recomputes the ordinary home payload (`_home_payload`: one read-only
reachability check, the account's region and plan, the sizes), keeps `offer`,
drops `step`, sets private `phase: "build"`, and re-issues it with
`replace_payload` under a new hash, so the card shows its AWS state, the sizes
and Build (see [Signing in to AWS](#signing-in-to-aws)). A Build before the
answer is refused (`home_choose_first`), an unknown answer is
`home_choice_invalid`, and `choose` on a card past the question is
`invalid_decision`. Not now declines it; a home stays one "move me to the cloud"
away in any later chat, where the agent proposes `kind: "home"`.

The kept or skipped job's result tells the agent the question is on screen
(`outcome.home_choice` on the cron card, worded by `setup_flow._result_text`).
It must not conclude setup or ask the question again in prose; it guides the
cloud's steps one at a time. The card is the gateway's (`gateway_card`), so it
counts toward neither the agent's card budget nor the one-at-a-time rule. A
script's `kirocrew start --home cloud` shows the build card right after privacy
instead (`_show_chosen_home`), with `--aws-region` or the profile's own region and
no fallback region; `--home here|later` shows none, and no question is asked
later.

The build card is compact: one muted line with the AWS sign-in, the account and
the region ("AWS: signed in ✓ …5281 · eu-north-1"), or the sign-in and
account-creation steps first when AWS is not signed in; then one row per size
("Lite · 2 GB · $14/mo", badged "recommended" for `size_default` or "Needs the
paid plan"), one line saying the prices are approximate and billed by AWS, and
each size's trade-offs, instance and upgrade link behind one "What's the
difference?" disclosure; then Build. The Simulated badge is small, and a card
without size options keeps its single size and cost line (tests:
`test_setup_flow.py::TestWhereTheCrewLives`, `SetupCardHomeOffer.test.tsx`,
`SetupCardHomeSizes.test.tsx`).

### Signing in to AWS

A home is built in the owner's own AWS account, so the machine running Kiro Crew
needs an AWS CLI sign-in first, and the terminal asks nothing. When the card's
payload says AWS is not signed in, the card offers **Sign in to AWS**, which posts
`decision: "aws_signin"` (`setup_aws_signin.decide_signin`, owner-only and
governed like every decide):

1. It asks AWS who the payload's profile signs in as (`local_signin.detect`, one
   read-only `sts get-caller-identity`). An answer returns the card to `pending`
   signed in, and nothing is spawned.
2. It refuses with `aws_signin_remote` unless the owner's browser is on this
   machine: the decide request came straight from loopback with no forwarding
   header (`origin.is_direct_local_request`), the install shape is a desktop
   (`auth.shape.detect_shape`, the choice the Kiro sign-in's transport is made
   from; an SSH session or a container is not one), and this process can open a
   browser (a Linux host needs a display). The refusal's outcome carries
   `aws_signin: {state: "remote", command}`, the `aws login --remote` command to
   run in a terminal on that host; the card then offers Build.
3. Otherwise it runs `aws login [--profile P] --region R` as a child of the
   gateway, with stdin, stdout and stderr closed and a scrubbed environment, in
   its own session. The profile and region are the payload's, re-validated; the
   `default` profile passes no `--profile`, and `--region` also picks the region's
   sign-in endpoint. With no stdin `aws login` asks nothing: it opens the page
   and waits for its redirect, creating the profile if it does not exist. The
   card goes `waiting` with `aws_signin: {state: "waiting", expires_ts}`. One
   sign-in runs at a time (`aws_signin_busy`); an AWS CLI older than 2.32 is
   refused (`aws_cli_too_old`).
4. A watcher checks the child and asks AWS again every few seconds. When AWS
   answers, the card returns to `pending` with `aws_signed_in: true` and the
   account's last four digits in its OUTCOME, and its payload is recomputed now
   that AWS answers (`setup_flow.refresh_home_payload`): the account's own region,
   its plan and the size options, under a new hash (`setup_cards.replace_payload`).
   The owner sees the new card before Build; a click carrying the old hash is
   refused as `card_hash_mismatch`. A child that exits without a sign-in
   (`aws_signin_failed`) or `SIGNIN_WAIT_SECS` (ten minutes) without one
   (`aws_signin_timeout`, the AWS CLI's own wait, which also leaves time to create
   an account first) returns the card to `pending` with the reason. So does a
   profile that already holds access keys, which `aws login` refuses at once with
   exit status 253 (`aws_signin_profile_has_keys`). The card reaches the sign-in
   only when AWS did not answer, so this is a profile whose keys were revoked or
   expired. A retry cannot help, because the card's profile is fixed by its
   payload, so the card tells the owner to ask the chat for a home under a new
   profile, and the `crew-setup` skill proposes `kind: "home"` again with
   `profile: "kirocrew"`.

**No AWS account yet.** A card built on a machine with no AWS sign-in (and not
simulated) also carries `signup_url`, `signup_builder_id` and
`aws_cli_installed` in its payload. `signup_url` is
`local_signin.signup_url(builder_id)`: AWS's Builder ID sign-up when this
machine's Kiro sign-in is exactly Builder ID (one bounded `kiro-cli whoami`,
`local_signin.kiro_signs_in_with_builder_id`), the plain sign-up for a social or
Identity Center sign-in, none, or an unknown answer. `aws_cli_installed` is
`local_signin.aws_cli_present()`. A signed-in or simulated card has none of the
three and runs no whoami. The card links "Create an AWS account" beside Sign in
to AWS, opening the page in a new tab through the safe-URL helper; it never
frames or proxies an AWS page. Following it switches the card to a local,
untimed "finish creating your account" state whose primary, "I've created it —
sign in", is the `aws_signin` decision above, and whose Back returns. A missing
AWS CLI adds one line linking AWS's install page; nothing is installed for the
owner.

`input.cancel` stops a sign-in in progress and returns the card to `pending`. The
child is stopped on timeout, on cancel, whenever the card leaves the sign-in, and
when the gateway exits. It lives in process memory only (the card store is
writable from the agent's sandbox, so nothing on disk names a process to
signal), which is also why a gateway restart leaves a card `waiting`: once it is
past `expires_ts`, the card offers Try again and the decide admits a fresh start.
The sign-in is audited as `setup_card.aws_signin`, with its outcome word only.

### The home's size

The card offers sizes and the owner picks one (`setup_cards.home_size_options`,
the tiers in `cloud/sizes.py`), measured with a real kiro-cli: the idle gateway is
1.3 GB, each open chat adds about 0.4–0.5 GB and stays alive, three chats plus a
sub-agent peak at 3.7 GB, and the on-box dashboard build peaks at 2.6 GB. So a
full home needs 8 GB. The two tiers below 8 GB run a slimmed home instead (the
tier's `home_profile`; see [cloud](cloud.md#slimmed-homes)) and count on the
launcher shipping the dashboard it built
([cloud](cloud.md#the-prebuilt-dashboard)); every home also caps its live chats
by memory ([session](session.md#live-chat-cap-sessionmax_live_sessions)).

The "About" column is us-east-1, disk included; the card prices each option in
the card's own region (see [Prices per region](#prices-per-region)).

| Option | Tier | Shape | About | Offered on |
|---|---|---|---|---|
| Lite | `lite` | `t4g.small`, arm64, 2 vCPU, 2 GB | $14/month | both (`free_plan_ok`); it gives up meaning-based memory search (keyword only), dictation (no local speech-to-text), a warm first reply after a quiet spell, and runs a few things at once, which its card line says |
| Economy | `economy` | `t4g.medium`, arm64, 2 vCPU, 4 GB | $26/month | the paid plan; everything on, idle chats end after 30 minutes |
| Small | `small` | `t4g.large`, arm64, 2 vCPU, 8 GB | $51/month | the paid plan (its default) |
| Starter | `starter` | `m7i-flex.large`, x86_64, 2 vCPU, 8 GB | $72/month | the Free plan (its default; `free_plan_ok`: the Free plan's EC2 launches free-tier types only), and the paid plan only where it is no dearer than Small in the card's region |
| Standard | `light` | `t4g.xlarge`, arm64, 4 vCPU, 16 GB | $101/month | both; on the Free plan it is marked as needing the paid plan |

Which sizes each plan gets, and its default, is data: `setup_cards.HOME_PLAN_SIZES`
(per plan: the sizes and the preselected one) and `HOME_SIZE_OFFERS` (per size: a
plain label and a note code the dashboard words: `lite_tradeoffs`, `all_on`,
`free_plan_credits`, `few_chats`, `many_chats`; `lite_tradeoffs` and
`free_plan_credits` also name the credit's weeks when they are known). A size is
a tier in `cloud/sizes.py` plus those entries. A plan not known yet (not signed
in, or an unreadable plan) gets the Free plan's list, since a new account starts
on it and Starter builds on every plan. The options are sorted cheapest first,
and each carries `key`, `label`, `note`, `instance_type`, `vcpu`, `ram_gb`,
`monthly_usd` and `free_plan_ok`; a Free-plan size also carries `credits_usd` and
`credit_weeks` when the plan's remaining credits are known. Each option is one
row with its label, memory and monthly cost ("Small · 8 GB · $51/mo"), its notes
behind the "What's the difference?" disclosure, and the card preselects and
badges `size_default`. A card on a machine not signed in to AWS adds a note
that a brand-new account starts on the Free plan. The `crew-setup` skill explains
the options when asked and never picks for the owner.

Once AWS answers for the profile, `_home_payload` reads, side by side and
read-only (`cloud/local_signin.py`): the region the account can build in
(`resolve_home_region`: `ec2 describe-availability-zones` in the card's region,
then the profile's, then `HOME_REGION_CANDIDATES` — us-east-2, eu-north-1 and
ap-southeast-2 — moving on only after an access refusal; a new sign-up account
refuses every region but its own), which becomes the card's `region` and the
build's; and the plan (`account_plan`: `freetier get-account-plan-state` gives
`{type: FREE|PAID|unknown, credits_usd?, expires?}`; an account older than the
plans answers `ResourceNotFoundException` and is PAID). The plan is never
changed from here.

### Asking for the region

When no region answers (`resolve_home_region` returns `""`), the payload carries
`region_unknown: true` and `region_choices`, the regions a home can be built in
(`local_signin.HOME_REGIONS`: the commercial regions every account has without an
opt-in), and its `region` becomes the one the profile names in `~/.aws/config` when
that is one of them. The card shows a native select of those regions, preselecting
`region`, under "AWS didn't say which region this account uses. Pick the one
shown in your AWS console." Its primary button is **Use this region**, the
`region` decision with `input.region`. `_decide_home_region` refuses a card that
does not ask (`invalid_decision`), a stale hash and a governance denial like any
decision, and a region that fails `setup_cards.validate_home_region` (the
`build_home` region shape and `HOME_REGIONS`: `home_region_not_offered`), before
anything is sent to AWS. It then probes that one region read-only
(`local_signin.probe_region`, one `ec2 describe-availability-zones`), reads the
plan again, and re-issues the card for the pick through `replace_payload`, under a
new hash and with that region's prices. A pick that answered drops
`region_unknown`, so the card shows Build. A pick that did not answer is kept as
the card's region, with the picker still shown and the recoverable error
`home_region_no_answer`; while the select still shows that region, the primary
button is Build, so an owner whose console shows it can build there (tests:
`test_home_region.py::TestNoRegionAnswers`, `TestTheRegionDecision`,
`SetupCardHomeRegion.test.tsx`).

### Prices per region

`setup_cards.monthly_estimate_usd(key, region)` is the instance's on-demand Linux
hourly price times 730 plus its disk at the region's gp3 price, rounded to whole
dollars. The prices are data in `cloud/sizes.py` (`ON_DEMAND_USD_PER_HR`,
`GP3_USD_PER_GB_MONTH`, via `region_prices`) for us-east-1 and a new account's
three home regions, from AWS's public price list (published 2026-09-25). A region
not in the table gets the us-east-1 figure (`PRICE_FALLBACK_REGION`), which the
card shows as "about" like every price. `home_size_options(plan, region)` prices
every option in the card's region, so the rule that Starter joins the paid plan's
list only when it is no dearer than Small is decided per region. Monthly figures,
instance plus disk:

| Size | us-east-1 | us-east-2 | eu-north-1 | ap-southeast-2 |
|---|---|---|---|---|
| Lite | $14 | $14 | $14 | $17 |
| Economy | $26 | $26 | $27 | $33 |
| Small | $51 | $51 | $53 | $65 |
| Starter | $72 | $72 | $77 | $90 |
| Standard | $101 | $101 | $104 | $128 |

### Building the home

**Build my home** is the `commit` in phase `build` (`_commit_home`) and posts
`input.size`. Before anything is spent:

- AWS must answer for the profile again (`aws_not_signed_in` otherwise).
- `_chosen_home_size` refuses a size the card did not offer
  (`home_size_not_offered`), a paid-plan size on the Free plan
  (`home_size_needs_paid_plan`; the card links AWS's page on the plans), and a
  size above the account's EC2 on-demand vCPU quota in that region
  (`local_signin.vcpu_quota`, Service Quotas `L-1216C47A`:
  `home_vcpu_quota_low`, with a link to the Service Quotas page).
- The build signs the home in with the same kind of identity this computer's
  kiro-cli uses (`_inherited_login_target`). An Identity Center target whose
  region could not be read is refused (`home_identity_region_unknown`), because
  a job carrying it could never be read back.

The region and size built are the payload's and the click's, never the card's
private settings, since only the payload is hash-bound. The click records
whether the owner's browser is on this machine (`on_claim`, see
[The home's Kiro sign-in](#the-homes-kiro-sign-in)), starts the launch job,
goes `waiting`, and a watcher (`_watch_home`) mirrors the build's steps onto the
card until it is ready to move or fails. The authorized Build click also stores
`auto_move` and its approved payload hash: after the home is signed in, the
watcher waits for an idle setup chat and continues through the ordinary
hash-bound, governance-checked `decide` path. A changed payload or a policy
refusal requires a new decision; the progress state never grants permission. A build that fails on the
account's spend limit is `home_spend_limit` rather than the generic
`home_build_failed`.

A build the card can no longer follow is stopped: its job unreadable or gone for
`_UNTRACKED_POLLS` (3) polls in a row, or the watcher itself failing, sets the
launch's own cancel event, whose worker rolls its stack back at the next
checkpoint, and the card fails with `home_build_untracked`
(`_stop_untracked_build`). A build nobody can see is one nobody would stop.

At most one watcher runs per card (`_start_home_watch`, keyed by card id), and
it lives in process memory, so a gateway restart takes it and the launch worker
along. At boot, after the session restore and next to the first-run session,
`resume_home_builds` runs the launch store's once-per-process reap
(`handlers_cloud._astore`, `cloud/launch_job.py`
`LaunchJobStore.reap_orphans`: a job no worker here drives is failed before its
connect step, and parked done, not signed in, after it) and starts the watcher
again for every home card still `waiting` with a private `job_id`, with
`may_open=False`: a watcher resumed after a restart never opens a page. The
watcher then follows a build still driven in this process, and otherwise
settles the card at its first poll from the job's real outcome: failed is
`home_build_failed` with the job's reason (for a restart before the home
registered, "Kiro Crew restarted while this setup was running", and the stack
it may have left is the owner's to check in the crews list), done and signed in
is `pending` and `ready`, done but not signed in is phase `signin` with
`needs_signin`, and a job it cannot read is `home_build_untracked`
(`test_home_resume.py`). A build cut short is not driven on from the new
process.

### What a restart leaves in AWS

A build a restart cut short once its stack may have been created has no worker
left to roll the stack back, and the stack keeps billing. The launch job says
which: `launch_job.interrupted_by_restart` (the reap's `RESTART_INTERRUPTED`
error) and `stack_may_exist` (a tag, and the provision step reached). The
watcher that settles such a card adds `outcome.leftover: {tag, stack, region}`,
and the card says, instead of the job's reason, "Kiro Crew lost track of this
home's build when it restarted, so parts of it may still be in your AWS account
and billing. Remove them here, or from Remote Crew." A `home_build_untracked`
card is worded from `outcome.stopped`: `true` (this process's worker got the
cancel and rolls the stack back) keeps "…so it stopped it; anything it had
created in AWS is being removed", and `false` (no worker here: nothing rolls it
back, and the tag is not known) says the same restart sentence ending "Remove
them from Remote Crew."; the server's own message, which the agent reads, says
the same.

A card with `leftover` shows the stack and region it would delete and a **Remove
what it created** button: the `remove` decision (`input.tag`), a `claimed=False`
decision on the home action. Nothing runs before the click.
`_decide_home_remove` checks governance and the payload hash, then reads the
launch job again and acts on it alone, never on the card, whose store the
agent's sandbox can write: the card must be `failed`, the job must satisfy both
checks above (`home_nothing_to_remove` otherwise), and `input.tag` must be the
job's tag (`card_hash_mismatch` otherwise). Under the store lock it records
`outcome.removal: {state: active}`, so a second click while it runs or after it
is done is `home_remove_running`, and runs the Instances hub's destroy in the
background: `handlers_cloud.teardown_stack`, which is `ec2.destroy` (no wait),
then `_teardown_after_delete`, the same code `DELETE /api/cloud/{tag}` runs
(`wait_for_delete`, then the instance's registration and the uploaded source,
only once AWS confirms the stack is gone). The card stays `failed`, keeps its
error, and shows the removal running, then `done` ("Removed: AWS confirms the
stack … is gone.") or `failed` with the button again. The AWS error goes to the
log only, since it can carry the whole account id (SC4). A removal a restart cut
short is marked `failed` at boot by `resume_home_builds`. Audited as
`setup_card.remove` (tests: `test_home_leftover.py`,
`SetupCardHomeLeftover.test.tsx`).

### The home's Kiro sign-in

The home signs in to Kiro with its OWN device-code sign-in; nothing is copied
from this machine, so each machine keeps its own session and refresh token (the
choice and why: RFC §6.8 rule 5). The build waits at "Sign in to Kiro" until the
owner approves the code, so that approval is made one click
(`dashboard/home_signin.py`):

- **The click records where the browser is.** Every `commit` on a home card
  records `private.browser_is_here`, the three-part check the AWS sign-in uses
  (`setup_aws_signin.browser_is_here`: a direct-local request, a desktop install
  shape, a browser this process can open). Build hands that answer, read off the
  card the click claimed, to its own watcher as `may_open`.
- **Only what this process issued opens.** The launch-job file lives under
  `run/`, which is `VISIBLE` in the sandbox, so an agent can rewrite its
  `signin` (swapping in its OWN device code, which the owner would then approve
  for the agent's session) and its `login_target.start_url`. So the launch
  worker records each prompt it publishes, with the target's start URL, in
  process memory (`launch_job._issue_signin` on both the launch and the retry
  path; `issued_signin(job_id)` reads it back, never from disk), and the watcher
  opens only when the file's `url` and `code` equal that record. The host check
  runs on the record's URL and start URL too, so a start URL rewritten in the
  file widens nothing. No record (another process, or this one after a restart)
  opens nothing. The card shows that record too (`setup_flow._shown_signin`),
  so a code planted in the file is neither opened nor shown while the worker
  runs; only with no record does the card show the file's copy.
- **The page opens once.** When the build shows `signin: {url, code}`, the
  watcher opens the URL in the owner's browser (`cloud.login._open_browser`) if
  `may_open`, the home is not simulated, it is what this process issued, and the
  issued URL is HTTPS on a Kiro sign-in host (`*.awsapps.com`,
  `device.sso.<region>.amazonaws.com`, `*.kiro.dev`) or on the issued login
  target's own Identity Center start-URL host. kiro-cli prints the
  page with the user code in it (`verification_uri_complete`, which
  `parse_login_output` prefers), so a browser already signed in to Kiro needs
  one confirmation; the card keeps showing the code beside the link. A digest
  of the code is recorded before the open, so each code opens at most once,
  and a fresh code (a restarted sign-in) opens once more. A watcher whose
  `may_open` is false never opens anything: only a click's own watcher knows the
  owner is at this machine. Anything else leaves the link on the card.
- **One notice.** The chat that owns the card gets ONE `home_signin` system
  notice, meta `{kind, opened, card}`: the home waits for one click, and either
  its page opened in the browser or the link is on its card. It is not a model
  turn, so it costs no quota and raises no card (SC8), and it waits while that
  chat is mid-turn so it never lands between the rows of a reply. The dashboard
  draws localized copy keyed on `meta.opened` (`pages/chat/HomeSigninNotice.tsx`).
- **The card** says it is the home's own sign-in, that each machine keeps its
  own, and that a browser already signed in to Kiro needs one click.

The `private` flags (`browser_is_here`, `kiro_signin_opened`,
`kiro_signin_open_ok`, `kiro_signin_noticed`) only stop a repeat; the card store
is writable from the agent's sandbox, so none of them can start an open on its
own, and no URL comes from the card. The page that opens is always one the
worker in this process received from kiro-cli on the home.

**A build that finished unsigned.** The build finishes (`DONE`) even when its
sign-in step was skipped: the device code ran out unapproved, or a restart cut
the wait short, leaving `signin_detected` false. Such a home's agent cannot
answer, so the watcher (`setup_flow._home_built`) does NOT offer Move in: it
sets phase `signin` and leaves the card `pending` with outcome `{ready: false,
needs_signin: true, steps}`, plus the `signin` link and code when the job still
holds them. The card's commit in that phase, **Sign the home in to Kiro**
(`_sign_home_in`), runs `handlers_cloud.restart_signin`, the same body as the
Instances hub's `POST /api/cloud/launch/{id}/signin/restart`, with every refusal
it has (already signed in, an unreadable identity, no crew to sign in on, a setup
or sign-in already running, no launch engine); a refusal is the card's error and
the card stays in phase `signin`. "Already signed in" goes straight to Move in.
Otherwise the card goes `waiting` and is watched again, with `may_open` from the
`browser_is_here` this click recorded, so the fresh code's page may open once in
the owner's browser. When that watch sees `DONE` with `signin_detected`, the card
moves to phase `move`. The phase is on the stored card, so a card left at
`needs_signin` across a gateway restart still offers the button. A simulated
home is never held here.

### Moving in

When a live home's build is done, the card's private record holds the EC2
instance id the launch registered in the Instances hub ("Added to Your crews").
New builds move automatically after their own Kiro sign-in. The watcher waits
for the source chat to finish a turn, retries a busy snapshot, and resumes a
pending or interrupted move after a gateway restart. Other failures leave the
card with its reason and a Move in retry. Legacy cards keep that explicit action.
Move in (`setup_move_in.move_in`) runs four steps, each on the card as it runs
(`outcome.move_steps`); a simulated home walks four steps and moves nothing:

1. **Reach.** Needs `instances.enabled` and the tunnel manager the gateway starts
   at boot, the same gate every `/api/instances` route applies. When Remote Crew
   is off, the authorized home move sets `instances.enabled`, restarts the
   gateway once, and returns the card to `pending` with `move_in_restarting`.
   A new build resumes automatically; a legacy card offers Move in again.
   Then it finds the registry record whose `ssm_target` is that instance id and
   connects it (`SshTunnelManager.connect`).
2. **Pack.** `portability.create_export_zip`: memory (every store), schedules,
   skills, workspace, plan memory, hooks, notifications, crew teams,
   `persona/SOUL.md` and `USER.md`, and `config.json`. The vault, `.env`, the SEL
   key and connection grants are never in it.
3. **Chat.** The card's chat goes to the home through the session-transfer path
   (`build_transfer_bundle_async`, the publication hold, then
   `SshTunnelManager.send_session_bundle`), as a copy: the chat here is untouched.
   The home answers with the copy's slot key, which the card records.
4. **Carry.** A schedule reports to the chat its `session_key` names
   (`dashboard:<slot>`), and this chat's key names nothing on the home. So every
   job in the archive's `crons.json` whose `session_key` is this chat's
   (`card.session_key`, `dashboard:<card.slot>`), enabled or not, is pointed at
   `dashboard:<copy key>` (`rebind_jobs_to_chat`); only the archive changes, and
   the jobs here keep their own key. Then the enabled schedules the archive
   carries that run a prompt are switched off here (`enable_job_async(id, False)`,
   so disabled, not deleted), and only then is the archive posted to the home's
   `POST /api/portability/import?mode=merge` through
   `SshTunnelManager.proxy_request`, which keeps the tunnel credential inside the
   manager. A schedule that runs a command or a script stays enabled here: the
   import pauses it on the home and the export carries no script. If the home
   does not confirm the import, the switched-off schedules come back on and the
   card returns to `pending` with the reason. A schedule the home rejected, or
   every schedule when the home could not read its own schedule list, comes back
   on here and is listed as kept. The merge restores `config.json` only on a
   home that has none; the arrival step separately applies the setup profile
   and persona. A chat reply without a valid slot key stops the move before any
   schedules are handed over.

After carry, the owner-only `POST /api/setup/home-arrival` adopts the transferred
slot as the home's first-run and main chat (`setup_home_arrival.adopt_main_chat`).
The dedicated transfer uses bundle version 3 and `setup_transfer` to carry the
full setup display history from the same guarded snapshot as the provider
context: speech, tool rows, setup-card references and setup-result injections.
The card faces travel as bounded, redacted receipts with fresh target IDs.
They are stored server-side, terminal and inert: no private state, credential
codes or executable decisions travel. Ordinary sends remain version 2 and
speech-only. An older peer refuses the home bundle rather than silently
discarding its cards; a failed provider-context installation fails the handoff.
With Tool Search enabled, dashboard startup deliberately rebuilds the native
session and replays Crew's durable history (see [providers](providers.md)).
The setup-result injections therefore carry the user's card-only answers into
the first cloud prompt too; a copied native session file alone cannot do that.

Arrival applies only the saved agent name, language, timezone, technical level,
user role and SOUL/USER persona; cloud runtime configuration, privacy consent
and security policy remain local. It completes the carried home receipt after
the archive lands. The sign-in notice keeps its system-notice identity and
reports that the cloud is signed in, rather than repeating an outstanding login
request. The arrived receipt has no action to open this same home again.
The main chat's live crew overview identifies this home as signed in and the
move as complete, including when native context still contains an older sign-in
request.
It keeps the source title, clears the Imported folder, pins `kirocrew-main`, and
persists that metadata before recording the main marker. Known setup stages
travel as presentation progress only; privacy consent, secrets and grants do
not. The cloud's previous welcome chat is archived, never deleted. A busy
welcome chat delays completion; the previous key survives a retry. Generic
session imports retain their provenance title and folder. Both gateways must
support the arrival endpoint; an older home leaves a retryable error and the
already copied chat is reused after updating it.

The chat goes before the carry so the archive can name the copy's key: a moved
job never runs on the home under an owner key that names no chat there. SC5 (a job
runs on exactly one crew) fixes the order inside step 4: the home's cron service
loads an imported job on its next sync, so switching the local copies off after the
import would leave both running for a moment. The card's `private.move` records
each finished step (the chat's copy key, the carry, then adoption), so pressing Move in
again never sends the chat or the archive twice: a retry after a failed carry
packs afresh, keeps the recorded copy key and carries again. A failed chat step
has moved nothing else. A reply lost after the home applied the archive is the one
case that runs a schedule on both crews, until the owner presses Move in again and
the home's merge skips the names it already has. A gateway stopping mid-move turns
the schedules back on and returns the card to `pending`.

The committed outcome names the home (`home.instance_id`, `home.name`,
`home.remote_key`), `jobs_moved` (off here), `jobs_kept_here` with a reason,
`jobs_follow_chat` (the jobs now owned by the chat's copy), `carried` (the home's
import summary items), `settings_moved`, and `reenter`: the vault's secret names,
the credential file's credential names, and the curated connections holding a
grant here, never a value. A successful live move does not start another local
agent turn after the snapshot. The dashboard draws the committed card from that
outcome (`components/setup/HomeMovedDetail.tsx`). A move observed in this window
switches automatically to `home.remote_key` on the cloud, including when the
owner navigated to another local page during the build. Opening a historical
confirmation never redirects by itself, and another active crew keeps focus.
The Open your home action also targets that exact conversation. The parent
relays `openSession` in its host model; the existing iframe navigates without a
reload and acknowledges with `mc-session-opened`. Pending navigation survives a
cold pane's readiness handshake; only its own frame and origin can acknowledge. Every step is audited as
`setup_card.move_in`.

**Next time.** The committed outcome, live or simulated, also says how to reach
the home again (`setup_move_in.reach_back`): `home.tag`, `home.region` and
`home.profile` from the launch job the card's build recorded (`private.job_id`),
and `reconnect`, a list of `{purpose, command}` built by
`cloud/reconnect.reconnect_commands`: `open` (`kirocrew cloud connect`), `stop`
(pause billing), `start`, `status`, each with `--tag` and `--region`, and `list`
with `--region`; `--profile` rides every command unless the profile is the AWS
CLI's default. The helper validates the tag, region and profile with the cloud
commands' own validators and shell-quotes every argument, so a launch record that
does not read leaves `reconnect` empty rather than holding a command that cannot
work. `cloud connect` mints a fresh dashboard token over SSM and opens an SSM
port-forward each time, so no command, card, chat line or log carries a token, a
URL with a token, or a credential, and the home keeps no inbound port. The flags
are explicit because a card build does not write the launch record
(`cloud/launch_state.py`) a bare `kirocrew cloud connect` resolves, and another
computer has none; `ec2.stack_name(tag)` is the stack both the card's build and
`cloud connect --tag` address (`test_cloud_reconnect.py`). The `[Setup card
result]` hands the agent the `open` command to tell the user in one line; a
simulated move's result says the command finds no home. The card's "Next time"
block (`HomeNextTime` in `HomeMovedDetail.tsx`) shows the `open` command with a
copy button, the others behind a "More commands" disclosure, and one line for
another computer (install with the one-line setup and sign in to AWS first); on a
simulated home it says the commands are simulated and will not find a home.
`kirocrew start` prints the same line after its running line
([cli](cli.md#start-command)).

A move-in's `SshTunnelManager.connect` records the home's `was_connected`, so a
later gateway start with Remote Crew on reconnects the home's tunnel by itself
(`server._revive_intended_instances`) and Your crews opens it without the command,
as long as this computer's AWS sign-in for the profile is still valid; an expired
one leaves the tab in its error state until the owner signs in to AWS and retries.

## The main chat

Once the first run's home card is committed or declined, `setup_flow.graduate`
makes it the **main chat**, even when no job was kept: it records `main` in the first-run state, renames the slot
after the agent (`agent.bot_name`, an explicit title the auto-titler leaves
alone), keeps it pinned, and appends a `main_chat` system notice. A missing,
pending or failed home choice prevents the completion notice. A scripted
`--home here|later` already answers the choice; a kept or declined job can then
graduate directly. A cloud move adopts this conversation as the remote main
chat. Only keeping a job marks `job_kept` and starts [the first week](#the-first-week);
skipping scheduling does neither.
`_theme_payload` reports `main_slot`; `kirocrew start` lands on it. Like the
rest of the state file, the marker is presentation only: it decides where the
product opens and whether the overview is attached, never what a turn may do.

Any other dashboard chat can be made the main chat later: "Make this my main
chat" in the session menu (sidebar row and chat header) posts
`POST /api/setup/main-chat {slot}` (owner-only; `setup_flow.make_main_chat`),
which moves the marker, pins the chat and appends a `main_chat` notice. Only a
chat whose key starts with `chat-` and that did not come from a channel is
eligible (`slot_not_eligible`, 409). The web and desktop apps land, with nothing
remembered and no `?sid=`, on the main chat, then the first-run chat, then the
first row. "Ask in main chat" on a job and "Ask about this chat in main chat" on a
session pre-fill the main chat's composer, unsent.

**The crew overview.** In the main chat, and in the first-run chat until it
graduates (no chat is the main chat yet, and graduation waits on the home card,
so "what's going on?" during a build is asked there), every top-level turn
carries a `[CREW OVERVIEW]` block (`setup_flow.crew_overview`, attached in
`chat_runner` beside the theme persona): other live chats with their status (working, waiting
on the user, idle), setup cards open anywhere, enabled jobs in due order, and the
home's state. Titles are flattened (no brackets, one line, bounded) before
quoting, and the block is capped at `OVERVIEW_MAX_CHARS`. It carries what
`list_sessions` and `setup_status` already return, so it widens nothing. Both
word a card's state the same way (`setup_cards.card_state_words`,
`home_state_words`): a card whose own work is running (a build, a sign-in, a
preview) reads "nothing needed from the user", never the raw `waiting`. A pending
card also says what it means for the next proposal, from
`setup_actions.holds_others` (the same predicate the one-at-a-time refusal
applies): "a new card waits until it is decided", or, for a `stack_exempt` kind
or a gateway step, "other cards can still be shown while it waits". Without the
second, "waiting for the user's decision" on the home card read as "no other
card can be shown", and the agent refused a job the user asked for.

**The main chat's agent.** The first-run chat runs on the `kirocrew-main` agent
spec (`slot.agent`, `agent_files.MAIN_CHAT_AGENT_NAME`), set when the chat is
created, because switching a chat's agent later resets its session, and
handing long work to its own chat needs the session tools, which live on the
opt-in `kirocrew-dashboard` server the default agent never mounts.
`kirocrew-main` is the default agent's spec on disk plus that server, with only
`session_create` and `session_read_message` auto-approved; `session_send` and
`session_stop` stay behind the approval gate, and a governance ceiling on the
server withholds both grants. Every other chat keeps the default agent, and a
chat the main chat creates without naming an agent starts on the default agent
rather than inheriting `kirocrew-main`. Context, skills and model resolution
treat it as the default agent (`agent_files.PRIMARY_AGENT_NAMES`), so the persona
files and the skill catalog still reach this chat. The spec, its freshness gate
and what each harness makes of it:
[agent-spec-fields](../../../src/kiro_crew/docs/agent-spec-fields.md) and the
[agent host contract](agent-host-contract.md) §5. On the Claude harness the server
mounts but no grant reaches the harness, so all four verbs prompt; on codex,
OpenCode, goose, Pi and DeepSeek it is not mounted, exactly as for the conductors,
so the main chat there has no session tools.

**Hand-off notices.** The main chat is told when a chat it handed work to
finishes. When a chat whose `_created_by` (stamped by `session_create`) is the
main chat ends a turn and is idle (nothing queued, no approval or question
waiting, no plan or sub-agent still going), `dashboard/handoff_notice.py` posts
one `handoff_done` system notice in the main chat, meta
`{kind, slot, title, outcome}`, the title flattened and bounded as in the
overview. `outcome` is `done` when the turn replied and `error` when it ended on
an `error` row with no reply. A turn with a `stop_event` row posts nothing: every
Stop press and `session_stop` writes one, so whoever stopped it already knows.
The notice runs no model turn, so it costs no quota and raises no card (SC8). It
is checked at two cycle ends, `note_cycle_end` in
`chat_runner._finish_queue_cycle` after `chat_done` and `note_controller_end`
where `_stage_loop` releases the slot (run once the plan's task has ended, since
the controller keeps the slot reserved until then; a plan paused on the user is
not reported done). For a chat nobody created, both return without reading the
disk. There is at most one notice per turn of that chat, none while the main
chat's newest row is already the same notice, and none for the main chat itself.
A notice due while the main chat is mid-turn or mid-plan is held until its next
cycle end, and the hold is mirrored to `handoff_owed` in the first-run state file
(at most `_MAX_HELD` per chat), so a restart in between delivers it:
`restore_held` runs in `server.py` after the session restore, drops notices held
for a chat that is no longer the main chat, bounds the title again, and dedupes
against the newest row. The dashboard draws the notice with Open "title" and,
for `done` only, Ask for the result, which sends `What did "title" find?` as an
ordinary user message; the main chat then reads the chat with
`session_read_message`. An `error` notice is a warning with Open only.

The main chat is never released by the live chat cap
([session](session.md#live-chat-cap-sessionmax_live_sessions)), and the agent
never closes it (a `crew-setup` rule).

## The first week

`dashboard/first_week.py` posts at most one tip a day in the main chat for the
seven days after graduation (the `job_kept` stage). A tip is a fixed system
notice (`first_week_tip` in `dashboard/system_notices.py`), never a model turn
and never a card (SC8). `next_tip` picks the first tip not yet shown whose
condition still holds (no connection, not staying on, no channel, one job, no
SOUL.md, then the skill tip), so at most six, and only when the user sent a
message in the main chat within `ACTIVE_WITHIN_SECS`, in local daytime, and at
least `TIP_MIN_GAP_SECS` after the last tip. Two tips in a row with no reply,
the phrase "no more tips" in the main chat (`note_user_message`, called from
`api_chat`), or the end of the week stops them. The gateway starts the loop at
boot (`server.py`); an install without a first run returns at once. The
bookkeeping is `first_week` in the first-run state file, presentation only.

## Pasted secrets

`api_chat` passes every message through `capture_pasted_secrets` after the
identity gates and before any branch queues, stores or sends it. Credentials the
shared detectors (`security.redaction.redact_credentials_with_records`) find are
replaced by `secret://NAME`; for the owner the value is stored in the vault
under a name derived from the detector rule (reusing a name that already holds
the same value). AWS keys and private keys are removed and never stored. A
`secret_captured` system notice (`dashboard/system_notices.py`) says what
happened.
