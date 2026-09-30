// Width behaviour for the dashboard's main navigation rail, driven from the
// single grabber on the rail's right edge (components/NavigationGrabber).
//
// The rail has two resting states: EXPANDED (labels shown, any width inside
// [NAV_WIDTH_MIN, NAV_WIDTH_MAX]) and COLLAPSED (the icon-only strip). While a
// drag is in flight the width follows the pointer continuously anywhere in
// [NAV_WIDTH_COLLAPSED, NAV_WIDTH_MAX]; on release it snaps to one of the two
// resting states around NAV_SNAP_COLLAPSE_BELOW.
//
// The COLLAPSED flag stays App-owned (`mc-nav`) because App's preview-expand
// and keyboard-toggle paths already drive it, so this hook is controlled on
// that axis and only asks for a change through `onCollapsedChange`. What it
// owns is the EXPANDED width, persisted at `mc-nav-width`. The stored value is
// only ever an expanded width: collapsing never banks the intermediate widths
// a drag swept through, so reopening restores what the user last chose.
//
// Deliberately not built on useColumnResize: that hook owns its collapsed flag
// and snaps collapse with overshoot hysteresis at `min - slop`, where this rail
// needs a controlled flag, a live intermediate width below `min`, and a single
// release threshold. The pointer mechanics (capture, cancel, lost capture,
// secondary-button rejection) are shared through usePointerDrag.
import { useCallback, useEffect, useRef, useState } from 'react'

import { safeGetItem, safeSetItem } from '../utils/safeStorage'
import { usePointerDrag } from './usePointerDrag'

/** Where the EXPANDED rail width is persisted. The collapsed flag is `mc-nav`. */
export const NAV_WIDTH_STORAGE_KEY = 'mc-nav-width'
/** Expanded width before the user has chosen one — the rail track's existing
 *  expanded width (hooks/useRailWidth). */
export const NAV_WIDTH_DEFAULT = 236
/** Narrowest expanded width: below this the labels do not fit. */
export const NAV_WIDTH_MIN = 176
/** Widest expanded width. */
export const NAV_WIDTH_MAX = 300
/** The icon-only strip — the rail track's existing collapsed width. */
export const NAV_WIDTH_COLLAPSED = 74
/** A release below this width collapses; at or above it the rail expands to at
 *  least NAV_WIDTH_MIN. */
export const NAV_SNAP_COLLAPSE_BELOW = 126
/** Px per arrow press, and per Shift+arrow press (same steps as ResizeHandle). */
export const NAV_KEY_STEP = 16
export const NAV_KEY_COARSE_STEP = 64

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** Parse a stored expanded width. Anything missing, non-numeric, non-finite or
 *  non-positive falls back to the default; a finite positive value is clamped
 *  into the expanded range and rounded, so a hand-edited or stale value (e.g.
 *  the collapsed strip width) can never restore a labelless "expanded" rail. */
export function parseNavWidth(raw: string | null): number {
  if (raw === null || raw.trim() === '') return NAV_WIDTH_DEFAULT
  const n = Number(raw)
  if (!Number.isFinite(n) || n <= 0) return NAV_WIDTH_DEFAULT
  return Math.round(clamp(n, NAV_WIDTH_MIN, NAV_WIDTH_MAX))
}

/** The stored expanded width, or the default when storage is empty, invalid or
 *  unavailable (safeGetItem never throws). */
export function loadNavWidth(): number {
  return parseNavWidth(safeGetItem(NAV_WIDTH_STORAGE_KEY))
}

export interface UseNavigationResizeOptions {
  /** App-owned collapsed flag (`mc-nav`). */
  collapsed: boolean
  /** Asked to flip the collapsed flag; App persists it. */
  onCollapsedChange: (value: boolean) => void
  /** Fired when the user takes hold of the rail (an accepted pointer-down or a
   *  handled key), so App can drop any preview-expand restoration it holds. */
  onUserResize?: () => void
}

/** Everything NavigationGrabber needs, shaped to be spread onto it. */
export interface NavigationGrabberBindings {
  handleProps: ReturnType<typeof usePointerDrag>
  onKeyDown: (e: React.KeyboardEvent) => void
  onDoubleClick: (e: React.MouseEvent) => void
  /** Rendered width, for aria-valuenow. */
  value: number
  min: number
  max: number
  collapsed: boolean
  dragging: boolean
}

export interface NavigationResize {
  /** Rendered rail width in px: live while dragging, else 74 or the expanded width. */
  width: number
  /** True only while a pointer drag is in flight. */
  dragging: boolean
  /** True when the rendered width is too narrow for labels — collapsed, or a
   *  drag passing through the intermediate range below NAV_WIDTH_MIN. */
  compact: boolean
  /** The remembered expanded width (what an expand restores). */
  expandedWidth: number
  grabberProps: NavigationGrabberBindings
}

