import { X } from 'lucide-react'
import { Glass } from './Glass'
import { KiroGhostMark } from './KiroGhostMark'

import { i18nT } from '../i18n/t'

// The agent a chat runs on while Crew Mode is on lives in lib/crewMode.
export { CREW_MODE_AGENT } from '../lib/crewMode'

interface CrewModeChipProps {
  on: boolean
  onToggle: () => void
  /** Greyed out while a reply is running: the agent switch refuses mid-turn. */
  disabled?: boolean
}

/** The composer's Crew Mode switch, sitting to the right of the memory-mode chip.
 *  Same glass chip primitive as `MemoryModeChip`; `aria-pressed` carries the state. */
export function CrewModeChip({ on, onToggle, disabled = false }: CrewModeChipProps) {
  const label = on ? i18nT('components.crewMode.chip_on') : i18nT('components.crewMode.chip_off')
  const hint = on ? i18nT('components.crewMode.chip_hint_on') : i18nT('components.crewMode.chip_hint_off')
  return (
    <Glass
      as="button"
      variant="chip"
      radius={8}
      type="button"
      data-testid="crew-mode-chip"
      aria-pressed={on}
      title={hint}
      disabled={disabled}
      onClick={onToggle}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1 text-[12px] transition-colors cursor-pointer disabled:cursor-default disabled:opacity-60 ${
        on
          ? 'border-transparent glass-accent text-aim font-semibold'
          : 'border-transparent glass-hover text-muted hover:text-text'
      }`}
    >
      <KiroGhostMark size={13} />
      <span>{label}</span>
      {/* A visible "off" mark, so a reader who meets the chip mid-chat can see
          that clicking it turns the mode off. */}
      {on && <X size={12} aria-hidden="true" />}
    </Glass>
  )
}
