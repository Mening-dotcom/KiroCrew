import { describe, it, expect, vi } from 'vitest'
import { screen, fireEvent, within } from '@testing-library/react'
import { renderWithProviders } from '../test/helpers'
import { CrewModeChip, CREW_MODE_AGENT } from './CrewModeChip'
import CrewModeWelcome from './CrewModeWelcome'

describe('CrewModeChip', () => {
  it('runs on the existing conductor, not a spec of its own', () => {
    expect(CREW_MODE_AGENT).toBe('kirocrew-conductor')
  })

  it('reads off by default and reports its state through aria-pressed', () => {
    renderWithProviders(<CrewModeChip on={false} onToggle={vi.fn()} />)
    const chip = screen.getByTestId('crew-mode-chip')
    expect(chip).toHaveAttribute('aria-pressed', 'false')
    expect(chip).toHaveTextContent('Crew Mode')
    expect(chip).not.toHaveTextContent('Crew Mode on')
  })

  it('says it is on when on', () => {
    renderWithProviders(<CrewModeChip on onToggle={vi.fn()} />)
    const chip = screen.getByTestId('crew-mode-chip')
    expect(chip).toHaveAttribute('aria-pressed', 'true')
    expect(chip).toHaveTextContent('Crew Mode on')
  })

  it('calls onToggle on click', () => {
    const onToggle = vi.fn()
    renderWithProviders(<CrewModeChip on={false} onToggle={onToggle} />)
    fireEvent.click(screen.getByTestId('crew-mode-chip'))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('cannot be flipped while a reply is running', () => {
    const onToggle = vi.fn()
    renderWithProviders(<CrewModeChip on onToggle={onToggle} disabled />)
    const chip = screen.getByTestId('crew-mode-chip')
    expect(chip).toBeDisabled()
    fireEvent.click(chip)
    expect(onToggle).not.toHaveBeenCalled()
  })
})

describe('CrewModeWelcome', () => {
  it('says what it is in big type and what it does in four short labels', () => {
    renderWithProviders(<CrewModeWelcome />)
    const page = screen.getByTestId('crew-mode-welcome')
    expect(within(page).getByRole('heading', { name: 'Crew Mode' })).toBeInTheDocument()
    expect(page).toHaveTextContent('You set the goal.')
    expect(page).toHaveTextContent('Your crew does the work.')
    for (const label of ['New chat', 'Pass notes', 'Take asks', 'Check in']) {
      expect(within(page).getByText(label)).toBeInTheDocument()
    }
  })

  it('marks its task board as a preview, with the question that needs the person first', () => {
    renderWithProviders(<CrewModeWelcome />)
    const board = screen.getByRole('region', { name: 'Your crew' })
    expect(within(board).getByText('Preview')).toBeInTheDocument()
    const text = board.textContent ?? ''
    expect(text.indexOf('Which day works for you?')).toBeLessThan(text.indexOf('Email'))
    for (const state of ['Done', 'Working', 'Needs you', 'Waiting']) {
      expect(within(board).getByText(state)).toBeInTheDocument()
    }
  })
})
