/**
 * Test: the crew switcher is reachable at phone widths.
 *
 * The switcher used to be gated out below 768px entirely, which left a phone
 * with no route to another crew at all. On the phone it is now the current-crew
 * chooser at the top of the shared navigation rail (every route; see
 * App.mobileNavEveryRoute.test.tsx), and the header's duplicate inline
 * switcher is removed. This pins that the chooser is MOUNTED on mobile, is the
 * only switcher, and its menu reaches the remote crew and Add remote crew.
 */
import { describe, it, expect, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from './helpers'
import type { RootState } from '../store'
import App from '../App'
import { useMobileNavRail } from '../components/MobileNavRailContext'

vi.mock('../hooks/useIsMobile', () => ({ useIsMobile: () => true }))
// Renders the rail it is handed, as the real page's sessions drawer does.
vi.mock('../pages/ChatPage', () => ({
  default: function ChatPageStub() {
    const rail = useMobileNavRail()
    return <div data-testid="chat-page">{rail?.({ onActivate: () => {}, replace: true })}</div>
  },
}))
vi.mock('../pages/SystemPage', () => ({ default: () => null }))
vi.mock('../pages/ProjectsPage', () => ({ default: () => null }))
vi.mock('../pages/LogsPage', () => ({ default: () => null }))
vi.mock('../pages/KiroCrewAgentsPage', () => ({ default: () => null }))
vi.mock('../pages/NotificationsPage', () => ({ default: () => null }))
vi.mock('../pages/SchedulePage', () => ({ default: () => null }))
vi.mock('../hooks/useWebSocket', () => ({ useWebSocket: () => ({ subscribeLogs: () => {} }) }))
vi.mock('../hooks/useAgents', () => ({ useAgents: vi.fn(() => ({ agents: [{ name: 'kirocrew' }], defaultAgent: 'kirocrew' })) }))
vi.mock('../providers/context', () => ({ useProvider: () => ({ id: 'acp' }) }))
vi.mock('../components/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <span>{content}</span>, Lightbox: () => null }))

// One remembered remote crew: the switcher renders only when at least one exists
// (visibleInstanceTabs), so a single-crew user's header is unchanged. Built inside
// vi.hoisted because the mock factory below is hoisted above module scope.
const { crew } = vi.hoisted(() => ({
  crew: {
    id: 'devbox',
    name: 'devbox',
    ssh_host: 'devbox.example',
    remote_port: 5476,
    local_port: 5500,
    ttl: '8h',
    remote_bin: 'kirocrew',
    connection_method: 'ssh',
    ssm_target: '',
    aws_profile: '',
    aws_region: '',
    ssm_run_as: '',
    was_connected: true,
    status: { state: 'connected', unread: 0 },
  },
}))

vi.mock('../api/client', () => ({
  api: {
    chatSlots: vi.fn().mockResolvedValue([]),
    notifications: vi.fn().mockResolvedValue({ notifications: [] }),
    status: vi.fn().mockResolvedValue({ uptime: '1h', sessions: 0, messages: 0, cron_jobs: 0, subagents: 0, lessons: 0 }),
    sessionsUsage: vi.fn().mockResolvedValue({ usage: null }),
    listApps: vi.fn().mockResolvedValue([]),
    system: vi.fn().mockResolvedValue({ mem_used_gb: 4, mem_total_gb: 16, cpu_pct: 25, disk_total_gb: 100, disk_free_gb: 60 }),
    chatSlotAgent: vi.fn().mockResolvedValue({}),
    chatSlotReasoningEffort: vi.fn().mockResolvedValue({}),
    chatSlotModel: vi.fn().mockResolvedValue({}),
    chatMode: vi.fn().mockResolvedValue({}),
    listInstances: vi.fn().mockResolvedValue({ active: true, instances: [crew], warm_set_cap: 5 }),
  },
  isAuthBannerShown: vi.fn(() => false),
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
    }
  },
}))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: query === '(prefers-color-scheme: dark)',
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
})
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver

describe('crew switcher at phone widths', () => {
  const state = {
    dashboard: { connected: true, status: { platform: 'linux' }, slots: [], approvalMode: 'normal' } as unknown as RootState['dashboard'],
  }

  it('mounts the current-crew chooser in the rail, not inline in the header', async () => {
    renderWithProviders(<App />, { route: '/chat', preloadedState: state })
    // The chooser at the top of the navigation rail is the phone's switcher: it
    // names the crew on screen and its menu lists every crew, so it alone is a
    // complete switcher.
    const rail = await screen.findByTestId('mobile-nav-rail')
    const chooser = await within(rail).findByTestId('navigation-crew-switcher')
    expect(chooser).toHaveAccessibleName(/^Local — Switch crew/)
    // The header's duplicate inline switcher is gone, so there is exactly one.
    const header = document.querySelector('header.topbar') as HTMLElement
    expect(header.querySelector('.instance-tab-bar-inline')).toBeNull()
    expect(screen.getAllByLabelText(/Switch crew/)).toEqual([chooser])
    // The nav-drawer toggle is not on this route (the chat drawer's rail is the
    // navigation), and the chat page's title slot must still be there.
    expect(screen.queryByLabelText('Open menu')).toBeNull()
    expect(screen.getByTestId('mobile-topbar-slot')).toBeTruthy()
  })

  it('lists the remembered remote crew and the Add action from the chooser', async () => {
    const user = userEvent.setup()
    renderWithProviders(<App />, { route: '/chat', preloadedState: state })
    const chooser = await screen.findByTestId('navigation-crew-switcher')
    await user.click(chooser)
    expect(await screen.findByRole('menuitemradio', { name: /devbox/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Add remote crew' })).toBeInTheDocument()
  })
})
