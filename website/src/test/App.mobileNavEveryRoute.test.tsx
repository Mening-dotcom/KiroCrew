/**
 * Phone: ONE navigation rail on every tab.
 *
 * The chat page shows the shell's 72px captioned rail beside its Sessions pane
 * (App.mobileSingleTopbar.test.tsx pins that hand-off). Every OTHER phone route
 * reaches the SAME rail through the shell's rail-only drawer, opened from the
 * header's 40px panel icon. This pins, per route:
 *
 *  - the header carries the panel toggle and no inline crew switcher (the
 *    current-crew chooser lives at the top of the rail instead);
 *  - the drawer holds exactly one rail: crew chooser pinned first, every
 *    destination in one scroll region, Search pinned last;
 *  - the row for the route you are on is the one active row;
 *  - keyboard: rows are Enter-operable, Escape dismisses, the crew chooser opens
 *    by keyboard and offers Add remote crew;
 *  - history: the shell drawer PUSHES (it added no history entry of its own),
 *    while the chat drawer's rail REPLACES its duplicate entry.
 */
import { describe, it, expect, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useLocation, useNavigationType } from 'react-router-dom'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderWithProviders } from './helpers'
import App, { NavItem } from '../App'
import { useMobileNavRail } from '../components/MobileNavRailContext'

// Stands in for the real ChatPage's consumer side, which passes replace:true
// (asserted in ChatPage.mobileSingleTopbar.test.tsx).
function ChatPageStub() {
  const rail = useMobileNavRail()
  return <div data-testid="chat-page">{rail?.({ onActivate: () => {}, replace: true })}</div>
}
vi.mock('../pages/ChatPage', () => ({ default: ChatPageStub }))
vi.mock('../pages/SchedulePage', () => ({ default: () => <div data-testid="route-page">schedule</div> }))
vi.mock('../pages/ArtifactsPage', () => ({ default: () => <div data-testid="route-page">artifacts</div> }))
vi.mock('../pages/apps/DiscoverPage', () => ({ default: () => <div data-testid="route-page">apps</div> }))
vi.mock('../pages/apps/LibraryPage', () => ({ default: () => <div data-testid="route-page">library</div> }))
vi.mock('../pages/CapabilitiesPage', () => ({ default: () => <div data-testid="route-page">capabilities</div> }))
vi.mock('../pages/SettingsPage', () => ({ default: () => <div data-testid="route-page">settings</div> }))
vi.mock('../hooks/useWebSocket', () => ({ useWebSocket: () => ({ subscribeLogs: () => {} }) }))
vi.mock('../hooks/useAgents', () => ({ useAgents: vi.fn(() => ({ agents: [{ name: 'kirocrew' }], defaultAgent: 'kirocrew' })) }))
vi.mock('../providers/context', () => ({ useProvider: () => ({ id: 'acp' }) }))
vi.mock('../components/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <span>{content}</span>, Lightbox: () => null }))
vi.mock('../api/client', () => ({
  api: {
    chatSlots: vi.fn().mockResolvedValue([]),
    notifications: vi.fn().mockResolvedValue({ notifications: [] }),
    status: vi.fn().mockResolvedValue({ uptime: '1h', sessions: 0, messages: 0, cron_jobs: 0, subagents: 0, lessons: 0 }),
    sessionsUsage: vi.fn().mockResolvedValue({ usage: null }),
    listApps: vi.fn().mockResolvedValue([]),
    kirocrewConfig: vi.fn().mockResolvedValue({ agent: { acp_backend: 'claude' } }),
    system: vi.fn().mockResolvedValue({ mem_used_gb: 4.0, mem_total_gb: 16.0, cpu_pct: 25.0, disk_total_gb: 100.0, disk_free_gb: 60.0 }),
    chatSlotAgent: vi.fn().mockResolvedValue({}),
    chatSlotReasoningEffort: vi.fn().mockResolvedValue({}),
    chatSlotModel: vi.fn().mockResolvedValue({}),
    chatMode: vi.fn().mockResolvedValue({}),
    listInstances: vi.fn().mockResolvedValue({ instances: [], warm_set_cap: 5 }),
    themes: vi.fn().mockResolvedValue({ themes: [] }),
    themeDetail: vi.fn().mockResolvedValue({}),
    themeBoot: vi.fn().mockResolvedValue({ mode: '', color: '', onboarded: true, import_onboarded: true }),
    updateThemeConfig: vi.fn().mockResolvedValue({}),
    onboardingImportScan: vi.fn().mockResolvedValue({ sources: [], skipped: [], merge_only: true }),
    onboardingImportState: vi.fn().mockResolvedValue({}),
    beaconStatus: vi.fn().mockResolvedValue({ enabled: true, would_send: true, reason: 'ready', endpoint_configured: true, env_override: false, env_var: 'KIROCREW_TELEMETRY_DISABLED' }),
    patchConfig: vi.fn().mockResolvedValue({}),
    createChatSlot: vi.fn().mockResolvedValue({ key: 's', title: 's', messages: 0, running: false }),
    chatSlotContext: vi.fn().mockResolvedValue({ ok: true }),
    sendChat: vi.fn().mockResolvedValue({ ok: true }),
  },
  isAuthBannerShown: vi.fn(() => false),
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) { super(message); this.status = status }
  },
}))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: query === '(max-width: 767px)',
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
})
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as typeof ResizeObserver

