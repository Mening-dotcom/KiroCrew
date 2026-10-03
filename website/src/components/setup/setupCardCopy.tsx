/**
 * Copy and small pure helpers for SetupCard: result details, error wording, and
 * the "Use classic setup" target check. Which kind uses which is
 * `setupCardRegistry.tsx`.
 *
 * Every lookup here runs in RENDER position (called from a component body), so
 * `i18nT` re-resolves on a language switch; nothing is memoized across one.
 */
import type React from 'react'

import { i18nT } from '../../i18n/t'
import HomeMovedDetail, { HomeNextTime } from './HomeMovedDetail'
import type { SetupCard, SetupCardClassic, SetupCardStatus } from '../../api/setupCards'

/** Terminal status → its word on the result line. */
export const STATUS_KEY = {
  committed: 'components.setupCard.status_committed',
  declined: 'components.setupCard.status_declined',
  failed: 'components.setupCard.status_failed',
  expired: 'components.setupCard.status_expired',
} as const satisfies Partial<Record<SetupCardStatus, string>>

/** Machine error codes the gateway returns → a sentence the owner can act on. */
const ERROR_KEY = {
  card_not_pending: 'components.setupCard.error_card_not_pending',
  card_hash_mismatch: 'components.setupCard.error_card_hash_mismatch',
  owner_required: 'components.setupCard.error_owner_required',
  card_not_found: 'components.setupCard.error_card_not_found',
  credential_empty: 'components.setupCard.error_credential_empty',
  service_not_installed: 'components.setupCard.error_service_not_installed',
  aws_not_signed_in: 'components.setupCard.error_aws_not_signed_in',
  // The home card's "Sign in to AWS" (dashboard/setup_aws_signin.py).
  aws_signin_remote: 'components.setupCard.error_aws_signin_remote',
  aws_signin_timeout: 'components.setupCard.error_aws_signin_timeout',
  aws_signin_failed: 'components.setupCard.error_aws_signin_failed',
  aws_signin_busy: 'components.setupCard.error_aws_signin_busy',
  aws_signin_not_needed: 'components.setupCard.error_aws_signin_not_needed',
  aws_cli_missing: 'components.setupCard.error_aws_cli_missing',
  aws_cli_too_old: 'components.setupCard.error_aws_cli_too_old',
  aws_signin_profile_has_keys: 'components.setupCard.error_aws_signin_profile_has_keys',
  launch_already_running: 'components.setupCard.error_launch_already_running',
  // The home's build and its Kiro sign-in (setup_flow._commit_home, _sign_home_in).
  home_identity_region_unknown: 'components.setupCard.error_home_identity_region_unknown',
  home_signin_unavailable: 'components.setupCard.error_home_signin_unavailable',
  launch_job_not_found: 'components.setupCard.error_home_signin_unavailable',
  launch_has_no_instance: 'components.setupCard.error_home_signin_unavailable',
  login_target_unreadable: 'components.setupCard.error_login_target_unreadable',
  // The home's size and the account it is built in (setup_flow._chosen_home_size).
  home_size_not_offered: 'components.setupCard.error_home_size_not_offered',
  home_size_needs_paid_plan: 'components.setupCard.error_home_size_needs_paid_plan',
  home_vcpu_quota_low: 'components.setupCard.error_home_vcpu_quota_low',
  home_spend_limit: 'components.setupCard.error_home_spend_limit',
  // A build the home card lost track of was stopped (setup_flow._stop_untracked_build).
  home_build_untracked: 'components.setupCard.error_home_build_untracked',
  // Removing what a build a restart cut short left (setup_flow._decide_home_remove).
  home_nothing_to_remove: 'components.setupCard.error_home_nothing_to_remove',
  home_remove_running: 'components.setupCard.error_home_remove_running',
  // The home card's region picker (setup_flow._decide_home_region).
  home_region_not_offered: 'components.setupCard.error_home_region_not_offered',
  home_region_no_answer: 'components.setupCard.error_home_region_no_answer',
  // The live move-in's refusals (dashboard/setup_move_in.py). Each leaves the
  // card pending; the failed step's own detail on the card keeps the server's
  // specifics (the tunnel error, the home's refusal code).
  move_in_restarting: 'components.setupCard.error_move_in_restarting',
  move_in_instances_off: 'components.setupCard.error_move_in_instances_off',
  move_in_home_not_registered: 'components.setupCard.error_move_in_home_not_registered',
  move_in_home_unsupported: 'components.setupCard.error_move_in_home_unsupported',
  move_in_unreachable: 'components.setupCard.error_move_in_unreachable',
  move_in_jobs_busy: 'components.setupCard.error_move_in_jobs_busy',
  move_in_carry_failed: 'components.setupCard.error_move_in_carry_failed',
  move_in_carry_refused: 'components.setupCard.error_move_in_carry_refused',
  move_in_chat_missing: 'components.setupCard.error_move_in_chat_missing',
  move_in_chat_not_persistent: 'components.setupCard.error_move_in_chat_not_persistent',
  move_in_chat_busy: 'components.setupCard.error_move_in_chat_busy',
  move_in_chat_failed: 'components.setupCard.error_move_in_chat_failed',
  move_in_failed: 'components.setupCard.error_move_in_failed',
  move_in_interrupted: 'components.setupCard.error_move_in_interrupted',
  channel_token_empty: 'components.setupCard.error_channel_token_empty',
  channel_token_invalid: 'components.setupCard.error_channel_token_invalid',
  channel_token_rejected: 'components.setupCard.error_channel_token_rejected',
  pair_attempts: 'components.setupCard.error_pair_attempts',
  // The first run's scripted steps (setup_flow._commit_harness, _commit_harness_signin,
  // _commit_path). A check that could not finish keeps the server's own detail.
  harness_invalid: 'components.setupCard.error_harness_invalid',
  harness_denied: 'components.setupCard.error_harness_denied',
  harness_not_installed: 'components.setupCard.error_harness_not_installed',
  harness_not_signed_in: 'components.setupCard.error_harness_not_signed_in',
  harness_outdated: 'components.setupCard.error_harness_outdated',
  harness_check_first: 'components.setupCard.error_harness_check_first',
  // A check that could not finish, and a sandbox that refused to start the
  // harness, by layer (harness_readiness.sandbox_code). Plain words only: the
  // classified error's own message, remedy included, is the detail beneath.
  harness_check_failed: 'components.setupCard.error_harness_check_failed',
  harness_sandbox_nested: 'components.setupCard.error_harness_sandbox_nested',
  harness_sandbox_crew: 'components.setupCard.error_harness_sandbox_crew',
  harness_sandbox_harness: 'components.setupCard.error_harness_sandbox_harness',
  change_engine_unavailable: 'components.setupCard.error_change_engine_unavailable',
  path_invalid: 'components.setupCard.error_path_invalid',
  step_interrupted: 'components.setupCard.error_step_interrupted',
} as const

