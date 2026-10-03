/**
 * Setup cards: the wire shapes and the React Query cache seam.
 *
 * A setup card is a SERVER-SIDE pending action with a rendered face (RFC
 * one-chat first run, §6.3). The model proposes one; the gateway builds it,
 * stores it and appends a transcript row carrying only `meta.setupCard.id`.
 * The dashboard renders the card from `GET /api/setup/cards/{id}` and never
 * from row text, and only an owner's click (`POST .../decide`, bound to the
 * card's payload `hash`) commits anything.
 *
 * The HTTP methods live on `api` in `./client` (setupCards / setupCard /
 * decideSetupCard / firstRun). This module holds what both the card and the
 * WebSocket handler need without importing the whole client: the types, the
 * query keys, and the one function that folds an updated card into the cache.
 *
 * NOTHING a card's input carries (a pasted credential, a bot token) is ever
 * written through here: the decide RESPONSE is the card, whose outcome holds a
 * vault reference at most, never the secret.
 */
import type { QueryClient } from '@tanstack/react-query'

export type SetupCardKind =
  | 'privacy'
  | 'profile'
  | 'soul'
  | 'import'
  | 'connect'
  | 'credential'
  | 'channel'
  | 'cron'
  | 'service'
  /** A permanent home in the user's own AWS account, built in the background. */
  | 'home'
  /** The first run's scripted steps before any model turn: the agent engine, its
   *  install and sign-in, and how the owner wants to start. Gateway-only. */
  | 'harness'
  | 'harness_signin'
  | 'path'

export type SetupCardStatus =
  | 'pending'
  | 'working'
  | 'waiting'
  | 'committed'
  | 'declined'
  | 'failed'
  | 'expired'

/** `aws_signin` is the home card's "Sign in to AWS"; `input.cancel` stops it.
 *  `change_engine` is a first-run step's "Choose a different engine". */
export type SetupDecision = 'commit' | 'decline' | 'preview' | 'aws_signin' | 'region' | 'choose' | 'remove' | 'change_engine'

export interface SetupCardError {
  code: string
  message: string
}

/** Where "Use classic setup" leads: a route, a window event, or nowhere. */
export interface SetupCardClassic {
  kind: 'route' | 'event' | 'none'
  target: string
}

export interface SetupCard {
  /** A transferred receipt records a decision; it cannot execute an action. */
  historical?: boolean
  id: string
  slot: string
  kind: SetupCardKind
  status: SetupCardStatus
  stakes: 'low' | 'high'
  /** sha256 of the canonical payload; every decide must echo it. */
  hash: string
  payload: Record<string, unknown>
  outcome: Record<string, unknown> | null
  error: SetupCardError | null
  created_ts: number
  decided_ts: number | null
  classic: SetupCardClassic
}

export interface SetupDecideBody {
  decision: SetupDecision
  hash: string
  input?: Record<string, unknown>
}

export interface FirstRunState {
  slot: string | null
  active: boolean
}

export interface SetupCardList {
  cards: SetupCard[]
}

/**
 * The scripted steps, in the order the gateway shows them. While one is live in
 * the first-run chat, no model can answer there yet: the gateway refuses a typed
 * message (`setup_step_pending`) and the composer says which step it waits on.
 */
export const SCRIPTED_SETUP_KINDS: readonly SetupCardKind[] = ['harness', 'harness_signin', 'privacy', 'path']

/** The gateway's scripted message before a scripted card (`meta.setupStep`). */
export interface SetupStepRef {
  step: string
  card: string
  /** The agent engine's name, for the sign-in steps. */
  label: string
}

/** Read `meta.setupStep` off a transcript row, or null when it is not a step row. */
export function setupStepOf(meta: Record<string, unknown> | null | undefined): SetupStepRef | null {
  const raw = meta?.setupStep
  if (!raw || typeof raw !== 'object') return null
  const { step, card, label } = raw as { step?: unknown; card?: unknown; label?: unknown }
  if (typeof step !== 'string' || !step) return null
  return { step, card: typeof card === 'string' ? card : '', label: typeof label === 'string' ? label : '' }
}

/** The transcript row's pointer (`meta.setupCard`). */
export interface SetupCardRef {
  id: string
  kind: string
}

/**
 * A pending approval a running job preview waits on
 * (`GET /api/setup/cards/{id}/approvals`). Answered through the one-shot
 * `api.resolveApproval`, the same path the Notifications feed uses.
 */
export interface SetupCardApproval {
  id: string
  tool: string
  tool_input: string
  tool_purpose: string
  ts: number
}

/**
 * Whether a sign-in card's harness says it is signed in
 * (`GET /api/setup/cards/{id}/signin-status`), from the harness's own status
 * command. `null` is unknown: Continue still asks by starting the harness.
 */
export interface SetupCardSigninStatus {
  signed_in: boolean | null
}

export const setupCardQueryKey = (id: string) => ['setup-card', id] as const
export const setupCardApprovalsQueryKey = (id: string) => ['setup-card-approvals', id] as const
export const setupCardSigninStatusQueryKey = (id: string) => ['setup-card-signin-status', id] as const
export const setupCardsQueryKey = (slot: string) => ['setup-cards', slot] as const

const TERMINAL: ReadonlySet<SetupCardStatus> = new Set(['committed', 'declined', 'failed', 'expired'])

export function isTerminalSetupStatus(status: SetupCardStatus | undefined): boolean {
  return !!status && TERMINAL.has(status)
}

/** Read `meta.setupCard` off a transcript row, or null when it is not a card row. */
export function setupCardRefOf(meta: Record<string, unknown> | null | undefined): SetupCardRef | null {
  const raw = meta?.setupCard
  if (!raw || typeof raw !== 'object') return null
  const { id, kind } = raw as { id?: unknown; kind?: unknown }
  if (typeof id !== 'string' || !id) return null
  return { id, kind: typeof kind === 'string' ? kind : '' }
}

/** A frame is only applied when it is shaped like a card: an id and a status. */
export function isSetupCardShape(value: unknown): value is SetupCard {
  if (!value || typeof value !== 'object') return false
  const c = value as { id?: unknown; status?: unknown; kind?: unknown }
  return typeof c.id === 'string' && !!c.id && typeof c.status === 'string' && typeof c.kind === 'string'
}

/**
 * Fold a fresher copy of a card into both caches: the card's own entry and its
 * slot's list (when that list is cached; an unobserved list is not created).
 *
 * One guard: a terminal card never goes back to a live status. Terminal states
 * are final on the server, so a non-terminal copy arriving after a terminal one
 * is a stale frame (a poll response that raced the WebSocket, or a replay), and
 * applying it would re-enable buttons on a card the gateway will refuse.
 */
export function applySetupCardUpdate(qc: QueryClient, card: SetupCard, slot?: string): void {
  const key = setupCardQueryKey(card.id)
  const cached = qc.getQueryData<SetupCard>(key)
  if (cached && isTerminalSetupStatus(cached.status) && !isTerminalSetupStatus(card.status)) return
  qc.setQueryData<SetupCard>(key, card)
  const listSlot = slot || card.slot
  if (!listSlot) return
  qc.setQueryData<SetupCardList>(setupCardsQueryKey(listSlot), old => {
    if (!old || !Array.isArray(old.cards)) return old
    const at = old.cards.findIndex(c => c.id === card.id)
    if (at < 0) return { cards: [...old.cards, card] }
    const cards = old.cards.slice()
    cards[at] = card
    return { cards }
  })
}
