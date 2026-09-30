/**
 * What a committed LIVE move-in did (setup_move_in.py's outcome), under the home
 * card's result line: where this chat now continues (with a way to open that
 * home), which schedules now run there and are off here, which stay here and
 * why, whether the home kept its own settings, and the secret and connection
 * NAMES to set up again on the home — names only; the outcome never carries a
 * value.
 *
 * Kept to a few short lines: the full account went to the agent, which tells
 * the owner in chat. Last comes "Next time" (`HomeNextTime`): the command that
 * opens the home again, and the others behind a disclosure.
 */
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'

import { api } from '../../api/client'
import { fmtList, fmtNumber } from '../../i18n/format'
import { useSelectInstance } from '../../hooks/useSelectInstance'
import { Btn } from '../ui'
import CommandLine from './CommandLine'

/** Where "Your crews" lives (Settings → Remote Crew). */
const YOUR_CREWS_PATH = '/settings/instances'
/** Most names one line lists before it counts the rest. */
const LISTED_NAMES = 6

/**
 * The backend's reasons a schedule stayed here (`setup_move_in.KEPT_*`) → copy.
 * The KEYS are wire values, matched byte-for-byte against the Python constants;
 * an unknown reason is shown as sent.
 */
const KEPT_REASON_KEY = {
  'it runs a command or script on this computer': 'components.setupCard.home_kept_runs_here',
  'your home did not accept it': 'components.setupCard.home_kept_home_refused',
  'your home could not read its own schedule list': 'components.setupCard.home_kept_home_no_store',
} as const

function keptReason(t: (key: string) => string, reason: string): string {
  return Object.prototype.hasOwnProperty.call(KEPT_REASON_KEY, reason)
    ? t(KEPT_REASON_KEY[reason as keyof typeof KEPT_REASON_KEY])
    : reason
}

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x) : []

/** A bounded, locale-joined list: the first few names, then a count of the rest. */
function nameList(names: string[]): string {
  const shown = names.slice(0, LISTED_NAMES)
  const rest = names.length - shown.length
  return fmtList(rest > 0 ? [...shown, `+${fmtNumber(rest)}`] : shown)
}

/**
 * `cloud.reconnect`'s purposes other than opening the home → their label. The
 * KEYS are wire values; a purpose not listed here is not shown.
 */
const OTHER_PURPOSE_KEY = {
  stop: 'components.setupCard.home_next_time_stop',
  start: 'components.setupCard.home_next_time_start',
  status: 'components.setupCard.home_next_time_status',
  list: 'components.setupCard.home_next_time_list',
} as const

type OtherPurpose = keyof typeof OTHER_PURPOSE_KEY

interface ReconnectCommand { purpose: string; command: string }

function reconnectCommands(raw: unknown): ReconnectCommand[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((c): ReconnectCommand[] => {
    if (!c || typeof c !== 'object') return []
    const { purpose, command } = c as { purpose?: unknown; command?: unknown }
    return typeof purpose === 'string' && typeof command === 'string' && command
      ? [{ purpose, command }]
      : []
  })
}

/**
 * "Next time": how the owner reaches the home again, from the committed
 * outcome's `reconnect` (setup_move_in.reach_back). The commands carry no token
 * or URL: `kirocrew cloud connect` gets a fresh sign-in over SSM each time. A
 * simulated home's commands are shown too, marked as finding nothing.
 */
export function HomeNextTime({ outcome, simulated = false }: { outcome: Record<string, unknown>; simulated?: boolean }) {
  const { t } = useTranslation()
  const commands = reconnectCommands(outcome.reconnect)
  const open = commands.find(c => c.purpose === 'open')
  if (!open) return null
  const others = commands.filter((c): c is ReconnectCommand & { purpose: OtherPurpose } =>
    Object.prototype.hasOwnProperty.call(OTHER_PURPOSE_KEY, c.purpose))
  const copyLabel = t('components.setupCard.copy_command')
  return (
    <div className="mt-1.5 flex flex-col gap-1 min-w-0" data-testid="setup-card-home-next-time">
      <span className="font-medium text-text">{t('components.setupCard.home_next_time')}</span>
      {simulated && (
        <span className="text-warn" data-testid="setup-card-home-next-time-simulated">
          {t('components.setupCard.home_next_time_simulated')}
        </span>
      )}
      <span>{t('components.setupCard.home_next_time_open')}</span>
      <CommandLine text={open.command} copyLabel={copyLabel} testId="setup-card-home-next-time-open" />
      {others.length > 0 && (
        <details data-testid="setup-card-home-next-time-more">
          <summary className="cursor-pointer text-accent">{t('components.setupCard.home_next_time_more')}</summary>
          <ul className="mt-1 flex flex-col gap-1.5 min-w-0">
            {others.map(c => (
              <li key={c.purpose} className="flex flex-col gap-0.5 min-w-0">
                <span>{t(OTHER_PURPOSE_KEY[c.purpose])}</span>
                <CommandLine text={c.command} copyLabel={copyLabel} testId={`setup-card-home-next-time-${c.purpose}`} />
              </li>
            ))}
          </ul>
        </details>
      )}
      <span data-testid="setup-card-home-next-time-elsewhere">{t('components.setupCard.home_next_time_elsewhere')}</span>
    </div>
  )
}

