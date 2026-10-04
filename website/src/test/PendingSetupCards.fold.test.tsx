/**
 * The tray shows every pending card as a one-line HINT and never opens one on
 * its own (decision docs/decisions/2026-10-02-setup-cards-arrive-as-hints.md).
 *
 * The hint carries the kind's icon (the shield for a high-stakes card), the
 * title, a short summary from the payload or the card's live state, "N more"
 * when several wait, Not now when the card may be declined, and Review, which
 * opens the full card in place: one card at a time, the one the latest turn
 * proposed, while the others stay hints. Nothing commits from the hint. Open, the tray
 * folds back on Hide, once the conversation moves on, or on a scroll up, except
 * while the owner is using a card. It is capped at a third of its chat pane.
 * Driven through MSW with the real SetupCard.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { useRef } from 'react'

import { server } from '../../integration/mocks/server'
import PendingSetupCards from '../components/setup/PendingSetupCards'
import SetupCardRow from '../components/setup/SetupCardRow'
import { conversationMoves, highlightSetupCardRow, latestProposedCard } from '../components/setup/setupCardTray'
import { applySetupCardUpdate, type SetupCard as Card, type SetupDecideBody } from '../api/setupCards'
import { mergeRenderers, resolveRenderer, type MessageRenderContext } from '../app-sdk/messageRenderers'
import { createTranscriptRenderers } from '../pages/chat/transcriptRenderers'
import type { ChatMessage } from '../types'

const SLOT = 'chat-1-1790000000'

function card(over: Partial<Card> & Pick<Card, 'id' | 'kind'>): Card {
  return {
    slot: SLOT, status: 'pending', stakes: 'low', hash: 'b'.repeat(64), payload: {},
    outcome: null, error: null, created_ts: 1790000000, decided_ts: null,
    classic: { kind: 'none', target: '' }, ...over,
  }
}

/** The slot list, each card, and decide (which settles the card as asked). */
function serveCards(cards: Card[]) {
  const byId = new Map(cards.map(c => [c.id, c]))
  const decided: Array<{ id: string; body: SetupDecideBody }> = []
  server.use(
    http.get('/api/setup/cards', () => HttpResponse.json({ cards: [...byId.values()] })),
    http.get('/api/setup/cards/:id', ({ params }) => HttpResponse.json(byId.get(String(params.id)))),
    http.post('/api/setup/cards/:id/decide', async ({ params, request }) => {
      const id = String(params.id)
      const body = (await request.json()) as SetupDecideBody
      decided.push({ id, body })
      const next = { ...byId.get(id)!, status: body.decision === 'decline' ? 'declined' as const : 'committed' as const }
      byId.set(id, next)
      return HttpResponse.json(next)
    }),
  )
  return { decided }
}

const cardRow = (id: string, kind = 'profile'): ChatMessage =>
  ({ role: 'inject', cls: '', content: 'model-visible summary', meta: { setupCard: { id, kind } } }) as ChatMessage
const say = (content: string): ChatMessage => ({ role: 'assistant', cls: '', content }) as ChatMessage
const USER_REPLY = { role: 'user', cls: '', content: 'Can I change it later?' } as ChatMessage
/** The next agent turn: a setup result opens it, then the reply. */
const NEXT_TURN = [
  { role: 'inject', cls: '', content: 'Setup result: kept.', meta: { injectKind: 'setup_result' } } as ChatMessage,
  say('Also: which email should the morning brief go to?'),
]

interface SurfaceProps {
  messages: ChatMessage[]
  atBottom?: boolean
  onLocate?: (id: string, behavior: ScrollBehavior) => void
  paneHeight?: number
  /** Transcript rows to draw above the tray, the way ChatPage does. */
  rows?: ChatMessage[]
}

function Surface({ messages, atBottom = true, onLocate, paneHeight, rows = [] }: SurfaceProps) {
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const registry = mergeRenderers(createTranscriptRenderers({ slot: SLOT, setupCardTray: true }))
  const ctx = (i: number): MessageRenderContext => ({
    index: i, messages: rows, running: false, key: `k${i}`, hideCardOwnedOAuth: false,
    autoDeniedIds: new Set(), wrapper: c => c, row: c => c,
  })
  return (
    <div
      data-setup-tray-pane=""
      ref={el => {
        if (el && paneHeight) Object.defineProperty(el, 'clientHeight', { configurable: true, value: paneHeight })
      }}
    >
      <div ref={scrollerRef} data-testid="scroller">
        <div data-testid="transcript">
          {rows.map((m, i) => <div key={i}>{resolveRenderer(m, registry)!.render(m, ctx(i))}</div>)}
        </div>
      </div>
      <PendingSetupCards slotKey={SLOT} messages={messages} atBottom={atBottom} scrollerRef={scrollerRef} onLocate={onLocate} />
    </div>
  )
}

