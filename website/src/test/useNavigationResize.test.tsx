// The main navigation rail's grabber: useNavigationResize + NavigationGrabber.
// A controlled harness stands in for App, which owns the collapsed flag
// (`mc-nav`); the hook owns only the expanded width (`mc-nav-width`).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent, act } from '@testing-library/react'
import { useState } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import NavigationGrabber, { DASHBOARD_NAVIGATION_ID } from '../components/NavigationGrabber'
import {
  useNavigationResize, parseNavWidth, NAV_WIDTH_STORAGE_KEY, NAV_WIDTH_DEFAULT,
  NAV_WIDTH_MIN, NAV_WIDTH_MAX, NAV_WIDTH_COLLAPSED, NAV_SNAP_COLLAPSE_BELOW,
} from '../hooks/useNavigationResize'

const onUserResize = vi.fn()
const onCollapsedChange = vi.fn()

function Harness({ initialCollapsed = false }: { initialCollapsed?: boolean }) {
  const [collapsed, setCollapsed] = useState(initialCollapsed)
  const nav = useNavigationResize({
    collapsed,
    onCollapsedChange: (v) => { onCollapsedChange(v); setCollapsed(v) },
    onUserResize,
  })
  return (
    <div>
      <nav
        id={DASHBOARD_NAVIGATION_ID}
        data-testid="nav"
        data-collapsed={String(collapsed)}
        data-compact={String(nav.compact)}
        data-dragging={String(nav.dragging)}
        style={{ width: nav.width }}
      />
      {/* App's own toggle path (keyboard shortcut / preview expand). */}
      <button type="button" aria-label="toggle" data-testid="app-toggle" onClick={() => setCollapsed(c => !c)} />
      <NavigationGrabber {...nav.grabberProps} />
    </div>
  )
}

const setup = (initialCollapsed = false) => {
  const r = render(<Harness initialCollapsed={initialCollapsed} />)
  const nav = r.getByTestId('nav')
  const grabber = r.getByRole('separator')
  return {
    ...r,
    grabber,
    width: () => nav.style.width,
    collapsed: () => nav.dataset.collapsed === 'true',
    compact: () => nav.dataset.compact === 'true',
    dragging: () => nav.dataset.dragging === 'true',
  }
}

const down = (el: HTMLElement, x: number, extra: Record<string, unknown> = {}) =>
  fireEvent.pointerDown(el, { clientX: x, clientY: 0, pointerId: 1, button: 0, pointerType: 'mouse', ...extra })
const move = (el: HTMLElement, x: number) => fireEvent.pointerMove(el, { clientX: x, clientY: 0, pointerId: 1 })
const up = (el: HTMLElement, x: number) => fireEvent.pointerUp(el, { clientX: x, clientY: 0, pointerId: 1 })
const drag = (el: HTMLElement, from: number, to: number) => { down(el, from); move(el, to); up(el, to) }
const key = (el: HTMLElement, k: string, shiftKey = false) => fireEvent.keyDown(el, { key: k, shiftKey })
const stored = () => localStorage.getItem(NAV_WIDTH_STORAGE_KEY)

