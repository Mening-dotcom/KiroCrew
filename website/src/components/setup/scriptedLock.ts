/**
 * The first-run chat's composer lock (UX.2). Until its scripted steps are done no
 * agent harness is ready to answer, so the composer is disabled with the step it
 * waits on, and the gateway refuses a send there anyway (`setup_step_pending`).
 *
 * Read from the slot's live setup cards (the tray's own list, subscribed to and
 * never fetched here), never from a readiness latch: the lock lifts the moment
 * the owner finishes the last step, and a list that failed to load locks nothing.
 */
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import {
  SCRIPTED_SETUP_KINDS,
  isTerminalSetupStatus,
  setupCardsQueryKey,
  type SetupCard,
} from '../../api/setupCards'
import { i18nT } from '../../i18n/t'

/** Why the composer is locked by *cards*, or '' when no scripted step waits. */
export function scriptedLockReason(cards: readonly SetupCard[] | undefined): string {
  const waiting = (cards ?? []).find(c => SCRIPTED_SETUP_KINDS.includes(c.kind) && !isTerminalSetupStatus(c.status))
  if (!waiting) return ''
  switch (waiting.kind) {
    case 'harness':
      return i18nT('components.setupStep.locked_harness')
    case 'harness_signin': {
      const label = typeof waiting.payload?.label === 'string' ? waiting.payload.label : ''
      return i18nT('components.setupStep.locked_signin', { label })
    }
    case 'privacy':
      return i18nT('components.setupStep.locked_privacy')
    default:
      return i18nT('components.setupStep.locked_path')
  }
}

/** The lock reason for *slot*'s composer: '' when it may send. */
export function useScriptedLockReason(slot: string | null | undefined): string {
  const list = useQuery({
    queryKey: setupCardsQueryKey(slot || ''),
    queryFn: () => api.setupCards(slot || ''),
    // PendingSetupCards owns the fetch for this slot (mounted beside every
    // composer); this only reads the same list.
    enabled: false,
  })
  return slot ? scriptedLockReason(list.data?.cards) : ''
}
