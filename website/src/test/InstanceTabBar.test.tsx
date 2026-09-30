import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useLocation } from 'react-router-dom'
import { renderWithProviders, createTestStore } from './helpers'
import InstanceTabBar, {
  setCrewPins,
  setStableOrder,
  resolvePinnedPref,
  clippedChipIds,
} from '../components/InstanceTabBar'
import type { InstanceView, SsoStatus } from '../api/client'
import { crewIdentityTint } from '../components/CrewIdentityMark'

vi.mock('../api/client', () => {
  class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    api: {
      listInstances: vi.fn(),
      connectInstance: vi.fn(),
    },
  }
})
import { api } from '../api/client'
vi.mock('../lib/embedded', () => ({ isEmbeddedPane: vi.fn(() => false) }))
import { isEmbeddedPane } from '../lib/embedded'

const conn = (over: Partial<InstanceView> = {}): InstanceView => ({
  id: 'cd-1',
  name: 'Cloud One',
  ssh_host: 'cd-1-alias',
  remote_port: 7777,
  local_port: 7778,
  ttl: '20h',
  remote_bin: '',
  was_connected: false,
  status: { instance_id: 'cd-1', state: 'connected', local_port: 7778, remote_port: 7777 },
  ...over,
})

const okSso: SsoStatus = { state: 'ok', seconds_remaining: 72000, expires_at: null, reason: 'valid' }

/** Typed builder for the `api.listInstances` mock resolved value. */
const listResp = (instances: InstanceView[]) => ({ active: true, instances, warm_set_cap: 5, sso: okSso })

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setCrewPins([])
  setStableOrder(false)
  vi.mocked(isEmbeddedPane).mockReturnValue(false)
})

/** Open the crew dropdown and return the menu row for `name`. */
async function openSwitcher(u: ReturnType<typeof userEvent.setup>, name: RegExp) {
  await u.click(await screen.findByRole('button', { name: /Switch crew/i }))
  return await screen.findByRole('menuitemradio', { name })
}

