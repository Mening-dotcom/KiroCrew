/**
 * The re-auth banner names a refused sign-in link as the link, not a session.
 *
 * A `kirocrew token` link works for a few minutes. Opened after that, the gateway
 * serves the app shell with no cookie, every API call is refused, and the banner
 * used to say "Session expired." although no session ever existed in this
 * document. The lead is chosen from two facts: this document was opened with a
 * `?token=` link, and nothing has authenticated here since.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const deny403 = (): Response =>
  new Response(JSON.stringify({ error: 'Token required' }), {
    status: 403,
    headers: { 'content-type': 'application/json', 'X-Auth-Required': 'true' },
  })
const terminalRefresh = (): Response =>
  new Response(JSON.stringify({ error: 'refresh_chain_revoked' }), { status: 401 })
const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0))
const lead = (): string | null | undefined =>
  document.getElementById('mc-session-expired')?.querySelector('b')?.textContent

/** The client module as a document at *path* loads it. */
async function clientAt(path: string) {
  window.history.replaceState(null, '', path)
  vi.resetModules()
  return import('../api/client')
}

describe('the banner lead after a refused sign-in link', () => {
  const originalFetch = globalThis.fetch

  afterEach(async () => {
    globalThis.fetch = originalFetch
    const client = await import('../api/client')
    client.__resetAuthRecoveryStateForTests()
    window.history.replaceState(null, '', '/')
  })

  it('says the link no longer works when a link-opened document never authenticated', async () => {
    const client = await clientAt('/chat?sid=chat-1-1&token=expired-link')
    globalThis.fetch = vi.fn().mockResolvedValue(terminalRefresh()) as unknown as typeof fetch
    client.checkSessionExpired(deny403())
    await flush()
    expect(lead()).toBe(
      'This sign-in link no longer works. Sign-in links expire a few minutes after they are made.',
    )
  })

  it('is not fooled by the public theme boot answering without a session', async () => {
    const client = await clientAt('/?token=expired-link')
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ theme: 'default' }), { status: 200 }),
    ) as unknown as typeof fetch
    await client.api.themeBoot()
    globalThis.fetch = vi.fn().mockResolvedValue(terminalRefresh()) as unknown as typeof fetch
    client.checkSessionExpired(deny403())
    await flush()
    expect(lead()).toMatch(/^This sign-in link no longer works\./)
  })

  it('keeps "Session expired." once this document has authenticated', async () => {
    const client = await clientAt('/chat?token=used-link')
    client.removeAuthBanner()
    globalThis.fetch = vi.fn().mockResolvedValue(terminalRefresh()) as unknown as typeof fetch
    client.checkSessionExpired(deny403())
    await flush()
    expect(lead()).toBe('Session expired.')
  })

  it('keeps "Session expired." for a document opened without a link', async () => {
    const client = await clientAt('/chat?sid=chat-1-1')
    globalThis.fetch = vi.fn().mockResolvedValue(terminalRefresh()) as unknown as typeof fetch
    client.checkSessionExpired(deny403())
    await flush()
    expect(lead()).toBe('Session expired.')
  })
})
