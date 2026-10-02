/**
 * The "needs your decision" tray (PendingSetupCards) and its transcript rows.
 *
 * The agent keeps writing after proposing a card, so the card's own row
 * scrolls away; the tray pins every LIVE card of the slot above the composer
 * and the row folds to a one-line pointer, so the full card is on screen
 * exactly once. Driven through MSW with the real SetupCard and the real
 * transcript registry entry.
 */
import { describe, it, expect } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import PendingSetupCards from '../components/setup/PendingSetupCards'
import { applySetupCardUpdate, type SetupCard as Card, type SetupDecideBody } from '../api/setupCards'
import { mergeRenderers, resolveRenderer, type MessageRenderContext } from '../app-sdk/messageRenderers'
import { createTranscriptRenderers } from '../pages/chat/transcriptRenderers'
import type { ChatMessage } from '../types'

const SLOT = 'chat-1-1790000000'
const HASH = 'b'.repeat(64)

function card(over: Partial<Card> & Pick<Card, 'id' | 'kind'>): Card {
  return {
    slot: SLOT,
    status: 'pending',
    stakes: 'low',
    hash: HASH,
    payload: {},
    outcome: null,
    error: null,
    created_ts: 1790000000,
    decided_ts: null,
    classic: { kind: 'none', target: '' },
    ...over,
  }
}

/** A tiny gateway: the slot list, each card, and decide (commits by default). */
function serveGateway(initial: Card[], opts: { listStatus?: number } = {}) {
  const cards = new Map(initial.map(c => [c.id, c]))
  const bodies: Array<{ id: string; body: SetupDecideBody }> = []
  const seen = { listGets: 0 }
  server.use(
    http.get('/api/setup/cards', ({ request }) => {
      seen.listGets += 1
      if (opts.listStatus) return HttpResponse.json({ error: 'nope', code: 'owner_required' }, { status: opts.listStatus })
      const slot = new URL(request.url).searchParams.get('slot')
      return HttpResponse.json({ cards: [...cards.values()].filter(c => c.slot === slot) })
    }),
    http.get('/api/setup/cards/:id', ({ params }) => {
      const c = cards.get(String(params.id))
      return c ? HttpResponse.json(c) : HttpResponse.json({ error: 'x', code: 'card_not_found' }, { status: 404 })
    }),
    http.post('/api/setup/cards/:id/decide', async ({ params, request }) => {
      const id = String(params.id)
      const body = (await request.json()) as SetupDecideBody
      bodies.push({ id, body })
      const next = { ...cards.get(id)!, status: body.decision === 'decline' ? 'declined' as const : 'committed' as const }
      cards.set(id, next)
      return HttpResponse.json(next)
    }),
  )
  return { cards, bodies, seen }
}

/** The tray plus the transcript rows of `ids`, the way ChatPage composes them. */
function renderSurface(ids: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } })
  const registry = mergeRenderers(createTranscriptRenderers({ slot: SLOT, setupCardTray: true }))
  const rows = ids.map(id => ({
    role: 'inject', cls: '', content: 'model-visible summary', meta: { setupCard: { id, kind: 'profile' } },
  }) as ChatMessage)
  const ctx = (i: number): MessageRenderContext => ({
    index: i, messages: rows, running: false, key: `k${i}`, hideCardOwnedOAuth: false,
    autoDeniedIds: new Set(), wrapper: c => c, row: c => c,
  })
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <div data-testid="transcript">
          {rows.map((m, i) => <div key={i}>{resolveRenderer(m, registry)!.render(m, ctx(i))}</div>)}
        </div>
        <PendingSetupCards slotKey={SLOT} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { qc, ...utils }
}