describe('InstanceTabBar', () => {
  it('renders nothing when embedded as an instance pane (no recursive nesting)', async () => {
    vi.mocked(isEmbeddedPane).mockReturnValue(true)
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: {}, activeId: 'cd-1', mru: ['cd-1'], unread: {} },
    })
    const { container } = renderWithProviders(<InstanceTabBar />, { store })
    // No switcher, and the instances poll is disabled while embedded.
    expect(container.querySelector('[aria-label^="Switch crew"]')).toBeNull()
    expect(api.listInstances).not.toHaveBeenCalled()
  })

  it('renders nothing when no instance is connected (single-instance experience)', async () => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([]))
    const { container } = renderWithProviders(<InstanceTabBar />)
    await waitFor(() => expect(api.listInstances).toHaveBeenCalled())
    expect(container.querySelector('[aria-label^="Switch crew"]')).toBeNull()
  })

  it('renders Local + a tab per connected instance and switches to Local', async () => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: {}, activeId: 'cd-1', mru: ['cd-1'], unread: {} },
    })
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />, { store })

    // The trigger names the crew on screen; the destinations live in its menu.
    const local = await openSwitcher(u, /Local/i)
    expect(local).toBeInTheDocument()
    expect(screen.getByRole('menuitemradio', { name: /Cloud One/i })).toBeInTheDocument()

    await u.click(local)
    await waitFor(() => expect(store.getState().instances.activeId).toBeNull())
  })

  it('connects a not-yet-warm instance when its tab is clicked', async () => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    vi.mocked(api.connectInstance).mockResolvedValue({ instance_id: 'cd-1', state: 'connected', local_port: 7778, token: 'tok' })
    const u = userEvent.setup()
    const { store } = renderWithProviders(<InstanceTabBar />)

    await u.click(await openSwitcher(u, /Cloud One/i))
    await waitFor(() => expect(api.connectInstance).toHaveBeenCalledWith('cd-1'))
    await waitFor(() => expect(store.getState().instances.warm['cd-1']).toEqual({ port: 7778, token: 'tok' }))
    expect(store.getState().instances.activeId).toBe('cd-1')
  })

  it('reconnects a warm-but-disconnected tab on click (stale warm after a mid-session drop)', async () => {
    // A tunnel that dropped mid-session: status flips to error but the in-memory
    // `warm` entry lingers. Clicking the (red) tab must still fire a reconnect —
    // gating only on `!warm[id]` would skip it and nothing would happen.
    const down = conn({
      status: { instance_id: 'cd-1', state: 'error', error: 'ssh unreachable', remote_port: 7777 },
      was_connected: true,
    })
    vi.mocked(api.listInstances).mockResolvedValue(listResp([down]))
    vi.mocked(api.connectInstance).mockResolvedValue({ instance_id: 'cd-1', state: 'connected', local_port: 7778, token: 'fresh' })
    const u = userEvent.setup()
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 'stale' } }, activeId: null, mru: ['cd-1'], unread: {} },
    })
    renderWithProviders(<InstanceTabBar />, { store })

    await u.click(await openSwitcher(u, /Cloud One/i))
    expect(store.getState().instances.activeId).toBe('cd-1')
    // Reconnect fires despite the lingering warm entry, and re-warms with a fresh token.
    await waitFor(() => expect(api.connectInstance).toHaveBeenCalledWith('cd-1'))
    await waitFor(() => expect(store.getState().instances.warm['cd-1']).toEqual({ port: 7778, token: 'fresh' }))
  })

  it('does NOT reconnect a warm + connected tab on click (no needless re-mint)', async () => {
    // The healthy path: a live, warm tab just switches — clicking it must not
    // re-mint/reload an in-use pane.
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const u = userEvent.setup()
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 'tok' } }, activeId: null, mru: ['cd-1'], unread: {} },
    })
    renderWithProviders(<InstanceTabBar />, { store })

    await u.click(await openSwitcher(u, /Cloud One/i))
    expect(store.getState().instances.activeId).toBe('cd-1')
    // Give any stray async a tick; connect must NOT have been called.
    await new Promise(r => setTimeout(r, 0))
    expect(api.connectInstance).not.toHaveBeenCalled()
  })

  it('shows the active tunnel connection status with a token auto-refresh countdown', async () => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([
      conn({ status: { instance_id: 'cd-1', state: 'connected', local_port: 7778, remote_port: 7777, token_ttl_remaining: 72000 } }),
    ]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: 'cd-1', mru: ['cd-1'], unread: {} },
    })
    renderWithProviders(<InstanceTabBar />, { store })
    // ttl 20h (72000s), 72000s remaining -> refresh fires at 80% elapsed (20% left),
    // so untilRefresh = 72000 - 14400 = 57600s ≈ 16h.
    expect(await screen.findByText(/connected · refresh/i)).toBeInTheDocument()
    expect(screen.getByTitle(/Tunnel connected.*auto-refresh in/i)).toBeInTheDocument()
  })

  it('keeps a sticky tab for a was_connected instance whose tunnel is down', async () => {
    // A tab exists for an instance the user intends to be connected
    // (was_connected) even when its live tunnel is down after a restart.
    const down = conn({
      status: { instance_id: 'cd-1', state: 'error', error: 'ssh unreachable', remote_port: 7777 },
      was_connected: true,
    })
    vi.mocked(api.listInstances).mockResolvedValue(listResp([down]))
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />)
    expect(await openSwitcher(u, /Cloud One/i)).toBeInTheDocument()
    // The error state reaches assistive tech through the row's accessible name,
    // not only through a red dot and a hover tooltip.
    expect(screen.getByRole('menuitemradio', { name: /tunnel error/i })).toBeInTheDocument()
    expect(screen.getByTitle(/— tunnel error/i)).toBeInTheDocument()
    // ...and it is on SCREEN, not only in the accessible name: the dot is the
    // other carrier of state and it is colour alone, so a colourblind user
    // scanning for the crew that failed would otherwise have to hover each row.
    const word = screen.getByText(/tunnel error/i, { selector: 'span' })
    expect(word).not.toHaveClass('sr-only')
  })

  it('shows no tab for an instance that was never connected and is down', async () => {
    const never = conn({
      status: { instance_id: 'cd-1', state: 'disconnected', remote_port: 7777 },
      was_connected: false,
    })
    vi.mocked(api.listInstances).mockResolvedValue(listResp([never]))
    const { container } = renderWithProviders(<InstanceTabBar />)
    await waitFor(() => expect(api.listInstances).toHaveBeenCalled())
    expect(container.querySelector('[aria-label^="Switch crew"]')).toBeNull()
  })

  it('keeps the tab and activates it when a reconnect attempt fails', async () => {
    const down = conn({
      status: { instance_id: 'cd-1', state: 'error', error: 'ssh unreachable', remote_port: 7777 },
      was_connected: true,
    })
    vi.mocked(api.listInstances).mockResolvedValue(listResp([down]))
    vi.mocked(api.connectInstance).mockRejectedValue(new Error('still unreachable'))
    const u = userEvent.setup()
    const { store } = renderWithProviders(<InstanceTabBar />)

    await u.click(await openSwitcher(u, /Cloud One/i))
    // Activated immediately (so the in-pane error panel shows) and a reconnect
    // was attempted...
    await waitFor(() => expect(store.getState().instances.activeId).toBe('cd-1'))
    await waitFor(() => expect(api.connectInstance).toHaveBeenCalledWith('cd-1'))
    // ...but the failed connect neither warms it nor removes the tab.
    expect(store.getState().instances.warm['cd-1']).toBeUndefined()
    expect(await openSwitcher(u, /Cloud One/i)).toBeInTheDocument()
  })

  it('carries unread counts for crews the dropdown is hiding', async () => {
    // Collapsing the strip into a menu would otherwise hide every unread badge
    // behind a closed menu: the trigger has to answer "is anything waiting?"
    // without being opened, and each row still owns its own count.
    const other = conn({ id: 'cd-2', name: 'Cloud Two', ssh_host: 'cd-2-alias', remote_port: 7779 })
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn(), other]))
    const store = createTestStore({
      instances: {
        warm: { 'cd-1': { port: 7778, token: 't' } },
        activeId: 'cd-1',
        mru: ['cd-1'],
        unread: { 'cd-1': 4, 'cd-2': 3 },
      },
    })
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />, { store })

    // Only the crews NOT on screen are counted, so the active pane's own stale
    // count never inflates the badge.
    // The trigger's own accessible name carries the count, so it is announced
    // even by screen readers that skip a button's child content.
    expect(await screen.findByRole('button', { name: /3 unread elsewhere/i })).toBeInTheDocument()

    await u.click(screen.getByRole('button', { name: /Switch crew/i }))
    expect(await screen.findByLabelText('3 unread')).toBeInTheDocument()
  })

  it('does not print the crew name twice when its host alias IS its name', async () => {
    // "clouddeskARM / clouddeskARM" reads as a rendering bug rather than detail.
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn({ name: 'clouddeskARM', ssh_host: 'clouddeskARM' })]))
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />)
    const row = await openSwitcher(u, /clouddeskARM/i)
    expect(row.textContent?.match(/clouddeskARM/g) ?? []).toHaveLength(1)
  })

  it('pins one crew out of the dropdown into an always-visible chip, and remembers it', async () => {
    // Nothing pinned by default: the crew lives behind the dropdown.
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: null, mru: ['cd-1'], unread: {} },
    })
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />, { store })

    // No chip row at all until something is pinned, so a single-crew user pays
    // no header width for the feature.
    expect(await screen.findByRole('button', { name: /Switch crew/i })).toBeInTheDocument()
    expect(screen.queryByTestId('crew-chip-row')).toBeNull()

    // Pin it from the crew's own row: one lit/unlit pin per destination, no
    // second list of the same crews.
    await u.click(screen.getByRole('button', { name: /Switch crew/i }))
    const pinItem = await screen.findByTestId('crew-pin-cd-1')
    expect(pinItem).toHaveAttribute('aria-checked', 'false')
    await u.click(pinItem)
    // Persisted as a set of ids, so it survives reloads and pane switches.
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('mc-crew-switcher-pinned')!)).toEqual(['cd-1']),
    )
    // ...and the chip is now on screen, outside the menu.
    const row = await screen.findByTestId('crew-chip-row')
    expect(row.textContent).toMatch(/Cloud One/)
  })

  it('toggles the pin without switching crews, and keeps the menu open', async () => {
    // The pin shares a row with the destination, so the two must stay separable:
    // pinning a crew the user is not on must not navigate there, and the menu has
    // to survive the click so a second crew can be pinned in the same visit.
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: null, mru: ['cd-1'], unread: {} },
    })
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />, { store })

    await u.click(await screen.findByRole('button', { name: /Switch crew/i }))
    await u.click(await screen.findByTestId('crew-pin-cd-1'))
    expect(store.getState().instances.activeId).toBeNull()

    // Still open, and the same control now reads as checked — the icon is a
    // toggle, not a one-way action.
    const again = await screen.findByTestId('crew-pin-cd-1')
    expect(again).toHaveAttribute('aria-checked', 'true')
    await u.click(again)
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('mc-crew-switcher-pinned')!)).toEqual([]),
    )
  })

  it('pins from the keyboard, since Enter is the only way a keyboard user can', async () => {
    // Radix fires `onSelect` for pointer AND Enter/Space; a raw DOM `onClick`
    // fires for neither reliably here, which would leave the icon-only control
    // mouse-only. Arrow keys walk row -> pin, so Enter lands on the pin.
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />)

    await u.click(await screen.findByRole('button', { name: /Switch crew/i }))
    const pin = await screen.findByTestId('crew-pin-cd-1')
    pin.focus()
    await u.keyboard('{Enter}')
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('mc-crew-switcher-pinned')!)).toEqual(['cd-1']),
    )
  })

  it('gives every row its own pin control instead of a separate pin section', async () => {
    // The regression this guards: pinning used to be a second flat list of the
    // same crews below the destinations, so each crew appeared twice.
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />)

    await u.click(await screen.findByRole('button', { name: /Switch crew/i }))
    // One pin per destination (Local + the crew), each naming its own crew.
    expect(await screen.findByTestId('crew-pin-__local__')).toHaveAccessibleName(/Pin Local/i)
    expect(await screen.findByTestId('crew-pin-cd-1')).toHaveAccessibleName(/Pin Cloud One/i)
    // ...and each crew is listed once, as one switch target.
    expect(screen.getAllByRole('menuitemradio', { name: /Cloud One/i })).toHaveLength(1)
  })

  it('keeps the resting pin at full strength, since it is the only affordance', async () => {
    // The labelled "Pin crews" list is gone, so this glyph is the whole feature's
    // discoverability. `--muted` below full strength measures ~2.3:1 on the
    // default themes, under the 3:1 floor a UI control has to clear, and a
    // control a low-vision user cannot see is a feature they cannot reach.
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />)

    await u.click(await screen.findByRole('button', { name: /Switch crew/i }))
    const glyph = (await screen.findByTestId('crew-pin-cd-1')).querySelector('svg')
    expect(glyph?.getAttribute('class')).toMatch(/text-muted/)
    expect(glyph?.getAttribute('class')).not.toMatch(/opacity-/)
  })

  it('marks a pinned crew by FILL, so no state reads as the unpinned outline', async () => {
    // Fill is the only thing separating pinned from unpinned, and it must not be
    // conditional: a pinned crew rendered as an outline (however tinted) reads as
    // "not pinned" and invites a click that unpins it. Opacity is out for the same
    // reason plus a harder one — `--accent` differs per theme, so no single faded
    // value is measurably above the 3:1 floor everywhere.
    setCrewPins(['cd-1'])
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />)

    await u.click(await screen.findByRole('button', { name: /Switch crew/i }))
    const glyph = (await screen.findByTestId('crew-pin-cd-1')).querySelector('svg')
    expect(glyph?.getAttribute('class')).toMatch(/fill-current/)
    expect(glyph?.getAttribute('class')).not.toMatch(/opacity-/)
  })

  it('switches by clicking a pinned crew chip, without re-minting a warm pane', async () => {
    setCrewPins(['cd-1'])
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: null, mru: ['cd-1'], unread: {} },
    })
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />, { store })

    // No dropdown to open — the chip is on screen and switches on a single click.
    await u.click(await screen.findByRole('button', { name: /Cloud One/i }))
    expect(store.getState().instances.activeId).toBe('cd-1')
    // A live, warm pane just switches — clicking it must not re-mint.
    await new Promise(r => setTimeout(r, 0))
    expect(api.connectInstance).not.toHaveBeenCalled()
  })

  it('leads with the active crew and keeps it out of the pinned row', async () => {
    // Both destinations pinned, Local active: the chip row carries only the crew.
    setCrewPins(['__local__', 'cd-1'])
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: null, mru: ['cd-1'], unread: {} },
    })
    renderWithProviders(<InstanceTabBar />, { store })

    const row = await screen.findByTestId('crew-chip-row')
    expect(row.textContent).toMatch(/Cloud One/)
    expect(row.textContent).not.toMatch(/Local/)
  })

  it('stable order keeps a pinned active crew in place instead of leading with it', async () => {
    // Frequent switchers opt in: the crew on screen holds its configured slot and
    // is only highlighted, so switching never reshuffles the row.
    setCrewPins(['__local__', 'cd-1'])
    setStableOrder(true)
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: 'cd-1', mru: ['cd-1'], unread: {} },
    })
    const { container } = renderWithProviders(<InstanceTabBar />, { store })

    // No separate leading active chip — the active crew lives inside the row.
    const row = await screen.findByTestId('crew-chip-row')
    expect(container.querySelector('.tb-crew-active-chip')).toBeNull()
    // Both pinned destinations are in the row, in their configured order.
    expect(row.textContent).toMatch(/Local/)
    expectLocalCrewIcon(within(row).getByRole('button', { name: /Local/i }), 16)
    expect(row.textContent).toMatch(/Cloud One/)
    // ...and the active crew is highlighted where it sits, not moved.
    expect(within(row).getByRole('button', { name: /Cloud One/i })).toHaveAttribute('aria-current', 'true')
  })

  it('still leads with the active crew under stable order when it is NOT pinned', async () => {
    // Stable order fixes the pinned row; an unpinned active crew has no slot
    // there, so it still leads to stay reachable without opening the dropdown.
    setCrewPins(['__local__'])
    setStableOrder(true)
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: 'cd-1', mru: ['cd-1'], unread: {} },
    })
    const { container } = renderWithProviders(<InstanceTabBar />, { store })

    const lead = await waitFor(() => {
      const el = container.querySelector('.tb-crew-active-chip')
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    expect(lead.textContent).toMatch(/Cloud One/)
  })

  it.each([false, true])('omits ordering controls without changing saved order (%s)', async stableOrder => {
    setStableOrder(stableOrder)
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const u = userEvent.setup()
    renderWithProviders(<InstanceTabBar />)

    const local = await openSwitcher(u, /^Local$/)
    expect(local).toHaveTextContent(/^Local$/)
    expect(screen.queryByText('Local dashboard')).toBeNull()
    expect(screen.queryByRole('menuitemcheckbox', { name: 'Keep tab order fixed' })).toBeNull()
    expect(screen.getByRole('menuitem', { name: 'Add remote crew' })).toBeInTheDocument()
    expect(localStorage.getItem('mc-crew-switcher-stable-order')).toBe(stableOrder ? '1' : '0')
  })

  it('breaks and clamps an unbreakable list error so it cannot paint over the notice\'s own Ask-the-agent control', async () => {
    const unbreakable = '<!DOCTYPE html><html><head><meta charset="utf-8">'
    vi.mocked(api.listInstances).mockRejectedValue(new Error(unbreakable))
    renderWithProviders(<InstanceTabBar />)

    const notice = await screen.findByTestId('instance-tab-bar-list-error')
    expect(within(notice).getByRole('button', { name: /Ask the agent/i })).toBeInTheDocument()
    // `truncate` on the flex root cannot ellipsise, and its nowrap inherits down
    // and removes the message's last break opportunity.
    expect(notice.className).not.toMatch(/truncate|whitespace-nowrap/)
    const msg = within(notice).getByText(unbreakable)
    expect(msg.className).toMatch(/line-clamp-1/)
    // No `break-all`: the span's own `overflow-wrap: anywhere` breaks the unbreakable
    // token AND prefers word boundaries, so prose does not get cut mid-word.
    expect(msg.className).not.toMatch(/break-all/)
  })

})

