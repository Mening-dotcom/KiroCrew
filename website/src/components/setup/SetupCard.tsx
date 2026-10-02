/**
 * SetupCard — the rendered face of a server-side setup action (one-chat first
 * run, RFC §6.3), drawn inline in the chat transcript.
 *
 * The contract this component holds (docs: /api/setup/cards, and the RFC):
 *
 *   - It renders from `GET /api/setup/cards/{id}` ONLY. The transcript row that
 *     mounts it carries an id and nothing else this component reads, so no row
 *     text can forge a card's face or its buttons.
 *   - Every decision posts the card's `hash`, so a click can only ever apply to
 *     the payload the owner is looking at (a changed payload is another card).
 *   - A credential or a bot token lives in an UNCONTROLLED password field: it is
 *     read from the DOM at the moment of the click, cleared from the field in the
 *     same tick, and handed to the request through a ref. It is never React
 *     state, never a mutation variable (React Query keeps those in its mutation
 *     cache), never Redux, never logged, and never rendered back.
 *   - High-stakes cards (a credential, a connection, a channel, the service) look
 *     different from low-stakes ones -- accent border, a shield, an explicit verb
 *     on the button. A home's authorized build continues its move after sign-in.
 *   - Every card offers "Use classic setup" unless the gateway says `none`.
 *
 * Live state arrives three ways: the decide response, the owner-only
 * `setup_card_update` WebSocket frame (useWebSocket folds it into this card's
 * cache entry), and a 3 s poll while the card is `working` or `waiting`.
 */
import { useCallback, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion, useReducedMotion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowDown,
  CircleCheck,
  CircleSlash,
  CircleX,
  Clock,
  Loader2,
  ShieldCheck,
} from 'lucide-react'

import { api } from '../../api/client'
import { ApiError } from '../../api/apiError'
import { parseErrorCode } from '../../utils/errorReport'
import {
  applySetupCardUpdate,
  isTerminalSetupStatus,
  setupCardQueryKey,
  setupCardsQueryKey,
  type SetupCard as SetupCardData,
  type SetupDecision,
} from '../../api/setupCards'
import ErrorNotice from '../ErrorNotice'
import { Btn, SendBtn } from '../ui'
import type { SetupActions, SetupFooter } from './SetupCardBodies'
import { HomeLeftoverRemoval } from './SetupCardBodies'
import { classicAction, errorText, failedText, homeLeftover, homeRemovalState, resultStatusKey } from './setupCardCopy'
import { cardTitle, committedDetail, SetupCardBody, setupCardEntry } from './setupCardRegistry'
import { openSetupCardInTray } from './setupCardTray'

/** How often a `working` / `waiting` card re-reads itself, beside the WS push. */
export const SETUP_CARD_POLL_MS = 3000

/** The secret a password field handed over for exactly one request. */
interface PendingSecret {
  field: 'value' | 'token'
  value: string
}

interface DecideVars {
  decision: SetupDecision
  hash: string
  /** Non-secret input only. A secret rides `pendingSecretRef`, never here. */
  input?: Record<string, unknown>
}

/**
 * Where this instance of a card is drawn.
 *
 * - `inline` (default): the full card, wherever it is mounted.
 * - `tray`: the "needs your decision" tray above the composer
 *   (PendingSetupCards). Only a live card renders here; a decided one draws
 *   nothing, because its result line belongs to the transcript.
 * - `transcript`: the card's own transcript row on a surface that ALSO mounts
 *   the tray. While the tray holds the live card, the row is a one-line pointer
 *   to it, so the full card is on screen exactly once; once decided, the row is
 *   the compact result line. If the tray does not hold it (its list failed to
 *   load, or has not yet), the row falls back to the full card rather than
 *   pointing at nothing.
 */
export type SetupCardPlacement = 'inline' | 'tray' | 'transcript'

