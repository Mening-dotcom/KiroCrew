/**
 * Each kind's tray hint (setupCardHints.ts, through the registry): a short
 * summary from the payload while the card is pending, its live state while it
 * works, and the title alone for a kind that supplies none.
 */
import { describe, it, expect } from 'vitest'

import type { SetupCard } from '../api/setupCards'
import { cardHint, cardIcon, cardDeclinableFromHint, SETUP_CARD_KINDS } from '../components/setup/setupCardRegistry'

const card = (over: Partial<SetupCard> & Pick<SetupCard, 'kind'>): SetupCard => ({
  id: 'sc-1', slot: 's', status: 'pending', stakes: 'low', hash: 'h', payload: {}, outcome: null, error: null,
  created_ts: 0, decided_ts: null, classic: { kind: 'none', target: '' }, ...over,
})

describe('the hint per kind', () => {
  it('import: what comes over, by category, summed across sources', () => {
    const h = cardHint(card({
      kind: 'import',
      payload: { sources: [
        { id: 'hermes', name: 'Hermes', categories: [{ id: 'memories', label: 'Memories', count: 2 }, { id: 'skills', label: 'Skills', count: 1 }] },
        { id: 'claude', name: 'Claude', categories: [{ id: 'memories', label: 'Memories', count: 1 }] },
      ] },
    }))
    expect(h).toEqual({ text: '3 memories, 1 skill' })
  })

  it('cron: the job and when it runs', () => {
    expect(cardHint(card({ kind: 'cron', payload: { name: 'Morning brief', schedule_human: 'every weekday at 08:00' } })))
      .toEqual({ text: 'Morning brief, every weekday at 08:00' })
  })

  it('home: the cheapest size, from the choice step and the size step alike', () => {
    expect(cardHint(card({ kind: 'home', payload: { step: 'choose', from_usd: 14 } }))).toEqual({ text: 'From $14/mo' })
    expect(cardHint(card({ kind: 'home', payload: { size_options: [{ monthly_usd: 72 }, { monthly_usd: 14 }] } })))
      .toEqual({ text: 'From $14/mo' })
  })

  it('home: where the build is, then what it waits on, then the move', () => {
    const steps = [{ label: 'Create the server', state: 'done' }, { label: 'Start Kiro Crew', state: 'active' }]
    expect(cardHint(card({ kind: 'home', status: 'waiting', outcome: { steps } })))
      .toEqual({ text: 'Now: Start Kiro Crew', state: 'busy' })
    expect(cardHint(card({ kind: 'home', status: 'waiting', outcome: { steps, signin: { url: 'https://x', code: 'AB' } } })))
      .toEqual({ text: 'needs you', state: 'needs-you' })
    expect(cardHint(card({ kind: 'home', status: 'pending', outcome: { ready: true } })))
      .toEqual({ text: 'Ready to move in', state: 'needs-you' })
    expect(cardHint(card({ kind: 'home', status: 'working', outcome: { move_steps: [{ label: 'Copy memories', state: 'active' }] } })))
      .toEqual({ text: 'Moving in: Copy memories', state: 'busy' })
  })

  it('profile: the name, language and role it saves', () => {
    expect(cardHint(card({ kind: 'profile', payload: { fields: { bot_name: 'Nova', language: 'English', role: 'SRE', timezone: 'UTC' } } })))
      .toEqual({ text: 'Nova, English, SRE' })
  })

  it('credential: where the secret is sent', () => {
    expect(cardHint(card({ kind: 'credential', payload: { name: 'GITHUB_TOKEN', hosts: ['api.github.com'] } })))
      .toEqual({ text: 'api.github.com' })
  })

  it('connect and channel: the title names the provider, so only a wait on the owner is said', () => {
    expect(cardHint(card({ kind: 'connect', payload: { provider: { name: 'GitHub' } } }))).toBeNull()
    expect(cardHint(card({ kind: 'connect', status: 'waiting', payload: { provider: { name: 'GitHub' } } })))
      .toEqual({ text: 'needs you', state: 'needs-you' })
    expect(cardHint(card({ kind: 'channel', status: 'waiting', payload: { label: 'Telegram' } })))
      .toEqual({ text: 'needs you', state: 'needs-you' })
  })

  it('a kind with no summary shows the title alone, and "working" while it works', () => {
    expect(cardHint(card({ kind: 'service' }))).toBeNull()
    expect(cardHint(card({ kind: 'cron', status: 'working', payload: { name: 'n' } }))).toEqual({ text: 'working', state: 'busy' })
  })
})

describe('every kind brings its own icon, and only the mandatory one refuses Not now', () => {
  it('has an icon per kind', () => {
    for (const kind of Object.keys(SETUP_CARD_KINDS) as Array<keyof typeof SETUP_CARD_KINDS>) {
      expect(cardIcon(card({ kind })), kind).toBeTruthy()
    }
  })

  it('offers Not now on a pending card unless it is mandatory', () => {
    expect(cardDeclinableFromHint(card({ kind: 'profile' }))).toBe(true)
    expect(cardDeclinableFromHint(card({ kind: 'privacy' }))).toBe(false)
    expect(cardDeclinableFromHint(card({ kind: 'profile', status: 'working' }))).toBe(false)
  })
})
