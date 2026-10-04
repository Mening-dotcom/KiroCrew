import { type ReactNode, useId } from 'react'
import { ChevronRight, type LucideIcon } from 'lucide-react'

import { useScrollEdgesY } from '../../hooks/useScrollEdges'
import { useRowDisclosure } from './rowDisclosure'

interface MarkdownDisclosureCardProps {
  icon: LucideIcon
  title: string
  detail?: ReactNode | ((state: { expanded: boolean; hasBody: boolean }) => ReactNode)
  body?: ReactNode
  disclosureKey?: string
  testId: string
  bodyTestId: string
  status?: string
}

/**
 * Shared transcript card for gateway-authored markdown that should not occupy
 * the conversation until the user asks to read it.
 */
export default function MarkdownDisclosureCard({
  icon: Icon,
  title,
  detail,
  body,
  disclosureKey,
  testId,
  bodyTestId,
  status,
}: MarkdownDisclosureCardProps) {
  const [expanded, setExpanded] = useRowDisclosure(disclosureKey, false)
  const [attachScroller, scrollEdges, , attachContent] = useScrollEdgesY<HTMLDivElement>()
  const headlineId = useId()
  const bodyId = useId()
  const hasBody = body !== undefined && body !== null
  const renderedDetail = typeof detail === 'function' ? detail({ expanded, hasBody }) : detail
  const header = (
    <>
      {hasBody && (
        <ChevronRight
          size={13}
          className={`lucide-inline shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
          aria-hidden="true"
        />
      )}
      <Icon size={13} className="lucide-inline shrink-0" aria-hidden="true" />
      <span id={headlineId} className="font-medium text-text shrink-0">
        {title}
      </span>
      {renderedDetail && (
        <span className="truncate text-[12px] leading-5 opacity-75 min-w-0">
          {renderedDetail}
        </span>
      )}
    </>
  )

  return (
    <div
      className="self-center w-full max-w-full min-w-0 rounded-md ring-1 ring-inset forced-colors:border ring-border bg-card text-muted animate-scale-in"
      data-testid={testId}
      data-status={status}
      data-expanded={hasBody ? expanded : undefined}
    >
      {hasBody ? (
        <button
          type="button"
          onClick={() => setExpanded(value => !value)}
          aria-expanded={expanded}
          aria-controls={bodyId}
          className="w-full flex items-center gap-2 px-3 py-2 min-w-0 text-left text-[13px] leading-5 font-body text-inherit bg-transparent border-none hover:text-text transition-colors cursor-pointer"
          data-testid={`${testId}-toggle`}
        >
          {header}
        </button>
      ) : (
        <div className="flex items-center gap-2 px-3 py-2 min-w-0 text-[13px] leading-5">
          {header}
        </div>
      )}
      {hasBody && expanded && (
        <div
          ref={attachScroller}
          id={bodyId}
          className={`px-3 pb-3 pt-2 min-w-0 text-[13px] leading-5 border-t border-border max-h-[24rem] overflow-y-auto overflow-x-hidden focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${scrollEdges.bottom ? 'markdown-disclosure-scroll-more' : ''}`}
          data-testid={bodyTestId}
          data-scroll-more={scrollEdges.bottom ? '' : undefined}
          role="region"
          aria-labelledby={headlineId}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
        >
          <div ref={attachContent}>{body}</div>
        </div>
      )}
    </div>
  )
}
