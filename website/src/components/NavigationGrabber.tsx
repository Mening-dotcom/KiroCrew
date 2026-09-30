import type { NavigationGrabberBindings } from '../hooks/useNavigationResize'

import { i18nT } from '../i18n/t'
import { cn } from '../lib/utils'

/** The id App must put on the navigation element this grabber resizes. */
export const DASHBOARD_NAVIGATION_ID = 'dashboard-navigation'

/** The main navigation rail's right-edge grabber: the only visible resize
 * control (existing app keyboard shortcuts and preview auto-collapse remain).
 * Spread `useNavigationResize().grabberProps` onto it.
 *
 * Operation: pointer drag (width follows live, snaps on release), double-click
 * toggles, ArrowLeft/Right step 16px (Shift 64px), Home collapses, End goes to
 * the maximum width, Enter toggles. It is the ARIA window-splitter pattern —
 * a focusable vertical separator controlling `#dashboard-navigation`.
 *
 * Visual contract: a thin transparent full-height hit strip (never a window
 * drag region, so the desktop shell's titlebar band cannot swallow the drag)
 * carrying a short centered handle. The handle is always visible in a muted
 * token at rest and turns accent on hover, keyboard focus and while dragging —
 * deliberately not ResizeHandle's full-height bar that is invisible at rest,
 * because this grip is the rail's only collapse affordance and must be
 * discoverable. Keyboard focus widens the handle itself instead of drawing
 * an exterior ring. Colours are theme tokens; the only motion is a colour
 * transition. */
export default function NavigationGrabber({
  handleProps, onKeyDown, onDoubleClick, value, min, max, collapsed, dragging, className,
}: NavigationGrabberBindings & {
  /** Positioning for the hit strip (e.g. absolute placement on the rail's
   *  edge). Merged with tailwind-merge. */
  className?: string
}) {
  return (
    // A FOCUSABLE separator is the window-splitter widget, which owns a tab
    // stop and the keys below; jsx-a11y only models the static divider.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- focusable separator = the window-splitter widget; onKeyDown IS its documented operation
    <div
      {...handleProps}
      onKeyDown={onKeyDown}
      onDoubleClick={onDoubleClick}
      role="separator"
      aria-orientation="vertical"
      aria-controls={DASHBOARD_NAVIGATION_ID}
      aria-label={i18nT('app.main_navigation')}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- the operable window-splitter widget needs a tab stop
      tabIndex={0}
      title={i18nT('components.resizeHandle.drag_to_resize')}
      data-testid="navigation-grabber"
      data-collapsed={collapsed ? 'true' : 'false'}
      data-dragging={dragging ? 'true' : undefined}
      className={cn( // focus-cue-ok: the child grip widens and paints accent via group-focus-visible/navgrab.
        'group/navgrab relative flex h-full w-2 shrink-0 items-center justify-center cursor-col-resize select-none outline-none',
        className,
      )}
      style={{
        touchAction: 'none',
        // Opt out of any enclosing Electron drag region, or the OS takes the
        // press as a window move and the grabber never sees it.
        WebkitAppRegion: 'no-drag',
      } as React.CSSProperties}
    >
      <div
        aria-hidden="true"
        data-testid="navigation-grabber-handle"
        className={cn(
          'h-8 w-1 rounded-full transition-colors duration-150 group-focus-visible/navgrab:w-1.5',
          dragging
            ? 'bg-accent'
            : 'bg-border-strong group-hover/navgrab:bg-accent group-focus-visible/navgrab:bg-accent',
        )}
      />
    </div>
  )
}