function renderSurface(props: SurfaceProps) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  const wrap = (p: SurfaceProps) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter><Surface {...p} /></MemoryRouter>
    </QueryClientProvider>
  )
  const utils = render(wrap(props))
  return { ...utils, qc, update: (p: SurfaceProps) => utils.rerender(wrap(p)) }
}

const tray = () => screen.getByTestId('setup-card-tray')
const cards = () => screen.getByTestId('setup-card-tray-cards')
const hint = () => screen.getByTestId('setup-card-hint')
const toggle = () => screen.getByTestId('setup-card-tray-toggle')
const expectOpen = () => {
  expect(tray()).toHaveAttribute('data-expanded', 'true')
  expect(cards()).not.toHaveAttribute('inert')
  expect(toggle()).toHaveAttribute('aria-expanded', 'true')
}
const expectFolded = () => {
  expect(tray()).toHaveAttribute('data-expanded', 'false')
  expect(cards()).toHaveAttribute('inert')
  expect(cards()).toHaveAttribute('aria-hidden', 'true')
  expect(toggle()).toHaveAttribute('aria-expanded', 'false')
}
/** Wait for the tray and its first card to load. */
const loaded = async () => {
  await screen.findByTestId('setup-card-hint')
  await within(cards()).findAllByTestId('setup-card')
}

const PROFILE = card({ id: 'sc-a', kind: 'profile', payload: { fields: { bot_name: 'Nova', language: 'English', role: 'SRE' } } })
const ASKED = [say('Here is what I gathered about you.'), cardRow('sc-a')]
const TRAILING = say('I’ve put a profile card on screen: check it and click Save when it looks right.')

const sizeOption = (key: string, label: string, monthly: number) => ({
  key, label, note: 'many_chats', instance_type: 't4g.xlarge', vcpu: 2, ram_gb: 8, monthly_usd: monthly, free_plan_ok: true,
})
const HOME = card({
  id: 'sc-home', kind: 'home', stakes: 'high', classic: { kind: 'route', target: '/settings' },
  payload: {
    provider: { id: 'aws_ec2', label: 'Your AWS account' }, simulated: false, region: 'us-east-1', profile: 'default',
    size: { key: 'starter', label: 'Starter', instance_type: 'm7i-flex.large', ram_gb: 8, vcpu: 2 },
    monthly_usd: 72, billed_by: 'AWS, to your own account', aws_signed_in: true, aws_account: '…1234',
    sign_in_commands: ['aws login'],
    size_options: [sizeOption('lite', 'Lite', 14), sizeOption('starter', 'Starter', 72), sizeOption('light', 'Standard', 101)],
    size_default: 'starter', plan: { type: 'PAID' },
  },
})
const HOME_ASKED = [say('Where should your crew live?'), cardRow('sc-home', 'home')]

beforeEach(() => { serveCards([PROFILE]) })

