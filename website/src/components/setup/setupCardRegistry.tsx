/**
 * The dashboard half of the setup-action registry: one entry per card kind the
 * gateway registers (`src/kiro_crew/setup_actions/`). SetupCard reads a kind's
 * body, title and flags from here instead of branching on the kind.
 *
 * `test/test_setup_action_parity.py` parses the keys of `SETUP_CARD_KINDS` and
 * `SETUP_CARD_TITLE_KEY` and fails when a gateway kind has no entry here, an
 * entry here has no gateway kind, or a title key is missing from the catalog.
 * A card whose kind this build has no entry for draws `FallbackBody`: the kind
 * works, minimally, before the dashboard catches up.
 */
import type React from 'react'
import type { ComponentType } from 'react'
import {
  CalendarClock,
  CircleDot,
  Cloud,
  Cpu,
  KeyRound,
  Lock,
  LogIn,
  MessageCircle,
  PackageOpen,
  Plug,
  Power,
  ScrollText,
  Signpost,
  UserRound,
  type LucideIcon,
} from 'lucide-react'

import { i18nT } from '../../i18n/t'
import type { SetupCard, SetupCardKind } from '../../api/setupCards'
import {
  ChannelBody,
  ConnectBody,
  CredentialBody,
  CronBody,
  FallbackBody,
  HomeBody,
  ImportBody,
  PrivacyBody,
  ProfileBody,
  ServiceBody,
  SoulBody,
  type SetupBodyProps,
} from './SetupCardBodies'
import { HarnessBody, HarnessSigninBody, PathBody } from './ScriptedStepBodies'
import {
  channelResultDetail,
  credentialResultDetail,
  homeResultDetail,
  importResultDetail,
  isHomeOffer,
  soulFileName,
} from './setupCardCopy'
import {
  credentialHint,
  cronHint,
  homeHint,
  importHint,
  profileHint,
  waitingOnOwnerHint,
  type SetupCardHint,
} from './setupCardHints'