describe('useNavigationResize + NavigationGrabber', () => {
  beforeEach(() => {
    localStorage.clear()
    onUserResize.mockClear()
    onCollapsedChange.mockClear()
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  })
  afterEach(() => { vi.restoreAllMocks() })

  describe('stored width', () => {
    it('defaults to the existing rail width with nothing stored', () => {
      expect(setup().width()).toBe(`${NAV_WIDTH_DEFAULT}px`)
    })

    it('restores a stored expanded width', () => {
      localStorage.setItem(NAV_WIDTH_STORAGE_KEY, '260')
      expect(setup().width()).toBe('260px')
    })

    it.each([
      ['', NAV_WIDTH_DEFAULT], ['   ', NAV_WIDTH_DEFAULT], ['abc', NAV_WIDTH_DEFAULT],
      ['NaN', NAV_WIDTH_DEFAULT], ['Infinity', NAV_WIDTH_DEFAULT], ['-40', NAV_WIDTH_DEFAULT],
      ['0', NAV_WIDTH_DEFAULT], ['74', NAV_WIDTH_MIN], ['9000', NAV_WIDTH_MAX], ['200.6', 201],
    ])('parses %j as %d', (raw, expected) => {
      expect(parseNavWidth(raw)).toBe(expected)
      localStorage.setItem(NAV_WIDTH_STORAGE_KEY, raw)
      const { width, unmount } = setup()
      expect(width()).toBe(`${expected}px`)
      unmount()
    })

    it('falls back to the default, and keeps working, when storage throws', () => {
      vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('SecurityError') })
      vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('SecurityError') })
      vi.spyOn(console, 'warn').mockImplementation(() => {})
      const { grabber, width } = setup()
      expect(width()).toBe(`${NAV_WIDTH_DEFAULT}px`)
      drag(grabber, 300, 340)
      expect(width()).toBe(`${NAV_WIDTH_DEFAULT + 40}px`)
    })

    it('does not write on mount or on a tap', () => {
      const { grabber } = setup()
      expect(stored()).toBeNull()
      down(grabber, 300); up(grabber, 300)
      expect(stored()).toBeNull()
      expect(onCollapsedChange).not.toHaveBeenCalled()
    })
  })

  describe('controlled collapse', () => {
    it('follows App-driven collapse and restores the remembered width', () => {
      localStorage.setItem(NAV_WIDTH_STORAGE_KEY, '280')
      const { getByTestId, width, compact } = setup()
      fireEvent.click(getByTestId('app-toggle'))
      expect(width()).toBe(`${NAV_WIDTH_COLLAPSED}px`)
      expect(compact()).toBe(true)
      fireEvent.click(getByTestId('app-toggle'))
      expect(width()).toBe('280px')
      expect(compact()).toBe(false)
      // App owns the flag: the hook asked for nothing.
      expect(onCollapsedChange).not.toHaveBeenCalled()
    })

    it('starts collapsed when App does', () => {
      const { width, grabber } = setup(true)
      expect(width()).toBe(`${NAV_WIDTH_COLLAPSED}px`)
      expect(grabber.dataset.collapsed).toBe('true')
    })
  })

  describe('pointer drag', () => {
    it('follows the pointer through intermediate widths and banks the expanded result', () => {
      const { grabber, width, compact, dragging } = setup()
      down(grabber, 300)
      expect(dragging()).toBe(true)
      move(grabber, 250) // 236 - 50 = 186
      expect(width()).toBe('186px')
      move(grabber, 200) // 136: intermediate, labels hidden
      expect(width()).toBe('136px')
      expect(compact()).toBe(true)
      move(grabber, 330) // 266
      up(grabber, 330)
      expect(width()).toBe('266px')
      expect(dragging()).toBe(false)
      expect(stored()).toBe('266')
      expect(onCollapsedChange).not.toHaveBeenCalled()
    })

    it('clamps the live width to [collapsed, max]', () => {
      const { grabber, width } = setup()
      down(grabber, 300)
      move(grabber, 9000)
      expect(width()).toBe(`${NAV_WIDTH_MAX}px`)
      move(grabber, -9000)
      expect(width()).toBe(`${NAV_WIDTH_COLLAPSED}px`)
      up(grabber, -9000)
    })

    it(`a release below ${NAV_SNAP_COLLAPSE_BELOW}px collapses without banking the swept width`, () => {
      localStorage.setItem(NAV_WIDTH_STORAGE_KEY, '280')
      const { grabber, width, collapsed } = setup()
      drag(grabber, 300, 300 - (280 - (NAV_SNAP_COLLAPSE_BELOW - 1)))
      expect(collapsed()).toBe(true)
      expect(width()).toBe(`${NAV_WIDTH_COLLAPSED}px`)
      expect(onCollapsedChange).toHaveBeenLastCalledWith(true)
      expect(stored()).toBe('280')
      // Reopening (Enter) restores 280, not the minimum.
      key(grabber, 'Enter')
      expect(width()).toBe('280px')
    })

    it(`a release at ${NAV_SNAP_COLLAPSE_BELOW}px or above snaps up to the minimum`, () => {
      const { grabber, width, collapsed } = setup()
      drag(grabber, 300, 300 - (NAV_WIDTH_DEFAULT - NAV_SNAP_COLLAPSE_BELOW))
      expect(collapsed()).toBe(false)
      expect(width()).toBe(`${NAV_WIDTH_MIN}px`)
      expect(stored()).toBe(String(NAV_WIDTH_MIN))
    })

    it('drags out of the collapsed strip from 74 and expands at the release width', () => {
      const { grabber, width, collapsed } = setup(true)
      down(grabber, 100)
      move(grabber, 140) // 114, still intermediate
      expect(width()).toBe('114px')
      move(grabber, 250) // 224
      up(grabber, 250)
      expect(collapsed()).toBe(false)
      expect(width()).toBe('224px')
      expect(onCollapsedChange).toHaveBeenLastCalledWith(false)
    })

    it('pointercancel ends from the last tracked position, not the cancel coordinates', () => {
      const { grabber, width, dragging } = setup()
      down(grabber, 300)
      move(grabber, 340)
      fireEvent.pointerCancel(grabber, { clientX: 0, clientY: 0, pointerId: 1 })
      expect(dragging()).toBe(false)
      expect(width()).toBe(`${NAV_WIDTH_DEFAULT + 40}px`)
      expect(document.body.style.cursor).toBe('')
    })

    it('lost pointer capture ends the drag and releases the body styles', () => {
      const { grabber, width, dragging } = setup()
      down(grabber, 300)
      move(grabber, 280)
      expect(document.body.style.userSelect).toBe('none')
      fireEvent.lostPointerCapture(grabber, { pointerId: 1 })
      expect(dragging()).toBe(false)
      expect(width()).toBe(`${NAV_WIDTH_DEFAULT - 20}px`)
      expect(document.body.style.userSelect).toBe('')
    })

    it('ignores a secondary mouse button entirely', () => {
      const { grabber, dragging } = setup()
      down(grabber, 300, { button: 2 })
      move(grabber, 100)
      expect(dragging()).toBe(false)
      expect(onUserResize).not.toHaveBeenCalled()
      expect(document.body.style.cursor).toBe('')
      expect(document.activeElement).not.toBe(grabber)
    })

    it('reports the accepted start and focuses the grabber', () => {
      const { grabber } = setup()
      down(grabber, 300)
      expect(onUserResize).toHaveBeenCalledTimes(1)
      expect(document.activeElement).toBe(grabber)
      up(grabber, 300)
    })

    it('restores the body styles that were in force before the drag', () => {
      document.body.style.cursor = 'wait'
      document.body.style.userSelect = 'text'
      const { grabber } = setup()
      down(grabber, 300)
      expect(document.body.style.cursor).toBe('col-resize')
      // A replacement pointer-down mid-drag must not bank the drag's own styles.
      down(grabber, 310)
      up(grabber, 320)
      expect(document.body.style.cursor).toBe('wait')
      expect(document.body.style.userSelect).toBe('text')
    })

    it('restores the body styles when unmounted mid-drag', () => {
      const { grabber, unmount } = setup()
      down(grabber, 300)
      move(grabber, 250)
      unmount()
      expect(document.body.style.cursor).toBe('')
      expect(document.body.style.userSelect).toBe('')
    })
  })

  describe('double click', () => {
    it('toggles, restoring the remembered width, and keeps focus', () => {
      localStorage.setItem(NAV_WIDTH_STORAGE_KEY, '250')
      const { grabber, width } = setup()
      act(() => { grabber.focus() })
      fireEvent.doubleClick(grabber)
      expect(width()).toBe(`${NAV_WIDTH_COLLAPSED}px`)
      fireEvent.doubleClick(grabber)
      expect(width()).toBe('250px')
      expect(onUserResize).toHaveBeenCalledTimes(2)
      expect(document.activeElement).toBe(grabber)
    })
  })

  describe('keyboard', () => {
    it('ArrowRight/Left step 16px, Shift 64px, and persist', () => {
      const { grabber, width } = setup()
      key(grabber, 'ArrowRight')
      expect(width()).toBe(`${NAV_WIDTH_DEFAULT + 16}px`)
      key(grabber, 'ArrowLeft', true)
      expect(width()).toBe(`${NAV_WIDTH_DEFAULT + 16 - 64}px`)
      expect(stored()).toBe(String(NAV_WIDTH_DEFAULT + 16 - 64))
      expect(onUserResize).toHaveBeenCalledTimes(2)
    })

    it('clamps at the maximum and collapses past the minimum, keeping the remembered width', () => {
      localStorage.setItem(NAV_WIDTH_STORAGE_KEY, String(NAV_WIDTH_MIN))
      const { grabber, width, collapsed } = setup()
      key(grabber, 'ArrowLeft')
      expect(collapsed()).toBe(true)
      expect(stored()).toBe(String(NAV_WIDTH_MIN))
      key(grabber, 'ArrowLeft') // nowhere further to go
      expect(collapsed()).toBe(true)
      key(grabber, 'ArrowRight') // reopens at the remembered width
      expect(width()).toBe(`${NAV_WIDTH_MIN}px`)
      key(grabber, 'End')
      key(grabber, 'ArrowRight', true)
      expect(width()).toBe(`${NAV_WIDTH_MAX}px`)
    })

    it('Home collapses, End expands to the maximum, Enter toggles', () => {
      const { grabber, width, collapsed } = setup()
      key(grabber, 'Home')
      expect(collapsed()).toBe(true)
      key(grabber, 'Enter')
      expect(width()).toBe(`${NAV_WIDTH_DEFAULT}px`)
      key(grabber, 'Home')
      key(grabber, 'End')
      expect(collapsed()).toBe(false)
      expect(width()).toBe(`${NAV_WIDTH_MAX}px`)
      expect(stored()).toBe(String(NAV_WIDTH_MAX))
      key(grabber, 'Enter')
      expect(width()).toBe(`${NAV_WIDTH_COLLAPSED}px`)
    })

    it('keeps focus on the grabber across a collapse and expand', () => {
      const { grabber } = setup()
      act(() => { grabber.focus() })
      key(grabber, 'Enter')
      key(grabber, 'Enter')
      expect(document.activeElement).toBe(grabber)
    })

    it('leaves unhandled and modified keys alone', () => {
      const { grabber, width } = setup()
      const ev = new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true })
      grabber.dispatchEvent(ev)
      expect(ev.defaultPrevented).toBe(false)
      fireEvent.keyDown(grabber, { key: 'ArrowRight', metaKey: true })
      fireEvent.keyDown(grabber, { key: 'Home', ctrlKey: true })
      expect(width()).toBe(`${NAV_WIDTH_DEFAULT}px`)
      expect(onUserResize).not.toHaveBeenCalled()
    })
  })

  describe('NavigationGrabber', () => {
    it('is a focusable vertical separator controlling the navigation', () => {
      const { grabber } = setup()
      expect(grabber.getAttribute('aria-orientation')).toBe('vertical')
      expect(grabber.getAttribute('aria-controls')).toBe('dashboard-navigation')
      expect(grabber.getAttribute('aria-label')).toBe('Main navigation')
      expect(grabber.getAttribute('title')).toBe('Drag to resize')
      expect(grabber.tabIndex).toBe(0)
      expect(grabber.getAttribute('aria-valuenow')).toBe(String(NAV_WIDTH_DEFAULT))
      expect(grabber.getAttribute('aria-valuemin')).toBe(String(NAV_WIDTH_COLLAPSED))
      expect(grabber.getAttribute('aria-valuemax')).toBe(String(NAV_WIDTH_MAX))
    })

    it('is a no-drag, touch-action-none hit strip with a short muted handle that accents while dragging', () => {
      const { grabber, getByTestId } = setup()
      expect(grabber.style.touchAction).toBe('none')
      // happy-dom drops the vendor property from the style it renders, so pin
      // the source: Electron needs it to keep a titlebar drag region from
      // swallowing the press.
      const src = readFileSync(join(process.cwd(), 'src', 'components', 'NavigationGrabber.tsx'), 'utf8')
      expect(src).toContain("WebkitAppRegion: 'no-drag'")
      const handle = getByTestId('navigation-grabber-handle')
      expect(handle.className).toContain('bg-border-strong')
      expect(handle.className).toContain('group-hover/navgrab:bg-accent')
      expect(handle.className).toContain('group-focus-visible/navgrab:bg-accent')
      expect(handle.className).not.toContain('h-full')
      // Focus changes the grip itself, never an exterior capsule or a
      // full-height outline. Pointer focus remains available to keyboard input.
      expect(grabber.className).toContain('outline-none')
      expect(grabber.className).not.toContain('focus-ring')
      expect(grabber.className).not.toMatch(/(^|\s)focus-visible:ring/)
      expect(handle.className).not.toMatch(/(?:^|[\s:])ring-/)
      expect(handle.className).toContain('group-focus-visible/navgrab:w-1.5')
      down(grabber, 300)
      expect(handle.className).toContain('bg-accent')
      expect(handle.className).not.toContain('bg-border-strong')
      expect(grabber.dataset.dragging).toBe('true')
      up(grabber, 300)
    })
  })
})