export function useNavigationResize({
  collapsed, onCollapsedChange, onUserResize,
}: UseNavigationResizeOptions): NavigationResize {
  const [expandedWidth, setExpandedWidth] = useState<number>(loadNavWidth)
  const [liveWidth, setLiveWidth] = useState<number | null>(null)
  const [dragging, setDragging] = useState(false)

  const restingWidth = collapsed ? NAV_WIDTH_COLLAPSED : expandedWidth
  const width = liveWidth ?? restingWidth

  // Latest values for handlers usePointerDrag calls through its own ref, so a
  // drag never resolves against a stale render.
  const live = useRef({ collapsed, expandedWidth, restingWidth, onCollapsedChange, onUserResize })
  live.current = { collapsed, expandedWidth, restingWidth, onCollapsedChange, onUserResize }

  const startWidthRef = useRef(0)
  // Body styles in force before the drag pinned its own, restored verbatim on
  // every end path so a cursor another surface set is not wiped to ''.
  const savedBodyStyle = useRef<{ cursor: string, userSelect: string } | null>(null)

  const restoreBody = useCallback(() => {
    const saved = savedBodyStyle.current
    if (!saved) return
    savedBodyStyle.current = null
    document.body.style.cursor = saved.cursor
    document.body.style.userSelect = saved.userSelect
  }, [])

  const setCollapsed = useCallback((value: boolean) => {
    if (value !== live.current.collapsed) live.current.onCollapsedChange(value)
  }, [])

  /** Rest expanded at `w` (already in range) and persist it. */
  const expandTo = useCallback((w: number) => {
    const next = Math.round(clamp(w, NAV_WIDTH_MIN, NAV_WIDTH_MAX))
    setExpandedWidth(next)
    // Never throws; a blocked store keeps the width for this session only.
    safeSetItem(NAV_WIDTH_STORAGE_KEY, String(next))
    setCollapsed(false)
  }, [setCollapsed])

  const toggle = useCallback(() => {
    // Expanding restores the remembered width; collapsing leaves it untouched.
    setCollapsed(!live.current.collapsed)
  }, [setCollapsed])

  const handleProps = usePointerDrag({
    threshold: 0,
    onStart: (e) => {
      startWidthRef.current = live.current.restingWidth
      // A replacement pointer-down (usePointerDrag restarts without an end)
      // must not save the styles this drag itself pinned.
      if (!savedBodyStyle.current) {
        savedBodyStyle.current = {
          cursor: document.body.style.cursor,
          userSelect: document.body.style.userSelect,
        }
      }
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      setDragging(true)
      // usePointerDrag preventDefaults the pointer-down, which also cancels
      // the focus a click would give; focus explicitly so the keyboard can
      // continue from where the pointer left the grabber.
      ;(e.currentTarget as HTMLElement).focus({ preventScroll: true })
      live.current.onUserResize?.()
    },
    onMove: ({ dx }) => {
      setLiveWidth(clamp(startWidthRef.current + dx, NAV_WIDTH_COLLAPSED, NAV_WIDTH_MAX))
    },
    onEnd: ({ dx }) => {
      const raw = clamp(startWidthRef.current + dx, NAV_WIDTH_COLLAPSED, NAV_WIDTH_MAX)
      setLiveWidth(null)
      setDragging(false)
      restoreBody()
      // A tap (the first half of a double-click, or a stray press) changes
      // nothing and writes nothing.
      if (dx === 0) return
      if (raw < NAV_SNAP_COLLAPSE_BELOW) setCollapsed(true)
      else expandTo(Math.max(NAV_WIDTH_MIN, raw))
    },
  })

  // Unmount mid-drag: usePointerDrag's end never fires, so the pinned body
  // cursor / selection lock would outlive the rail.
  useEffect(() => restoreBody, [restoreBody])

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    // Leave modified keys to app / browser shortcuts.
    if (e.altKey || e.ctrlKey || e.metaKey) return
    const { collapsed: isCollapsed, expandedWidth: open } = live.current
    let act: (() => void) | null = null
    switch (e.key) {
      case 'ArrowLeft':
      case 'ArrowRight': {
        const step = (e.shiftKey ? NAV_KEY_COARSE_STEP : NAV_KEY_STEP) * (e.key === 'ArrowRight' ? 1 : -1)
        if (isCollapsed) {
          // Growing out of the strip reopens at the remembered width; shrinking
          // it further has nowhere to go.
          act = step > 0 ? () => setCollapsed(false) : () => {}
        } else if (open + step < NAV_WIDTH_MIN) {
          // Past the minimum collapses rather than stopping at a wall the
          // keyboard could never cross; the remembered width is kept.
          act = () => setCollapsed(true)
        } else {
          act = () => expandTo(open + step)
        }
        break
      }
      case 'Home': act = () => setCollapsed(true); break
      case 'End': act = () => expandTo(NAV_WIDTH_MAX); break
      case 'Enter': act = toggle; break
      default: return
    }
    e.preventDefault()
    live.current.onUserResize?.()
    act()
  }, [expandTo, setCollapsed, toggle])

  const onDoubleClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    live.current.onUserResize?.()
    toggle()
  }, [toggle])

  return {
    width,
    dragging,
    compact: width < NAV_WIDTH_MIN,
    expandedWidth,
    grabberProps: {
      handleProps,
      onKeyDown,
      onDoubleClick,
      value: Math.round(width),
      min: NAV_WIDTH_COLLAPSED,
      max: NAV_WIDTH_MAX,
      collapsed,
      dragging,
    },
  }
}