describe('a pending card arrives as a one-line hint', () => {
  it('never opens a fresh card on its own, even as the newest thing with its turn’s own text after it', async () => {
    renderSurface({ messages: [...ASKED, TRAILING] })
    await loaded()
    expectFolded()
    expect(toggle()).toHaveTextContent('Review')
  })

  it('shows the kind’s icon, the title and a summary from the payload', async () => {
    renderSurface({ messages: ASKED })
    await loaded()
    expect(hint()).toHaveTextContent('Your profile')
    expect(within(hint()).getByTestId('setup-card-hint-text')).toHaveTextContent('Nova, English, SRE')
    expect(hint().querySelector('svg.lucide-user-round')).not.toBeNull()
  })

  it('wears the shield on a high-stakes card, with its own summary', async () => {
    serveCards([HOME])
    renderSurface({ messages: HOME_ASKED })
    await loaded()
    expect(hint()).toHaveAttribute('data-stakes', 'high')
    expect(hint().querySelector('svg.lucide-shield-check')).not.toBeNull()
    expect(within(hint()).getByTestId('setup-card-hint-text')).toHaveTextContent('From $14/mo')
  })

  it('shows the title alone for a kind with no summary', async () => {
    serveCards([card({ id: 'sc-svc', kind: 'service' })])
    renderSurface({ messages: [cardRow('sc-svc', 'service')] })
    await loaded()
    expect(hint()).toHaveTextContent('Keep running in the background')
    expect(screen.queryByTestId('setup-card-hint-text')).toBeNull()
  })

  it('names the card the latest turn proposed and counts the rest', async () => {
    serveCards([
      PROFILE,
      card({ id: 'sc-cron', kind: 'cron', payload: { name: 'Morning brief', schedule_human: 'every weekday at 08:00' } }),
      card({ id: 'sc-svc', kind: 'service' }),
    ])
    renderSurface({ messages: ASKED })
    await screen.findByTestId('setup-card-hint')
    await waitFor(() => expect(screen.getByTestId('setup-card-hint-more')).toHaveTextContent('2 more'))
    expect(hint()).toHaveTextContent('Your profile')
  })

  it('shows a working card’s state: the home build’s current step', async () => {
    serveCards([{ ...HOME, status: 'waiting', outcome: { steps: [
      { key: 'a', label: 'Create the server', state: 'done' },
      { key: 'b', label: 'Start Kiro Crew', state: 'active' },
    ] } }])
    renderSurface({ messages: HOME_ASKED })
    await screen.findByTestId('setup-card-hint')
    const text = await screen.findByTestId('setup-card-hint-text')
    expect(text).toHaveTextContent('Now: Start Kiro Crew')
    expect(text).toHaveAttribute('data-state', 'busy')
    // A card at work cannot be declined from its hint.
    expect(screen.queryByTestId('setup-card-hint-decline')).toBeNull()
  })

  it('says "needs you" while the build waits on the owner’s sign-in', async () => {
    serveCards([{ ...HOME, status: 'waiting', outcome: { aws_signin: { state: 'waiting', expires_ts: Date.now() / 1000 + 600 } } }])
    renderSurface({ messages: HOME_ASKED })
    const text = await screen.findByTestId('setup-card-hint-text')
    expect(text).toHaveTextContent('needs you')
    expect(text).toHaveAttribute('data-state', 'needs-you')
  })
})

describe('Review opens the card in place; Hide folds it back', () => {
  it('opens and folds, with aria-expanded and aria-controls on the one toggle', async () => {
    renderSurface({ messages: ASKED })
    await loaded()
    expect(toggle()).toHaveAttribute('aria-controls', cards().id)
    fireEvent.click(toggle())
    expectOpen()
    expect(toggle()).toHaveTextContent('Hide')
    fireEvent.click(toggle())
    expectFolded()
  })
})

describe('several live cards: Review opens the one the latest turn proposed', () => {
  // E2E run 2: with the home card still waiting, the owner asked for a job card.
  // Review opened BOTH, stacked under the third-of-the-pane cap, and the card
  // they had just asked for sat out of frame below the older one.
  const CRON = card({
    id: 'sc-cron', kind: 'cron', created_ts: 1790000100,
    payload: { name: 'Morning brief', schedule_human: 'every weekday at 08:00' },
  })
  const BOTH = [...HOME_ASKED, say('The home card can wait; here is the brief.'), cardRow('sc-cron', 'cron')]
  const slot = (id: string) => cards().querySelector(`[data-tray-card="${id}"]`)
  const rows = () => screen.queryAllByTestId('setup-card-hint-row')

  beforeEach(() => { serveCards([HOME, CRON]) })

  it('names that card in the folded hint, counts the other, and opens only it', async () => {
    renderSurface({ messages: BOTH })
    await loaded()
    expect(hint()).toHaveAttribute('data-kind', 'cron')
    expect(screen.getByTestId('setup-card-hint-more')).toHaveTextContent('1 more')
    expect(rows()).toHaveLength(0)
    fireEvent.click(toggle())
    expectOpen()
    expect(slot('sc-cron')).not.toHaveAttribute('inert')
    expect(slot('sc-home')).toHaveAttribute('inert')
    // The older card stays a one-line hint with its own Not now and Review.
    expect(rows().map(r => r.getAttribute('data-kind'))).toEqual(['home'])
    expect(within(rows()[0]).getAllByRole('button').map(b => b.getAttribute('data-testid')))
      .toEqual(['setup-card-hint-row-decline', 'setup-card-hint-row-review'])
    expect(within(rows()[0]).queryByTestId('setup-card-primary')).toBeNull()
  })

  it('opens another card from its own hint, and the open one folds to a hint', async () => {
    renderSurface({ messages: BOTH })
    await loaded()
    fireEvent.click(toggle())
    fireEvent.click(within(rows()[0]).getByTestId('setup-card-hint-row-review'))
    expectOpen()
    expect(slot('sc-home')).not.toHaveAttribute('inert')
    expect(slot('sc-cron')).toHaveAttribute('inert')
    expect(hint()).toHaveAttribute('data-kind', 'home')
    expect(rows().map(r => r.getAttribute('data-kind'))).toEqual(['cron'])
  })

  it('declines a card from its hint while another is open', async () => {
    const gw = serveCards([HOME, CRON])
    renderSurface({ messages: BOTH })
    await loaded()
    fireEvent.click(toggle())
    fireEvent.click(within(rows()[0]).getByRole('button', { name: 'Not now: Your home in the cloud' }))
    await waitFor(() => expect(gw.decided).toEqual([{ id: 'sc-home', body: { decision: 'decline', hash: 'b'.repeat(64) } }]))
    await waitFor(() => expect(rows()).toHaveLength(0))
    expectOpen()
    expect(slot('sc-cron')).not.toHaveAttribute('inert')
  })

  it('keeps the card the owner is using open through the next turn', async () => {
    const { update } = renderSurface({ messages: BOTH })
    await loaded()
    fireEvent.click(toggle())
    fireEvent.pointerDown(within(slot('sc-cron') as HTMLElement).getByTestId('setup-card'))
    update({ messages: [...BOTH, ...NEXT_TURN] })
    expectOpen()
    expect(slot('sc-cron')).not.toHaveAttribute('inert')
  })
})

