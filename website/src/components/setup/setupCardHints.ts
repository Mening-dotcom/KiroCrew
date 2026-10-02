/**
 * The one-line hint a setup card shows in the tray before the owner opens it
 * (PendingSetupCards): a short summary of what the card holds, or, while it
 * works, where it is. Each kind supplies its own through the registry's `hint`
 * (setupCardRegistry.tsx); a kind without one shows its title alone.
 *
 * A hint is a summary, never the consent: every figure a click agrees to is in
 * the expanded card, and nothing commits from the hint.
 */
import { i18nT } from '../../i18n/t'
import { fmtCurrency, fmtList, fmtNumber } from '../../i18n/format'
import type { SetupCard } from '../../api/setupCards'

export interface SetupCardHint {
  /** The words beside the title: a payload summary, or the card's live state. */
  text: string
  /** What the line reports: the owner is needed now, or the card is working. */
  state?: 'needs-you' | 'busy'
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x) : [])
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' ? (v as Record<string, unknown>) : {})
/** Several short parts on one line, with the locale's own separator. */
const parts = (items: string[]): string => fmtList(items.filter(Boolean), { type: 'unit', style: 'short' })
const inFlight = (card: SetupCard) => card.status === 'working' || card.status === 'waiting'
const needsYou = (): SetupCardHint => ({ text: i18nT('components.setupCardTray.needs_you'), state: 'needs-you' })

// ── import: "3 memories, 1 skill" ──────────────────────────────────────────

/** One plural key per category the import knows (`onboarding_import.CATEGORY_IDS`). */
const IMPORT_CATEGORY_KEY = {
  instructions: 'components.setupCardHint.import_instructions',
  memories: 'components.setupCardHint.import_memories',
  workspaces: 'components.setupCardHint.import_workspaces',
  mcp_servers: 'components.setupCardHint.import_mcp_servers',
  skills: 'components.setupCardHint.import_skills',
  schedules: 'components.setupCardHint.import_schedules',
  settings: 'components.setupCardHint.import_settings',
} as const

export function importHint(card: SetupCard): SetupCardHint | null {
  if (inFlight(card)) return null
  // Summed across sources, in the import's own category order.
  const totals = new Map<string, { label: string; count: number }>()
  for (const s of Array.isArray(card.payload?.sources) ? card.payload.sources : []) {
    for (const c of Array.isArray(obj(s).categories) ? (obj(s).categories as unknown[]) : []) {
      const cat = obj(c)
      const id = str(cat.id)
      const count = typeof cat.count === 'number' && cat.count > 0 ? cat.count : 0
      if (!id || !count) continue
      const prev = totals.get(id)
      totals.set(id, { label: str(cat.label) || id, count: (prev?.count ?? 0) + count })
    }
  }
  const known = Object.keys(IMPORT_CATEGORY_KEY) as Array<keyof typeof IMPORT_CATEGORY_KEY>
  const ordered = [...known.filter(id => totals.has(id)), ...[...totals.keys()].filter(id => !(id in IMPORT_CATEGORY_KEY))]
  const text = parts(ordered.map(id => {
    const { label, count } = totals.get(id)!
    return id in IMPORT_CATEGORY_KEY
      ? i18nT(IMPORT_CATEGORY_KEY[id as keyof typeof IMPORT_CATEGORY_KEY], { count })
      : i18nT('components.setupCardHint.import_other', { label, count: fmtNumber(count) })
  }))
  return text ? { text } : null
}

// ── profile: "Nova, English, SRE" ──────────────────────────────────────────

export function profileHint(card: SetupCard): SetupCardHint | null {
  if (inFlight(card)) return null
  const f = obj(card.payload?.fields)
  const text = parts([str(f.bot_name), str(f.language), str(f.role)])
  return text ? { text } : null
}

// ── credential: where it is sent ───────────────────────────────────────────

export function credentialHint(card: SetupCard): SetupCardHint | null {
  if (inFlight(card)) return null
  const text = parts(strList(card.payload?.hosts))
  return text ? { text } : null
}

// ── connect, channel: the title names the provider; the hint is the wait ───

/** Waiting on the owner: the OAuth consent page (connect), the `/pair` code (channel). */
export function waitingOnOwnerHint(card: SetupCard): SetupCardHint | null {
  return card.status === 'waiting' ? needsYou() : null
}

// ── cron: "Morning brief, every weekday at 08:00" ──────────────────────────

export function cronHint(card: SetupCard): SetupCardHint | null {
  if (inFlight(card)) return null
  const p = card.payload ?? {}
  const text = parts([str(p.name), str(p.schedule_human)])
  return text ? { text } : null
}

// ── home: "From $14/mo", then where the build or the move is ───────────────

interface Step { label: string; state: string }

function readSteps(raw: unknown): Step[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((s): Step[] => {
    const step = obj(s)
    const label = str(step.label)
    return label ? [{ label, state: str(step.state) }] : []
  })
}

/** The step the work is on now, else the one that failed, else the last done. */
function currentStep(steps: Step[]): string {
  return (steps.find(s => s.state === 'active') ?? steps.find(s => s.state === 'failed')
    ?? [...steps].reverse().find(s => s.state === 'done'))?.label ?? ''
}

function cheapestMonthly(p: Record<string, unknown>): number | null {
  if (typeof p.from_usd === 'number') return p.from_usd
  const prices = (Array.isArray(p.size_options) ? p.size_options : [])
    .map(o => obj(o).monthly_usd)
    .filter((v): v is number => typeof v === 'number' && v > 0)
  if (prices.length) return Math.min(...prices)
  return typeof p.monthly_usd === 'number' ? p.monthly_usd : null
}

export function homeHint(card: SetupCard): SetupCardHint | null {
  const p = card.payload ?? {}
  const o = card.outcome ?? {}
  const steps = readSteps(o.steps)
  const moveSteps = readSteps(o.move_steps)
  if (moveSteps.length > 0 && inFlight(card)) {
    const step = currentStep(moveSteps)
    return {
      text: step ? i18nT('components.setupCardHint.home_moving_step', { step }) : i18nT('components.setupCardHint.home_moving'),
      state: 'busy',
    }
  }
  // The AWS sign-in tab, or the home's own Kiro sign-in: the build waits on the owner.
  if (card.status === 'waiting' && str(obj(o.aws_signin).state) === 'waiting') return needsYou()
  if (inFlight(card) && o.signin && typeof o.signin === 'object') return needsYou()
  if (card.status === 'waiting' || (card.status === 'working' && steps.length > 0)) {
    const step = currentStep(steps)
    return {
      text: step ? i18nT('components.setupCard.home_compact_step', { step }) : i18nT('components.setupCard.home_compact_building'),
      state: 'busy',
    }
  }
  if (o.needs_signin === true && o.ready !== true) return needsYou()
  if (o.ready === true) {
    return o.auto_move === true && !card.error
      ? { text: i18nT('components.setupCardHint.home_moving'), state: 'busy' }
      : { text: i18nT('components.setupCardHint.home_ready'), state: 'needs-you' }
  }
  if (inFlight(card)) return null
  const monthly = cheapestMonthly(p)
  return monthly !== null
    ? { text: i18nT('components.setupCardHint.home_from', { amount: fmtCurrency(monthly, 'USD', { maximumFractionDigits: 0 }) }) }
    : null
}
