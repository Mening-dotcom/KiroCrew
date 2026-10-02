/**
 * The gateway's scripted message before each first-run step card (UX.2): the
 * welcome and harness choice, the harness's sign-in, privacy, how to start. No
 * model can answer yet, so these are the chat's only words until the agent
 * takes over.
 *
 * The row is an `inject` row whose `meta.setupStep` names the step; its content
 * is the English breadcrumb the MODEL reads on replay, and is never drawn. The
 * words here come from the catalog by step name, like the guardrail notices.
 * A step this build does not know draws nothing.
 */
import { useTranslation } from 'react-i18next'

import type { SetupStepRef } from '../../api/setupCards'

const STEP_KEY = {
  welcome: 'components.setupStep.welcome',
  harness: 'components.setupStep.harness',
  signin: 'components.setupStep.signin',
  signin_again: 'components.setupStep.signin_again',
  privacy: 'components.setupStep.privacy',
  path: 'components.setupStep.path',
} as const

export default function SetupStepMessage({ step }: { step: SetupStepRef }) {
  const { t } = useTranslation()
  if (!Object.prototype.hasOwnProperty.call(STEP_KEY, step.step)) return null
  const name = step.step as keyof typeof STEP_KEY
  return (
    <div
      className="w-full max-w-2xl min-w-0 rounded-lg bg-card px-4 py-3"
      data-testid="setup-step-message"
      data-step={step.step}
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
        {t('components.setupStep.eyebrow')}
      </p>
      <p className="mt-1 text-[14px] leading-relaxed text-text break-words">
        {t(STEP_KEY[name], { label: step.label })}
      </p>
    </div>
  )
}
