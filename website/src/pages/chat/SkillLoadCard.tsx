import { memo } from 'react'
import { BookOpen } from 'lucide-react'

import MarkdownRenderer from '../../components/MarkdownRenderer'
import { i18nT } from '../../i18n/t'
import { useLanguageGeneration } from '../../i18n/useLanguageGeneration'
import type { ChatMessage } from '../../types'
import MarkdownDisclosureCard from './MarkdownDisclosureCard'

export interface LoadedSkillSnapshot {
  name: string
  body: string
}

// Wire value from gateways that predate the structured skill_load metadata.
// Keep the English exact. It is parsed into catalog-backed card copy and never
// rendered directly by this component.
const LEGACY_SKILL_LOAD_RE = /^\s*\u{1F4CE}\s+Loaded skill\(s\) via `\$`:\s+\*\*(.+?)\*\*\s*$/u

function legacyNames(content: string): LoadedSkillSnapshot[] {
  const match = LEGACY_SKILL_LOAD_RE.exec(content)
  if (!match) return []
  return match[1]
    .split(',')
    .map(name => name.trim())
    .filter(Boolean)
    .map(name => ({ name, body: '' }))
}

/** Validate the untrusted transcript metadata before rendering it. */
export function readSkillLoad(message: Pick<ChatMessage, 'content' | 'meta'>): LoadedSkillSnapshot[] {
  const raw = message.meta?.skills
  if (!Array.isArray(raw)) return legacyNames(message.content)

  const snapshots = raw.flatMap(item => {
    if (!item || typeof item !== 'object') return []
    const name = (item as { name?: unknown }).name
    const body = (item as { body?: unknown }).body
    if (typeof name !== 'string' || !name.trim() || typeof body !== 'string') return []
    return [{ name: name.trim(), body }]
  })
  return snapshots.length > 0 ? snapshots : legacyNames(message.content)
}

/** True for the structured row and for the exact legacy notice shape. */
export function isSkillLoadRow(
  message: Pick<ChatMessage, 'role' | 'content' | 'kind' | 'meta'>,
): boolean {
  if (message.role !== 'system') return false
  const kind = message.kind ?? message.meta?.kind
  return kind === 'skill_load' || LEGACY_SKILL_LOAD_RE.test(message.content)
}

export default memo(function SkillLoadCard({
  message,
  disclosureKey,
}: {
  message: ChatMessage
  disclosureKey?: string
}) {
  useLanguageGeneration()
  const skills = readSkillLoad(message)
  if (skills.length === 0) return null

  const bodies = skills.filter(skill => skill.body.length > 0)
  const names = skills.map(skill => skill.name).join(', ')
  const body = bodies.length > 0 ? (
    <div className="min-w-0">
      <p className="mb-3 text-[12px] leading-5 text-muted">
        {i18nT('pages.chat.skillLoadCard.body_context')}
      </p>
      <div className="skill-load-markdown min-w-0 space-y-4">
        {bodies.map((skill, index) => (
          <section
            key={`${skill.name}-${index}`}
            className={`min-w-0 ${index > 0 ? 'pt-4 border-t border-border' : ''}`}
            aria-label={skill.name}
          >
            {skills.length > 1 && (
              <div className="mb-2 font-medium text-text break-words" translate="no">
                {skill.name}
              </div>
            )}
            <MarkdownRenderer content={skill.body} />
          </section>
        ))}
      </div>
    </div>
  ) : undefined

  return (
    <MarkdownDisclosureCard
      icon={BookOpen}
      title={i18nT('pages.chat.skillLoadCard.title', { count: skills.length })}
      detail={({ hasBody }) => (
        <>
          <span translate="no">{names}</span>
          {!hasBody && (
            <>
              <span className="mx-1.5" aria-hidden="true">·</span>
              <span>{i18nT('pages.chat.skillLoadCard.body_unavailable')}</span>
            </>
          )}
        </>
      )}
      body={body}
      disclosureKey={disclosureKey}
      testId="skill-load-card"
      bodyTestId="skill-load-card-body"
      status="loaded"
    />
  )
})
