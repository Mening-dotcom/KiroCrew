/**
 * The first run's "Where should your crew live?" step: the home card the gateway
 * shows on its own when the first job is kept (`payload.offer`, `step: "choose"`).
 * It asks with two radio rows and Continue ("This machine" settles it as
 * "Staying on this machine"; "In the cloud" moves the same card on to its AWS
 * steps and sizes). Without `offer` (a `--home cloud` card, an agent's proposal)
 * and once a build starts, the card is the home as before.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupCard from '../components/setup/SetupCard'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'

const HASH = '1'.repeat(64)
const PAYLOAD = {
  provider: { id: 'aws_ec2', label: 'Your AWS account' },
  simulated: false, region: 'eu-west-1', profile: 'default',
  size: { key: 'light', label: 'Light', instance_type: 't3.small', ram_gb: 2, vcpu: 2 },
  monthly_usd: 17, billed_by: 'AWS, to your own account', aws_signed_in: true, aws_account: '…1234',
  sign_in_commands: ['aws login'],
}
const LEAD = 'Choose where Kiro Crew lives: on this machine while it is on, or in the cloud so it stays available when this machine is off.'

function home(over: Partial<Card> = {}, payload: Record<string, unknown> = {}): Card {
  return {
    id: 'sc-offer0123456789a', slot: 'chat-1-1790000000', kind: 'home', status: 'pending', stakes: 'high',
    hash: HASH, payload: { ...PAYLOAD, ...payload }, outcome: null, error: null, created_ts: 1790000000,
    decided_ts: null, classic: { kind: 'route', target: '/settings' },
    ...over,
  }
}

function serve(card: Card, after?: Card) {
  const gw = { bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${card.id}`, () => HttpResponse.json(card)),
    http.post(`/api/setup/cards/${card.id}/decide`, async ({ request }) => {
      gw.bodies.push((await request.json()) as SetupDecideBody)
      return HttpResponse.json(after ?? card)
    }),
  )
  return gw
}

function renderHome(card: Card) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat']}>
        <SetupCard cardId={card.id} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return screen.findByTestId('setup-card')
}

describe('SetupCard — home: the "Where should your crew live?" step', () => {
  const CHOICE = { offer: true, step: 'choose', from_usd: 14 }

  it('asks with two compact rows, and Continue posts the pick', async () => {
    const choice = home({}, CHOICE)
    const gw = serve(choice)
    const el = await renderHome(choice)
    expect(within(el).getByRole('heading')).toHaveTextContent('Where should your crew live?')
    expect(el).toHaveTextContent(LEAD)
    const rows = within(screen.getByTestId('setup-card-home-choice')).getAllByRole('radio')
    expect(rows).toHaveLength(2)
    expect(screen.getByTestId('setup-card-home-choice-here')).toHaveTextContent('This machine')
    expect(screen.getByTestId('setup-card-home-choice-here')).toHaveTextContent('free · runs while it’s on')
    expect(screen.getByTestId('setup-card-home-choice-cloud')).toHaveTextContent('In the cloud')
    expect(screen.getByTestId('setup-card-home-choice-cloud')).toHaveTextContent('always on · from $14/mo')
    // Nothing about AWS or sizes yet.
    expect(screen.queryByTestId('setup-card-home-sizes')).toBeNull()
    expect(screen.queryByTestId('setup-card-home-meta')).toBeNull()
    const go = screen.getByTestId('setup-card-primary')
    expect(go).toHaveTextContent('Continue')
    expect(go).toBeDisabled()
    await userEvent.click(within(screen.getByTestId('setup-card-home-choice-cloud')).getByRole('radio'))
    expect(go).toBeEnabled()
    await userEvent.click(go)
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'choose', hash: HASH, input: { where: 'cloud' } }]))
    expect(screen.getByTestId('setup-card-decline')).toHaveTextContent('Not now')
  })

  it('"This machine" settles as "Staying on this machine"', async () => {
    const choice = home({}, CHOICE)
    const gw = serve(choice, { ...choice, status: 'committed', outcome: { stayed: true }, decided_ts: 1790000100 })
    await renderHome(choice)
    await userEvent.click(within(screen.getByTestId('setup-card-home-choice-here')).getByRole('radio'))
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'choose', hash: HASH, input: { where: 'here' } }]))
    const result = await screen.findByTestId('setup-card-result')
    expect(result).toHaveTextContent('Where should your crew live?')
    expect(result).toHaveTextContent('Staying on this machine')
  })

  it('"In the cloud" is the same card, moved on to AWS and the sizes', async () => {
    const cloud = home({}, {
      offer: true,
      size_options: [{ key: 'lite', label: 'Lite', note: 'lite_tradeoffs', instance_type: 't4g.small', vcpu: 2, ram_gb: 2, monthly_usd: 14, free_plan_ok: true }],
      size_default: 'lite',
    })
    serve(cloud)
    const el = await renderHome(cloud)
    expect(within(el).getByRole('heading')).toHaveTextContent('Where should your crew live?')
    expect(screen.queryByTestId('setup-card-home-choice')).toBeNull()
    expect(screen.getByTestId('setup-card-home-meta')).toHaveTextContent('AWS: signed in ✓ …1234 · eu-west-1')
    expect(screen.getByTestId('setup-card-home-size-lite')).toHaveTextContent('Lite · 2 GB · $14/mo')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Build my home')
    expect(screen.getByTestId('setup-card-decline')).toHaveTextContent('Not now')
  })

  it('keeps the sign-in and sign-up steps first on a machine not signed in to AWS', async () => {
    const offer = home({}, {
      offer: true, aws_signed_in: false, aws_account: '',
      signup_url: 'https://signin.aws.amazon.com/signup?request_type=register', signup_builder_id: false,
      aws_cli_installed: true,
    })
    serve(offer)
    await renderHome(offer)
    expect(screen.getByTestId('setup-card-home-meta')).toHaveTextContent('AWS: not signed in · eu-west-1')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Sign in to AWS')
    expect(screen.getByTestId('setup-card-home-aws-signup')).toHaveTextContent('Create an AWS account')
  })

  it('without `offer` the card is the home as before', async () => {
    const plain = home()
    serve(plain, { ...plain, status: 'declined', decided_ts: 1790000100 })
    const el = await renderHome(plain)
    expect(within(el).getByRole('heading')).toHaveTextContent('Your home in the cloud')
    expect(screen.queryByTestId('setup-card-home-choice')).toBeNull()
    const decline = screen.getByTestId('setup-card-decline')
    expect(decline).toHaveTextContent('Not now')
    await userEvent.click(decline)
    expect(await screen.findByTestId('setup-card-result')).toHaveTextContent('Skipped')
  })

  it('once the build starts, the step is the home itself', async () => {
    const building = home(
      { status: 'waiting', outcome: { job_id: 'job-1', status: 'running', steps: [], error: '' } },
      { offer: true },
    )
    serve(building)
    const el = await renderHome(building)
    expect(within(el).getByRole('heading')).toHaveTextContent('Your home in the cloud')
    expect(screen.queryByTestId('setup-card-home-choice')).toBeNull()
  })
})
