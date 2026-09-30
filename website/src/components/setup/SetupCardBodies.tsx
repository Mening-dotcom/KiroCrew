/**
 * The per-kind bodies of a SetupCard. Each renders what its card's payload
 * shows, then hands its actions to the shared `footer`, which owns the error
 * notice, the button row (at most a primary and a secondary) and the link row.
 * Which body draws which kind is `setupCardRegistry.tsx`.
 *
 * Payload fields are read defensively: the payload is server data validated on
 * the gateway, but a field of the wrong type must degrade to "not shown", never
 * to a crash inside the transcript.
 */
import { useId, useRef, useState } from 'react'
import type React from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Circle, CircleCheck, CircleSlash, CircleX, ExternalLink, FlaskConical, Loader2 } from 'lucide-react'

import { api } from '../../api/client'
import type { SetupCard, SetupDecision } from '../../api/setupCards'
import { fmtCurrency, fmtList, fmtNumber, fmtUnit } from '../../i18n/format'
import { useImeGuard } from '../../hooks/useImeGuard'
import ErrorNotice from '../ErrorNotice'
import MarkdownRenderer from '../MarkdownRenderer'
import SegmentedControl from '../SegmentedControl'
import { Btn, Checkbox, Input } from '../ui'
import { NativeSelect, NativeSelectOption } from '../ui/native-select'
import {
  PrivacyCommandList,
  PrivacyDisclosureSections,
  TelemetryToggle,
  type BeaconStatus,
} from '../PrivacyDisclosure'
import CommandLine from './CommandLine'
import CronPreviewApprovals from './CronPreviewApprovals'
import { safeConsentUrl, soulFileName, type HomeLeftover } from './setupCardCopy'

export interface SetupAction {
  label: string
  onClick: () => void
  disabled?: boolean
}

export interface SetupActions {
  primary?: SetupAction
  secondary?: SetupAction
  /** `false` hides "Not now" (the privacy card is mandatory). */
  decline?: boolean
  /** The words on "Not now" when declining means something specific. */
  declineLabel?: string
}

export type SetupFooter = (actions: SetupActions) => React.ReactNode

export interface SetupBodyProps {
  card: SetupCard
  busy: boolean
  run: (decision: SetupDecision, input?: Record<string, unknown>) => void
  /** Commit with a secret read from `el` (and cleared from it) at click time. */
  runWithSecret: (field: 'value' | 'token', el: HTMLInputElement | null) => void
  footer: SetupFooter
  /** Drawn in the tray above the composer: long-running states keep to a
   *  line or two so they do not crowd the composer. */
  compact?: boolean
}

/** The chat command that pairs a messaging account. A wire token: never translated. */
export const PAIR_COMMAND = '/pair'

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x) : [])

const LEAD = 'mt-1 text-[13px] leading-relaxed text-muted break-words'

// ── a kind this build does not know ────────────────────────────────────────

/**
 * A card whose kind the gateway registers and this dashboard build does not
 * (the gateway gained it first). It shows the title and the two decisions and
 * nothing from the payload: this build has no idea which fields are safe or
 * meaningful to show, so it shows none. The commit is still hash-bound and
 * checked on the gateway like any other.
 */