export default function SetupCard({ cardId, placement = 'inline' }: { cardId: string; placement?: SetupCardPlacement }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const reduceMotion = useReducedMotion()
  const pendingSecretRef = useRef<PendingSecret | null>(null)
  // A poll landing while a decide is in flight would paint the pre-click state
  // over the optimistic `working` one; the decide response is the next truth.
  const decidingRef = useRef(false)

  const query = useQuery({
    queryKey: setupCardQueryKey(cardId),
    queryFn: () => api.setupCard(cardId),
    refetchInterval: q => {
      if (decidingRef.current) return false
      const data = q.state.data
      const status = data?.status
      if (status === 'working' || status === 'waiting') return SETUP_CARD_POLL_MS
      return data && homeRemovalState(data) === 'active' ? SETUP_CARD_POLL_MS : false
    },
  })
  const card = query.data
  // Subscribe to (never fetch) the slot's tray list: the transcript row only
  // folds into a pointer when the tray really holds this card live.
  const trayList = useQuery({
    queryKey: setupCardsQueryKey(card?.slot ?? ''),
    queryFn: () => api.setupCards(card?.slot ?? ''),
    enabled: false,
  })

  const decide = useMutation({
    mutationFn: async (vars: DecideVars) => {
      // Take the secret out of the ref before the first await, so it is held by
      // nothing but this request's body from here on.
      const secret = pendingSecretRef.current
      pendingSecretRef.current = null
      const input = secret ? { ...(vars.input ?? {}), [secret.field]: secret.value } : vars.input
      return api.decideSetupCard(cardId, {
        decision: vars.decision,
        hash: vars.hash,
        ...(input ? { input } : {}),
      })
    },
    onMutate: async () => {
      decidingRef.current = true
      await qc.cancelQueries({ queryKey: setupCardQueryKey(cardId) })
      const previous = qc.getQueryData<SetupCardData>(setupCardQueryKey(cardId))
      if (previous && !isTerminalSetupStatus(previous.status)) {
        // Optimistic: the buttons disable and the spinner shows at once. Only a
        // status flip; the payload and hash stay the server's. A decided card's
        // own action (a home's removal) keeps its result line meanwhile.
        qc.setQueryData<SetupCardData>(setupCardQueryKey(cardId), { ...previous, status: 'working', error: null })
      }
      return { previous }
    },
    onSuccess: (updated, vars) => {
      applySetupCardUpdate(qc, updated, updated.slot)
      // The privacy card writes `dashboard.privacy_acked` server-side (it is the
      // flag's only writer on this path) and the import card may mark import
      // done; re-read the boot flags so this tab's chapters agree with the
      // server instead of re-offering what the card just settled.
      if (vars.decision === 'commit' && setupCardEntry(updated.kind)?.refreshesBoot) {
        void qc.invalidateQueries({ queryKey: ['theme-boot'] })
      }
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(setupCardQueryKey(cardId), ctx.previous)
      // Already decided elsewhere, or the stored payload moved: the cached copy
      // is stale, so fetch the real one rather than leave buttons that will 409.
      const code = err instanceof ApiError ? parseErrorCode(err.body) : undefined
      if (code === 'card_not_pending' || code === 'card_hash_mismatch' || code === 'card_not_found') {
        void qc.invalidateQueries({ queryKey: setupCardQueryKey(cardId) })
        const slot = ctx?.previous?.slot
        if (slot) void qc.invalidateQueries({ queryKey: setupCardsQueryKey(slot) })
      }
    },
    onSettled: () => {
      decidingRef.current = false
    },
  })

  const run = useCallback((decision: SetupDecision, input?: Record<string, unknown>) => {
    if (!card) return
    decide.reset()
    decide.mutate({ decision, hash: card.hash, ...(input ? { input } : {}) })
  }, [card, decide])

  const runWithSecret = useCallback((field: 'value' | 'token', el: HTMLInputElement | null) => {
    if (!card || !el) return
    const value = el.value
    // Cleared in the same tick it is read: the field never shows the value
    // again, whatever the request's outcome.
    el.value = ''
    pendingSecretRef.current = { field, value }
    decide.reset()
    decide.mutate({ decision: 'commit', hash: card.hash })
  }, [card, decide])

  if (query.isPending) {
    if (placement === 'tray') return null
    return (
      <div
        className="w-full max-w-2xl min-w-0 flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-3 text-[13px] text-muted"
        data-testid="setup-card-loading"
        aria-busy="true"
      >
        <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
        {t('components.setupCard.loading')}
      </div>
    )
  }

  if (!card) {
    // The transcript row reports a failed read; the tray stays quiet about it.
    if (placement === 'tray') return null
    // A read failure with nothing typed yet, so the hand-off loses nothing.
    return (
      <div className="w-full max-w-2xl min-w-0" data-testid="setup-card-load-error">
        <ErrorNotice
          title={t('components.setupCard.load_failed')}
          message={query.error instanceof Error ? query.error.message : t('components.setupCard.error_generic')}
          askAgent
        />
      </div>
    )
  }

  const high = card.stakes === 'high'
  const title = cardTitle(card)
  const terminal = isTerminalSetupStatus(card.status)

  // A decided card's result line lives in the transcript, never in the tray.
  if (placement === 'tray' && terminal) return null
  const heldByTray =
    placement === 'transcript'
    && !terminal
    && !!trayList.data?.cards?.some(c => c.id === card.id && !isTerminalSetupStatus(c.status))
  if (heldByTray) {
    // A pointer to the card's hint in the tray; clicking it opens the card there.
    return (
      <Btn
        type="button"
        onClick={() => openSetupCardInTray(card.id)}
        className="w-full max-w-2xl min-w-0 justify-start gap-2 rounded-md bg-card px-3 py-2 text-left text-muted hover:text-text"
        data-testid="setup-card-ref"
        data-kind={card.kind}
        data-status={card.status}
      >
        {high
          ? <ShieldCheck className="lucide-inline shrink-0 text-accent" aria-hidden="true" />
          : <ArrowDown className="lucide-inline shrink-0" aria-hidden="true" />}
        <span className="min-w-0 break-words">{t('components.setupCard.in_tray_ref', { title })}</span>
      </Btn>
    )
  }
  // The tray card and the transcript's result line are the SAME card in two
  // places: one layout id hands the box from the tray down to its row when it
  // is decided, so the eye can follow it instead of watching it vanish.
  const sharedLayoutId =
    !reduceMotion && (placement === 'tray' || (placement === 'transcript' && terminal))
      ? `setup-card-${card.id}`
      : undefined
  const busy = decide.isPending || card.status === 'working'
  const classic = classicAction(card.classic)

  const decideError = decide.error
  const decideCode = decideError instanceof ApiError ? parseErrorCode(decideError.body) : undefined
  const decideMessage = decideError
    ? errorText(decideCode, decideError instanceof Error ? decideError.message : '')
    : ''
  const cardErrorMessage = card.error ? errorText(card.error.code, card.error.message) : ''

  const openClassic = () => {
    if (!classic) return
    if (classic.kind === 'route') navigate(classic.target)
    else window.dispatchEvent(new CustomEvent(classic.target, { detail: { continueOnboarding: true } }))
  }

  // Everything below the body: the error (ABOVE the buttons, per the contract),
  // the action row (at most primary + secondary), and a separate link row for
  // "Not now" and "Use classic setup".
  const footer: SetupFooter = (actions: SetupActions) => {
    const canDecline = actions.decline !== false && card.status === 'pending'
    return (
      <div className="mt-3 flex flex-col gap-2">
        {/* One notice, whichever failed: the server's recoverable `error` on a
            still-pending card, or this click's own refused request.
            No hand-off on a draft kind (its registry entry's `draft`): the
            credential field, the bot-token field and the import checkboxes
            hold an unsaved draft that the hand-off's navigation would destroy
            (and the token fields are cleared anyway). Elsewhere the card is
            server-side state, so a hand-off loses nothing. */}
        <ErrorNotice
          message={decideMessage || (card.status === 'pending' ? cardErrorMessage : '')}
          askAgent={setupCardEntry(card.kind)?.draft !== true}
          testId="setup-card-error"
        />
        {(actions.primary || actions.secondary) && (
          <div className="flex flex-wrap items-center gap-2">
            {actions.primary && (
              <SendBtn
                type="button"
                onClick={actions.primary.onClick}
                disabled={busy || actions.primary.disabled}
                className="inline-flex items-center gap-1.5"
                data-testid="setup-card-primary"
              >
                {high && <ShieldCheck className="lucide-inline" aria-hidden="true" />}
                {actions.primary.label}
              </SendBtn>
            )}
            {actions.secondary && (
              <Btn
                type="button"
                onClick={actions.secondary.onClick}
                disabled={busy || actions.secondary.disabled}
                className="min-h-9"
                data-testid="setup-card-secondary"
              >
                {actions.secondary.label}
              </Btn>
            )}
            {busy && (
              <span className="inline-flex items-center gap-1.5 text-[12px] text-muted" role="status">
                <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
                {t('components.setupCard.working')}
              </span>
            )}
          </div>
        )}
        {(canDecline || classic) && (
          <div className="flex flex-wrap items-center gap-x-1 gap-y-1">
            {canDecline && (
              <Btn
                type="button"
                onClick={() => run('decline')}
                disabled={busy}
                className="border-transparent text-muted hover:text-text"
                data-testid="setup-card-decline"
              >
                {actions.declineLabel ?? t('components.setupCard.not_now')}
              </Btn>
            )}
            {classic && (
              <Btn
                type="button"
                onClick={openClassic}
                className="border-transparent text-muted hover:text-text"
                data-testid="setup-card-classic"
              >
                {t('components.setupCard.use_classic_setup')}
              </Btn>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <motion.section
      // ONE element across every state: pending, working, waiting and the
      // compact result line are this same box changing shape, so `layout`
      // animates the collapse rather than swapping one card for another.
      layout={!reduceMotion}
      layoutId={sharedLayoutId}
      transition={{ layout: { duration: 0.2, ease: [0.2, 0.8, 0.2, 1] } }}
      aria-label={t('components.setupCard.region_label', { title })}
      data-testid="setup-card"
      data-kind={card.kind}
      data-status={card.status}
      data-stakes={card.stakes}
      className={`w-full max-w-2xl min-w-0 rounded-lg border bg-card text-text ${
        terminal ? 'px-3 py-2' : 'px-4 py-3'
      } ${high && !terminal ? 'border-accent ring-1 ring-accent/30' : 'border-border'}`}
    >
      {terminal ? (
        <ResultLine
          card={card}
          title={title}
          busy={decide.isPending}
          actionError={decideMessage}
          onRemove={tag => run('remove', { tag })}
        />
      ) : (
        <>
          <header className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
            {high && (
              <span
                className="inline-flex items-center gap-1 text-[12px] font-medium text-accent"
                data-testid="setup-card-high-stakes"
              >
                <ShieldCheck className="lucide-inline" aria-hidden="true" />
                {t('components.setupCard.needs_approval')}
              </span>
            )}
            <h3 className="text-sm font-semibold text-text-strong min-w-0 break-words">{title}</h3>
          </header>
          <SetupCardBody
            card={card}
            busy={busy}
            run={run}
            runWithSecret={runWithSecret}
            footer={footer}
            compact={placement === 'tray'}
          />
        </>
      )}
    </motion.section>
  )
}

/**
 * The compact line a decided card collapses into. A failed home card whose build
 * a restart cut short also offers to remove what it left in AWS.
 */
function ResultLine({ card, title, busy, actionError, onRemove }: {
  card: SetupCardData
  title: string
  busy: boolean
  actionError: string
  onRemove: (tag: string) => void
}) {
  const { t } = useTranslation()
  const Icon =
    card.status === 'committed' ? CircleCheck
      : card.status === 'declined' ? CircleSlash
        : card.status === 'failed' ? CircleX
          : Clock
  const tone =
    card.status === 'committed' ? 'text-ok' : card.status === 'failed' ? 'text-danger' : 'text-muted'
  const detail = card.status === 'committed' ? committedDetail(card) : null
  const statusKey = resultStatusKey(card)
  const leftover = card.historical ? null : homeLeftover(card)
  const removal = leftover ? homeRemovalState(card) : ''
  return (
    <div className="flex flex-col gap-1 min-w-0" data-testid="setup-card-result">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 min-w-0 text-[13px]">
        <Icon className={`lucide-inline shrink-0 ${tone}`} aria-hidden="true" />
        <span className="font-medium text-text min-w-0 break-words">{title}</span>
        {statusKey && <span className="text-muted">{t(statusKey)}</span>}
      </div>
      {detail}
      {/* A failed card is settled server-side; the hand-off loses nothing. */}
      {card.status === 'failed' && card.error && removal !== 'done' && (
        <ErrorNotice
          variant="inline"
          message={failedText(card)}
          askAgent
          testId="setup-card-failed-error"
        />
      )}
      {leftover && (
        <HomeLeftoverRemoval
          leftover={leftover}
          state={removal}
          busy={busy}
          error={actionError}
          onRemove={() => onRemove(leftover.tag)}
        />
      )}
    </div>
  )
}
