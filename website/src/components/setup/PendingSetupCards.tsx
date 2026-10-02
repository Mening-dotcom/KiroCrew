/**
 * PendingSetupCards — the "needs your decision" tray above the composer.
 *
 * A setup card is proposed mid-turn, and the agent usually keeps writing after
 * it, so the card's own transcript row ends up far above the newest message —
 * where stick-to-bottom scrolling fights anyone reaching for it. The tray keeps
 * every LIVE card (`pending`, `working`, `waiting`) of the slot pinned where the
 * owner is already looking, drawn by the same SetupCard (same buttons, same
 * test ids), while each card's transcript row folds to a one-line pointer
 * (SetupCard `placement="transcript"`). A decided card leaves the tray and its
 * row becomes the result line, so the full card is on screen exactly once.
 *
 * Cards arrive as a one-line HINT, never open (decision
 * docs/decisions/2026-10-02-setup-cards-arrive-as-hints.md): the kind's icon
 * (the shield on a high-stakes card), the title, a short summary from the
 * payload or the card's live state (setupCardHints.ts, through the registry),
 * "N more" when several wait, Not now when the card may be declined, and Review
 * to open the card in place. Consent stays in the open card: every decision
 * but Not now is made there, against the details the click is bound to.
 *
 * Open, the tray is capped at a third of its chat pane and scrolls inside;
 * Hide folds it back to the hint. It also folds once the conversation moves on
 * after the owner opened it (`conversationMoves`: a user message, a later turn
 * or a notice) or when they scroll up to read, except while they are using a
 * card (a pointer press, a key, focus inside it): that holds until the card is
 * decided, they hide it, or they scroll up themselves. The hold is keyed to the
 * card, not to where focus happens to be: focus moves for reasons that are not
 * the user leaving (a label's mousedown blurs the button before it, and a
 * decided card's button unmounts without a blur). The cards stay mounted
 * (inert) while folded, so a half-filled one keeps its input, and the hint is
 * the same box as the open tray, so the fold animates.
 *
 * Same placement as PendingQuestionCard: mounted above the composer by the
 * single-chat view and by every grid pane, for that surface's own slot.
 *
 * The list is `['setup-cards', slot]`; the owner-only `setup_card_update`
 * WebSocket frame keeps it current (useWebSocket upserts into it), and a
 * reconnect re-reads it. Loading it seeds each card's own cache entry so the
 * tray's cards render without a second round trip.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Loader2, ShieldCheck } from 'lucide-react'
import type React from 'react'

import { api } from '../../api/client'
import { ApiError } from '../../api/apiError'
import {
  applySetupCardUpdate,
  isTerminalSetupStatus,
  setupCardQueryKey,
  setupCardsQueryKey,
  type SetupCard as SetupCardData,
} from '../../api/setupCards'
import type { ChatMessage } from '../../types'
import { parseErrorCode } from '../../utils/errorReport'
import ErrorNotice from '../ErrorNotice'
import { Btn } from '../ui'
import SetupCard from './SetupCard'
import { errorText } from './setupCardCopy'
import { cardDeclinableFromHint, cardHint, cardIcon, cardTitle } from './setupCardRegistry'
import {
  conversationMoves,
  highlightTrayCard,
  useSetupCardOpenRequests,
  useTrayCardHighlighted,
} from './setupCardTray'

/** Finger travel (px) before a touch drag on the transcript counts as a scroll. */
const TOUCH_SLOP_PX = 8

type ScrollIntent = 'up' | 'down'

function keyIntent(e: KeyboardEvent): ScrollIntent | null {
  const t = e.target as HTMLElement | null
  // An arrow key in a field moves its caret; it does not scroll the transcript.
  const editable = !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
  if (e.key === 'PageUp' || e.key === 'Home' || (e.key === 'ArrowUp' && !editable)) return 'up'
  if (e.key === 'PageDown' || e.key === 'End' || (e.key === 'ArrowDown' && !editable)) return 'down'
  return null
}