export default function HomeMovedDetail({ outcome }: { outcome: Record<string, unknown> }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const home = (outcome.home && typeof outcome.home === 'object' ? outcome.home : {}) as Record<string, unknown>
  const homeName = typeof home.name === 'string' ? home.name : ''
  const instanceId = typeof home.instance_id === 'string' ? home.instance_id : ''
  const remoteKey = typeof home.remote_key === 'string' ? home.remote_key : undefined
  const arrived = outcome.arrived === true
  // The shared Instances list, so opening the home uses the same select-and-
  // reconnect the crew switcher does.
  const instances = useQuery({
    queryKey: ['instances'],
    queryFn: () => api.listInstances(),
    enabled: !!instanceId && !arrived,
  })
  const { selectInstance } = useSelectInstance(instances.data?.instances ?? [])

  const moved = (Array.isArray(outcome.jobs_moved) ? outcome.jobs_moved : [])
    .map(j => (j && typeof j === 'object' ? String((j as { name?: unknown }).name ?? '') : ''))
    .filter(Boolean)
  const kept = (Array.isArray(outcome.jobs_kept_here) ? outcome.jobs_kept_here : [])
    .flatMap(j => {
      if (!j || typeof j !== 'object') return []
      const { name, reason } = j as { name?: unknown; reason?: unknown }
      if (typeof name !== 'string' || !name) return []
      const r = typeof reason === 'string' ? reason : ''
      return [r ? t('components.setupCard.home_kept_item', { name, reason: keptReason(t, r) }) : name]
    })
  const reenter = (outcome.reenter && typeof outcome.reenter === 'object' ? outcome.reenter : {}) as Record<string, unknown>
  const secrets = strings(reenter.secrets)
  const connections = strings(reenter.connections)

  const open = () => {
    if (instanceId) selectInstance(instanceId, remoteKey)
    else navigate(YOUR_CREWS_PATH)
  }

  return (
    <div className="pl-5 flex flex-col gap-0.5 text-[12px] text-muted min-w-0 break-words" data-testid="setup-card-home-moved">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
        <span className="text-text">
          {homeName
            ? t('components.setupCard.home_moved_where', { name: homeName })
            : t('components.setupCard.home_result_moved')}
        </span>
        {!arrived && <Btn type="button" onClick={open} className="!py-0.5" data-testid="setup-card-home-open">
          <ExternalLink className="lucide-inline" aria-hidden="true" />
          {t('components.setupCard.home_open')}
        </Btn>}
      </div>
      {moved.length > 0 && (
        <div data-testid="setup-card-home-jobs-moved">{t('components.setupCard.home_jobs_moved', { names: nameList(moved) })}</div>
      )}
      {kept.length > 0 && (
        <div data-testid="setup-card-home-jobs-kept">{t('components.setupCard.home_jobs_kept', { names: nameList(kept) })}</div>
      )}
      {outcome.settings_moved === false && (
        <div data-testid="setup-card-home-settings-kept">{t('components.setupCard.home_settings_kept')}</div>
      )}
      {secrets.length > 0 && (
        <div data-testid="setup-card-home-reenter-secrets">
          {t('components.setupCard.home_reenter_secrets', { names: nameList(secrets) })}
        </div>
      )}
      {connections.length > 0 && (
        <div data-testid="setup-card-home-reenter-connections">
          {t('components.setupCard.home_reenter_connections', { names: nameList(connections) })}
        </div>
      )}
      <HomeNextTime outcome={outcome} />
    </div>
  )
}
