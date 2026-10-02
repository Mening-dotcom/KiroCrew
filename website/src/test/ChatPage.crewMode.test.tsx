/**
 * Crew Mode on ChatPage: the switch sits right of the memory chip, flips the
 * chat to the existing conductor through the ordinary agent switch, swaps the
 * empty chat's welcome page for the Crew Mode one, and stays above the composer
 * once the chat has messages so it can be turned off. WelcomeView is mocked to
 * nothing, so a Crew Mode page found here comes from ChatPage's own branch.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, act, fireEvent, waitFor, within } from '@testing-library/react'
import { Provider } from 'react-redux'
import { MemoryRouter } from 'react-router-dom'
import { configureStore } from '@reduxjs/toolkit'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import chatReducer from '../store/chatSlice'
import dashboardReducer from '../store/dashboardSlice'
import notificationsReducer from '../store/notificationsSlice'
import { ThemeProvider } from '../hooks/useTheme'
import type { RootState } from '../store'

interface VirtuosoMockProps {
  data?: unknown[]
  itemContent: (index: number, item: unknown) => ReactNode
}
vi.mock('react-virtuoso', () => ({ Virtuoso: ({ data, itemContent }: VirtuosoMockProps) => <div data-testid="virtuoso">{data?.map((d: unknown, i: number) => <div key={i}>{itemContent(i, d)}</div>)}</div> }))

type Msg = { role: string; content: string }
const detail = vi.hoisted(() => ({ messages: [] as Msg[] }))
const createChatSlot = vi.hoisted(() => vi.fn())
const deleteChatSlot = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
const chatSlotAgent = vi.hoisted(() => vi.fn(async (_slot: string, agent: string) => ({ agent, workspace: 'default' })))
const dashboardConfig = vi.hoisted(() => vi.fn().mockResolvedValue({}))
vi.mock('../api/client', () => ({
  api: {
    createChatSlot,
    deleteChatSlot,
    chatSlotAgent,
    dashboardConfig,
    chatSlots: vi.fn().mockResolvedValue([]),
    chatSlotDetail: vi.fn(async () => ({ messages: detail.messages, running: false, has_more: false, total: detail.messages.length })),
    chatHistory: vi.fn().mockResolvedValue({ sessions: [] }),
    models: vi.fn().mockResolvedValue([]),
    agents: vi.fn().mockResolvedValue([]),
    agentDetail: vi.fn().mockResolvedValue({}),
    workspaces: vi.fn().mockResolvedValue({ workspaces: [] }),
    slackChannels: vi.fn().mockResolvedValue([]),
    spawnList: vi.fn().mockResolvedValue({ agents: [] }),
  },
  SEARCH_MIN_CHARS: 2,
}))
vi.mock('../hooks/useVoiceInput', () => ({ useVoiceInput: () => ({ recording: false, transcribing: false, toggle: vi.fn() }), voiceInputSupported: false }))
vi.mock('../hooks/useBranding', () => ({ useBranding: () => ({ botName: 'Test', avatar: '' }) }))
vi.mock('../hooks/useAgents', () => ({ useAgents: () => ({ agents: [], defaultAgent: 'default' }) }))
vi.mock('../components/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <span>{content}</span> }))
vi.mock('../components/WelcomeView', () => ({ default: () => null }))
vi.mock('../components/MarkdownPanel', () => ({ default: () => null }))
vi.mock('../pages/chat/ActivityViewer', () => ({ default: () => null }))
vi.mock('../components/DetailPanel', () => ({ default: () => null }))
vi.mock('../hooks/useWebSocket', () => ({ useWebSocket: () => ({ subscribeLogs: () => {} }) }))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
})

import ChatPage from '../pages/ChatPage'
// The surface registry is populated by module side effect, and only `App.tsx`
// imports it in production -- so a harness that mounts ChatPage directly starts
// with an EMPTY registry and every surface lookup misses. Import it here for the
// same reason the app does. (The miss degrades safely to the surface-free
// sentence, which is what the unregistered-surface case below asserts.)
import '../surfaces/builtins'

type Slot = { messages: Msg[]; mode?: string; agent?: string; slotKeys?: string[] }

function makeStore({ messages, mode = '', agent = 'default', slotKeys = ['slot-a'] }: Slot) {
  return configureStore({
    reducer: { dashboard: dashboardReducer, chat: chatReducer, notifications: notificationsReducer },
    preloadedState: {
      dashboard: {
        status: null,
        slots: slotKeys.map(key => ({ key, messages: key === 'slot-a' ? messages.length : 0, running: false, mode: key === 'slot-a' ? mode : '', agent: key === 'slot-a' ? agent : 'default', pending_approval: false, waiting_for_input: false, last_activity_ts: undefined })),
        unreadSlots: [], refreshTrigger: 0, approvalMode: 'normal',
        subagentRunning: {}, subagentDetails: {}, subagentText: {},
      } as unknown as RootState['dashboard'],
      chat: {
        activeSlot: 'slot-a', messages,
        slotRunning: false, slotStopping: false, slotState: 'idle',
        history: [], historyHasMore: false, pendingInput: null,
        unresumableResume: null, lastResumeRequestId: null,
        subagents: {}, toolLog: [], activityOpen: false, activityTab: 'tools',
        slotHasMore: false, slotOldestIndex: 0, loadingOlder: false,
        slotStatusDetail: {}, slotContextPct: {}, slotActivity: {}, slotHistory: [],
        historyOffset: 0, _wsChunkedDuringFetch: false,
        slotMessages: {}, slotLoading: false,
      } as unknown as RootState['chat'],
      notifications: { items: [] } as unknown as RootState['notifications'],
    },
  })
}

async function renderWith(slot: Slot) {
  detail.messages = slot.messages
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const store = makeStore(slot)
  await act(async () => {
    render(
      <QueryClientProvider client={qc}>
        <Provider store={store}>
          <ThemeProvider>
            <MemoryRouter><ChatPage /></MemoryRouter>
          </ThemeProvider>
        </Provider>
      </QueryClientProvider>,
    )
  })
  return store
}

describe('Crew Mode on ChatPage', () => {
  it('sits right of the memory chip on the welcome state', async () => {
    await renderWith({ messages: [] })
    const row = screen.getByTestId('composer-memory-chip')
    const memory = within(row).getByTestId('memory-mode-chip')
    const crew = within(row).getByTestId('crew-mode-chip')
    expect(crew).toHaveAttribute('aria-pressed', 'false')
    expect(memory.compareDocumentPosition(crew) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByTestId('crew-mode-welcome')).toBeNull()
  })

  it('turns on through the ordinary agent switch to the conductor', async () => {
    const store = await renderWith({ messages: [] })
    fireEvent.click(screen.getByTestId('crew-mode-chip'))
    await waitFor(() => expect(chatSlotAgent).toHaveBeenCalledWith('slot-a', 'kirocrew-conductor', 'template'))
    await waitFor(() => expect(store.getState().dashboard.slots[0].agent).toBe('kirocrew-conductor'))
    expect(await screen.findByTestId('crew-mode-welcome')).toBeInTheDocument()
    expect(screen.getByTestId('crew-mode-chip')).toHaveAttribute('aria-pressed', 'true')
  })

  it('stays above the composer mid-chat and turns off back to the default agent', async () => {
    chatSlotAgent.mockClear()
    await renderWith({ messages: [{ role: 'user', content: 'hello' }], agent: 'kirocrew-conductor' })
    expect(screen.queryByTestId('memory-mode-chip')).toBeNull()
    const row = screen.getByTestId('composer-memory-chip')
    const chip = within(row).getByTestId('crew-mode-chip')
    expect(chip).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(chip)
    await waitFor(() => expect(chatSlotAgent).toHaveBeenCalledWith('slot-a', 'default'))
  })

  it('is not offered on another agent or on a mode session', async () => {
    await renderWith({ messages: [], agent: 'custom-x' })
    expect(screen.queryByTestId('crew-mode-chip')).toBeNull()
  })

  it('is not offered on a legacy orchestrator-mode session', async () => {
    await renderWith({ messages: [], mode: 'orchestrator' })
    expect(screen.queryByTestId('crew-mode-chip')).toBeNull()
  })
})
