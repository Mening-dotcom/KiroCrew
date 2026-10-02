import { Glass } from './Glass'
import { KiroGhostMark } from './KiroGhostMark'

import { i18nT } from '../i18n/t'

/** The agent a chat runs on while Crew Mode is on. Crew Mode is a UI over the
 *  existing conductor: the switch is an ordinary agent switch to this name, and
 *  turning it off switches back to the default agent. Mirrors the backend's
 *  `agent_files.CREW_MODE_AGENT_NAME`. */
export const CREW_MODE_AGENT = 'kirocrew-conductor'

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
          ? 'border-aim bg-aim-subtle text-aim font-semibold'
          : 'border-transparent glass-hover text-muted hover:text-text'
      }`}
    >
      <KiroGhostMark size={13} />
      <span>{label}</span>
    </Glass>
  )
}
