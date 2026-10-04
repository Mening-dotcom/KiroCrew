// The pinned prompt's band is opaque. The card is a narrow right-aligned
// bubble, and with a transparent band the reply scrolling up beneath it ran on
// beside the bubble, its lines cut where the bubble sat (the proof run's vault
// and move-in frames: "What's going on?" over its own answer). Behind an opaque
// band a row is either below the band and readable or under it and hidden whole.
// No layout in happy-dom, so this pins the structure that paints it.
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import PinnedPrompt from '../pages/chat/PinnedPrompt'

function renderCard(over: Partial<Parameters<typeof PinnedPrompt>[0]> = {}) {
  return render(
    <PinnedPrompt
      text="What's going on?"
      fullText="What's going on?"
      images={[]}
      bodyBeyondPreview={false}
      pushUp={0}
      bannerH={40}
      expanded={false}
      onToggleExpanded={() => {}}
      onJump={() => {}}
      onCollapsedHeight={() => {}}
      {...over}
    />,
  )
}

describe('PinnedPrompt backing', () => {
  beforeEach(() => {
    if (!('ResizeObserver' in window)) {
      ;(window as unknown as { ResizeObserver: unknown }).ResizeObserver = class { observe() {} disconnect() {} }
    }
  })

  it('backs the band with the page colour, beneath the card', () => {
    renderCard()
    const backing = screen.getByTestId('pinned-prompt-backing')
    const card = screen.getByTestId('pinned-prompt')
    expect(backing.className).toMatch(/\bbg-bg\b/)
    expect(backing.getAttribute('aria-hidden')).toBe('true')
    // It spans the band the card sits in, and comes first so the card paints over it.
    expect(backing.parentElement?.contains(card)).toBe(true)
    expect(backing.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('keeps its fade outside the clip the push opens', () => {
    // A push clips the band so the card is revealed away; the fade that softens
    // text passing under the band must not pop out of existence with it.
    renderCard({ pushUp: 12 })
    const backing = screen.getByTestId('pinned-prompt-backing')
    expect(backing.closest('[style*="overflow: hidden"]')).toBeNull()
    expect(backing.querySelector('.bg-gradient-to-b')).not.toBeNull()
  })
})
