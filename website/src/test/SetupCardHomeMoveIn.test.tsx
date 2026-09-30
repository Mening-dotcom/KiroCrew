/**
 * The home card after a LIVE move-in (dashboard/setup_move_in.py):
 *
 * - while the move is `working`, each move step shows its `detail` under it;
 * - a refused move comes back `pending` with the error in the owner's language
 *   and the move's steps (the failed one carrying its reason) beside the retry;
 * - a committed live move says what happened, from the outcome: where the chat
 *   now continues (with "Open your home"), the schedules that moved, the ones
 *   that stayed and why, whether the home kept its own settings, and the secret
 *   and connection NAMES to set up again there.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import { createTestStore } from './helpers'
import SetupCard from '../components/setup/SetupCard'
import type { SetupCard as Card } from '../api/setupCards'
import { setupCardQueryKey } from '../api/setupCards'
import { setActiveId } from '../store/instancesSlice'
import { useHomeMoveHandoff } from '../hooks/useHomeMoveHandoff'

// "Open your home" selects the instance and connects it; the connect itself
// (tunnel + token mint) is the crew switcher's and is not under test here.
vi.mock('../lib/connectInstance', () => ({ connectInstanceInto: vi.fn().mockResolvedValue(undefined) }))

const HASH = 'c'.repeat(64)
const PAYLOAD = {
  provider: { id: 'aws_ec2', label: 'Your AWS account' },
  simulated: false, region: 'eu-west-1', profile: 'default',
  size: { key: 'balanced', label: 'Development', instance_type: 't3.large', ram_gb: 8, vcpu: 2 },
  monthly_usd: 61, billed_by: 'AWS, to your own account', aws_signed_in: true, aws_account: '…1234',
  sign_in_commands: [],
}

function home(over: Partial<Card> = {}): Card {
  return {
    id: 'sc-home0123456789ab', slot: 'chat-1-1790000000', kind: 'home', status: 'pending', stakes: 'high',
    hash: HASH, payload: PAYLOAD, outcome: null, error: null, created_ts: 1790000000, decided_ts: null,
    classic: { kind: 'route', target: '/settings' },
    ...over,
  }
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname}</div>
}

function Handoff() {
  useHomeMoveHandoff([])
  return null
}

function renderHomeCard(card: Card) {
  server.use(http.get(`/api/setup/cards/${card.id}`, () => HttpResponse.json(card)))
  const store = createTestStore()
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <Provider store={store}>
        <MemoryRouter initialEntries={['/chat']}>
          <Routes>
            <Route path="*" element={<><Handoff /><SetupCard cardId={card.id} /><LocationProbe /></>} />
          </Routes>
        </MemoryRouter>
      </Provider>
    </QueryClientProvider>,
  )
  return { store, qc, ...utils }
}

const MOVE_STEPS = [
  { key: 'reach', label: 'Reach your home', state: 'done', detail: 'nova-home' },
  { key: 'pack', label: 'Pack memory, schedules, skills, settings and persona', state: 'done', detail: '2 schedules to hand over, 1 stay here.' },
  { key: 'carry', label: 'Carry them to your home and unpack them there', state: 'failed', detail: 'Your home refused the move (import_failed), so the schedules still run here; press Move in again' },
  { key: 'chat', label: 'Bring this chat along', state: 'pending', detail: '' },
]

beforeEach(() => {
  server.use(http.get('/api/instances', () => HttpResponse.json({
    active: true, warm_set_cap: 3, sso: {},
    instances: [{ id: 'inst-1', name: 'nova-home', status: { state: 'disconnected' } }],
  })))
})

describe('home card — a live move-in', () => {
  it('an arrived receipt confirms the move without offering to open this same home', async () => {
    const { store } = renderHomeCard(home({ status: 'committed', historical: true, outcome: {
      moved: true, arrived: true, settings_moved: true,
      home: { name: 'nova-home', remote_key: 'chat-9-1' },
    } }))
    expect(await screen.findByTestId('setup-card-home-moved')).toHaveTextContent('nova-home')
    expect(screen.queryByTestId('setup-card-home-open')).toBeNull()
    expect(store.getState().instances.activeId).toBeNull()
  })

  it('switches this window to the copied main chat as the watched move finishes', async () => {
    const card = home({ status: 'waiting' })
    const { store, qc } = renderHomeCard(card)
    await waitFor(() => expect(qc.getQueryData(setupCardQueryKey(card.id))).toBeDefined())
    await act(async () => {
      qc.setQueryData(setupCardQueryKey(card.id), home({ status: 'committed', outcome: {
        moved: true, home: { instance_id: 'inst-1', remote_key: 'chat-9-1', name: 'nova-home' },
      } }))
    })
    await waitFor(() => expect(store.getState().instances.activeId).toBe('inst-1'))
    expect(store.getState().instances.openSessions).toEqual({ 'inst-1': 'chat-9-1' })
    act(() => { store.dispatch(setActiveId(null)) })
    expect(store.getState().instances.activeId).toBeNull()
  })

  it('an old move confirmation does not switch crews but opens the exact chat on click', async () => {
    const { store } = renderHomeCard(home({ status: 'committed', outcome: {
      moved: true, home: { instance_id: 'inst-1', remote_key: 'chat-9-1', name: 'nova-home' },
    } }))
    const open = await screen.findByTestId('setup-card-home-open')
    expect(store.getState().instances.activeId).toBeNull()
    await userEvent.click(open)
    expect(store.getState().instances.openSessions).toEqual({ 'inst-1': 'chat-9-1' })
  })

  it('a finished move does not steal focus from another crew', async () => {
    const card = home({ status: 'waiting' })
    const { store, qc } = renderHomeCard(card)
    await waitFor(() => expect(qc.getQueryData(setupCardQueryKey(card.id))).toBeDefined())
    await act(async () => {
      store.dispatch(setActiveId('another-crew'))
      qc.setQueryData(setupCardQueryKey(card.id), home({ status: 'committed', outcome: {
        moved: true, home: { instance_id: 'inst-1', remote_key: 'chat-9-1' },
      } }))
    })
    await screen.findByTestId('setup-card-home-open')
    expect(store.getState().instances.activeId).toBe('another-crew')
  })
  it('shows each move step’s detail under it while the move is working', async () => {
    renderHomeCard(home({
      status: 'working',
      outcome: { ready: true, move_steps: [
        { key: 'reach', label: 'Reach your home', state: 'done', detail: 'nova-home' },
        { key: 'pack', label: 'Pack memory, schedules, skills, settings and persona', state: 'active', detail: '' },
      ] },
    }))
    const steps = await screen.findByTestId('setup-card-move-steps')
    const items = within(steps).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Reach your home')
    expect(items[0]).toHaveTextContent('nova-home')
    expect(items[1]).toHaveAttribute('data-state', 'active')
  })

  it('a refused move is back to pending: the error in the owner’s words, and the failed step with its reason', async () => {
    renderHomeCard(home({
      outcome: { ready: true, move_steps: MOVE_STEPS },
      error: { code: 'move_in_carry_refused', message: 'your home refused the move (import_failed), …' },
    }))
    expect(await screen.findByTestId('setup-card-error'))
      .toHaveTextContent('Your home refused the move, so the schedules still run here. Press Move in again.')
    const steps = screen.getByTestId('setup-card-move-steps')
    const failed = within(steps).getAllByRole('listitem').find(li => li.getAttribute('data-state') === 'failed')!
    // The failed step keeps the server's specifics (the home's refusal code).
    expect(failed).toHaveTextContent('import_failed')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Move in')
  })

  it.each([
    ['move_in_restarting', 'Kiro Crew restarts now; press Move in again when the chat is back.'],
    ['move_in_instances_off', 'Remote Crew is off'],
    ['move_in_home_not_registered', 'Your home is not in Your crews.'],
    ['move_in_home_unsupported', 'This home runs no dashboard'],
    ['move_in_unreachable', 'Your home could not be reached.'],
    ['move_in_jobs_busy', 'could not be paused for the move'],
    ['move_in_carry_failed', 'Your home did not confirm the move'],
    ['move_in_chat_missing', 'This chat is not open'],
    ['move_in_chat_not_persistent', 'This chat keeps no transcript'],
    ['move_in_chat_busy', 'could not be copied cleanly just now, and nothing else has moved yet'],
    ['move_in_chat_failed', 'Your home did not take this chat, and nothing else has moved yet.'],
    ['move_in_failed', 'Something went wrong while moving.'],
    ['move_in_interrupted', 'The move was interrupted.'],
  ])('says %s in the owner’s language', async (code, text) => {
    renderHomeCard(home({ outcome: { ready: true }, error: { code, message: 'server text' } }))
    expect(await screen.findByTestId('setup-card-error')).toHaveTextContent(text)
  })

  it('a committed live move says where the chat is, what moved, what stayed and what to set up again', async () => {
    const { store } = renderHomeCard(home({
      status: 'committed',
      outcome: {
        moved: true,
        move_steps: MOVE_STEPS.map(s => ({ ...s, state: 'done' })),
        home: { instance_id: 'inst-1', name: 'nova-home', remote_key: 'chat-9-1', messages: 12 },
        jobs_moved: [{ id: 'j1', name: 'Morning brief' }, { id: 'j2', name: 'PR watch' }],
        jobs_kept_here: [
          { name: 'Backup photos', reason: 'it runs a command or script on this computer' },
          { name: 'Odd one', reason: 'some future reason' },
        ],
        carried: ['memory (3)'],
        settings_moved: false,
        reenter: { secrets: ['GITHUB_TOKEN', 'SLACK_BOT_TOKEN'], connections: ['GitHub'] },
      },
    }))
    const moved = await screen.findByTestId('setup-card-home-moved')
    expect(moved).toHaveTextContent('This chat now continues on your home, nova-home.')
    expect(screen.getByTestId('setup-card-home-jobs-moved'))
      .toHaveTextContent('Now run on your home, off here: Morning brief and PR watch')
    expect(screen.getByTestId('setup-card-home-jobs-kept')).toHaveTextContent(
      'Still run here: Backup photos (it runs a command or script on this computer) and Odd one (some future reason)',
    )
    expect(screen.getByTestId('setup-card-home-settings-kept')).toHaveTextContent('Your home kept its own settings.')
    expect(screen.getByTestId('setup-card-home-reenter-secrets'))
      .toHaveTextContent('Secrets to enter again on your home: GITHUB_TOKEN and SLACK_BOT_TOKEN')
    expect(screen.getByTestId('setup-card-home-reenter-connections'))
      .toHaveTextContent('Connections to set up again on your home: GitHub')
    // Open your home: the crew switcher's own select-and-connect.
    await userEvent.click(screen.getByTestId('setup-card-home-open'))
    await waitFor(() => expect(store.getState().instances.activeId).toBe('inst-1'))
  })

  it('leaves out what did not happen, and bounds long name lists', async () => {
    const secrets = Array.from({ length: 9 }, (_, i) => `SECRET_${i + 1}`)
    renderHomeCard(home({
      status: 'committed',
      outcome: {
        moved: true, home: { instance_id: 'inst-1', name: 'nova-home' },
        jobs_moved: [], jobs_kept_here: [], settings_moved: true,
        reenter: { secrets, connections: [] },
      },
    }))
    await screen.findByTestId('setup-card-home-moved')
    expect(screen.queryByTestId('setup-card-home-jobs-moved')).toBeNull()
    expect(screen.queryByTestId('setup-card-home-jobs-kept')).toBeNull()
    expect(screen.queryByTestId('setup-card-home-settings-kept')).toBeNull()
    expect(screen.queryByTestId('setup-card-home-reenter-connections')).toBeNull()
    expect(screen.getByTestId('setup-card-home-reenter-secrets')).toHaveTextContent('SECRET_6, and +3')
    expect(screen.getByTestId('setup-card-home-reenter-secrets')).not.toHaveTextContent('SECRET_7')
  })

  it('a simulated move keeps its one-line note and no live detail', async () => {
    renderHomeCard(home({ status: 'committed', outcome: { moved: true, simulated: true } }))
    expect(await screen.findByTestId('setup-card-result-detail')).toHaveTextContent('Simulated move')
    expect(screen.queryByTestId('setup-card-home-moved')).toBeNull()
  })

  const RECONNECT = [
    { purpose: 'open', command: 'kirocrew cloud connect --tag kc-4d5e6f --region eu-west-1' },
    { purpose: 'stop', command: 'kirocrew cloud stop --tag kc-4d5e6f --region eu-west-1' },
    { purpose: 'start', command: 'kirocrew cloud start --tag kc-4d5e6f --region eu-west-1' },
    { purpose: 'status', command: 'kirocrew cloud status --tag kc-4d5e6f --region eu-west-1' },
    { purpose: 'list', command: 'kirocrew cloud list --region eu-west-1' },
    { purpose: 'someday', command: 'kirocrew cloud future-verb' },
  ]

  it('"Next time": the open command with a copy button, the rest behind a disclosure, and another computer', async () => {
    renderHomeCard(home({
      status: 'committed',
      outcome: { moved: true, home: { instance_id: 'inst-1', name: 'nova-home' }, reconnect: RECONNECT },
    }))
    const next = await screen.findByTestId('setup-card-home-next-time')
    expect(next).toHaveTextContent('Next time')
    expect(screen.getByTestId('setup-card-home-next-time-open'))
      .toHaveTextContent('kirocrew cloud connect --tag kc-4d5e6f --region eu-west-1')
    expect(screen.getByTestId('setup-card-home-next-time-open-copy')).toHaveAccessibleName('Copy command')
    const more = screen.getByTestId('setup-card-home-next-time-more')
    expect(more.tagName).toBe('DETAILS')
    expect(more).not.toHaveAttribute('open')
    expect(within(more).getByText('More commands')).toBeInTheDocument()
    expect(within(more).getByText('Pause billing')).toBeInTheDocument()
    expect(within(more).getByTestId('setup-card-home-next-time-stop'))
      .toHaveTextContent('kirocrew cloud stop --tag kc-4d5e6f --region eu-west-1')
    expect(within(more).getByTestId('setup-card-home-next-time-list'))
      .toHaveTextContent('kirocrew cloud list --region eu-west-1')
    // A purpose this build does not know is not shown.
    expect(next).not.toHaveTextContent('future-verb')
    expect(screen.getByTestId('setup-card-home-next-time-elsewhere')).toHaveTextContent(
      'On another computer, install Kiro Crew with the one-line setup and sign in to AWS first.',
    )
    expect(screen.queryByTestId('setup-card-home-next-time-simulated')).toBeNull()
  })

  it('a simulated move shows the commands too, saying plainly they will not find a home', async () => {
    renderHomeCard(home({ status: 'committed', outcome: { moved: true, simulated: true, reconnect: RECONNECT } }))
    expect(await screen.findByTestId('setup-card-result-detail')).toHaveTextContent('Simulated move')
    expect(screen.getByTestId('setup-card-home-next-time-simulated'))
      .toHaveTextContent('These commands are simulated and will not find a home.')
    expect(screen.getByTestId('setup-card-home-next-time-open'))
      .toHaveTextContent('kirocrew cloud connect --tag kc-4d5e6f --region eu-west-1')
    expect(screen.queryByTestId('setup-card-home-moved')).toBeNull()
  })

  it('no reconnect commands, no "Next time"', async () => {
    renderHomeCard(home({ status: 'committed', outcome: { moved: true, home: { name: 'nova-home' }, reconnect: [] } }))
    await screen.findByTestId('setup-card-home-moved')
    expect(screen.queryByTestId('setup-card-home-next-time')).toBeNull()
  })

  it('without an instance id, "Open your home" goes to Your crews', async () => {
    renderHomeCard(home({ status: 'committed', outcome: { moved: true, home: { name: 'nova-home' } } }))
    await userEvent.click(await screen.findByTestId('setup-card-home-open'))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/settings/instances'))
  })
})