function RouteProbe() {
  const { pathname, search } = useLocation()
  return <output data-testid="switcher-route">{pathname}{search}</output>
}

function expectLocalCrewIcon(root: HTMLElement, size: number) {
  const icon = within(root).getByTestId('local-crew-icon')
  expect(icon.tagName).toBe('IMG')
  expect(icon).toHaveAttribute('src', '/logo.png')
  expect(icon).toHaveAttribute('alt', '')
  expect(icon).toHaveAttribute('aria-hidden', 'true')
  expect(icon).toHaveAttribute('width', String(size))
  expect(icon).toHaveAttribute('height', String(size))
  expect(within(root).queryByTestId('kiro-ghost-mark')).toBeNull()
}

describe('InstanceTabBar navigation variant', () => {
  const nav = (collapsed = false) => <InstanceTabBar variant="navigation" collapsed={collapsed} />

  it('keeps Local selectable and offers the first remote when no crews exist', async () => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([]))
    const u = userEvent.setup()
    renderWithProviders(<>{nav()}<RouteProbe /></>)
    await waitFor(() => expect(api.listInstances).toHaveBeenCalled())
    const identity = await screen.findByRole('button', { name: 'Local — Switch crew' })
    expect(identity).toHaveTextContent('Local')
    expectLocalCrewIcon(identity, 36)
    await u.click(identity)
    const local = await screen.findByRole('menuitemradio', { name: /Local/ })
    expect(local).toHaveAttribute('aria-checked', 'true')
    expectLocalCrewIcon(local, 16)
    await u.click(screen.getByRole('menuitem', { name: 'Add remote crew' }))
    expect(screen.getByTestId('switcher-route')).toHaveTextContent('/settings/instances?highlight=key%3Aremote-crew-add')
    expect(api.connectInstance).not.toHaveBeenCalled()
  })

  it('offers setup when crews are gated off without trying to connect', async () => {
    const { ApiError } = await import('../api/client')
    vi.mocked(api.listInstances).mockRejectedValue(new ApiError(403, 'forbidden'))
    const u = userEvent.setup()
    renderWithProviders(<>{nav()}<RouteProbe /></>)
    await waitFor(() => expect(api.listInstances).toHaveBeenCalled())
    await u.click(await screen.findByRole('button', { name: 'Local — Switch crew' }))
    await u.click(screen.getByRole('menuitem', { name: 'Add remote crew' }))
    expect(screen.getByTestId('switcher-route')).toHaveTextContent('/settings/instances?highlight=key%3Aremote-crew-add')
    expect(screen.queryByTestId('instance-tab-bar-list-error')).toBeNull()
    expect(api.connectInstance).not.toHaveBeenCalled()
  })

  it('keeps the collapsed Local name for assistive tech and still opens its menu', async () => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([]))
    const u = userEvent.setup()
    renderWithProviders(nav(true))
    const identity = await screen.findByRole('button', { name: 'Local — Switch crew' })
    expect(within(identity).getByText('Local')).toHaveClass('sr-only')
    expectLocalCrewIcon(identity, 36)
    await u.click(identity)
    expect(await screen.findByRole('menuitem', { name: 'Add remote crew' })).toBeInTheDocument()
  })

  it.each([false, true])('uses an understated keyboard cue without an outer outline (collapsed=%s)', async collapsed => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([]))
    const u = userEvent.setup()
    renderWithProviders(nav(collapsed))
    const chooser = await screen.findByRole('button', { name: 'Local — Switch crew' })
    expect(chooser).toHaveClass('outline-none', 'data-[keyboard-focus=true]:focus-visible:bg-bg-hover')
    expect(chooser.className).not.toContain('shadow')
    expect(chooser).not.toHaveClass('focus-ring', 'focus-visible:bg-bg-hover')
    expect(chooser).not.toHaveAttribute('data-keyboard-focus')
    await u.tab()
    expect(chooser).toHaveFocus()
    expect(chooser).toHaveAttribute('data-keyboard-focus', 'true')
    await u.keyboard('{Enter}')
    expect(await screen.findByRole('menuitem', { name: 'Add remote crew' })).toBeInTheDocument()
    await u.keyboard('{Escape}')
    expect(chooser).toHaveFocus()
  })

  it.each([false, true])('clears keyboard paint on pointer selection without losing focus (collapsed=%s)', async collapsed => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([]))
    const u = userEvent.setup()
    renderWithProviders(nav(collapsed))
    const chooser = await screen.findByRole('button', { name: 'Local — Switch crew' })
    await u.tab()
    expect(chooser).toHaveAttribute('data-keyboard-focus', 'true')
    await u.click(chooser)
    await u.click(await screen.findByRole('menuitemradio', { name: /Local/ }))
    await waitFor(() => expect(chooser).toHaveFocus())
    expect(chooser).not.toHaveAttribute('data-keyboard-focus')
    await u.tab()
    await u.tab({ shift: true })
    expect(chooser).toHaveFocus()
    expect(chooser).toHaveAttribute('data-keyboard-focus', 'true')
  })

  it('names and tints the active crew on the chooser, and switches to Local from its menu', async () => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: 'cd-1', mru: ['cd-1'], unread: {} },
    })
    const u = userEvent.setup()
    const { container } = renderWithProviders(nav(), { store })

    const chooser = await screen.findByRole('button', { name: /^Cloud One — Switch crew/ })
    expect(chooser).toHaveAccessibleName(/^Cloud One — Switch crew/)
    expect(chooser).toHaveTextContent('Cloud One')
    expect(within(chooser).getByTestId('crew-identity-mark').style.color).toBe(crewIdentityTint('cd-1'))
    // The chooser itself is the active crew: no second leading active chip.
    expect(container.querySelector('.tb-crew-active-chip')).toBeNull()

    await u.click(chooser)
    await u.click(await screen.findByRole('menuitemradio', { name: /Local/i }))
    await waitFor(() => expect(store.getState().instances.activeId).toBeNull())
    // The trigger keeps its identity while showing Local's product icon.
    expect(screen.getByTestId('navigation-crew-switcher')).toBe(chooser)
    await waitFor(() => expect(chooser).toHaveAccessibleName(/^Local — Switch crew/))
    expectLocalCrewIcon(chooser, 36)
  })

  it('hides the crew name and chevron when collapsed but keeps the accessible name', async () => {
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: 'cd-1', mru: ['cd-1'], unread: {} },
    })
    renderWithProviders(nav(true), { store })
    const chooser = await screen.findByRole('button', { name: /^Cloud One — Switch crew/ })
    expect(within(chooser).getByText('Cloud One')).toHaveClass('sr-only')
    expect(chooser).toHaveAccessibleName(/^Cloud One — Switch crew/)
  })

  it('reconnects a warm-but-down crew chosen from the navigation menu', async () => {
    const down = conn({
      status: { instance_id: 'cd-1', state: 'error', error: 'ssh unreachable', remote_port: 7777 },
      was_connected: true,
    })
    vi.mocked(api.listInstances).mockResolvedValue(listResp([down]))
    vi.mocked(api.connectInstance).mockResolvedValue({ instance_id: 'cd-1', state: 'connected', local_port: 7778, token: 'fresh' })
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 'stale' } }, activeId: null, mru: ['cd-1'], unread: {} },
    })
    const u = userEvent.setup()
    renderWithProviders(nav(), { store })

    await u.click(await openSwitcher(u, /Cloud One/i))
    expect(store.getState().instances.activeId).toBe('cd-1')
    await waitFor(() => expect(api.connectInstance).toHaveBeenCalledWith('cd-1'))
    await waitFor(() => expect(store.getState().instances.warm['cd-1']).toEqual({ port: 7778, token: 'fresh' }))
  })

  it('carries unread for hidden crews on the navigation chooser', async () => {
    const other = conn({ id: 'cd-2', name: 'Cloud Two', ssh_host: 'cd-2-alias', remote_port: 7779 })
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn(), other]))
    const store = createTestStore({
      instances: {
        warm: { 'cd-1': { port: 7778, token: 't' } },
        activeId: 'cd-1',
        mru: ['cd-1'],
        unread: { 'cd-1': 4, 'cd-2': 3 },
      },
    })
    const u = userEvent.setup()
    renderWithProviders(nav(), { store })

    const chooser = await screen.findByTestId('navigation-crew-switcher')
    await waitFor(() => expect(chooser).toHaveAccessibleName(/3 unread elsewhere/i))
    await u.click(chooser)
    expect(await screen.findByLabelText('3 unread')).toBeInTheDocument()
  })

  it('keeps chained crews as a tree in the navigation menu', async () => {
    const hop = conn({ id: 'cd-2', name: 'Inner', ssh_host: 'inner-alias', remote_port: 7779, via_instance_id: 'cd-1' })
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn(), hop]))
    const u = userEvent.setup()
    renderWithProviders(nav())

    const row = await openSwitcher(u, /Inner/i)
    // The chain connector glyph and the "via" subtitle survive the move.
    expect(row.textContent).toContain('\u2514')
    expect(row.textContent).toContain('Cloud One › inner-alias')
  })

  it('still offers pinned crew chips that switch in one click', async () => {
    setCrewPins(['cd-1'])
    vi.mocked(api.listInstances).mockResolvedValue(listResp([conn()]))
    const store = createTestStore({
      instances: { warm: { 'cd-1': { port: 7778, token: 't' } }, activeId: null, mru: ['cd-1'], unread: {} },
    })
    const u = userEvent.setup()
    renderWithProviders(nav(), { store })

    const row = await screen.findByTestId('crew-chip-row')
    await u.click(within(row).getByRole('button', { name: /Cloud One/i }))
    expect(store.getState().instances.activeId).toBe('cd-1')
    await new Promise(r => setTimeout(r, 0))
    expect(api.connectInstance).not.toHaveBeenCalled()
  })

  it('renders the parent-relayed model when embedded and relays the choice up', async () => {
    vi.mocked(isEmbeddedPane).mockReturnValue(true)
    const posted: unknown[] = []
    Object.defineProperty(window, 'parent', {
      value: { postMessage: (m: unknown) => { posted.push(m) } },
      configurable: true,
    })
    try {
      const store = createTestStore({
        instances: {
          warm: {},
          activeId: null,
          mru: [],
          unread: {},
          host: {
            tabs: [{ id: 'cd-1', name: 'Relayed One', sshHost: 'relay-alias', state: 'connected', unread: 0 }],
            activeId: 'cd-1',
            self: null,
            macInset: false,
            winInset: false,
            focusMode: null,
            electron: false,
            pinnedCrews: [],
            stableOrder: null,
          } as never,
        },
      })
      const u = userEvent.setup()
      renderWithProviders(nav(), { store })

      const chooser = await screen.findByTestId('navigation-crew-switcher')
      expect(chooser).toHaveAccessibleName(/^Relayed One — Switch crew/)
      // The pane derives the parent's tint from the same stable id.
      expect(within(chooser).getByTestId('crew-identity-mark').style.color).toBe(crewIdentityTint('cd-1'))
      expect(api.listInstances).not.toHaveBeenCalled()

      await u.click(chooser)
      const local = await screen.findByRole('menuitemradio', { name: /Local/i })
      expectLocalCrewIcon(local, 16)
      await u.click(local)
      expect(posted).toContainEqual({ type: 'mc-switch-instance', v: 1, id: null })
    } finally {
      Object.defineProperty(window, 'parent', { value: window, configurable: true })
    }
  })
})

