import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Copy } from 'lucide-react'

import { copyToClipboard } from '../../utils/clipboard'
import { IconButton } from '../ui'

/** A command (or chat message) to copy, with a copy button beside it. */
export default function CommandLine({ text, copyLabel, testId }: { text: string; copyLabel: string; testId: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const copy = async () => {
    // A tick only once the text is really on the clipboard (copyToClipboard's
    // contract): a false "copied" is worse than none.
    if (!(await copyToClipboard(text))) return
    setCopied(true)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="flex items-center gap-2 min-w-0 rounded-md border border-border bg-bg px-2.5 py-1.5">
      <code className="flex-1 min-w-0 break-all font-mono text-[13px] text-text select-all" translate="no" data-testid={testId}>
        {text}
      </code>
      <IconButton
        aria-label={copied ? t('components.setupCard.copied') : copyLabel}
        title={copied ? t('components.setupCard.copied') : copyLabel}
        onClick={() => { void copy() }}
        data-testid={`${testId}-copy`}
      >
        {copied
          ? <Check size={14} className="text-ok" aria-hidden="true" />
          : <Copy size={14} aria-hidden="true" />}
      </IconButton>
    </div>
  )
}