describe('latestProposedCard', () => {
  const live = [card({ id: 'sc-1', kind: 'home' }), card({ id: 'sc-2', kind: 'cron' }), card({ id: 'sc-3', kind: 'connect' })]
  it('is the newest card row whose card is still live', () => {
    expect(latestProposedCard(live, [cardRow('sc-1'), cardRow('sc-3'), cardRow('sc-2'), say('ok')])?.id).toBe('sc-2')
    expect(latestProposedCard(live, [cardRow('sc-2'), cardRow('sc-gone')])?.id).toBe('sc-2')
  })
  it('falls back to the newest live card when no row names one', () => {
    expect(latestProposedCard(live, [say('hello')])?.id).toBe('sc-3')
    expect(latestProposedCard(live)?.id).toBe('sc-3')
    expect(latestProposedCard([])).toBeUndefined()
  })
})

describe('consent stays in the open card', () => {
  it.each([
    ['a low-stakes card', PROFILE, ASKED],
    ['a high-stakes card', HOME, HOME_ASKED],
  ] as const)('the hint of %s carries no commit, only Review and Not now', async (_label, c, messages) => {
    serveCards([c])
    renderSurface({ messages: [...messages] })
    await loaded()
    expect(within(hint()).queryByTestId('setup-card-primary')).toBeNull()
    expect(within(hint()).getAllByRole('button').map(b => b.getAttribute('data-testid')))
      .toEqual(['setup-card-hint-decline', 'setup-card-tray-toggle'])
    // The card's own buttons are folded out of reach until Review.
    expect(cards()).toHaveAttribute('inert')
  })

  it('declines from the hint with the card’s hash', async () => {
    const gw = serveCards([PROFILE])
    renderSurface({ messages: ASKED })
    await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Not now: Your profile' }))
    await waitFor(() => expect(gw.decided).toEqual([{ id: 'sc-a', body: { decision: 'decline', hash: 'b'.repeat(64) } }]))
    await waitFor(() => expect(screen.queryByTestId('setup-card-tray')).toBeNull())
  })

  it('offers no Not now on a card that cannot be declined', async () => {
    serveCards([card({ id: 'sc-p', kind: 'privacy' })])
    renderSurface({ messages: [cardRow('sc-p', 'privacy')] })
    await screen.findByTestId('setup-card-hint')
    expect(screen.queryByTestId('setup-card-hint-decline')).toBeNull()
  })
})

describe('the transcript’s "in the tray below" row opens its card', () => {
  it('opens the tray and highlights the card', async () => {
    renderSurface({ messages: ASKED, rows: [cardRow('sc-a')] })
    await loaded()
    expectFolded()
    fireEvent.click(await within(screen.getByTestId('transcript')).findByTestId('setup-card-ref'))
    expectOpen()
    expect(within(cards()).getByTestId('setup-card-tray-highlight')).toBeInTheDocument()
  })
})