export function FallbackBody({ run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  return <>{footer({ primary: { label: t('components.setupCard.fallback_approve'), onClick: () => run('commit') } })}</>
}

// ── privacy ────────────────────────────────────────────────────────────────

/**
 * The SAME disclosure the Privacy chapter renders — the same components and
 * the same catalog keys — so the first-run card cannot paraphrase it. The
 * toggle persists on change exactly as it does there; Continue also carries the
 * position the owner is looking at, because the card's commit is what records
 * the acknowledgement.
 */
export function PrivacyBody({ run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const status = useQuery<BeaconStatus>({ queryKey: ['beaconStatus'], queryFn: () => api.beaconStatus() })
  return (
    <>
      <p className={LEAD}>{t('components.privacyChapter.subtitle')}</p>
      <div className="mt-3">
        <TelemetryToggle />
      </div>
      <div className="mt-4 border-t border-border pt-4">
        <PrivacyDisclosureSections />
        <p className="mt-4 mb-2 text-sm leading-relaxed text-muted">{t('privacyDisclosure.controlsBody')}</p>
        <PrivacyCommandList />
      </div>
      {footer({
        primary: {
          label: t('components.privacyChapter.continue'),
          onClick: () => run('commit', { telemetry: status.data?.enabled ?? false }),
          // What is sent is what the toggle shows, so wait until it shows something.
          disabled: status.isPending,
        },
        decline: false,
      })}
    </>
  )
}

// ── profile ────────────────────────────────────────────────────────────────

const PROFILE_FIELD_KEY = {
  bot_name: 'components.setupCard.field_bot_name',
  language: 'components.setupCard.field_language',
  timezone: 'components.setupCard.field_timezone',
  technical_level: 'components.setupCard.field_technical_level',
  role: 'components.setupCard.field_role',
} as const

const TECHNICAL_LEVEL_KEY = {
  codes: 'components.setupCard.level_codes',
  'somewhat-technical': 'components.setupCard.level_somewhat_technical',
  'non-technical': 'components.setupCard.level_non_technical',
} as const

export function ProfileBody({ card, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const fields = (card.payload?.fields ?? {}) as Record<string, unknown>
  const rows = (Object.keys(PROFILE_FIELD_KEY) as Array<keyof typeof PROFILE_FIELD_KEY>)
    .map(name => [name, str(fields[name])] as const)
    .filter(([, value]) => value !== '')
  return (
    <>
      <p className={LEAD}>{t('components.setupCard.profile_intro')}</p>
      <dl className="mt-2 grid grid-cols-1 sm:grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[13px]">
        {rows.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted">{t(PROFILE_FIELD_KEY[name])}</dt>
            <dd className="text-text min-w-0 break-words mb-1 sm:mb-0">
              {name === 'technical_level' && value in TECHNICAL_LEVEL_KEY
                ? t(TECHNICAL_LEVEL_KEY[value as keyof typeof TECHNICAL_LEVEL_KEY])
                : value}
            </dd>
          </div>
        ))}
      </dl>
      {footer({ primary: { label: t('components.setupCard.save'), onClick: () => run('commit') } })}
    </>
  )
}

// ── soul ───────────────────────────────────────────────────────────────────

export function SoulBody({ card, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const p = card.payload ?? {}
  const content = str(p.content)
  const previous = typeof p.previous === 'string' ? p.previous : null
  const [view, setView] = useState<'after' | 'before'>('after')
  const shown = view === 'before' && previous !== null ? previous : content
  return (
    <>
      <p className={LEAD}>
        {p.file === 'USER' ? t('components.setupCard.soul_intro_user') : t('components.setupCard.soul_intro_soul')}
      </p>
      {previous !== null && (
        <div className="mt-2">
          <SegmentedControl<'after' | 'before'>
            segments={[
              { key: 'after', label: t('components.setupCard.soul_after') },
              { key: 'before', label: t('components.setupCard.soul_before') },
            ]}
            value={view}
            onChange={setView}
            layoutId={`setup-soul-${card.id}`}
            ariaLabel={t('components.setupCard.soul_versions', { file: soulFileName(p.file) })}
            collapse={false}
          />
        </div>
      )}
      <pre
        className="mt-2 max-h-72 overflow-auto rounded-md border border-border bg-bg px-3 py-2 font-mono text-[12px] leading-5 text-text whitespace-pre-wrap break-words"
        data-testid="setup-card-soul-content"
      >
        {shown}
      </pre>
      {footer({ primary: { label: t('components.setupCard.save'), onClick: () => run('commit') } })}
    </>
  )
}

// ── import ─────────────────────────────────────────────────────────────────

interface ImportCategory { id: string; label: string; count: number }
interface ImportSource { id: string; name: string; categories: ImportCategory[] }
interface ImportJob { name: string; schedule_human: string; note: string }

function readSources(raw: unknown): ImportSource[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((s): ImportSource[] => {
    if (!s || typeof s !== 'object') return []
    const src = s as Record<string, unknown>
    const id = str(src.id)
    if (!id) return []
    const categories = (Array.isArray(src.categories) ? src.categories : []).flatMap((c): ImportCategory[] => {
      if (!c || typeof c !== 'object') return []
      const cat = c as Record<string, unknown>
      const cid = str(cat.id)
      return cid ? [{ id: cid, label: str(cat.label) || cid, count: typeof cat.count === 'number' ? cat.count : 0 }] : []
    })
    return [{ id, name: str(src.name) || id, categories }]
  })
}

function readJobs(raw: unknown): ImportJob[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((j): ImportJob[] => {
    if (!j || typeof j !== 'object') return []
    const job = j as Record<string, unknown>
    const name = str(job.name)
    return name ? [{ name, schedule_human: str(job.schedule_human), note: str(job.note) }] : []
  })
}

/** One (source, category) pair as a set key; JSON keeps the two ids unambiguous. */
const pairKey = (sourceId: string, categoryId: string) => JSON.stringify([sourceId, categoryId])

export function ImportBody({ card, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const sources = readSources(card.payload?.sources)
  const jobs = readJobs(card.payload?.jobs)
  // Default: everything checked. Only the UNCHECKED pairs are tracked, because
  // that is what the commit sends.
  const [excluded, setExcluded] = useState<ReadonlySet<string>>(() => new Set())
  const allPairs = sources.flatMap(s => s.categories.map(c => pairKey(s.id, c.id)))
  const nothingSelected = allPairs.length > 0 && allPairs.every(k => excluded.has(k)) && jobs.length === 0
  const toggle = (key: string, checked: boolean) => {
    setExcluded(prev => {
      const next = new Set(prev)
      if (checked) next.delete(key)
      else next.add(key)
      return next
    })
  }
  const commit = () => {
    const exclude = sources.flatMap(s =>
      s.categories
        .filter(c => excluded.has(pairKey(s.id, c.id)))
        .map(c => ({ source_id: s.id, category_id: c.id })),
    )
    run('commit', { exclude })
  }
  return (
    <>
      <p className={LEAD}>{t('components.setupCard.import_intro')}</p>
      <div className="mt-2 flex flex-col gap-3">
        {sources.map(source => (
          <fieldset key={source.id} className="min-w-0">
            <legend className="text-[13px] font-medium text-text break-words">{source.name}</legend>
            <div className="mt-1 flex flex-col gap-1" role="group" aria-label={t('components.setupCard.import_categories_of', { name: source.name })}>
              {source.categories.map(category => {
                const key = pairKey(source.id, category.id)
                return (
                  <label key={key} className="flex items-center gap-2 min-w-0 text-[13px] text-text cursor-pointer">
                    <Checkbox
                      checked={!excluded.has(key)}
                      onChange={e => toggle(key, e.currentTarget.checked)}
                      data-testid={`setup-card-import-${source.id}-${category.id}`}
                    />
                    <span className="min-w-0 break-words">{category.label}</span>
                    <span className="ml-auto shrink-0 text-muted tabular-nums">{fmtNumber(category.count)}</span>
                  </label>
                )
              })}
            </div>
          </fieldset>
        ))}
        {jobs.length > 0 && (
          <div className="min-w-0">
            <div className="text-[13px] font-medium text-text">{t('components.setupCard.import_jobs_heading')}</div>
            <ul className="mt-1 flex flex-col gap-1">
              {jobs.map((job, i) => (
                <li key={`${job.name}-${i}`} className="text-[13px] min-w-0 break-words">
                  <span className="text-text">{job.name}</span>
                  {job.schedule_human && <span className="text-muted"> · {job.schedule_human}</span>}
                  {job.note && <div className="text-[12px] text-muted">{job.note}</div>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {nothingSelected && <p className="text-[12px] text-muted">{t('components.setupCard.import_nothing_selected')}</p>}
      </div>
      {footer({
        primary: { label: t('components.setupCard.import_commit'), onClick: commit, disabled: nothingSelected },
      })}
    </>
  )
}

// ── connect ────────────────────────────────────────────────────────────────

export function ConnectBody({ card, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const provider = (card.payload?.provider ?? {}) as Record<string, unknown>
  const name = str(provider.name) || str(provider.slug)
  const outcome = card.outcome ?? {}
  const consentUrl = safeConsentUrl(outcome.oauth_url)
  if (card.status === 'waiting' || (card.status === 'working' && outcome.state)) {
    return (
      <>
        {consentUrl ? (
          <div className="mt-2 flex flex-col gap-2">
            <a
              href={consentUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 self-start rounded-md bg-accent px-3 py-1.5 text-[13px] font-semibold text-accent-fg hover:bg-accent-hover"
              data-testid="setup-card-consent-link"
            >
              <ExternalLink className="lucide-inline" aria-hidden="true" />
              {t('components.setupCard.connect_open_consent')}
            </a>
            <p className="text-[13px] text-muted inline-flex items-center gap-1.5" role="status">
              <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
              {t('components.setupCard.connect_waiting', { name })}
            </p>
          </div>
        ) : (
          <p className="mt-2 text-[13px] text-muted inline-flex items-center gap-1.5" role="status">
            <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
            {t('components.setupCard.connect_minting')}
          </p>
        )}
        {footer({})}
      </>
    )
  }
  return (
    <>
      <p className={LEAD}>{t('components.setupCard.connect_intro', { name })}</p>
      {card.payload?.needs_client_config === true && (
        <p className="mt-1 text-[13px] leading-relaxed text-muted break-words" data-testid="setup-card-needs-client">
          {t('components.setupCard.connect_needs_client', { name })}
        </p>
      )}
      {footer({ primary: { label: t('components.setupCard.connect_commit', { name }), onClick: () => run('commit') } })}
    </>
  )
}

// ── credential + channel ───────────────────────────────────────────────────

export function CredentialBody(props: SetupBodyProps) {
  return <SecretBody {...props} field="value" />
}

export function ChannelBody(props: SetupBodyProps) {
  return <SecretBody {...props} field="token" />
}

/**
 * A credential (`field="value"`) or a messaging bot token (`field="token"`).
 *
 * The field is UNCONTROLLED on purpose: its value never enters React state.
 * `hasValue` is the only thing tracked, as a boolean, to enable the button.
 * The commit reads the value off the element and clears it in the same tick
 * (`runWithSecret`). `autoComplete="off"` plus the password-manager opt-outs
 * keep it from being offered for saving as a login.
 */
function SecretBody({ card, busy, runWithSecret, footer, field }: SetupBodyProps & { field: 'value' | 'token' }) {
  const { t } = useTranslation()
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [hasValue, setHasValue] = useState(false)
  const ime = useImeGuard()
  const p = card.payload ?? {}
  const isChannel = card.kind === 'channel'
  const outcome = card.outcome ?? {}
  const submit = () => {
    if (!inputRef.current?.value) return
    runWithSecret(field, inputRef.current)
    setHasValue(false)
  }

  if (isChannel && card.status === 'waiting') {
    const code = str(outcome.pair_code) || (typeof outcome.pair_code === 'number' ? String(outcome.pair_code) : '')
    const bot = str(outcome.bot_username).replace(/^@/, '')
    const pairMessage = `${PAIR_COMMAND} ${code}`
    return (
      <>
        <p className={LEAD}>
          {bot
            ? t('components.setupCard.channel_pair_bot', { bot })
            : t('components.setupCard.channel_pair_any')}
        </p>
        {code ? (
          <>
            <div className="mt-2">
              <CommandLine text={pairMessage} copyLabel={t('components.setupCard.channel_copy_pair')} testId="setup-card-pair" />
            </div>
            <p className="mt-1 text-[12px] text-muted">{t('components.setupCard.channel_pair_once')}</p>
          </>
        ) : (
          // The code lives in the gateway's memory only, so a restart drops it.
          <p className="mt-2 text-[12px] text-muted" data-testid="setup-card-pair-lost">
            {t('components.setupCard.channel_pair_lost')}
          </p>
        )}
        <p className="mt-2 text-[13px] text-muted inline-flex items-center gap-1.5" role="status">
          <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
          {t('components.setupCard.waiting')}
        </p>
        {footer({})}
      </>
    )
  }

  const name = str(p.name)
  const label = str(p.label) || str(p.channel)
  const hosts = strList(p.hosts)
  return (
    <>
      {isChannel ? (
        <p className={LEAD}>{t('components.setupCard.channel_intro', { label })}</p>
      ) : (
        <>
          {str(p.purpose) && <p className={LEAD}>{str(p.purpose)}</p>}
          {hosts.length > 0 && (
            <p className="mt-1 text-[12px] text-muted break-words">
              {t('components.setupCard.credential_hosts', { hosts: fmtList(hosts) })}
            </p>
          )}
          <p className="mt-1 text-[12px] text-muted">{t('components.setupCard.credential_note')}</p>
        </>
      )}
      <div className="mt-3 flex flex-col gap-1 min-w-0">
        <label htmlFor={inputId} className="text-[12px] font-medium text-text">
          {isChannel ? t('components.setupCard.channel_token_label') : t('components.setupCard.credential_input_label', { name })}
        </label>
        <Input
          id={inputId}
          ref={inputRef}
          type="password"
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          data-lpignore="true"
          data-1p-ignore="true"
          placeholder={t('components.setupCard.credential_placeholder')}
          disabled={busy}
          onInput={e => setHasValue(e.currentTarget.value.length > 0)}
          {...ime.bindComposition()}
          onKeyDown={e => {
            if (e.key === 'Enter' && ime.claimEnter(e)) submit()
          }}
          className="w-full flex-none font-mono"
          data-testid="setup-card-secret"
        />
      </div>
      {footer({
        primary: {
          label: isChannel ? t('components.setupCard.channel_commit') : t('components.setupCard.credential_commit'),
          onClick: submit,
          disabled: !hasValue,
        },
      })}
    </>
  )
}

// ── cron ───────────────────────────────────────────────────────────────────

/** How the approvals a preview run asked for ended (`outcome.preview.approvals`). */
interface CronApprovals { asked: number; notGiven: number; waitSecs: number }

interface CronPreview {
  status: 'success' | 'failure' | 'timeout'
  text: string
  approvals: CronApprovals | null
  /** The run needed an approval nobody gave, which is why it is a failure. */
  approvalNotGiven: boolean
}

const whole = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0)

function readApprovals(raw: unknown): CronApprovals | null {
  if (!raw || typeof raw !== 'object') return null
  const a = raw as Record<string, unknown>
  const asked = whole(a.asked)
  return asked ? { asked, notGiven: whole(a.rejected) + whole(a.unanswered), waitSecs: whole(a.wait_secs) } : null
}

function readPreview(raw: unknown): CronPreview | null {
  if (!raw || typeof raw !== 'object') return null
  const p = raw as Record<string, unknown>
  const status = p.status === 'success' || p.status === 'failure' || p.status === 'timeout' ? p.status : null
  if (!status) return null
  const approvals = readApprovals(p.approvals)
  return { status, text: str(p.text), approvals, approvalNotGiven: p.reason === 'approval_not_given' && !!approvals?.notGiven }
}

/** An unattended request's wait, in words ("3 minutes"). */
function fmtWait(secs: number): string {
  return secs % 60 === 0
    ? fmtUnit(secs / 60, 'minute', { unitDisplay: 'long' })
    : fmtUnit(secs, 'second', { unitDisplay: 'long' })
}

/**
 * "Preview now, then keep it" (RFC §5.4). Before a preview, and after one that
 * failed, the primary action runs one; once a preview succeeded, "Keep it"
 * becomes primary and re-running is the secondary. While a preview runs, the
 * approvals it waits on are answered here; after one that asked, the card says
 * the job will ask on every run.
 */
export function CronBody({ card, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const p = card.payload ?? {}
  const preview = readPreview(card.outcome?.preview)
  const schedule = str(p.schedule_human)
  const timezone = str(p.timezone)
  const keep = { label: t('components.setupCard.cron_keep'), onClick: () => run('commit') }
  const runPreview = (label: string) => ({ label, onClick: () => run('preview') })
  const approvals = preview?.approvals ?? null
  return (
    <>
      {str(p.name) && <p className="mt-1 text-[13px] font-medium text-text break-words">{str(p.name)}</p>}
      {str(p.prompt_summary) && <p className={LEAD}>{str(p.prompt_summary)}</p>}
      {schedule && (
        <p className="mt-1 text-[12px] text-muted break-words">
          {timezone
            ? t('components.setupCard.cron_schedule_tz', { schedule, timezone })
            : t('components.setupCard.cron_schedule', { schedule })}
        </p>
      )}
      {card.status === 'working' && <CronPreviewApprovals cardId={card.id} />}
      {preview ? (
        <div className="mt-3 min-w-0" data-testid="setup-card-preview" data-preview-status={preview.status}>
          <div className="text-[12px] font-medium text-muted mb-1">{t('components.setupCard.cron_preview_heading')}</div>
          {preview.status === 'success' ? (
            // A bg-bg well inside a bg-card box carries its own edge
            // (theming-contract: the two tokens can be identical).
            <div className="max-h-80 overflow-auto rounded-md border border-border bg-bg px-3 py-2 text-[13px] min-w-0">
              <MarkdownRenderer content={preview.text} softBreaks readOnlyCode />
            </div>
          ) : (
            // The preview run is settled server-side and this card holds no
            // draft, so the hand-off loses nothing.
            <ErrorNotice
              title={preview.status === 'timeout'
                ? t('components.setupCard.cron_preview_timeout')
                : t('components.setupCard.cron_preview_failed')}
              message={preview.approvalNotGiven && approvals
                ? t('components.setupCard.cron_preview_approval_not_given', { count: approvals.notGiven })
                : preview.text || t('components.setupCard.error_generic')}
              messagePlacement="below"
              askAgent
            />
          )}
          {approvals && (
            // No auto-approval is offered: keeping the job keeps it asking.
            <div className="mt-2 text-[12px] text-muted break-words" data-testid="setup-card-preview-asks">
              <p>{t('components.setupCard.cron_approvals_every_run', { count: approvals.asked })}</p>
              {approvals.waitSecs > 0 && (
                <p className="mt-1">{t('components.setupCard.cron_approvals_wait', { wait: fmtWait(approvals.waitSecs) })}</p>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="mt-2 text-[12px] text-muted">{t('components.setupCard.cron_preview_hint')}</p>
      )}
      {footer(preview?.status === 'success'
        ? { primary: keep, secondary: runPreview(t('components.setupCard.cron_run_again')) }
        : { primary: runPreview(t('components.setupCard.cron_run_preview')), secondary: keep })}
    </>
  )
}

// ── service ────────────────────────────────────────────────────────────────

export function ServiceBody({ card, run, footer }: SetupBodyProps) {
  const { t } = useTranslation()
  const p = card.payload ?? {}
  const command = str(p.command)
  if (p.platform === 'unsupported') {
    return (
      <>
        <p className={LEAD}>{t('components.setupCard.service_unsupported')}</p>
        {footer({})}
      </>
    )
  }
  if (p.needs_terminal === true) {
    return (
      <>
        <p className={LEAD}>{t('components.setupCard.service_terminal')}</p>
        {command && (
          <div className="mt-2">
            <CommandLine text={command} copyLabel={t('components.setupCard.copy_command')} testId="setup-card-command" />
          </div>
        )}
        {footer({ primary: { label: t('components.setupCard.service_check'), onClick: () => run('commit') } })}
      </>
    )
  }
  return (
    <>
      <p className={LEAD}>{t('components.setupCard.service_intro')}</p>
      {footer({ primary: { label: t('components.setupCard.service_keep'), onClick: () => run('commit') } })}
    </>
  )
}

// ── home ───────────────────────────────────────────────────────────────────

/** How long past its `expires_ts` a waiting AWS sign-in counts as lost; the
 *  gateway's `_STALE_GRACE_SECS` (dashboard/setup_aws_signin.py) decides it. */
const AWS_SIGNIN_STALE_GRACE_SECS = 30

/** The AWS CLI's official install page. A URL, not copy: never translated. */
export const AWS_CLI_INSTALL_URL = 'https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html'

type StepState = 'pending' | 'active' | 'done' | 'failed' | 'skipped'
interface HomeStep { key: string; label: string; state: StepState; detail: string }

const STEP_STATES: ReadonlySet<string> = new Set(['pending', 'active', 'done', 'failed', 'skipped'])

function readSteps(raw: unknown): HomeStep[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((s, i): HomeStep[] => {
    if (!s || typeof s !== 'object') return []
    const step = s as Record<string, unknown>
    const label = str(step.label)
    if (!label) return []
    const state = (typeof step.state === 'string' && STEP_STATES.has(step.state) ? step.state : 'pending') as StepState
    return [{ key: str(step.key) || String(i), label, state, detail: str(step.detail) }]
  })
}

/** Each step state's screen-reader word; the glyph carries it visually. */
const STEP_STATE_KEY = {
  pending: 'components.setupCard.home_step_pending',
  active: 'components.setupCard.home_step_active',
  done: 'components.setupCard.home_step_done',
  failed: 'components.setupCard.home_step_failed',
  skipped: 'components.setupCard.home_step_skipped',
} as const

/** A vertical progress list: one row per build (or move) step. */
function StepList({ steps, testId }: { steps: HomeStep[]; testId: string }) {
  const { t } = useTranslation()
  if (steps.length === 0) return null
  return (
    <ol className="mt-2 flex flex-col gap-1.5 min-w-0" data-testid={testId} aria-live="polite">
      {steps.map(step => {
        const Icon =
          step.state === 'done' ? CircleCheck
            : step.state === 'failed' ? CircleX
              : step.state === 'skipped' ? CircleSlash
                : step.state === 'active' ? Loader2
                  : Circle
        const tone =
          step.state === 'done' ? 'text-ok'
            : step.state === 'failed' ? 'text-danger'
              : step.state === 'active' ? 'text-accent animate-spin'
                : 'text-muted'
        return (
          <li key={step.key} className="flex items-start gap-2 min-w-0 text-[13px]" data-state={step.state}>
            <Icon className={`lucide-inline shrink-0 mt-[3px] ${tone}`} aria-hidden="true" />
            <span className="min-w-0 break-words">
              <span className="sr-only">{t(STEP_STATE_KEY[step.state])} </span>
              <span className={step.state === 'pending' || step.state === 'skipped' ? 'text-muted' : 'text-text'}>{step.label}</span>
              {step.detail && <span className="block text-[12px] text-muted">{step.detail}</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

/**
 * A failed home card's "Remove what it created" (the `remove` decision): the stack
 * it deletes before the click, then the removal running, then "Removed" once AWS
 * confirms the stack is gone, or that it did not finish (and the button again).
 */
export function HomeLeftoverRemoval({ leftover, state, busy, error, onRemove }: {
  leftover: HomeLeftover
  state: string
  busy: boolean
  /** A refused click, in the owner's words. */
  error: string
  onRemove: () => void
}) {
  const { t } = useTranslation()
  if (state === 'done') {
    return (
      <p className="flex items-start gap-1.5 text-[13px] text-text min-w-0 break-words" role="status" data-testid="setup-card-home-removed">
        <CircleCheck className="lucide-inline shrink-0 text-ok" aria-hidden="true" />
        {t('components.setupCard.home_removed', { stack: leftover.stack })}
      </p>
    )
  }
  if (state === 'active') {
    return (
      <p className="flex items-center gap-1.5 text-[13px] text-muted" role="status" data-testid="setup-card-home-removing">
        <Loader2 className="lucide-inline animate-spin shrink-0" aria-hidden="true" />
        {t('components.setupCard.home_removing')}
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-1.5 min-w-0" data-testid="setup-card-home-remove">
      {state === 'failed' && (
        <p className="text-[13px] text-danger min-w-0 break-words" data-testid="setup-card-home-remove-failed">
          {t('components.setupCard.home_remove_failed')}
        </p>
      )}
      <p className="text-[12px] text-muted min-w-0 break-words">
        {t('components.setupCard.home_remove_what', { stack: leftover.stack, region: leftover.region })}
      </p>
      {error && (
        <p className="text-[13px] text-danger min-w-0 break-words" role="alert" data-testid="setup-card-home-remove-error">{error}</p>
      )}
      <Btn
        type="button"
        danger
        onClick={onRemove}
        disabled={busy}
        className="self-start min-h-9"
        data-testid="setup-card-home-remove-button"
      >
        {t('components.setupCard.home_remove')}
      </Btn>
    </div>
  )
}

/** An AWS region code, the shape setup_cards.build_home accepts. */
const REGION_RE = /^[a-z]{2}(?:-[a-z]+)+-\d$/

/**
 * A permanent home in the owner's AWS account (RFC §10.1), in two phases:
 * build it (the chat carries on meanwhile), then move in. Every figure the
 * owner is agreeing to — the monthly cost, who bills it, the size, the region —
 * is on the card before the first click, and a simulated run says so in plain
 * sight.
 */
export function HomeBody({ card, busy, run, footer, compact }: SetupBodyProps) {
  const { t } = useTranslation()
  const regionSelectId = useId()
  // Where the owner picked, while the card asks where the crew lives.
  const [where, setWhere] = useState<'' | 'here' | 'cloud'>('')
  // The region the owner picked on the card while AWS named none.
  const [pickedRegion, setPickedRegion] = useState<string | null>(null)
  // The owner opened the AWS sign-up in another tab. Local and untimed: only
  // they know when the account exists, so the card waits for their click.
  const [creatingAccount, setCreatingAccount] = useState(false)
  // The size the owner picked; until they pick, the one the gateway suggests.
  const [pickedSize, setPickedSize] = useState<string | null>(null)
  const p = card.payload ?? {}
  const o = card.outcome ?? {}
  const size = (p.size ?? {}) as Record<string, unknown>
  const simulated = p.simulated === true
  // The payload says whether AWS was signed in when the card was shown; a
  // sign-in from the card lands in the outcome, since the payload never changes.
  const signedIn = simulated || p.aws_signed_in === true || o.aws_signed_in === true
  const awsSignin = (o.aws_signin ?? null) as Record<string, unknown> | null
  const awsSigninState = awsSignin ? str(awsSignin.state) : ''
  const steps = readSteps(o.steps)
  const moveSteps = readSteps(o.move_steps)
  const ready = o.ready === true
  const signin = (o.signin ?? null) as Record<string, unknown> | null
  const signinUrl = signin ? safeConsentUrl(signin.url) : null
  const signinCode = signin ? str(signin.code) : ''
  const buildError = str(o.error)

  const badge = simulated && (
    <p
      className="mt-1 inline-flex items-center gap-1 self-start rounded border border-border bg-bg px-1.5 text-[11px] font-medium text-warn"
      data-testid="setup-card-simulated"
    >
      <FlaskConical className="lucide-inline" size={12} aria-hidden="true" />
      {t('components.setupCard.home_simulated')}
    </p>
  )

  // Phase 2 in flight: moving the crew and this chat.
  if (moveSteps.length > 0 && (card.status === 'working' || card.status === 'waiting')) {
    return (
      <>
        {badge}
        <p className={LEAD}>{t('components.setupCard.home_moving')}</p>
        <StepList steps={moveSteps} testId="setup-card-move-steps" />
        {footer({})}
      </>
    )
  }

  // Signing in to AWS: the AWS CLI opened a sign-in tab in this computer's browser.
  if (card.status === 'waiting' && awsSigninState === 'waiting') {
    const expires = typeof awsSignin?.expires_ts === 'number' ? awsSignin.expires_ts : null
    // Past its time with the card still waiting: the gateway lost the sign-in
    // (a restart), and the gateway accepts a fresh start.
    const stalled = expires !== null && Date.now() / 1000 > expires + AWS_SIGNIN_STALE_GRACE_SECS
    const cancel = { label: t('components.setupCard.home_aws_signin_cancel'), onClick: () => run('aws_signin', { cancel: true }) }
    return (
      <>
        {badge}
        {stalled ? (
          <p className={LEAD} data-testid="setup-card-home-aws-stalled">{t('components.setupCard.home_aws_signin_stalled')}</p>
        ) : (
          <p className="mt-1 text-[13px] text-muted inline-flex flex-wrap items-center gap-x-1.5 min-w-0" role="status" data-testid="setup-card-home-aws-waiting">
            <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
            <span>{t('components.setupCard.home_aws_signin_waiting')}</span>
          </p>
        )}
        {footer(stalled
          ? { primary: { label: t('components.setupCard.home_aws_signin_retry'), onClick: () => run('aws_signin') }, secondary: cancel }
          : { secondary: cancel })}
      </>
    )
  }

  // Phase 1 in flight: the build runs in the background.
  if (card.status === 'waiting' || (card.status === 'working' && steps.length > 0)) {
    // In the tray a build that takes minutes keeps to one line: where it is
    // now. The sign-in prompt below still shows in full, since the build waits
    // on it.
    const current = steps.find(s => s.state === 'active') ?? steps.find(s => s.state === 'failed')
      ?? [...steps].reverse().find(s => s.state === 'done')
    return (
      <>
        {badge}
        {compact ? (
          <p className="mt-1 text-[13px] text-muted inline-flex flex-wrap items-center gap-x-1.5 min-w-0" role="status" data-testid="setup-card-home-compact">
            <Loader2 className="lucide-inline animate-spin" aria-hidden="true" />
            <span>{t('components.setupCard.home_compact_building')}</span>
            {current && <span className="text-text break-words">· {t('components.setupCard.home_compact_step', { step: current.label })}</span>}
          </p>
        ) : (
          <>
            <p className={LEAD}>{t('components.setupCard.home_building_note')}</p>
            <StepList steps={steps} testId="setup-card-steps" />
          </>
        )}
        {signin && (signinUrl || signinCode) && (
          <div className="mt-3 flex flex-col gap-2 min-w-0" data-testid="setup-card-home-signin">
            <p className="text-[13px] text-text">{t('components.setupCard.home_signin_intro')}</p>
            {signinUrl && (
              <a
                href={signinUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 self-start rounded-md bg-accent px-3 py-1.5 text-[13px] font-semibold text-accent-fg hover:bg-accent-hover"
                data-testid="setup-card-home-signin-link"
              >
                <ExternalLink className="lucide-inline" aria-hidden="true" />
                {t('components.setupCard.home_signin_open')}
              </a>
            )}
            {signinCode && (
              <CommandLine text={signinCode} copyLabel={t('components.setupCard.home_copy_code')} testId="setup-card-home-signin-code" />
            )}
          </div>
        )}
        {/* The build reported a failure it is still waiting out (a retry, a
            rollback). Server state only, so the hand-off loses nothing. */}
        {buildError && (
          <div className="mt-2">
            <ErrorNotice message={buildError} askAgent testId="setup-card-home-build-error" />
          </div>
        )}
        {footer({})}
      </>
    )
  }

  // Built, but its own Kiro sign-in never finished (the code ran out, or a
  // restart cut the wait short): its agent cannot answer, so no Move in yet.
  if (o.needs_signin === true && !ready) {
    return (
      <>
        {badge}
        <p className={LEAD} data-testid="setup-card-home-needs-signin">{t('components.setupCard.home_needs_signin')}</p>
        <StepList steps={steps} testId="setup-card-steps" />
        {footer({ primary: { label: t('components.setupCard.home_signin_start'), onClick: () => run('commit') } })}
      </>
    )
  }

  // Built: offer the move.
  if (ready) {
    const automatic = o.auto_move === true && !card.error
    return (
      <>
        {badge}
        <p className={LEAD}>{t(automatic ? 'components.setupCard.home_moving' : 'components.setupCard.home_ready')}</p>
        {/* After a failed move the card is back here with the move's steps on
            it: show those, so the step that failed (and its reason, in its
            detail) stays visible beside the retry. */}
        {moveSteps.length > 0
          ? <StepList steps={moveSteps} testId="setup-card-move-steps" />
          : <StepList steps={steps} testId="setup-card-steps" />}
        {footer(automatic ? {} : { primary: { label: t('components.setupCard.home_move_in'), onClick: () => run('commit') } })}
      </>
    )
  }

  // The first run's own question, asked when its first job is kept: this
  // machine settles the card, the cloud moves the same card on to its steps.
  if (p.step === 'choose' && (card.status === 'pending' || card.status === 'working')) {
    const fromUsd = typeof p.from_usd === 'number' ? p.from_usd : null
    const rows: { key: 'here' | 'cloud'; label: string; hint: string }[] = [
      {
        key: 'here',
        label: t('components.setupCard.home_choice_here'),
        hint: t('components.setupCard.home_choice_here_hint'),
      },
      {
        key: 'cloud',
        label: t('components.setupCard.home_choice_cloud'),
        hint: fromUsd !== null
          ? t('components.setupCard.home_choice_cloud_hint', { amount: fmtCurrency(fromUsd, 'USD', { maximumFractionDigits: 0 }) })
          : t('components.setupCard.home_choice_cloud_hint_no_price'),
      },
    ]
    return (
      <>
        {badge}
        <p className={LEAD}>{t('components.setupCard.home_choice_lead')}</p>
        <fieldset className="mt-2 min-w-0" data-testid="setup-card-home-choice">
          <legend className="sr-only">{t('components.setupCard.title_home_offer')}</legend>
          <div className="flex flex-col gap-1.5 min-w-0">
            {rows.map(row => (
              <label
                key={row.key}
                className={`flex items-center gap-2 rounded-md border px-3 py-2 text-[13px] cursor-pointer min-w-0 ${where === row.key ? 'border-accent bg-bg' : 'border-border'}`}
                data-testid={`setup-card-home-choice-${row.key}`}
              >
                <input
                  type="radio"
                  name={`home-choice-${card.id}`}
                  className="shrink-0"
                  aria-label={row.label}
                  checked={where === row.key}
                  disabled={busy}
                  onChange={() => setWhere(row.key)}
                />
                <span className="flex flex-wrap items-baseline gap-x-2 min-w-0 break-words">
                  <span className="font-medium text-text">{row.label}</span>
                  <span className="text-muted">{row.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {footer({
          primary: {
            label: t('components.setupCard.home_choice_continue'),
            onClick: () => run('choose', { where }),
            disabled: !where,
          },
        })}
      </>
    )
  }

  // Not built yet: everything the owner is agreeing to, then the build button.
  const sizeOptions = readSizeOptions(p.size_options)
  const selected = sizeOptions.find(opt => opt.key === pickedSize)
    ?? sizeOptions.find(opt => opt.key === str(p.size_default))
    ?? sizeOptions[0]
  const plan = (p.plan ?? null) as Record<string, unknown> | null
  const build = () => (selected ? run('commit', { size: selected.key }) : run('commit'))
  const monthly = selected ? selected.monthly_usd : typeof p.monthly_usd === 'number' ? p.monthly_usd : null
  const vcpu = typeof size.vcpu === 'number' ? size.vcpu : null
  const ram = typeof size.ram_gb === 'number' ? size.ram_gb : null
  const account = str(o.aws_account) || str(p.aws_account)
  // The browser sign-in cannot open where Kiro Crew runs: the gateway sent the
  // terminal command that works on that host instead.
  const remoteCommand = awsSigninState === 'remote' ? str(awsSignin?.command) : ''
  // No AWS account yet: the gateway sends the sign-up page (the Builder ID one
  // when Kiro signs in with Builder ID). It opens in a new tab, never in the card.
  const signupUrl = signedIn ? null : safeConsentUrl(p.signup_url)
  const creating = creatingAccount && !!signupUrl
  const cliMissing = !signedIn && p.aws_cli_installed === false
  // No region answered for this account: the owner picks one, and the gateway
  // checks it and shows the card again for it. After a pick AWS did not answer
  // either, the owner may still build in it.
  const regionChoices = signedIn && !simulated && p.region_unknown === true
    ? strList(p.region_choices).filter(r => REGION_RE.test(r))
    : []
  const cardRegion = str(p.region)
  const region = pickedRegion ?? (regionChoices.includes(cardRegion) ? cardRegion : regionChoices[0] ?? '')
  const askRegion = regionChoices.length > 0
    && !(region === cardRegion && card.status === 'pending' && card.error?.code === 'home_region_no_answer')
  const buildOrSignIn = signedIn
    ? { label: t('components.setupCard.home_build'), onClick: build }
    : creating
      ? {
          label: t('components.setupCard.home_aws_signup_done'),
          onClick: () => {
            setCreatingAccount(false)
            run('aws_signin')
          },
        }
      : remoteCommand
        ? { label: t('components.setupCard.home_build_signed_in'), onClick: build }
        : { label: t('components.setupCard.home_aws_signin'), onClick: () => run('aws_signin') }
  const primary = askRegion
    ? { label: t('components.setupCard.home_region_use'), onClick: () => run('region', { region }), disabled: !region }
    : buildOrSignIn
  // One muted line: the AWS sign-in, the account and where the home is built.
  const meta = simulated
    ? cardRegion
    : !signedIn
      ? t('components.setupCard.home_meta_not_signed_in', { region: cardRegion })
      : account
        ? t('components.setupCard.home_meta_signed_in', { account, region: cardRegion })
        : t('components.setupCard.home_meta_signed_in_plain', { region: cardRegion })
  return (
    <>
      {badge}
      {meta && (
        <p className="mt-1 text-[12px] text-muted min-w-0 break-words" data-testid="setup-card-home-meta">{meta}</p>
      )}
      {!simulated && <p className="mt-1 text-[12px] text-muted">{t('components.setupCard.home_auto_move')}</p>}
      {!signedIn && (
        <div className="mt-2 flex flex-col gap-2 min-w-0" data-testid="setup-card-home-aws-signin">
          {creating ? (
            <p className="text-[13px] text-text" role="status" data-testid="setup-card-home-aws-creating">
              {t('components.setupCard.home_aws_signup_creating')}
            </p>
          ) : remoteCommand ? (
            <>
              <p className="text-[13px] text-muted">{t('components.setupCard.home_aws_signin_remote')}</p>
              <CommandLine text={remoteCommand} copyLabel={t('components.setupCard.copy_command')} testId="setup-card-home-aws-command" />
            </>
          ) : (
            <p className="text-[13px] text-muted">{t('components.setupCard.home_aws_signin_intro')}</p>
          )}
          {signupUrl && !creating && (
            <div className="flex flex-col gap-1.5 min-w-0" data-testid="setup-card-home-aws-signup-block">
              <p className="text-[13px] text-muted">
                {p.signup_builder_id === true
                  ? t('components.setupCard.home_aws_signup_hint_builder_id')
                  : t('components.setupCard.home_aws_signup_hint')}
              </p>
              <a
                href={signupUrl}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => setCreatingAccount(true)}
                className="inline-flex items-center gap-1.5 self-start rounded-md border border-border bg-card px-3 py-1.5 text-[13px] font-medium text-text hover:bg-bg"
                data-testid="setup-card-home-aws-signup"
              >
                <ExternalLink className="lucide-inline" aria-hidden="true" />
                {t('components.setupCard.home_aws_signup')}
              </a>
            </div>
          )}
          {cliMissing && (
            <p className="text-[13px] text-muted" data-testid="setup-card-home-aws-cli-missing">
              {t('components.setupCard.home_aws_cli_needed')}{' '}
              <a
                href={AWS_CLI_INSTALL_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-accent underline underline-offset-2 hover:text-text"
                data-testid="setup-card-home-aws-cli-install"
              >
                {t('components.setupCard.home_aws_cli_install')}
                <ExternalLink className="lucide-inline" aria-hidden="true" />
              </a>
            </p>
          )}
        </div>
      )}
      {sizeOptions.length === 0 && monthly !== null && (
        <p className="mt-1 text-[13px] font-medium text-text break-words" data-testid="setup-card-home-cost">
          {t('components.setupCard.home_cost', {
            amount: fmtCurrency(monthly, 'USD', { maximumFractionDigits: 0 }),
            billedBy: str(p.billed_by),
          })}
        </p>
      )}
      {sizeOptions.length === 0 && (str(size.label) || str(size.instance_type)) && (
        <p className="mt-1 text-[13px] text-text min-w-0 break-words" data-testid="setup-card-home-size">
          {t('components.setupCard.home_size', {
            label: str(size.label) || str(size.instance_type),
            instanceType: str(size.instance_type),
            vcpu: vcpu !== null ? fmtNumber(vcpu) : '—',
            ram: ram !== null ? fmtUnit(ram, 'gigabyte') : '—',
          })}
        </p>
      )}
      {sizeOptions.length > 0 && selected && (
        <HomeSizeOptions
          name={`home-size-${card.id}`}
          options={sizeOptions}
          selected={selected.key}
          suggested={str(p.size_default)}
          onPick={setPickedSize}
          planType={plan ? str(plan.type) : ''}
          signedIn={signedIn}
          errorCode={card.status === 'pending' ? str(card.error?.code) : ''}
        />
      )}
      {regionChoices.length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5 min-w-0" data-testid="setup-card-home-region-pick">
          <label htmlFor={regionSelectId} className="text-[13px] text-text">
            {t('components.setupCard.home_region_unknown')}
          </label>
          <NativeSelect
            id={regionSelectId}
            value={region}
            disabled={busy}
            onChange={e => setPickedRegion(e.target.value)}
            translate="no"
            wrapperStyle={{ maxWidth: '16rem' }}
            data-testid="setup-card-home-region-select"
          >
            {regionChoices.map(r => <NativeSelectOption key={r} value={r}>{r}</NativeSelectOption>)}
          </NativeSelect>
        </div>
      )}
      {footer(creating
        ? { primary, secondary: { label: t('components.setupCard.home_aws_signup_back'), onClick: () => setCreatingAccount(false) } }
        : { primary })}
    </>
  )
}

interface HomeSizeOption {
  key: string
  label: string
  /** What the size runs well, as a code the card words (`setup_cards.HOME_SIZE_OFFERS`). */
  note: string
  instance_type: string
  vcpu: number
  ram_gb: number
  monthly_usd: number
  free_plan_ok: boolean
  credit_weeks: number | null
  credits_usd: number | null
}

/** The size options the gateway offered (`setup_cards.home_size_options`). */
function readSizeOptions(raw: unknown): HomeSizeOption[] {
  if (!Array.isArray(raw)) return []
  const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  return raw.flatMap((o): HomeSizeOption[] => {
    if (!o || typeof o !== 'object') return []
    const opt = o as Record<string, unknown>
    const key = str(opt.key)
    const monthly = num(opt.monthly_usd)
    if (!key || monthly === null) return []
    return [{
      key,
      label: str(opt.label),
      note: str(opt.note),
      instance_type: str(opt.instance_type),
      vcpu: num(opt.vcpu) ?? 0,
      ram_gb: num(opt.ram_gb) ?? 0,
      monthly_usd: monthly,
      free_plan_ok: opt.free_plan_ok === true,
      credit_weeks: num(opt.credit_weeks),
      credits_usd: num(opt.credits_usd),
    }]
  })
}

/** AWS's public page on the Free and paid plans, where the upgrade is. A URL, not copy. */
export const AWS_PLAN_UPGRADE_URL = 'https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html'
/** The Service Quotas page for EC2's on-demand vCPU quota (L-1216C47A). A URL, not copy. */
export const AWS_VCPU_QUOTA_URL = 'https://console.aws.amazon.com/servicequotas/home/services/ec2/quotas/L-1216C47A'

const HOME_SIZE_LABEL_KEY: Record<string, string> = {
  lite: 'components.setupCard.home_size_lite',
  economy: 'components.setupCard.home_size_economy',
  starter: 'components.setupCard.home_size_starter',
  small: 'components.setupCard.home_size_small',
  light: 'components.setupCard.home_size_standard',
}

/**
 * The home's size, chosen by the owner: one compact radio row per option (its
 * name, memory and monthly price, and a badge for the suggested size or one that
 * needs AWS's paid plan), with what each runs and gives up behind one "What's the
 * difference?" disclosure. The paid-plan badge shows unless the account is known
 * to be on the paid plan; the upgrade link shows when it is known to be on the
 * Free plan.
 */
function HomeSizeOptions({ name, options, selected, suggested, onPick, planType, signedIn, errorCode }: {
  name: string
  options: HomeSizeOption[]
  selected: string
  /** The size the gateway preselects (`size_default`), badged "recommended". */
  suggested: string
  onPick: (key: string) => void
  planType: string
  signedIn: boolean
  errorCode: string
}) {
  const { t } = useTranslation()
  const legendId = useId()
  const link = 'inline-flex items-center gap-1 text-accent underline underline-offset-2 hover:text-text'
  const badge = 'rounded border px-1.5 text-[11px] font-medium'
  const note = (opt: HomeSizeOption): string => {
    if (opt.note === 'free_plan_credits') {
      return opt.credit_weeks !== null && opt.credits_usd !== null
        ? t('components.setupCard.home_size_starter_note_credits', {
          time: fmtUnit(opt.credit_weeks, 'week', { unitDisplay: 'long' }),
          credits: fmtCurrency(opt.credits_usd, 'USD', { maximumFractionDigits: 0 }),
        })
        : t('components.setupCard.home_size_starter_note')
    }
    if (opt.note === 'lite_tradeoffs') {
      return opt.credit_weeks !== null && opt.credits_usd !== null
        ? t('components.setupCard.home_size_lite_note_credits', {
          time: fmtUnit(opt.credit_weeks, 'week', { unitDisplay: 'long' }),
          credits: fmtCurrency(opt.credits_usd, 'USD', { maximumFractionDigits: 0 }),
        })
        : t('components.setupCard.home_size_lite_note')
    }
    if (opt.note === 'all_on') return t('components.setupCard.home_size_economy_note')
    if (opt.note === 'few_chats') return t('components.setupCard.home_size_note_few_chats')
    if (opt.note === 'many_chats') return t('components.setupCard.home_size_standard_note')
    return ''
  }
  const labelOf = (opt: HomeSizeOption) => (HOME_SIZE_LABEL_KEY[opt.key] ? t(HOME_SIZE_LABEL_KEY[opt.key]) : opt.label)
  const needsPaid = (opt: HomeSizeOption) => !opt.free_plan_ok && planType !== 'PAID'
  return (
    <fieldset className="mt-2 min-w-0" aria-labelledby={legendId} data-testid="setup-card-home-sizes">
      <legend id={legendId} className="text-[12px] text-muted">{t('components.setupCard.home_size_label')}</legend>
      <div className="mt-1 flex flex-col gap-1 min-w-0">
        {options.map(opt => {
          const checked = opt.key === selected
          const label = labelOf(opt)
          return (
            <label
              key={opt.key}
              className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[13px] cursor-pointer min-w-0 ${checked ? 'border-accent bg-bg' : 'border-border'}`}
              data-testid={`setup-card-home-size-${opt.key}`}
              data-checked={checked}
            >
              <input
                type="radio"
                name={name}
                className="shrink-0"
                checked={checked}
                aria-label={label}
                onChange={() => onPick(opt.key)}
              />
              <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 min-w-0 break-words">
                <span className="font-medium text-text">
                  {t('components.setupCard.home_size_row', {
                    label,
                    ram: fmtUnit(opt.ram_gb, 'gigabyte', { unitDisplay: 'short' }),
                    amount: fmtCurrency(opt.monthly_usd, 'USD', { maximumFractionDigits: 0 }),
                  })}
                </span>
                {opt.key === suggested && (
                  <span className={`${badge} border-accent text-accent`} data-testid={`setup-card-home-size-${opt.key}-recommended`}>
                    {t('components.setupCard.home_size_recommended')}
                  </span>
                )}
                {needsPaid(opt) && (
                  <span className={`${badge} border-border text-warn`} data-testid={`setup-card-home-size-${opt.key}-paid`}>
                    {t('components.setupCard.home_size_needs_paid')}
                  </span>
                )}
              </span>
            </label>
          )
        })}
      </div>
      <p className="mt-1 text-[12px] text-muted">{t('components.setupCard.home_sizes_billed')}</p>
      <details className="mt-1 text-[12px]" data-testid="setup-card-home-sizes-details">
        <summary className="cursor-pointer text-accent">{t('components.setupCard.home_sizes_difference')}</summary>
        <ul className="mt-1 flex flex-col gap-1 min-w-0">
          {options.map(opt => {
            const text = note(opt)
            return (
              <li key={opt.key} className="min-w-0 break-words" data-testid={`setup-card-home-size-${opt.key}-note`}>
                <span className="font-medium text-text">{labelOf(opt)}</span>{' '}
                <span className="text-muted">
                  {t('components.setupCard.home_size_option_spec', {
                    instanceType: opt.instance_type,
                    vcpu: fmtNumber(opt.vcpu),
                    ram: fmtUnit(opt.ram_gb, 'gigabyte'),
                  })}
                </span>
                {text && <span className="block text-text">{text}</span>}
              </li>
            )
          })}
        </ul>
        {planType === 'FREE' && options.some(needsPaid) && (
          <p className="mt-1">
            <a href={AWS_PLAN_UPGRADE_URL} target="_blank" rel="noopener noreferrer" className={link} data-testid="setup-card-home-upgrade">
              {t('components.setupCard.home_size_upgrade')}
              <ExternalLink className="lucide-inline" aria-hidden="true" />
            </a>
          </p>
        )}
      </details>
      {!signedIn && (
        <p className="mt-1.5 text-[12px] text-muted" data-testid="setup-card-home-free-plan-note">
          {t('components.setupCard.home_size_free_plan_note')}
        </p>
      )}
      {errorCode === 'home_size_needs_paid_plan' && (
        <p className="mt-1.5 text-[13px]">
          <a href={AWS_PLAN_UPGRADE_URL} target="_blank" rel="noopener noreferrer" className={link} data-testid="setup-card-home-upgrade-after-error">
            {t('components.setupCard.home_size_upgrade')}
            <ExternalLink className="lucide-inline" aria-hidden="true" />
          </a>
        </p>
      )}
      {errorCode === 'home_vcpu_quota_low' && (
        <p className="mt-1.5 text-[13px]">
          <a href={AWS_VCPU_QUOTA_URL} target="_blank" rel="noopener noreferrer" className={link} data-testid="setup-card-home-quota">
            {t('components.setupCard.home_vcpu_quota_open')}
            <ExternalLink className="lucide-inline" aria-hidden="true" />
          </a>
        </p>
      )}
    </fieldset>
  )
}
