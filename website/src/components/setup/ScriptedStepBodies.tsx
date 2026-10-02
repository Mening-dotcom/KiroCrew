/**
 * The bodies of the first run's scripted cards (UX.2, UX.3): the steps the
 * gateway shows before any model can answer. Registered in setupCardRegistry.
 *
 * - `harness`: pick the agent engine (the HarnessPicker seam).
 * - `harness_signin`: install and sign in to it. Kiro CLI (and KAS, which runs
 *   it) read the live Kiro CLI status; any other harness shows its own install
 *   command and its own sign-in sentence, verbatim from the gateway. Kiro Crew
 *   runs neither: these are commands for the owner's terminal.
 * - `path`: get started with tips, or a more detailed setup.
 *
 * Continue on the sign-in card is the check: the gateway asks the harness
 * whether it answers, and only a yes shows the next step. The same button reads
 * "Check again" until the live status says ready, then lights up as Continue.
 * After one failed check the owner may continue without it.
 */
import { useId, useState } from 'react'
import type React from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CircleCheck, Circle, ExternalLink } from 'lucide-react'

import { api, type AcpBackendProbe, type KiroPrerequisiteStatus } from '../../api/client'
import { Btn } from '../ui'
import CommandLine from './CommandLine'
import HarnessPicker, { type HarnessOption } from './HarnessPicker'
import type { SetupBodyProps } from './SetupCardBodies'

const LEAD = 'mt-1 text-[13px] leading-relaxed text-muted break-words'
/** How often a live sign-in card re-reads the harness's status. */
const STATUS_POLL_MS = 5_000

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

function readOptions(raw: unknown): HarnessOption[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap(o => {
    if (!o || typeof o !== 'object') return []
    const { id, label } = o as { id?: unknown; label?: unknown }
    return typeof id === 'string' && typeof label === 'string' && label ? [{ id, label }] : []
  })
}

// ── harness ────────────────────────────────────────────────────────────────