/**
 * The words for a failed decide or a card's own `error`: a known code gets its
 * translated sentence, anything else keeps the server's human text, and a bare
 * failure with neither gets the generic retry line. *values* fills a sentence
 * that names the card's subject (`{{label}}`, the harness).
 */
export function errorText(code: string | undefined, serverMessage: string, values?: Record<string, string>): string {
  const key = code ? ERROR_KEY[code as keyof typeof ERROR_KEY] : undefined
  if (key) return i18nT(key, values)
  return serverMessage || i18nT('components.setupCard.error_generic')
}

/**
 * The server's own words beneath a translated sentence, for a disclosure: what
 * the sentence summarised (a harness's exit, a classified sandbox refusal). Empty
 * when the sentence IS the server's text, or the server gave none.
 */
export function errorDetail(code: string | undefined, serverMessage: string): string {
  const known = !!code && Object.prototype.hasOwnProperty.call(ERROR_KEY, code)
  return known && serverMessage && serverMessage !== code ? serverMessage : ''
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

/** What a home build a restart cut short may have left in AWS (`outcome.leftover`). */
export interface HomeLeftover {
  tag: string
  stack: string
  region: string
}

const LEFTOVER_TAG_RE = /^[a-zA-Z0-9-]{1,51}$/
const LEFTOVER_REGION_RE = /^[a-z]{2}(?:-[a-z]+)+-\d$/

/**
 * The stack a failed home card offers to remove, when its outcome names a
 * well-formed one. The gateway checks it again against the build's own record
 * before anything is deleted; this only decides what the card shows.
 */
export function homeLeftover(card: SetupCard): HomeLeftover | null {
  if (card.kind !== 'home' || card.status !== 'failed') return null
  const raw = card.outcome?.leftover
  if (!raw || typeof raw !== 'object') return null
  const l = raw as Record<string, unknown>
  const tag = str(l.tag)
  const region = str(l.region)
  if (!LEFTOVER_TAG_RE.test(tag) || !LEFTOVER_REGION_RE.test(region) || str(l.stack) !== `kirocrew-${tag}`) return null
  return { tag, stack: str(l.stack), region }
}

/** Where a removal of a build's leftovers stands: '', 'active', 'done' or 'failed'. */
export function homeRemovalState(card: SetupCard): string {
  const removal = card.outcome?.removal
  return removal && typeof removal === 'object' ? str((removal as Record<string, unknown>).state) : ''
}

/**
 * A failed card's sentence. A home build a restart cut short says it may have
 * left billed parts in AWS and where to remove them, since no worker is left to
 * roll them back; one stopped in this process keeps its own words.
 */
export function failedText(card: SetupCard): string {
  const error = card.error
  if (!error) return ''
  if (card.kind === 'home') {
    if (homeLeftover(card)) return i18nT('components.setupCard.error_home_build_leftover')
    if (error.code === 'home_build_untracked' && card.outcome?.stopped !== true) {
      return i18nT('components.setupCard.error_home_build_leftover_elsewhere')
    }
  }
  return errorText(error.code, error.message)
}

/**
 * The first run's "Where should your crew live?" step: the home card the gateway
 * shows on its own when the first job is kept (`payload.offer`), while it is still
 * the question or the cloud's steps, i.e. before anything is built. Once a build
 * starts it is the home itself.
 */
export function isHomeOffer(card: SetupCard): boolean {
  if (card.kind !== 'home' || card.payload?.offer !== true) return false
  const o = card.outcome ?? {}
  return !o.job_id && o.ready !== true && o.needs_signin !== true && o.moved !== true
}

/** The word beside a decided card's title; a home card answered "This machine" keeps the crew here. */
export function resultStatusKey(card: SetupCard): string | undefined {
  if (card.kind === 'home' && card.status === 'committed' && card.outcome?.stayed === true) {
    return 'components.setupCard.home_offer_declined'
  }
  // A first-run step the owner left through "Choose a different engine".
  if (card.status === 'declined' && card.outcome?.change_engine === true) {
    return 'components.setupCard.status_engine_changed'
  }
  return STATUS_KEY[card.status as keyof typeof STATUS_KEY]
}

/** The extension of the two persona files (SOUL.md, USER.md); a file name, not copy. */
const PERSONA_FILE_EXT = '.md'

/** `SOUL` / `USER` → the file name the user edits. Anything else reads as SOUL.md. */
export function soulFileName(file: unknown): string {
  const stem = file === 'USER' ? 'USER' : 'SOUL'
  return stem + PERSONA_FILE_EXT
}

/** The import card's result: how much came over, and how many jobs arrived off. */
export function importResultDetail(card: SetupCard): React.ReactNode {
  const o = card.outcome ?? {}
  const imported = typeof o.imported_count === 'number' ? o.imported_count : null
  const jobs = typeof o.jobs_added_disabled === 'number' ? o.jobs_added_disabled : 0
  if (imported === null && !jobs) return null
  return (
    <div className="text-[12px] text-muted pl-5" data-testid="setup-card-result-detail">
      {imported !== null && <div>{i18nT('components.setupCard.import_result', { count: imported })}</div>}
      {jobs > 0 && <div>{i18nT('components.setupCard.import_jobs_result', { count: jobs })}</div>}
    </div>
  )
}

/** The home card's result once the crew moved in. */
export function homeResultDetail(card: SetupCard): React.ReactNode {
  const o = card.outcome ?? {}
  // A live move-in: what moved, what stayed, and what to set up again there.
  if (o.moved === true && o.simulated !== true) {
    return <HomeMovedDetail outcome={o} />
  }
  if (o.moved === true) {
    return (
      <div className="text-[12px] text-muted pl-5 flex flex-col min-w-0" data-testid="setup-card-result-detail">
        <span>{i18nT('components.setupCard.home_result_simulated')}</span>
        <HomeNextTime outcome={o} simulated />
      </div>
    )
  }
  return null
}

/** The channel card's result: who paired. */
export function channelResultDetail(card: SetupCard): React.ReactNode {
  const o = card.outcome ?? {}
  if (o.paired !== true) return null
  // `username` is the sender's @handle as the gateway narrowed it; an account
  // without one pairs just the same.
  const username = str(o.username)
  return (
    <div className="text-[12px] text-muted pl-5 min-w-0 break-words" data-testid="setup-card-result-detail">
      {username
        ? i18nT('components.setupCard.channel_paired', { username })
        : i18nT('components.setupCard.channel_paired_account')}
    </div>
  )
}

/** The credential card's result: the vault reference, never the value. */
export function credentialResultDetail(card: SetupCard): React.ReactNode {
  const o = card.outcome ?? {}
  if (typeof o.ref !== 'string' || !o.ref) return null
  return (
    <div className="text-[12px] text-muted pl-5 min-w-0 break-words" data-testid="setup-card-result-detail">
      {i18nT('components.setupCard.credential_result')}{' '}
      <code className="font-mono text-[12px] text-text" translate="no">{o.ref}</code>
    </div>
  )
}

/** A validated "Use classic setup" target, or null to hide the affordance. */
export function classicAction(classic: SetupCardClassic | null | undefined): SetupCardClassic | null {
  if (!classic || typeof classic.target !== 'string') return null
  if (classic.kind === 'route') {
    // Same-app paths only: the target is gateway-authored, but a navigation
    // this card performs must never leave the dashboard (`//host` is a
    // protocol-relative URL, a backslash is one on some parsers).
    const t = classic.target
    return t.startsWith('/') && !t.startsWith('//') && !t.includes('\\') ? classic : null
  }
  if (classic.kind === 'event') {
    // The dashboard's own window events (`mc-start-import`, ...), nothing else.
    return /^mc-[a-z0-9-]+$/.test(classic.target) ? classic : null
  }
  return null
}

/** Only an http(s) consent URL becomes a link; anything else is not rendered. */
export function safeConsentUrl(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw) return null
  try {
    const u = new URL(raw)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.toString() : null
  } catch {
    return null
  }
}