describe('crewIdentityTint', () => {
  it('gives Local its own token', () => {
    expect(crewIdentityTint(null)).toBe('var(--aim)')
  })

  it('derives a remote tint from the stable id alone, never its position', () => {
    const ids = ['cd-1', 'cd-2', 'clouddeskARM', 'x', 'a-very-long-remote-crew-identifier']
    const first = ids.map(crewIdentityTint)
    // Same id, same tint, however the list is reordered or trimmed.
    expect([...ids].reverse().map(crewIdentityTint)).toEqual([...first].reverse())
    expect(crewIdentityTint('cd-2')).toBe(first[1])
    for (const tint of first) {
      expect(tint).toMatch(/^var\(--(ok|info|warn|clarify|danger)\)$/)
      expect(tint).not.toBe('var(--aim)')
    }
  })
})

describe('resolvePinnedPref', () => {
  it('reads a stored id list', () => {
    expect(resolvePinnedPref(JSON.stringify(['a', 'b']), null)).toEqual(['a', 'b'])
  })

  it('ignores a corrupted or non-array value instead of throwing', () => {
    expect(resolvePinnedPref('{oops', null)).toEqual([])
    expect(resolvePinnedPref('"a string"', null)).toEqual([])
    expect(resolvePinnedPref(JSON.stringify([1, 'a', null]), null)).toEqual(['a'])
  })

  it('migrates the legacy expand switch to a pinned Local, not to nothing', () => {
    // Someone who had the switcher pinned open wanted chips; migrating them to
    // an empty set would read as the feature having been removed.
    expect(resolvePinnedPref(null, '1')).toEqual(['__local__'])
    expect(resolvePinnedPref(null, '0')).toEqual([])
  })

  it('prefers a stored set over the legacy switch', () => {
    expect(resolvePinnedPref(JSON.stringify(['cd-1']), '1')).toEqual(['cd-1'])
  })

  it('defaults to nothing pinned', () => {
    expect(resolvePinnedPref(null, null)).toEqual([])
  })
})

