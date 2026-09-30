import { useSyncExternalStore } from 'react'

/**
 * Width (px) of the app shell's left nav rail track.
 *
 * The rail is App-local state (`navCollapsed`, persisted at `mc-nav`) but its
 * width is a LAYOUT FACT that consumers outside App need: ChatPage sizes the
 * activity panel's beside-vs-fill decision against the space actually left for
 * the chat, and the rail is the first thing subtracted from it.
 *
 * Published from the shell's target track, never measured from the DOM.
 * Keyboard toggles publish one target before the 150ms grid transition;
 * pointer drags disable that transition and publish each live width. Consumers
 * therefore follow the space actually available during direct manipulation,
 * without chasing intermediate DOM measurements during an animated toggle.
 *
 * Module-level (same shape as usePanelTabs) so the value survives consumer
 * remounts and needs no context provider.
 */
const RAIL_W_EXPANDED = 236
const RAIL_W_COLLAPSED = 74

/**
 * Duration of the shell's `grid-template-columns` transition (App.tsx), plus a
 * couple of frames of slack so the window closes AFTER the final resize of the
 * animation rather than in the middle of it.
 *
 * MUST stay in step with that CSS duration. If they drift apart the window
 * either closes early (the tail of the animation resumes thrashing) or stays
 * open too long (one extra frame of held-back height sync) — neither is a
 * correctness bug, but both blunt the point of the window.
 */
const RAIL_TRANSITION_MS = 150
const RAIL_SETTLE_SLACK_MS = 40
export const RAIL_SETTLE_MS = RAIL_TRANSITION_MS + RAIL_SETTLE_SLACK_MS

/** Rail width for the shell's current state. Mobile has no rail track. */
export function railWidthFor({ isMobile, collapsed }: { isMobile: boolean; collapsed: boolean }): number {
  if (isMobile) return 0
  return collapsed ? RAIL_W_COLLAPSED : RAIL_W_EXPANDED
}

let railWidth = RAIL_W_EXPANDED
const listeners = new Set<() => void>()

/**
 * Timestamp until which the rail's collapse animation is considered in flight.
 *
 * The collapse animates a LAYOUT property (`grid-template-columns`), so the
 * content column's width changes on every frame of it and every mounted
 * transcript row rewraps. Consumers that measure the DOM in a ResizeObserver
 * therefore see ~9 frames of transitional geometry whose measurements are all
 * superseded at the final width. Measured in isolation, that inflates the
 * virtualizer's ResizeObserver work and forced layout reads by 13-18x per
 * toggle, and none of the extra measurements change the final cached heights.
 *
 * Published here beside the width so both keyboard transitions and live
 * pointer resizes mark row rewrapping. A drag re-arms this timestamp per move;
 * geometryScheduling still flushes its pending work on a bounded timer, and
 * the actively streaming row remains exempt from that batching.
 */
let railSettlingUntil = 0

/** True while the rail's collapse/expand animation is still in flight. */
export function isRailSettling(): boolean {
  return performance.now() < railSettlingUntil
}

/** Called by App when the rail track changes. No-op when the value is unchanged. */
export function setRailWidth(w: number) {
  if (w === railWidth) return
  railWidth = w
  // Armed HERE rather than at a separate call site: this is already the single
  // point App notifies on a track change, so the window cannot be forgotten by
  // a future edit that changes how the rail collapses.
  railSettlingUntil = performance.now() + RAIL_SETTLE_MS
  listeners.forEach(l => l())
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => { listeners.delete(cb) }
}

const getSnapshot = () => railWidth

export function useRailWidth() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

/** Test seam: restore the module default between cases. */
export function __resetRailWidth() {
  railWidth = RAIL_W_EXPANDED
  railSettlingUntil = 0
  listeners.clear()
}