describe('an open card folds when the chat moves past it, unless the owner is using it', () => {
  const opened = async (props: SurfaceProps) => {
    const utils = renderSurface(props)
    await loaded()
    fireEvent.click(toggle())
    expectOpen()
    return utils
  }

  it('stays open through its own turn’s text, folds once the user writes', async () => {
    const { update } = await opened({ messages: ASKED })
    update({ messages: [...ASKED, TRAILING] })
    expectOpen()
    update({ messages: [...ASKED, TRAILING, USER_REPLY] })
    expectFolded()
  })

  it('folds when the next agent turn starts', async () => {
    const { update } = await opened({ messages: ASKED })
    update({ messages: [...ASKED, ...NEXT_TURN] })
    expectFolded()
  })

  it('holds through the next turn once the card was touched, then Hide lets go', async () => {
    const { update } = await opened({ messages: ASKED })
    fireEvent.pointerDown(within(cards()).getAllByTestId('setup-card')[0])
    update({ messages: [...ASKED, ...NEXT_TURN] })
    expectOpen()
    fireEvent.click(toggle())
    expectFolded()
    update({ messages: [...ASKED, ...NEXT_TURN, USER_REPLY] })
    expectFolded()
  })

  it('folds on an upward scroll, even a touched card', async () => {
    await opened({ messages: ASKED })
    fireEvent.pointerDown(within(cards()).getAllByTestId('setup-card')[0])
    fireEvent.wheel(screen.getByTestId('scroller'), { deltaY: -120 })
    expectFolded()
  })

  it('lets go once the touched card is decided, and the rest fold to the hint', async () => {
    serveCards([PROFILE, card({ id: 'sc-b', kind: 'service' })])
    const { qc, update } = await opened({ messages: ASKED })
    fireEvent.pointerDown(within(cards()).getAllByTestId('setup-card')[0])
    update({ messages: [...ASKED, ...NEXT_TURN] })
    expectOpen()
    act(() => { applySetupCardUpdate(qc, { ...PROFILE, status: 'committed' }, SLOT) })
    await waitFor(() => expectFolded())
    // Folded, the hint names the card left; no hint rows linger for a card that is gone.
    expect(hint()).toHaveAttribute('data-kind', 'service')
    expect(screen.queryAllByTestId('setup-card-hint-row')).toHaveLength(0)
  })
})

describe('the hint points at the card’s own row', () => {
  it('asks the host to scroll to the row, smoothly unless motion is reduced', async () => {
    const onLocate = vi.fn()
    renderSurface({ messages: ASKED, onLocate })
    const locate = await screen.findByTestId('setup-card-tray-locate')
    expect(locate).toHaveAccessibleName('Find “Your profile” in the chat')
    fireEvent.click(locate)
    expect(onLocate).toHaveBeenCalledWith('sc-a', expect.stringMatching(/^(smooth|auto)$/))
  })

  it('lights the located row up, then lets it go', () => {
    vi.useFakeTimers()
    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } })
      render(
        <QueryClientProvider client={qc}>
          <MemoryRouter><SetupCardRow cardId="sc-a" placement="transcript" /></MemoryRouter>
        </QueryClientProvider>,
      )
      expect(screen.queryByTestId('setup-card-row-highlight')).toBeNull()
      act(() => highlightSetupCardRow('sc-a'))
      expect(screen.getByTestId('setup-card-row-highlight')).toHaveClass('animate-msg-highlight')
      act(() => { vi.advanceTimersByTime(2100) })
      expect(screen.queryByTestId('setup-card-row-highlight')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('the open tray is capped at a third of its pane', () => {
  it('caps its height from the measured pane', async () => {
    renderSurface({ messages: ASKED, paneHeight: 900 })
    await loaded()
    expect(tray()).toHaveStyle({ maxHeight: '300px' })
    expect(tray()).toHaveClass('overflow-y-auto')
  })

  it('falls back to a third of the viewport where there is no pane to measure', async () => {
    renderSurface({ messages: ASKED })
    await loaded()
    expect(tray()).toHaveClass('max-h-[33dvh]')
    expect(tray().style.maxHeight).toBe('')
  })
})

describe('conversationMoves', () => {
  it('counts the user writing, a later turn opening and a notice, never the turn’s own rows', () => {
    expect(conversationMoves([...ASKED, TRAILING, { role: 'tool', cls: '', content: '🔧 x' } as ChatMessage])).toBe(0)
    expect(conversationMoves([...ASKED, USER_REPLY])).toBe(1)
    expect(conversationMoves([...ASKED, { ...USER_REPLY, meta: { steer: true } }])).toBe(1)
    expect(conversationMoves([...ASKED, ...NEXT_TURN])).toBe(1)
    expect(conversationMoves([...ASKED, { role: 'nudge', cls: '', content: 'auto-nudge' } as ChatMessage])).toBe(1)
    expect(conversationMoves([...ASKED, { role: 'notice', cls: '', content: 'Paused' } as ChatMessage])).toBe(1)
    expect(conversationMoves([...ASKED, { ...say('The brief finished.'), meta: { kind: 'handoff_done' } }])).toBe(1)
  })
})