describe('clippedChipIds', () => {
  // jsdom performs no layout, so this rule is only testable as a pure function —
  // every offset in a rendered test is 0.
  it('reports a chip whose trailing edge passes the visible width', () => {
    expect([
      ...clippedChipIds(
        [
          { id: 'a', left: 0, width: 80 },
          { id: 'b', left: 84, width: 80 },
          { id: 'c', left: 168, width: 80 },
        ],
        200,
      ),
    ]).toEqual(['c'])
  })

  it('counts the partially visible chip at the boundary as clipped', () => {
    // It is exactly the chip a user cannot read, so the dropdown has to account
    // for it — the fade marks that edge rather than pretending it is present.
    expect([...clippedChipIds([{ id: 'a', left: 0, width: 120 }], 100)]).toEqual(['a'])
  })

  it('tolerates a sub-pixel overhang', () => {
    expect(clippedChipIds([{ id: 'a', left: 0, width: 100.4 }], 100).size).toBe(0)
  })

  it('reports nothing when every chip fits', () => {
    expect(
      clippedChipIds([{ id: 'a', left: 0, width: 80 }, { id: 'b', left: 84, width: 80 }], 400).size,
    ).toBe(0)
  })

  it('handles an empty row', () => {
    expect(clippedChipIds([], 300).size).toBe(0)
  })
})
