import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  defaultMessageRenderers,
  mergeRenderers,
  resolveRenderer,
  type MessageRenderContext,
} from '../app-sdk/messageRenderers'
import SkillLoadCard, {
  isSkillLoadRow,
  readSkillLoad,
} from '../pages/chat/SkillLoadCard'
import { createTranscriptRenderers } from '../pages/chat/transcriptRenderers'
import type { ChatMessage } from '../types'

const message = (over: Partial<ChatMessage> = {}): ChatMessage => ({
  role: 'system',
  content: 'Loaded skill(s) via `$`: **explain-for**',
  cls: '',
  ...over,
})

const loaded = message({
  meta: {
    kind: 'skill_load',
    skills: [
      {
        name: 'explain-for',
        body: '---\n# Explain for\n\n- Start with the audience\n- Use `plain words`\n\n---\n\n## Steps\n\nKeep both sections.',
      },
    ],
  },
})

const context = (messages: ChatMessage[]): MessageRenderContext => ({
  index: 0,
  messages,
  running: false,
  key: 'skill-row',
  hideCardOwnedOAuth: false,
  autoDeniedIds: new Set<string>(),
  wrapper: children => children,
  row: children => children,
})

describe('readSkillLoad', () => {
  it('validates the structured body snapshot without altering its markdown', () => {
    expect(readSkillLoad(loaded)).toEqual([
      {
        name: 'explain-for',
        body: '---\n# Explain for\n\n- Start with the audience\n- Use `plain words`\n\n---\n\n## Steps\n\nKeep both sections.',
      },
    ])
  })

  it('recovers names from a legacy notice without inventing unavailable bodies', () => {
    expect(readSkillLoad(message({
      content: '\u{1F4CE} Loaded skill(s) via `$`: **first, second**',
    }))).toEqual([
      { name: 'first', body: '' },
      { name: 'second', body: '' },
    ])
  })
})

describe('SkillLoadCard', () => {
  it('uses the singular title and book icon for one loaded skill', () => {
    const { container } = render(<SkillLoadCard message={loaded} />)

    expect(screen.getByText('Loaded skill')).toBeInTheDocument()
    expect(screen.queryByText('Loaded skills')).not.toBeInTheDocument()
    expect(container.querySelector('.lucide-book-open')).not.toBeNull()
    expect(container.querySelector('.lucide-paperclip')).toBeNull()
  })

  it('is collapsed by default and expands through a native button', () => {
    const { container } = render(<SkillLoadCard message={loaded} disclosureKey="skill-row" />)

    const card = screen.getByTestId('skill-load-card')
    expect(card).toHaveAttribute('data-expanded', 'false')
    expect(screen.getByText('Loaded skill')).toBeInTheDocument()
    expect(screen.getByText('explain-for')).toBeInTheDocument()
    expect(screen.queryByTestId('skill-load-card-body')).not.toBeInTheDocument()
    expect(container.textContent).not.toContain('Start with the audience')

    const toggle = screen.getByRole('button', { name: /Loaded skill/i })
    expect(toggle.tagName).toBe('BUTTON')
    expect(toggle).toHaveAttribute('type', 'button')
    fireEvent.click(toggle)

    expect(card).toHaveAttribute('data-expanded', 'true')
    const body = screen.getByTestId('skill-load-card-body')
    expect(body.querySelector('.skill-load-markdown')).not.toBeNull()
    expect(screen.getAllByText('Instructions given to the agent for this turn')).toHaveLength(1)
    expect(body.querySelector('h1')).toHaveTextContent('Explain for')
    expect(body.querySelector('code')).toHaveTextContent('plain words')
    expect(screen.getByRole('heading', { name: 'Steps' })).toBeInTheDocument()
    expect(body).toHaveTextContent('Keep both sections.')
    expect(body.textContent).not.toContain('description: explain clearly')
  })

  it('renders several skill snapshots in one disclosure', () => {
    const multi = message({
      meta: {
        kind: 'skill_load',
        skills: [
          { name: 'first', body: '# First\nONE' },
          { name: 'second', body: '# Second\nTWO' },
        ],
      },
    })
    render(<SkillLoadCard message={multi} />)

    expect(screen.getByText('Loaded skills')).toBeInTheDocument()
    expect(screen.getByText('first, second')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Loaded skills/i }))
    expect(screen.getAllByText('Instructions given to the agent for this turn')).toHaveLength(1)
    expect(screen.getByRole('heading', { name: 'First' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Second' })).toBeInTheDocument()
  })

  it('shows and clears the bottom scroll cue for a capped body', () => {
    const long = message({
      meta: {
        kind: 'skill_load',
        skills: [{ name: 'long-skill', body: `# Long\n${'line\n'.repeat(20_000)}` }],
      },
    })
    render(<SkillLoadCard message={long} />)
    fireEvent.click(screen.getByRole('button', { name: /Loaded skill/i }))

    const body = screen.getByTestId('skill-load-card-body')
    expect(body.className).toMatch(/max-h-\[/)
    expect(body).toHaveClass('overflow-y-auto', 'overflow-x-hidden', 'min-w-0')
    expect(body).toHaveAttribute('tabindex', '0')

    Object.defineProperties(body, {
      clientHeight: { configurable: true, value: 384 },
      scrollHeight: { configurable: true, value: 768 },
      scrollTop: { configurable: true, writable: true, value: 0 },
    })
    fireEvent.scroll(body)
    expect(body).toHaveClass('markdown-disclosure-scroll-more')
    expect(body).toHaveAttribute('data-scroll-more', '')

    body.scrollTop = 384
    fireEvent.scroll(body)
    expect(body).not.toHaveClass('markdown-disclosure-scroll-more')
    expect(body).not.toHaveAttribute('data-scroll-more')
  })

  it('renders a legacy name-only notice as a finished non-interactive card', () => {
    const legacy = message({
      content: '\u{1F4CE} Loaded skill(s) via `$`: **old-skill**',
    })
    const { container } = render(<SkillLoadCard message={legacy} />)

    expect(screen.getByText('Loaded skill')).toBeInTheDocument()
    expect(screen.getByText('old-skill')).toBeInTheDocument()
    expect(screen.getByText('Instructions were not saved for this turn')).toBeInTheDocument()
    expect(container.querySelector('[role="button"]')).toBeNull()
    expect(screen.queryByTestId('skill-load-card-body')).not.toBeInTheDocument()
  })
})

describe('loaded-skill renderer registration', () => {
  it('matches only structured or legacy skill-load system rows', () => {
    expect(isSkillLoadRow(loaded)).toBe(true)
    expect(isSkillLoadRow(message({ content: '\u{1F4CE} Loaded skill(s) via `$`: **old**' }))).toBe(true)
    expect(isSkillLoadRow(message({ content: 'ordinary system row' }))).toBe(false)
    expect(isSkillLoadRow(message({ role: 'assistant', meta: loaded.meta }))).toBe(false)
  })

  it('resolves through both dashboard and SDK registries ahead of generic system rows', () => {
    expect(resolveRenderer(loaded, defaultMessageRenderers)?.id).toBe('skill_load')
    const dashboard = mergeRenderers(createTranscriptRenderers({ slot: 's1' }))
    expect(resolveRenderer(loaded, dashboard)?.id).toBe('skill_load')

    const entry = resolveRenderer(loaded, dashboard)!
    const { container } = render(<>{entry.render(loaded, context([loaded]))}</>)
    expect(container.querySelector('[data-testid="skill-load-card"]')).not.toBeNull()
  })
})
