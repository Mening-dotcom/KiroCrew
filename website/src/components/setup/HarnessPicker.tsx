/**
 * The agent-engine picker the `harness` card draws (UX.2). A SEAM: another team
 * is building the full harness selector, and it replaces this component by
 * keeping these props. The options, their order and the default are the
 * gateway's (`setup_flow._harness_payload`, after governance narrowed the
 * selectable set); the card's commit re-checks the choice on the gateway.
 */
import { useTranslation } from 'react-i18next'

import { Badge } from '../ui'

export interface HarnessOption {
  id: string
  label: string
}

export interface HarnessPickerProps {
  options: HarnessOption[]
  value: string
  onChange: (id: string) => void
  /** The gateway's default (Kiro), badged in the list. */
  defaultId: string
  /** Whether each harness is installed on the gateway host, when known. */
  installed?: Record<string, 'installed' | 'missing' | 'unknown'>
  disabled?: boolean
  /** Unique per card, so two cards' radio groups never share a name. */
  name: string
}

export default function HarnessPicker({ options, value, onChange, defaultId, installed, disabled, name }: HarnessPickerProps) {
  const { t } = useTranslation()
  return (
    <fieldset className="mt-2 min-w-0" data-testid="setup-card-harness-options">
      <legend className="sr-only">{t('components.setupCard.title_harness')}</legend>
      <div className="flex flex-col gap-1.5 min-w-0">
        {options.map(option => {
          const state = installed?.[option.id]
          return (
            <label
              key={option.id || 'default'}
              className={`flex items-center gap-2 rounded-md border px-3 py-2 text-[13px] cursor-pointer min-w-0 ${value === option.id ? 'border-accent bg-bg' : 'border-border'}`}
              data-testid="setup-card-harness-option"
              data-harness={option.id}
            >
              <input
                type="radio"
                name={name}
                className="shrink-0"
                aria-label={option.label}
                checked={value === option.id}
                disabled={disabled}
                onChange={() => onChange(option.id)}
              />
              <span className="flex flex-wrap items-baseline gap-x-2 min-w-0 break-words">
                <span className="font-medium text-text" translate="no">{option.label}</span>
                {option.id === defaultId && <Badge variant="muted">{t('components.setupCard.harness_default')}</Badge>}
                {state === 'installed' && <span className="text-muted">{t('components.setupCard.harness_installed')}</span>}
                {state === 'missing' && <span className="text-muted">{t('components.setupCard.harness_not_installed')}</span>}
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}