export default function PendingSetupCards({
  slotKey,
  className = '',
  style,
  messages,
  atBottom = true,
  scrollerRef,
  onLocate,
}: {
  /** The surface's own slot (the active slot, or a grid pane's). */
  slotKey: string | null | undefined
  className?: string
  style?: React.CSSProperties
  /** The transcript the tray sits under. Without it the tray never folds. */
  messages?: readonly ChatMessage[]
  /** Whether that transcript is scrolled to its newest row. */
  atBottom?: boolean
  /** Its scroller: the tray reads the user's scroll GESTURES there, never the
   *  geometry, since folding the tray itself moves the bottom edge. */
  scrollerRef?: React.RefObject<HTMLElement | null>
  /** Scroll the transcript to a card's own row. Without it the bar's title is text. */
  onLocate?: (cardId: string, behavior: ScrollBehavior) => void
}) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const reduceMotion = useReducedMotion()
  const slot = slotKey || ''
  const cardsId = useId()
  const list = useQuery({
    queryKey: setupCardsQueryKey(slot),
    queryFn: async () => {
      const res = await api.setupCards(slot)
      // Seed each card's own entry, never overwriting a fresher decided copy
      // with a live one (the same guard the WebSocket fold applies).
      for (const card of res.cards ?? []) {
        qc.setQueryData<SetupCardData>(setupCardQueryKey(card.id), prev =>
          prev && isTerminalSetupStatus(prev.status) && !isTerminalSetupStatus(card.status) ? prev : card)
      }
      return res
    },
    enabled: !!slot,
    // A refusal here (a non-owner viewer, a gateway without setup cards) is not
    // the owner's to fix: the tray stays empty and each transcript row, seeing
    // no tray copy, draws its full card instead.
    retry: false,
  })

  const live = (list.data?.cards ?? []).filter(c => !isTerminalSetupStatus(c.status))
  const hasLive = !!slot && live.length > 0

  // ── When the tray is open ─────────────────────────────────────────────────
  // Only the owner opens it (Review, or the transcript row's "open it below").
  const [userOpen, setUserOpen] = useState(false)
  // How many times the conversation had moved on when the owner opened it: once
  // that number grows, the chat has moved past what they opened and it folds.
  const moves = useMemo(() => (messages ? conversationMoves(messages) : 0), [messages])
  const openedAtRef = useRef(moves)
  // The user scrolled up to read. Set by an upward gesture on the transcript;
  // cleared by scrolling back down to the bottom, by a new row arriving while
  // they are there, or by Review.
  const [readingBack, setReadingBack] = useState(false)
  // The card the user is working in; holds only while that card is live.
  const [engagedId, setEngagedId] = useState<string | null>(null)
  const engaged = !!engagedId && live.some(c => c.id === engagedId)
  const lastIntentRef = useRef<ScrollIntent | null>(null)
  const atBottomRef = useRef(atBottom)
  atBottomRef.current = atBottom
  const sectionElRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    setReadingBack(false)
    setUserOpen(false)
    setEngagedId(null)
    lastIntentRef.current = null
  }, [slot])

  useEffect(() => {
    if (moves > openedAtRef.current) setUserOpen(false)
  }, [moves])

  useEffect(() => {
    if (atBottom && lastIntentRef.current === 'down') setReadingBack(false)
  }, [atBottom])

  const rowCount = messages?.length ?? 0
  useEffect(() => {
    if (atBottomRef.current) setReadingBack(false)
  }, [rowCount])

  useEffect(() => {
    const el = scrollerRef?.current
    if (!el || !hasLive) return
    const intent = (dir: ScrollIntent) => {
      lastIntentRef.current = dir
      if (dir === 'up') {
        setReadingBack(true)
        setUserOpen(false)
        setEngagedId(null)
      } else if (atBottomRef.current) {
        setReadingBack(false)
      }
    }
    const onWheel = (e: WheelEvent) => { if (e.deltaY) intent(e.deltaY < 0 ? 'up' : 'down') }
    const onKey = (e: KeyboardEvent) => { const dir = keyIntent(e); if (dir) intent(dir) }
    let touchY: number | null = null
    const onTouchStart = (e: TouchEvent) => { touchY = e.touches[0]?.clientY ?? null }
    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY
      if (touchY === null || y === undefined || Math.abs(y - touchY) < TOUCH_SLOP_PX) return
      // A finger moving DOWN drags the content down: the reader goes back up.
      intent(y > touchY ? 'up' : 'down')
      touchY = y
    }
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('keydown', onKey)
    el.addEventListener('touchstart', onTouchStart, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: true })
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('keydown', onKey)
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
    }
  }, [scrollerRef, hasLive])

  const expanded = engaged || (userOpen && !readingBack)

  const open = useCallback(() => {
    openedAtRef.current = moves
    setReadingBack(false)
    setUserOpen(true)
  }, [moves])

  const toggle = useCallback(() => {
    if (!expanded) { open(); return }
    setEngagedId(null)
    setUserOpen(false)
  }, [expanded, open])

  // The transcript's "in the tray below" row asks for its card: open the tray,
  // bring the card into the tray's own view once it has unfolded, and light it.
  const liveIdsRef = useRef<string[]>([])
  liveIdsRef.current = live.map(c => c.id)
  const reduceMotionRef = useRef(reduceMotion)
  reduceMotionRef.current = reduceMotion
  const onOpenRequest = useCallback((cardId: string) => {
    if (!liveIdsRef.current.includes(cardId)) return
    open()
    highlightTrayCard(cardId)
    window.setTimeout(() => {
      const slots = sectionElRef.current?.querySelectorAll<HTMLElement>('[data-tray-card]') ?? []
      ;[...slots].find(el => el.dataset.trayCard === cardId)
        ?.scrollIntoView({ block: 'nearest', behavior: reduceMotionRef.current ? 'auto' : 'smooth' })
    }, reduceMotionRef.current ? 0 : FOLD_MS + 20)
  }, [open])
  useSetupCardOpenRequests(onOpenRequest)

  // Not now, the one decision the hint carries: declining shows nothing the
  // owner has to read first. Same request and cache fold as the card's own.
  const decline = useMutation({
    mutationFn: (card: SetupCardData) => api.decideSetupCard(card.id, { decision: 'decline', hash: card.hash }),
    onSuccess: updated => applySetupCardUpdate(qc, updated, updated.slot),
    onError: (err, card) => {
      // Decided elsewhere, or its payload moved: re-read rather than keep a stale hint.
      const code = err instanceof ApiError ? parseErrorCode(err.body) : undefined
      if (code === 'card_not_pending' || code === 'card_hash_mismatch' || code === 'card_not_found') {
        void qc.invalidateQueries({ queryKey: setupCardQueryKey(card.id) })
        void qc.invalidateQueries({ queryKey: setupCardsQueryKey(card.slot) })
      }
    },
  })

  // ── Height cap: a third of the chat pane ──────────────────────────────────
  // Measured from the host's `data-setup-tray-pane` element; the `33dvh` class
  // is the fallback where there is none to measure.
  const [cap, setCap] = useState<number | null>(null)
  const observerRef = useRef<ResizeObserver | null>(null)
  const sectionRef = useCallback((node: HTMLElement | null) => {
    sectionElRef.current = node
    observerRef.current?.disconnect()
    observerRef.current = null
    const pane = node?.closest<HTMLElement>('[data-setup-tray-pane]')
    if (!pane) { setCap(null); return }
    const measure = () => { const h = pane.clientHeight; setCap(h > 0 ? Math.floor(h / 3) : null) }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(pane)
    observerRef.current = observer
  }, [])
  useEffect(() => () => observerRef.current?.disconnect(), [])

  if (!hasLive) return null

  // The hint names the FIRST card (the oldest, the one to settle first) and
  // counts the rest.
  const first = qc.getQueryData<SetupCardData>(setupCardQueryKey(live[0].id)) ?? live[0]
  const title = cardTitle(first)
  const hint = cardHint(first)
  const Icon = first.stakes === 'high' ? ShieldCheck : cardIcon(first)
  const declinable = !expanded && cardDeclinableFromHint(first)
  const declineMessage = decline.error && decline.variables?.id === first.id
    ? errorText(
        decline.error instanceof ApiError ? parseErrorCode(decline.error.body) : undefined,
        decline.error instanceof Error ? decline.error.message : '',
      )
    : ''
  const fold = reduceMotion ? { duration: 0 } : { duration: FOLD_MS / 1000, ease: [0.2, 0.8, 0.2, 1] as const }
  const Chevron = expanded ? ChevronDown : ChevronRight

  return (
    <section
      ref={sectionRef}
      className={`flex flex-col min-w-0 max-h-[33dvh] overflow-y-auto overscroll-contain ${className}`}
      style={cap ? { ...style, maxHeight: cap } : style}
      aria-label={t('components.setupCard.tray_label')}
      data-testid="setup-card-tray"
      data-expanded={String(expanded)}
    >
      {/* shrink-0 on both children: the section is a height-capped flex
          column, and an item that may overflow would otherwise give up its
          height first, folding the hint away under the cards. */}
      <div className="sticky top-0 z-10 shrink-0 min-w-0 bg-bg">
        <div
          className={`flex items-center gap-2 min-w-0 rounded-lg border bg-card px-3 py-1 text-[13px] ${
            first.stakes === 'high' ? 'border-accent' : 'border-border'
          } ${expanded ? 'mb-2' : ''}`}
          data-testid="setup-card-hint"
          data-kind={first.kind}
          data-status={first.status}
          data-stakes={first.stakes}
        >
          <Icon className="lucide-inline shrink-0 text-accent" aria-hidden="true" />
          {/* On a phone the summary wraps under the title rather than squeezing it. */}
          <div className="min-w-0 flex-1 flex flex-wrap md:flex-nowrap items-baseline gap-x-2">
            {onLocate ? (
              <Btn
                className="min-w-0 max-w-full min-h-11 md:min-h-0 border-none bg-transparent px-0 py-0 font-medium hover:bg-transparent hover:underline"
                onClick={() => onLocate(first.id, reduceMotion ? 'auto' : 'smooth')}
                title={t('components.setupCardTray.locate', { title })}
                aria-label={t('components.setupCardTray.locate', { title })}
                data-testid="setup-card-tray-locate"
              >
                <span className="truncate">{title}</span>
              </Btn>
            ) : (
              <span className="min-w-0 truncate font-medium">{title}</span>
            )}
            {hint && (
              <span
                className={`min-w-0 inline-flex items-center gap-1 truncate ${hint.state === 'needs-you' ? 'text-accent' : 'text-muted'}`}
                data-testid="setup-card-hint-text"
                data-state={hint.state ?? 'summary'}
              >
                {hint.state === 'busy' && <Loader2 className="lucide-inline shrink-0 animate-spin" aria-hidden="true" />}
                <span className="truncate">{hint.text}</span>
              </span>
            )}
            {live.length > 1 && (
              <span className="shrink-0 text-muted" data-testid="setup-card-hint-more">
                {t('components.setupCardHint.more', { count: live.length - 1 })}
              </span>
            )}
          </div>
          {declinable && (
            <Btn
              className="shrink-0 min-h-11 md:min-h-0 py-0.5 border-transparent text-muted hover:text-text"
              onClick={() => decline.mutate(first)}
              disabled={decline.isPending}
              aria-label={t('components.setupCardHint.decline_label', { title })}
              data-testid="setup-card-hint-decline"
            >
              {t('components.setupCard.not_now')}
            </Btn>
          )}
          <Btn
            className="shrink-0 min-h-11 md:min-h-0 py-0.5"
            onClick={toggle}
            aria-expanded={expanded}
            aria-controls={cardsId}
            data-testid="setup-card-tray-toggle"
          >
            {expanded ? t('components.setupCardTray.hide') : t('components.setupCardHint.review')}
            <Chevron className="lucide-inline" aria-hidden="true" />
          </Btn>
        </div>
        {/* The card holds nothing the owner typed here, so the hand-off loses nothing. */}
        {declineMessage && !expanded && (
          <div className="mt-1">
            <ErrorNotice message={declineMessage} askAgent testId="setup-card-hint-error" />
          </div>
        )}
      </div>
      <motion.div
        id={cardsId}
        className="flex flex-col gap-2 shrink-0 min-w-0"
        initial={false}
        animate={expanded
          ? { height: 'auto', opacity: 1, transitionEnd: { overflow: 'visible' } }
          : { height: 0, opacity: 0, overflow: 'hidden' }}
        transition={fold}
        // Folded cards stay mounted, so a half-filled one keeps its input, but
        // out of reach: no focus, no reading, no click.
        {...(expanded ? {} : { inert: '', 'aria-hidden': true })}
        data-testid="setup-card-tray-cards"
      >
        {/* Creation order, so the newest card sits last, nearest the composer. */}
        <AnimatePresence initial={false}>
          {live.map(card => (
            <TrayCard key={card.id} cardId={card.id} reduceMotion={!!reduceMotion} onEngage={setEngagedId} />
          ))}
        </AnimatePresence>
      </motion.div>
    </section>
  )
}

/** Height-fold duration of the hint opening and closing. */
const FOLD_MS = 200

/** One card in the open tray: marks the owner's engagement, carries the "opened from the chat" highlight. */
function TrayCard({ cardId, reduceMotion, onEngage }: {
  cardId: string
  reduceMotion: boolean
  onEngage: (cardId: string) => void
}) {
  const highlighted = useTrayCardHighlighted(cardId)
  return (
    <motion.div
      className="relative min-w-0"
      data-tray-card={cardId}
      // Capture phase, so the card's own handlers cannot hide the touch.
      onPointerDownCapture={() => onEngage(cardId)}
      onKeyDownCapture={() => onEngage(cardId)}
      onFocusCapture={() => onEngage(cardId)}
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduceMotion ? undefined : { opacity: 0 }}
      transition={{ duration: 0.18 }}
    >
      <SetupCard cardId={cardId} placement="tray" />
      {highlighted && (
        <span
          aria-hidden="true"
          data-testid="setup-card-tray-highlight"
          className="pointer-events-none absolute inset-0 rounded-lg animate-msg-highlight"
        />
      )}
    </motion.div>
  )
}