export function HarnessBody({ card, busy, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const name = useId()
  const options = readOptions(card.payload?.options)
  const defaultId = str(card.payload?.default)
  const [picked, setPicked] = useState<string>(() => str(card.payload?.current))
  // Which engines are already on the gateway host: a hint beside each option,
  // and nothing at all when the probe is unavailable (an older gateway).
  const probes = useQuery({
    queryKey: ['acp-backends', 'setup-card'],
    queryFn: () => api.acpBackends(),
    enabled: card.status === 'pending',
    retry: false,
    staleTime: STATUS_POLL_MS,
  })
  const installed = Object.fromEntries(
    (probes.data?.backends ?? []).map((row: AcpBackendProbe) => [row.id, row.installed]),
  ) as Record<string, 'installed' | 'missing' | 'unknown'>
  return (
    <>
      <p className={LEAD}>{t('components.setupCard.harness_lead')}</p>
      <HarnessPicker
        options={options}
        value={picked}
        onChange={setPicked}
        defaultId={defaultId}
        installed={installed}
        disabled={busy}
        name={`harness-${name}`}
      />
      {footer({
        primary: {
          label: t('components.setupCard.harness_continue'),
          onClick: () => run('commit', { backend: picked }),
          disabled: !options.some(o => o.id === picked),
        },
        decline: false,
      })}
    </>
  )
}

// ── harness_signin ─────────────────────────────────────────────────────────

function Step({ done, title, children, testId }: { done: boolean; title: string; children?: React.ReactNode; testId: string }) {
  const Icon = done ? CircleCheck : Circle
  return (
    <li className="flex items-start gap-2 min-w-0" data-testid={testId} data-done={done ? 'true' : 'false'}>
      <Icon className={`lucide-inline shrink-0 mt-[3px] ${done ? 'text-ok' : 'text-muted'}`} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className={`text-[13px] break-words ${done ? 'text-muted' : 'text-text font-medium'}`}>{title}</p>
        {!done && children ? <div className="mt-1.5 flex flex-col gap-1.5 min-w-0">{children}</div> : null}
      </div>
    </li>
  )
}

/** Kiro CLI, for the harnesses that run it: the live status the gate reads too. */
function KiroCliSteps({ status, label }: { status: KiroPrerequisiteStatus | undefined; label: string }) {
  const { t } = useTranslation()
  const installed = !!status?.installed
  const signedIn = installed && !!status?.authenticated
  return (
    <ol className="mt-2 flex flex-col gap-2 min-w-0" aria-live="polite" data-testid="setup-card-signin-steps">
      <Step
        done={installed}
        title={installed
          ? (status?.bundled_cli ? t('components.setupCard.signin_kiro_bundled') : t('components.setupCard.signin_kiro_installed'))
          : t('components.setupCard.signin_install', { label })}
        testId="setup-card-signin-install"
      >
        {status?.install_command ? (
          <CommandLine text={status.install_command} copyLabel={t('components.setupCard.copy_command')} testId="setup-card-install-command" />
        ) : null}
        {status?.docs_url ? (
          <a
            className="inline-flex items-center gap-1 text-[13px] text-accent hover:underline focus-ring self-start"
            href={status.docs_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t('components.setupCard.signin_kiro_setup_page')}
            <ExternalLink className="lucide-inline" aria-hidden="true" />
          </a>
        ) : null}
      </Step>
      <Step
        done={signedIn}
        title={signedIn ? t('components.setupCard.signin_done', { label }) : t('components.setupCard.signin_sign_in', { label })}
        testId="setup-card-signin-signin"
      >
        {installed && status?.login_command ? (
          <>
            <CommandLine text={status.login_command} copyLabel={t('components.setupCard.copy_command')} testId="setup-card-login-command" />
            <p className="text-[12px] leading-relaxed text-muted">{t('components.setupCard.signin_no_browser')}</p>
            {status.sso_login_command ? (
              <>
                <p className="text-[12px] leading-relaxed text-muted">{t('components.setupCard.signin_sso')}</p>
                <CommandLine text={status.sso_login_command} copyLabel={t('components.setupCard.copy_command')} testId="setup-card-sso-command" />
              </>
            ) : null}
          </>
        ) : null}
      </Step>
    </ol>
  )
}

/** Any other harness: its own install command and its own sign-in, as the gateway names them. */
function OwnSteps({ card, row, label }: { card: SetupBodyProps['card']; row: AcpBackendProbe | undefined; label: string }) {
  const { t } = useTranslation()
  const installed = row?.installed === 'installed'
  const installCommand = row?.install_command || str(card.payload?.install_command)
  const signIn = str(card.payload?.sign_in)
  return (
    <ol className="mt-2 flex flex-col gap-2 min-w-0" aria-live="polite" data-testid="setup-card-signin-steps">
      <Step
        done={installed}
        title={installed ? t('components.setupCard.signin_own_installed', { label }) : t('components.setupCard.signin_install', { label })}
        testId="setup-card-signin-install"
      >
        {installCommand ? (
          <CommandLine text={installCommand} copyLabel={t('components.setupCard.copy_command')} testId="setup-card-install-command" />
        ) : null}
        {row?.missing_components?.length ? (
          <p className="text-[12px] text-muted break-words" translate="no">{row.missing_components.join(', ')}</p>
        ) : null}
      </Step>
      <Step done={false} title={t('components.setupCard.signin_sign_in', { label })} testId="setup-card-signin-signin">
        {/* The harness's own sentence, from the gateway's declaration: verbatim,
            as the Agent Backend panel shows it. */}
        {signIn ? <p className="text-[13px] leading-relaxed text-text break-words">{signIn}</p> : null}
        <p className="text-[12px] leading-relaxed text-muted">{t('components.setupCard.signin_own_check', { label })}</p>
      </Step>
    </ol>
  )
}

export function HarnessSigninBody({ card, busy, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const label = str(card.payload?.label)
  const live = card.status === 'pending'
  const kiroCli = str(card.payload?.flow) === 'kiro_cli'
  const kiro = useQuery<KiroPrerequisiteStatus>({
    queryKey: ['kiro-prerequisite', 'setup-card'],
    // 'auto': the coalesced host probe, so several open tabs do not multiply it.
    queryFn: () => api.kiroPrerequisite('auto'),
    enabled: live && kiroCli,
    refetchInterval: live && kiroCli ? STATUS_POLL_MS : false,
    retry: false,
  })
  const probes = useQuery({
    queryKey: ['acp-backends', 'setup-card'],
    queryFn: () => api.acpBackends(),
    enabled: live && !kiroCli,
    refetchInterval: live && !kiroCli ? STATUS_POLL_MS : false,
    retry: false,
  })
  const row = (probes.data?.backends ?? []).find((r: AcpBackendProbe) => r.id === str(card.payload?.backend))
  // Kiro Crew never reads another harness's credentials, so for one of those
  // "ready" can only mean installed: Continue starts it once to ask.
  const ready = kiroCli ? !!kiro.data?.installed && !!kiro.data?.authenticated : row?.installed === 'installed'
  // A check came back no: the owner may now continue without one.
  const failedCheck = live && !!card.error && card.error.code.startsWith('harness_')
  return (
    <>
      <p className={LEAD}>{t('components.setupCard.signin_lead', { label })}</p>
      {kiroCli ? <KiroCliSteps status={kiro.data} label={label} /> : <OwnSteps card={card} row={row} label={label} />}
      {/* One button whatever the status: it is the check either way, and it
          lights up (and says Continue) once the live status reads ready. */}
      {footer({
        primary: {
          label: ready ? t('components.setupCard.signin_continue') : t('components.setupCard.signin_check'),
          onClick: () => run('commit'),
          lit: ready,
        },
        decline: false,
      })}
      {failedCheck && (
        <Btn
          type="button"
          onClick={() => run('commit', { skip: true })}
          disabled={busy}
          className="mt-1 self-start border-transparent text-muted hover:text-text"
          data-testid="setup-card-signin-skip"
        >
          {t('components.setupCard.signin_skip')}
        </Btn>
      )}
    </>
  )
}

// ── path ───────────────────────────────────────────────────────────────────

export function PathBody({ card, busy, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const name = useId()
  const [path, setPath] = useState<'tips' | 'detailed' | ''>('')
  const rows: { key: 'tips' | 'detailed'; label: string; hint: string }[] = [
    { key: 'tips', label: t('components.setupCard.path_tips'), hint: t('components.setupCard.path_tips_hint') },
    { key: 'detailed', label: t('components.setupCard.path_detailed'), hint: t('components.setupCard.path_detailed_hint') },
  ]
  return (
    <>
      <fieldset className="mt-2 min-w-0" data-testid="setup-card-path-options">
        <legend className="sr-only">{t('components.setupCard.title_path')}</legend>
        <div className="flex flex-col gap-1.5 min-w-0">
          {rows.map(row => (
            <label
              key={row.key}
              className={`flex items-center gap-2 rounded-md border px-3 py-2 text-[13px] cursor-pointer min-w-0 ${path === row.key ? 'border-accent bg-bg' : 'border-border'}`}
              data-testid={`setup-card-path-${row.key}`}
            >
              <input
                type="radio"
                name={`path-${name}-${card.id}`}
                className="shrink-0"
                aria-label={row.label}
                checked={path === row.key}
                disabled={busy}
                onChange={() => setPath(row.key)}
              />
              <span className="flex flex-col min-w-0 break-words">
                <span className="font-medium text-text">{row.label}</span>
                <span className="text-muted">{row.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {footer({
        primary: { label: t('components.setupCard.path_continue'), onClick: () => run('commit', { path }), disabled: !path },
        decline: false,
      })}
    </>
  )
}
