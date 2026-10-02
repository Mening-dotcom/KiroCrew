/**
 * The first run's scripted steps (UX.2, UX.3): the cards and messages the
 * gateway shows before any model can answer, and the composer lock that holds
 * until they are done. Driven through MSW at the network boundary, like
 * SetupCard.test.tsx: every assertion is about what the gateway was sent and
 * what the owner sees.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupCard from '../components/setup/SetupCard'
import ChatInput from '../components/ChatInput'
import { scriptedLockReason } from '../components/setup/scriptedLock'
import { cardDeclinableFromHint } from '../components/setup/setupCardRegistry'
import { mergeRenderers, resolveRenderer, type MessageRenderContext } from '../app-sdk/messageRenderers'
import { createTranscriptRenderers } from '../pages/chat/transcriptRenderers'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'
import type { ChatMessage } from '../types'
import { renderWithProviders } from './helpers'

const HASH = 'b'.repeat(64)
const ID = 'sc-00000000000000aa'

function card(over: Partial<Card> & Pick<Card, 'kind'>): Card {
  return {
    id: ID,
    slot: 'chat-1-1790000000',
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

function serveCard(initial: Card) {
  const state = { card: initial, bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${ID}`, () => HttpResponse.json(state.card)),
    http.post(`/api/setup/cards/${ID}/decide`, async ({ request }) => {
      state.bodies.push((await request.json()) as SetupDecideBody)
      return HttpResponse.json({ ...state.card, status: 'committed' })
    }),
  )
  return state
}

function serveKiro(status: Record<string, unknown>) {
  server.use(http.get('/api/kiro-prerequisite', () => HttpResponse.json({
    platform: 'Linux', ready: false, initial_setup_complete: false, repair_required: false,
    docs_url: 'https://kiro.dev/cli/', install_command: 'curl -fsSL https://cli.kiro.dev/install | bash',
    login_command: 'kiro-cli login', sso_login_command: 'kiro-cli login --use-device-flow --license pro',
    bundled_cli: false, setup_allowed: true, installed: false, authenticated: false, ...status,
  })))
}

function serveBackends(rows: Array<Record<string, unknown>>) {
  server.use(http.get('/api/acp-backends', () => HttpResponse.json({ backends: rows })))
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><SetupCard cardId={ID} /></MemoryRouter>
    </QueryClientProvider>,
  )
  return screen.findByTestId('setup-card')
}

afterEach(() => vi.restoreAllMocks())

const HARNESS_PAYLOAD = {
  options: [{ id: '', label: 'Kiro CLI' }, { id: 'claude', label: 'Claude Code' }],
  current: '',
  default: '',
}

describe('the harness card', () => {
  it('offers the gateway list with Kiro as the default, and commits the pick', async () => {
    serveBackends([
      { id: '', policy_id: 'kiro', selectable: true, installed: 'installed', missing_components: [], install_command: '', restart_required: false },
      { id: 'claude', policy_id: 'claude', selectable: true, installed: 'missing', missing_components: ['claude-agent-acp'], install_command: 'npm i -g x', restart_required: false },
    ])
    const gw = serveCard(card({ kind: 'harness', payload: HARNESS_PAYLOAD }))
    const el = await renderCard()
    expect(within(el).getByRole('heading', { name: 'Choose your agent engine' })).toBeInTheDocument()
    const options = within(el).getAllByTestId('setup-card-harness-option')
    expect(options.map(o => o.getAttribute('data-harness'))).toEqual(['', 'claude'])
    expect(within(options[0]).getByText('Default')).toBeInTheDocument()
    await waitFor(() => expect(within(options[1]).getByText('Not installed yet')).toBeInTheDocument())
    // Mandatory: no Not now, here or on its tray hint.
    expect(screen.queryByTestId('setup-card-decline')).toBeNull()
    expect(cardDeclinableFromHint(card({ kind: 'harness' }))).toBe(false)
    await userEvent.click(within(options[1]).getByRole('radio'))
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH, input: { backend: 'claude' } }]))
  })
})

describe('the sign-in card, for the harnesses that run kiro-cli', () => {
  const signin = (over: Partial<Card> = {}) => card({
    kind: 'harness_signin',
    payload: { backend: '', label: 'Kiro CLI', flow: 'kiro_cli' },
    ...over,
  })

  it('names the install command and keeps Continue unlit until the live status says ready', async () => {
    serveKiro({ installed: false })
    const gw = serveCard(signin())
    const el = await renderCard()
    expect(within(el).getByText('Set up Kiro CLI')).toBeInTheDocument()
    expect(await within(el).findByTestId('setup-card-install-command')).toHaveTextContent('curl -fsSL https://cli.kiro.dev/install | bash')
    const button = screen.getByTestId('setup-card-primary')
    expect(button).toHaveTextContent('Check again')
    expect(button).not.toHaveAttribute('data-lit')
    expect(screen.queryByTestId('setup-card-signin-skip')).toBeNull()
    await userEvent.click(button)
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH }]))
  })

  it('lights Continue once kiro-cli is installed and signed in', async () => {
    serveKiro({ installed: true, authenticated: true, ready: true })
    serveCard(signin())
    await renderCard()
    const button = screen.getByTestId('setup-card-primary')
    await waitFor(() => expect(button).toHaveAttribute('data-lit', 'true'))
    expect(button).toHaveTextContent('Continue')
  })

  it('shows the sign-in commands once installed, with how a headless host finishes', async () => {
    serveKiro({ installed: true, authenticated: false })
    serveCard(signin())
    const el = await renderCard()
    expect(await within(el).findByTestId('setup-card-login-command')).toHaveTextContent('kiro-cli login')
    expect(within(el).getByText(/prints a link and a code instead/)).toBeInTheDocument()
  })

  it('offers continuing without a check only after one failed', async () => {
    serveKiro({ installed: true, authenticated: false })
    const gw = serveCard(signin({ error: { code: 'harness_not_signed_in', message: 'nope' } }))
    await renderCard()
    expect(screen.getByText(/isn't signed in yet/)).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('setup-card-signin-skip'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH, input: { skip: true } }]))
  })
})

describe('the sign-in card, for any other harness', () => {
  it('shows its own sign-in sentence verbatim and its install command', async () => {
    serveBackends([{ id: 'codex', policy_id: 'codex', selectable: true, installed: 'missing', missing_components: ['codex-acp'], install_command: 'npm i -g codex-acp', restart_required: false }])
    serveCard(card({
      kind: 'harness_signin',
      payload: { backend: 'codex', label: 'OpenAI Codex', flow: 'own', install_command: '', sign_in: 'Run codex login once.' },
    }))
    const el = await renderCard()
    expect(await within(el).findByTestId('setup-card-install-command')).toHaveTextContent('npm i -g codex-acp')
    expect(within(el).getByText('Run codex login once.')).toBeInTheDocument()
    expect(within(el).getByText(/can't see whether you're signed in to OpenAI Codex/)).toBeInTheDocument()
    expect(screen.getByTestId('setup-card-primary')).not.toHaveAttribute('data-lit')
  })

  it('is lit once installed: only starting it can tell whether it is signed in', async () => {
    serveBackends([{ id: 'codex', policy_id: 'codex', selectable: true, installed: 'installed', missing_components: [], install_command: '', restart_required: false }])
    serveCard(card({ kind: 'harness_signin', payload: { backend: 'codex', label: 'OpenAI Codex', flow: 'own', sign_in: 'x' } }))
    await renderCard()
    await waitFor(() => expect(screen.getByTestId('setup-card-primary')).toHaveAttribute('data-lit', 'true'))
  })
})

describe('the path card', () => {
  it('needs a choice, then commits it', async () => {
    const gw = serveCard(card({ kind: 'path', payload: { options: ['tips', 'detailed'] } }))
    const el = await renderCard()
    expect(within(el).getByRole('heading', { name: 'How would you like to start?' })).toBeInTheDocument()
    expect(screen.getByTestId('setup-card-primary')).toBeDisabled()
    await userEvent.click(within(screen.getByTestId('setup-card-path-tips')).getByRole('radio'))
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH, input: { path: 'tips' } }]))
  })
})

describe('the step messages', () => {
  const ctx = (row: ChatMessage): MessageRenderContext => ({
    index: 0, messages: [row], running: false, key: 'k0', hideCardOwnedOAuth: false,
    autoDeniedIds: new Set(), wrapper: c => c, row: c => c,
  })
  const draw = (meta: Record<string, unknown>) => {
    const row = { role: 'inject', cls: '', content: '(Setup step shown to the user: MODEL TEXT)', meta } as ChatMessage
    const entry = resolveRenderer(row, mergeRenderers(createTranscriptRenderers({ slot: 's1' })))!
    expect(entry.id).toBe('setup_step')
    return render(<>{entry.render(row, ctx(row))}</>)
  }

  it('draws the welcome in the owner language, never the model breadcrumb', () => {
    draw({ setupStep: { step: 'welcome', card: ID } })
    expect(screen.getByTestId('setup-step-message')).toHaveTextContent(/Welcome to Kiro Crew\. Your crew needs an agent engine first/)
    expect(screen.queryByText(/MODEL TEXT/)).toBeNull()
  })

  it('names the engine on the sign-in steps', () => {
    draw({ setupStep: { step: 'signin_again', card: ID, label: 'Claude Code' } })
    expect(screen.getByTestId('setup-step-message')).toHaveTextContent(/Claude Code couldn't sign in/)
  })

  it('draws nothing for a step this build does not know', () => {
    const { container } = draw({ setupStep: { step: 'from-the-future', card: ID } })
    expect(container.textContent).toBe('')
  })
})

describe('the composer lock', () => {
  it('names the step the first-run chat waits on, and nothing once it is decided', () => {
    expect(scriptedLockReason([card({ kind: 'harness' })])).toBe('Pick an agent engine above, then you can chat.')
    expect(scriptedLockReason([card({ kind: 'harness_signin', payload: { label: 'Kiro CLI' } })]))
      .toBe('You can chat once Kiro CLI is signed in.')
    expect(scriptedLockReason([card({ kind: 'privacy' })])).toBe('Choose your privacy setting above to continue.')
    expect(scriptedLockReason([card({ kind: 'path' })])).toBe("Pick how you'd like to start above.")
    expect(scriptedLockReason([card({ kind: 'path', status: 'committed' })])).toBe('')
    // An agent's own card never locks the chat: a model can answer it.
    expect(scriptedLockReason([card({ kind: 'profile' })])).toBe('')
    expect(scriptedLockReason(undefined)).toBe('')
  })

  it('disables the composer and says why', () => {
    renderWithProviders(<ChatInput value="" onChange={vi.fn()} onSend={vi.fn()} lockedReason="Pick an agent engine above, then you can chat." />)
    const box = screen.getByPlaceholderText('Pick an agent engine above, then you can chat.')
    expect(box).toHaveAttribute('aria-disabled', 'true')
    expect(box).toHaveAttribute('readonly')
    expect(screen.getByLabelText('Send')).toBeDisabled()
  })
})
