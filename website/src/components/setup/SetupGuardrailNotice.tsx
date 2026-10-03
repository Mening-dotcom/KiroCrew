/**
 * The first run's guardrail notices (RFC one-chat first run §6.3 "Stall
 * watchdog", §6.7 "Quota awareness"): gateway-authored status rows that
 * `dashboard/setup_guardrails.py` posts in the first-run chat.
 *
 * - `setup_stalled` + `reason: no_output` — a turn made no progress for the
 *   watchdog's window (`meta.secs`).
 * - `setup_stalled` + `reason: kickoff_failed` — the first-run kickoff got no
 *   reply. Try again asks the gateway to send it once more
 *   (`POST /api/setup/first-run/retry`).
 * - `setup_quota` — the model allowance ran out; cards already shown and
 *   classic setup still work.
 *
 * The copy is keyed on `meta.kind` / `meta.reason`, never on the row's English
 * content, which is the fallback for surfaces without this renderer (the app
 * SDK's `SystemNoticeRow`). Every variant offers classic setup, the escape the
 * RFC requires next to any setup step.
 */
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { ApiError } from '../../api/apiError'
import { fmtElapsed } from '../../i18n/format'
import NoticeCard from '../../pages/chat/NoticeCard'
import type { ChatMessage } from '../../types'
import { parseErrorCode } from '../../utils/errorReport'
import ErrorNotice from '../ErrorNotice'
import { Btn } from '../ui'

/** Where classic setup lives: the four onboarding chapters. */
export const CLASSIC_SETUP_ROUTE = '/onboarding'

type GuardrailMeta = { kind?: unknown; reason?: unknown; secs?: unknown }

function guardrailMeta(m: Pick<ChatMessage, 'kind' | 'meta'>): { kind: string; reason: string; secs: number } | null {
  const meta = (m.meta ?? {}) as GuardrailMeta
  const kind = m.kind ?? meta.kind
  if (kind !== 'setup_stalled' && kind !== 'setup_quota') return null
  const secs = typeof meta.secs === 'number' && Number.isFinite(meta.secs) && meta.secs > 0 ? meta.secs : 90
  return { kind, reason: typeof meta.reason === 'string' ? meta.reason : '', secs }
}

/** An assistant row that is one of the first run's guardrail notices. */
export function isSetupGuardrailRow(m: Pick<ChatMessage, 'role' | 'kind' | 'meta'>): boolean {
  return m.role === 'assistant' && guardrailMeta(m) !== null
}

export default function SetupGuardrailNotice({ message }: { message: ChatMessage }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const retry = useMutation({ mutationFn: () => api.retryFirstRun() })
  const meta = guardrailMeta(message)
  if (!meta) return null

  const kickoff = meta.kind === 'setup_stalled' && meta.reason === 'kickoff_failed'
  const text =
    meta.kind === 'setup_quota'
      ? t('components.setupGuardrail.quota')
      : kickoff
        ? t('components.setupGuardrail.kickoff_failed')
        : t('components.setupGuardrail.no_output', { elapsed: fmtElapsed(meta.secs * 1000) })

  const code = retry.error instanceof ApiError ? parseErrorCode(retry.error.body) : undefined
  const retryError =
    code === 'kickoff_answered'
      ? t('components.setupGuardrail.retry_kickoff_answered')
      : code === 'turn_running'
        ? t('components.setupGuardrail.retry_turn_running')
        : t('components.setupCard.error_generic')

  return (
    <div className="w-full min-w-0 flex flex-col gap-2" data-testid="setup-guardrail-notice" data-kind={meta.kind} data-reason={meta.reason || undefined}>
      <NoticeCard
        content={text}
        tone="warn"
        actions={
          <>
            {kickoff && !retry.isSuccess && (
              <Btn
                type="button"
                onClick={() => retry.mutate()}
                disabled={retry.isPending}
                className="min-h-9"
                data-testid="setup-guardrail-retry"
              >
                {t('components.setupGuardrail.try_again')}
              </Btn>
            )}
            <Btn
              type="button"
              onClick={() => navigate(CLASSIC_SETUP_ROUTE)}
              className="border-transparent text-muted hover:text-text"
              data-testid="setup-guardrail-classic"
            >
              {t('components.setupCard.use_classic_setup')}
            </Btn>
          </>
        }
      />
      {retry.isError && (
        // A refusal of the retry request itself. No hand-off: the kickoff is what
        // did not start, so no agent can answer yet.
        <ErrorNotice title={retryError} message={retry.error instanceof Error ? retry.error.message : null} />
      )}
    </div>
  )
}