/** Last navigation's type + target, observed from inside the same router. */
function HistoryProbe() {
  const loc = useLocation()
  const type = useNavigationType()
  return <div data-testid="history-probe">{`${type} ${loc.pathname}${loc.search}`}</div>
}

const header = () => document.querySelector('header.topbar') as HTMLElement
const probe = () => screen.getByTestId('history-probe')

function renderAt(route: string) {
  localStorage.setItem('mc-onboarded', '1')
  return renderWithProviders(<><App /><HistoryProbe /></>, { route })
}

async function openShellDrawer() {
  const toggle = within(header()).getByTestId('mobile-topbar-navigation-toggle')
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  return screen.findByTestId('mobile-nav-rail')
}

const ROUTES: Array<[string, string]> = [
  ['/schedule', 'Schedule'],
  ['/artifacts', 'Artifacts'],
  ['/apps', 'Discover'],
  ['/capabilities', 'Agent Capabilities'],
  ['/settings', 'Settings'],
]

describe('phone: one navigation rail on every tab', () => {
  it.each(ROUTES)('%s: the header panel icon opens the shared rail with that tab active', async (route, activeName) => {
    renderAt(route)
    await screen.findByTestId('route-page')
    const h = header()
    // The 40px panel icon is the drawer trigger; no crew switcher in the bar.
    const toggle = within(h).getByTestId('mobile-topbar-navigation-toggle')
    expect(toggle).toHaveAccessibleName('Open menu')
    expect(toggle).toHaveClass('w-10', 'h-10')
    expect(h.querySelector('.instance-tab-bar-inline')).toBeNull()
    expect(within(h).queryByRole('button', { name: /Switch crew/ })).toBeNull()
    // Closed drawer: no rail in the tree.
    expect(screen.queryByTestId('mobile-nav-rail')).toBeNull()

    const rail = await openShellDrawer()
    expect(screen.getAllByTestId('mobile-nav-rail')).toHaveLength(1)
    expect(rail).toHaveAccessibleName('Main navigation')
    expect(rail).toHaveClass('w-[72px]', 'overflow-hidden')
    // Current crew chooser replaces the brand at the top, pinned outside the scroll.
    const chooser = await within(rail).findByTestId('navigation-crew-switcher')
    expect(chooser).toHaveAccessibleName('Local — Switch crew')
    expect(screen.queryByTestId('mobile-nav-rail-home')).toBeNull()
    const scroll = within(rail).getByTestId('mobile-nav-rail-scroll')
    expect(scroll).toHaveClass('overflow-y-auto', 'flex-1', 'min-h-0')
    expect(scroll).not.toContainElement(chooser)
    // Every destination scrolls together, Library included.
    for (const name of ['Sessions', 'Schedule', 'Artifacts', 'Discover', 'Library', 'Agent Capabilities', 'Settings']) {
      expect(within(scroll).getByRole('button', { name })).toBeInTheDocument()
    }
    const search = within(rail).getByTestId('mobile-nav-rail-search')
    expect(search).toHaveAccessibleName('Search sessions, files, and commands')
    expect(scroll).not.toContainElement(search)
    // Order: chooser, scroll region, Search.
    expect(chooser.compareDocumentPosition(scroll) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(scroll.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Exactly one active row, and it is this tab's.
    const active = [...rail.querySelectorAll('.nav-item.nav-active')]
    expect(active).toHaveLength(1)
    expect(active[0]).toHaveAccessibleName(activeName)
    // Captioned 64x56 touch tiles, each named.
    for (const row of within(scroll).getAllByRole('button')) {
      expect(row).toHaveAccessibleName()
      expect(row).toHaveClass('w-16', 'min-h-14', 'rounded-xl')
    }
  })

  it('shell drawer rows PUSH a history entry and close the drawer', async () => {
    renderAt('/schedule')
    await screen.findByTestId('route-page')
    const rail = await openShellDrawer()
    fireEvent.click(within(rail).getByRole('button', { name: 'Artifacts' }))
    await waitFor(() => expect(probe()).toHaveTextContent('PUSH /artifacts'))
    expect(within(header()).getByTestId('mobile-topbar-navigation-toggle')).toHaveAttribute('aria-expanded', 'false')
  })

  it('chat drawer rows REPLACE the drawer\'s duplicate history entry', async () => {
    renderAt('/chat')
    const rail = await screen.findByTestId('mobile-nav-rail')
    fireEvent.click(within(rail).getByRole('button', { name: 'Schedule' }))
    await waitFor(() => expect(probe()).toHaveTextContent('REPLACE /schedule'))
  })

  it('is keyboard-operable: Enter activates a row, Escape dismisses', async () => {
    renderAt('/settings')
    await screen.findByTestId('route-page')
    let rail = await openShellDrawer()
    // Tab order: the crew chooser first, Search last.
    const focusables = [...rail.querySelectorAll<HTMLElement>('button, [tabindex="0"]')]
    expect(focusables[0]).toBe(within(rail).getByTestId('navigation-crew-switcher'))
    expect(focusables[focusables.length - 1]).toBe(within(rail).getByTestId('mobile-nav-rail-search'))
    // Escape is the keyboard dismissal (the scrim is aria-hidden).
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(within(header()).getByTestId('mobile-topbar-navigation-toggle')).toHaveAttribute('aria-expanded', 'false')
    rail = await openShellDrawer()
    const row = within(rail).getByRole('button', { name: 'Schedule' })
    act(() => row.focus())
    fireEvent.keyDown(row, { key: 'Enter' })
    await waitFor(() => expect(probe()).toHaveTextContent('PUSH /schedule'))
  })

  it('pins Search: it opens the command palette from the shell drawer by keyboard', async () => {
    const user = userEvent.setup()
    renderAt('/artifacts')
    await screen.findByTestId('route-page')
    const rail = await openShellDrawer()
    within(rail).getByTestId('mobile-nav-rail-search').focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('dialog', { name: 'Search everywhere' })).toBeInTheDocument()
  })

  it('the current-crew chooser opens by keyboard and Add remote crew pushes the settings deep link', async () => {
    const user = userEvent.setup()
    renderAt('/schedule')
    await screen.findByTestId('route-page')
    const rail = await openShellDrawer()
    const chooser = await within(rail).findByTestId('navigation-crew-switcher')
    chooser.focus()
    await user.keyboard('{Enter}')
    const add = await screen.findByRole('menuitem', { name: 'Add remote crew' })
    await user.click(add)
    await waitFor(() => expect(probe()).toHaveTextContent('PUSH /settings/instances?highlight=key%3Aremote-crew-add'))
  })
})

describe('phone rail tile geometry (CSS contract)', () => {
  const css = readFileSync(join(__dirname, '..', 'index.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')

  it('paints selection on a 36px icon tile inside the 64x56 captioned target', () => {
    expect(css).toContain('.mobile-navigation-rail .nav-item > .nav-icon-frame > .app-icon-nav{width:36px;height:36px;border-radius:8px}')
    // Selection is the icon tile, never a full-tile slab.
    expect(css).toContain('.mobile-navigation-rail .nav-item.nav-active{background:transparent;')
    expect(css).toMatch(/\.mobile-navigation-rail \.nav-item\.nav-active > \.nav-icon-frame > \.app-icon-nav\{background:var\(--accent-subtle\)/)
  })

  it('lets long captions grow the touch target rather than clipping text', () => {
    renderWithProviders(<NavItem path="/apps/research" label="Research Notebook" icon={<span />} active={false} collapsed touch />)
    const row = screen.getByRole('button', { name: 'Research Notebook' })
    expect(row).toHaveClass('min-h-14', 'gap-0.5')
    expect(row).not.toHaveClass('h-14', 'gap-2.5')
    const caption = within(row).getByText('Research Notebook')
    expect(caption).toHaveClass('shrink-0', 'whitespace-normal', 'text-[11px]')
    expect(caption).not.toHaveClass('line-clamp-2')
  })

  it('gives the page scroller the shared workspace scrollbar class', () => {
    expect(css).toMatch(/\.workspace-scroll:focus-within\{scrollbar-color:var\(--muted\) transparent\}/)
    const layout = readFileSync(join(__dirname, '..', 'components', 'SidePanelLayout.tsx'), 'utf8')
    expect(layout).toContain('scrollbar-overlay workspace-scroll')
  })
})