export interface SetupCardKindEntry {
  /** What the card's payload shows, then its actions, handed to the footer. */
  Body: ComponentType<SetupBodyProps>
  /** The kind's glyph on its tray hint; a high-stakes card wears the shield instead. */
  Icon: LucideIcon
  /** The tray hint's short line: a summary of the payload ("3 memories, 1 skill"),
   *  or where the card is while it works. Absent or null: the title alone (and,
   *  while the card works, the generic "working"). See setupCardHints.ts. */
  hint?: (card: SetupCard) => SetupCardHint | null
  /** The card cannot be declined (the privacy disclosure), so its hint offers
   *  no Not now. Mirrors the body's own `decline: false`. */
  mandatory?: boolean
  /** Interpolation values for the kind's title (`SETUP_CARD_TITLE_KEY`). */
  titleValues?: (card: SetupCard) => Record<string, string>
  /** A heading that replaces the kind's own for this card, or null to keep it. */
  titleOverride?: (card: SetupCard) => string | null
  /** The optional second line under a committed card's result. */
  resultDetail?: (card: SetupCard) => React.ReactNode
  /** The card holds an unsaved draft (a secret field, import checkboxes), which a
   *  hand-off's navigation would destroy, so its error notice offers none. */
  draft?: boolean
  /** A commit settles a boot flag server-side (`dashboard.privacy_acked`, the
   *  import stage), so the tab re-reads the boot flags after one. */
  refreshesBoot?: boolean
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')

/**
 * Each kind's title, by kind: a flat map of full literal keys indexed inline at
 * the `i18nT()` call, the shape `scripts/check-i18n-keys.mjs` resolves statically.
 */
export const SETUP_CARD_TITLE_KEY = {
  // The SAME title the Privacy chapter shows: a disclosure is not paraphrased
  // between its two surfaces.
  privacy: 'components.privacyChapter.title',
  profile: 'components.setupCard.title_profile',
  soul: 'components.setupCard.title_soul',
  import: 'components.setupCard.title_import',
  connect: 'components.setupCard.title_connect',
  credential: 'components.setupCard.title_credential',
  channel: 'components.setupCard.title_channel',
  cron: 'components.setupCard.title_cron',
  service: 'components.setupCard.title_service',
  home: 'components.setupCard.title_home',
  harness: 'components.setupCard.title_harness',
  harness_signin: 'components.setupCard.title_harness_signin',
  path: 'components.setupCard.title_path',
} as const satisfies Record<SetupCardKind, string>

export const SETUP_CARD_KINDS = {
  privacy: { Body: PrivacyBody, Icon: Lock, mandatory: true, refreshesBoot: true },
  profile: { Body: ProfileBody, Icon: UserRound, hint: profileHint },
  soul: { Body: SoulBody, Icon: ScrollText, titleValues: card => ({ file: soulFileName(card.payload?.file) }) },
  import: { Body: ImportBody, Icon: PackageOpen, hint: importHint, resultDetail: importResultDetail, draft: true, refreshesBoot: true },
  connect: {
    Body: ConnectBody,
    Icon: Plug,
    // The title already names the provider ("Connect GitHub"); the hint says only when it waits on the owner.
    hint: waitingOnOwnerHint,
    titleValues: card => ({ name: str(((card.payload?.provider ?? {}) as { name?: unknown }).name) }),
  },
  credential: {
    Body: CredentialBody,
    Icon: KeyRound,
    hint: credentialHint,
    titleValues: card => ({ name: str(card.payload?.name) }),
    resultDetail: credentialResultDetail,
    draft: true,
  },
  channel: {
    Body: ChannelBody,
    Icon: MessageCircle,
    hint: waitingOnOwnerHint,
    titleValues: card => ({ label: str(card.payload?.label) || str(card.payload?.channel) }),
    resultDetail: channelResultDetail,
    draft: true,
  },
  cron: { Body: CronBody, Icon: CalendarClock, hint: cronHint },
  service: { Body: ServiceBody, Icon: Power },
  home: {
    Body: HomeBody,
    Icon: Cloud,
    hint: homeHint,
    // The first run's own "Where should your crew live?" step, while it is still the question.
    titleOverride: card => (isHomeOffer(card) ? i18nT('components.setupCard.title_home_offer') : null),
    resultDetail: homeResultDetail,
  },
  // The first run's scripted steps (UX.2, UX.3): mandatory, like privacy, because
  // nothing can answer in the chat until they are done.
  harness: { Body: HarnessBody, Icon: Cpu, mandatory: true },
  harness_signin: {
    Body: HarnessSigninBody,
    Icon: LogIn,
    mandatory: true,
    titleValues: card => ({ label: str(card.payload?.label) }),
  },
  path: { Body: PathBody, Icon: Signpost, mandatory: true },
} satisfies Record<SetupCardKind, SetupCardKindEntry>

/** The entry for *kind*, or null for a kind this build does not know. */
export function setupCardEntry(kind: string): SetupCardKindEntry | null {
  return Object.prototype.hasOwnProperty.call(SETUP_CARD_KINDS, kind)
    ? SETUP_CARD_KINDS[kind as SetupCardKind]
    : null
}

/** The card's heading, from its kind's entry; a kind this build does not know gets the generic one. */
export function cardTitle(card: SetupCard): string {
  const entry = setupCardEntry(card.kind)
  if (!entry) return i18nT('components.setupCard.title_generic')
  return entry.titleOverride?.(card) ?? i18nT(SETUP_CARD_TITLE_KEY[card.kind], entry.titleValues?.(card))
}

/** The glyph for the card's tray hint: its kind's own, or a neutral dot for a kind this build does not know. */
export function cardIcon(card: SetupCard): LucideIcon {
  return setupCardEntry(card.kind)?.Icon ?? CircleDot
}

/**
 * The tray hint's line for the card: its kind's summary or live state, or, for a
 * card that is working with nothing more specific to say, the generic "working".
 * Null: the hint shows the title alone.
 */
export function cardHint(card: SetupCard): SetupCardHint | null {
  const own = setupCardEntry(card.kind)?.hint?.(card) ?? null
  if (own) return own
  return card.status === 'working' || card.status === 'waiting'
    ? { text: i18nT('components.setupCardTray.in_progress'), state: 'busy' }
    : null
}

/** Whether the card's hint may offer Not now: a pending card that is not mandatory. */
export function cardDeclinableFromHint(card: SetupCard): boolean {
  return card.status === 'pending' && setupCardEntry(card.kind)?.mandatory !== true
}

/** The optional second line under a committed card's result. */
export function committedDetail(card: SetupCard): React.ReactNode {
  return setupCardEntry(card.kind)?.resultDetail?.(card) ?? null
}

/** The body for the card's kind, or the fallback for a kind this build does not know. */
export function SetupCardBody(props: SetupBodyProps) {
  const Body = setupCardEntry(props.card.kind)?.Body ?? FallbackBody
  return <Body {...props} />
}
