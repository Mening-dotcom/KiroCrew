/**
 * What the "needs your decision" tray (PendingSetupCards) reads from the
 * transcript it sits under, and the two small channels it shares with the
 * transcript's rows, kept out of the component so both hosts (the single-chat
 * page and every grid pane) and the tests share one definition.
 */
import { useEffect, useSyncExternalStore } from 'react'

import { isSystemNoticeKind } from '../../lib/systemNotice'
import { TURN_OPENER_ROLES } from '../../pages/chat/groupDisplayItems'
import { injectOpensTurn } from '../../pages/chat/RecoveryCard'
import type { ChatMessage } from '../../types'

/**
 * Whether a row moves the conversation on: the user writing (a steer included),
 * a row that opens a later turn (the same openers the turn grouping uses: a
 * nudge, a sub-agent completion, a cron or setup-result inject...), or a notice.
 * Rows of a turn already under way do not, whatever they say: the agent nearly
 * always follows a card with "I've put a card on screen", then tool lines and
 * its done marker.
 */
function movesConversation(m: ChatMessage): boolean {
  if (m.role === 'user' || m.role === 'notice') return true
  if (TURN_OPENER_ROLES.has(m.role) || injectOpensTurn(m)) return true
  return m.role === 'assistant' && isSystemNoticeKind(m.kind ?? (m.meta?.kind as string | undefined))
}

/**
 * How many times the conversation has moved on (see {@link movesConversation}).
 * The tray records it when the owner opens a card, and folds the card back to
 * its hint once it grows: the chat has moved past what they opened.
 */
export function conversationMoves(messages: readonly ChatMessage[]): number {
  let n = 0
  for (const m of messages) if (movesConversation(m)) n++
  return n
}

// ── A highlight one surface asks another to show ──────────────────────────────
// Module-level (the shape useRailWidth uses) so one surface can light up a box
// it does not render, and only that box re-renders. Two instances: the
// transcript row the tray's title scrolls to, and the tray card the transcript's
// "in the tray below" row opens.

/** How long a highlight stays: the msg-highlight utility's run. */
export const SETUP_CARD_ROW_HIGHLIGHT_MS = 2000

function createHighlight() {
  let highlighted: string | null = null
  let clearTimer: ReturnType<typeof setTimeout> | null = null
  const listeners = new Set<() => void>()
  const notify = () => listeners.forEach(l => l())
  const subscribe = (cb: () => void) => {
    listeners.add(cb)
    return () => { listeners.delete(cb) }
  }
  return {
    highlight(cardId: string) {
      if (clearTimer) clearTimeout(clearTimer)
      highlighted = cardId
      notify()
      clearTimer = setTimeout(() => {
        clearTimer = null
        highlighted = null
        notify()
      }, SETUP_CARD_ROW_HIGHLIGHT_MS)
    },
    useHighlighted(cardId: string): boolean {
      return useSyncExternalStore(subscribe, () => highlighted === cardId, () => false)
    },
  }
}

const rowHighlight = createHighlight()
const trayHighlight = createHighlight()

/** Highlight `cardId`'s transcript row (the tray title's "find it in the chat"). */
export const highlightSetupCardRow = (cardId: string) => rowHighlight.highlight(cardId)
export const useSetupCardRowHighlighted = (cardId: string) => rowHighlight.useHighlighted(cardId)
/** Highlight `cardId`'s card in the tray (the transcript row's "open it below"). */
export const highlightTrayCard = (cardId: string) => trayHighlight.highlight(cardId)
export const useTrayCardHighlighted = (cardId: string) => trayHighlight.useHighlighted(cardId)

// ── "Open this card": the transcript's row asks the tray ──────────────────────

const openListeners = new Set<(cardId: string) => void>()

/** Ask the tray that holds `cardId` to open it and highlight it. */
export function openSetupCardInTray(cardId: string) {
  openListeners.forEach(l => l(cardId))
}

/** The tray's side: `onOpen` runs for every open request, its own or not. */
export function useSetupCardOpenRequests(onOpen: (cardId: string) => void) {
  useEffect(() => {
    openListeners.add(onOpen)
    return () => { openListeners.delete(onOpen) }
  }, [onOpen])
}