describe('PendingSetupCards — the tray', () => {
  it('holds every live card of the slot, oldest first, and none of the decided ones', async () => {
    serveGateway([
      card({ id: 'sc-a', kind: 'profile', payload: { fields: { role: 'SRE' } } }),
      card({ id: 'sc-b', kind: 'profile', status: 'committed' }),
      card({ id: 'sc-c', kind: 'cron', status: 'working', payload: { name: 'n' } }),
      card({ id: 'sc-d', kind: 'profile', status: 'declined' }),
      card({ id: 'sc-e', kind: 'connect', stakes: 'high', status: 'waiting', payload: { provider: { name: 'GitHub' } }, outcome: { state: 'minting' } }),
    ])
    renderSurface([])
    const tray = await screen.findByTestId('setup-card-tray')
    await waitFor(() => expect(within(tray).getAllByTestId('setup-card')).toHaveLength(3))
    expect(within(tray).getAllByTestId('setup-card').map(n => n.getAttribute('data-status')))
      .toEqual(['pending', 'working', 'waiting'])
    expect(tray).toHaveAccessibleName('Needs your decision')
  })

  it('renders nothing when every card is decided', async () => {
    const gw = serveGateway([card({ id: 'sc-a', kind: 'profile', status: 'committed' })])
    renderSurface([])
    // Wait for the list to have been read, then for its response to settle.
    await waitFor(() => expect(gw.seen.listGets).toBe(1))
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(screen.queryByTestId('setup-card-tray')).toBeNull()
  })

  it('picks up a card the WebSocket reports after the list loaded', async () => {
    serveGateway([card({ id: 'sc-a', kind: 'profile', payload: { fields: { role: 'SRE' } } })])
    const { qc } = renderSurface([])
    const tray = await screen.findByTestId('setup-card-tray')
    await waitFor(() => expect(within(tray).getAllByTestId('setup-card')).toHaveLength(1))
    act(() => {
      applySetupCardUpdate(qc, card({ id: 'sc-new', kind: 'profile', payload: { fields: { role: 'PM' } } }), SLOT)
    })
    await waitFor(() => expect(within(tray).getAllByTestId('setup-card')).toHaveLength(2))
  })

  it('draws a long home build compactly: the current step, not the whole list', async () => {
    serveGateway([card({
      id: 'sc-home', kind: 'home', stakes: 'high', status: 'waiting',
      payload: { provider: { label: 'Your AWS account' } },
      outcome: { steps: [
        { key: 'a', label: 'Create the server', state: 'done' },
        { key: 'b', label: 'Start Kiro Crew', state: 'active' },
        { key: 'c', label: 'Connect it', state: 'pending' },
      ] },
    })])
    renderSurface([])
    const compact = await screen.findByTestId('setup-card-home-compact')
    expect(compact).toHaveTextContent('Building in the background')
    expect(compact).toHaveTextContent('Now: Start Kiro Crew')
    expect(screen.queryByTestId('setup-card-steps')).toBeNull()
  })
})

describe('PendingSetupCards — one card on screen, the row points at it', () => {
  it('folds the live card’s transcript row to a pointer, then to the result line once decided in the tray', async () => {
    const gw = serveGateway([card({ id: 'sc-a', kind: 'profile', payload: { fields: { role: 'SRE' } } })])
    renderSurface(['sc-a'])
    const tray = await screen.findByTestId('setup-card-tray')
    // The row is the one-line pointer; the full card exists once, in the tray.
    const ref = await within(screen.getByTestId('transcript')).findByTestId('setup-card-ref')
    expect(ref).toHaveTextContent('Your profile — waiting for your decision below')
    expect(screen.getAllByTestId('setup-card')).toHaveLength(1)
    expect(within(tray).getByTestId('setup-card')).toBeInTheDocument()
    expect(screen.queryByText('model-visible summary')).toBeNull()

    // The card arrives folded to its hint: Review opens it, then deciding in
    // the tray posts the decision with the card's hash…
    expect(within(tray).getByTestId('setup-card-tray-cards')).toHaveAttribute('inert')
    await userEvent.click(within(tray).getByTestId('setup-card-tray-toggle'))
    await userEvent.click(within(tray).getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ id: 'sc-a', body: { decision: 'commit', hash: HASH } }]))
    // …the tray empties, and the row becomes the compact result line.
    await waitFor(() => expect(screen.queryByTestId('setup-card-tray')).toBeNull())
    const transcript = screen.getByTestId('transcript')
    await waitFor(() => expect(within(transcript).getByTestId('setup-card')).toHaveAttribute('data-status', 'committed'))
    expect(within(transcript).getByTestId('setup-card-result')).toHaveTextContent('Done')
    expect(screen.queryByTestId('setup-card-ref')).toBeNull()
    expect(screen.getAllByTestId('setup-card')).toHaveLength(1)
  })

  it('"Not now" in the tray declines it the same way', async () => {
    const gw = serveGateway([card({ id: 'sc-a', kind: 'profile', payload: { fields: { role: 'SRE' } } })])
    renderSurface(['sc-a'])
    const tray = await screen.findByTestId('setup-card-tray')
    await userEvent.click(await within(tray).findByTestId('setup-card-tray-toggle'))
    await userEvent.click(await within(tray).findByTestId('setup-card-decline'))
    await waitFor(() => expect(gw.bodies[0]?.body).toEqual({ decision: 'decline', hash: HASH }))
    await waitFor(() => expect(screen.queryByTestId('setup-card-tray')).toBeNull())
  })

  it('keeps the full card in the transcript when the tray could not load its list', async () => {
    serveGateway([card({ id: 'sc-a', kind: 'profile', payload: { fields: { role: 'SRE' } } })], { listStatus: 403 })
    renderSurface(['sc-a'])
    const full = await within(screen.getByTestId('transcript')).findByTestId('setup-card')
    expect(full).toHaveAttribute('data-status', 'pending')
    expect(within(full).getByTestId('setup-card-primary')).toBeInTheDocument()
    expect(screen.queryByTestId('setup-card-tray')).toBeNull()
    expect(screen.queryByTestId('setup-card-ref')).toBeNull()
  })
})
