// Feature: chat-virtualizer — our own layout changes must not read as the reader
// leaving (the first-run E2E proof run, where follow was lost at the hello).
//
// The first streamed output of a turn can REGROUP the rows above it: the first
// run's setup rows were loose until the hello arrived, then the grouping wrapped
// them into one turn item. Fewer, re-keyed rows, and the content shrinks. The
// layout engine clamps a flush follower down to the shrunk bottom, with no write
// of ours, and the clamp's scroll event dispatches a frame later. The next
// output then grew the content again before anything had looked: the pin saw
// scrollTop below our last write with a gap under it and released follow, and
// the hello streamed on below the fold behind "Scroll to bottom".
//
// jsdom has no layout, so geometry is faked on a detached scroller (the
// technique of useVirtualChat.layoutShrink.test.tsx) and the clamp is applied
// the way the engine applies it: scrollTop moves, no scroll event yet.
//
// The clamp can also come and go inside one commit. Framer measures a
// `layoutId` node as it unmounts, so a regroup remounting the setup cards forced
// a layout on a half-removed transcript: the follower was clamped to its top,
// and the new rows grew the content back before any effect of ours looked. A
// layout effect declared ahead of the hook plays that part here.
//
// Before the hello nothing runs, so a follower found off our last write reads as
// a reader who left. Native scroll anchoring lifts a follower a few pixels as a
// card lands in its row, inside a commit or at frame time, and that was enough
// to release follow during the scripted steps.

import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useLayoutEffect, type RefObject } from 'react'

import { useVirtualChat } from '../hooks/virtualizer/useVirtualChat'
import type { UseVirtualChatOptions } from '../hooks/virtualizer/types'

interface Geom { scrollTop: number; scrollHeight: number; clientHeight: number }

function makeScroller(initial: Geom) {
  const el = document.createElement('div')
  const state: Geom = { ...initial }
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => state.scrollTop,
    set: (v: number) => { state.scrollTop = v },
  })
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => state.scrollHeight })
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => state.clientHeight })
  ;(el as unknown as { scrollTo: (o: { top: number }) => void }).scrollTo = (o) => { state.scrollTop = o.top }
  return { el, state }
}

interface Item { id: string }
const getKey = (it: Item) => it.id
const loose = (n: number): Item[] => Array.from({ length: n }, (_, i) => ({ id: `setup-${i}` }))
/** The same rows wrapped into one turn item, plus the streaming reply. */
const regrouped = (extra: number): Item[] => [{ id: 'turn-hello' }, ...Array.from({ length: extra }, (_, i) => ({ id: `tail-${i}` }))]

const CH = 717
const SH = 1300

type Props = UseVirtualChatOptions<Item> & { duringCommit?: () => void }

function mount(sessionId: string, runActive = true) {
  const { el, state } = makeScroller({ scrollTop: 0, scrollHeight: SH, clientHeight: CH })
  const ref: RefObject<HTMLDivElement | null> = { current: el }
  const props = (items: Item[], duringCommit?: () => void): Props => {
    // Once: the commit the rerender starts, not the re-renders it causes.
    let pending = duringCommit
    const once = () => { const f = pending; pending = undefined; f?.() }
    return { items, sessionId, getKey, externalScrollerRef: ref, runActive, duringCommit: once }
  }
  const view = renderHook((p: Props) => {
    // Runs in the commit, after its mutations and before the hook's own effects.
    useLayoutEffect(() => { p.duringCommit?.() })
    return useVirtualChat<Item>(p)
  }, { initialProps: props(loose(12)) })
  return { el, state, view, props }
}

/** The engine's half of a content shrink: scrollHeight drops, a flush reader is clamped, no scroll event. */
function shrinkTo(state: Geom, scrollHeight: number) {
  state.scrollHeight = scrollHeight
  state.scrollTop = Math.min(state.scrollTop, Math.max(0, scrollHeight - state.clientHeight))
}

describe('useVirtualChat: a regroup that shrinks the content while following', () => {
  beforeEach(() => localStorage.clear())

  it('keeps following when the regroup clamps the reader and the reply then grows', () => {
    const { el, state, view, props } = mount('regroup-grow')
    expect(el.scrollTop).toBe(SH - CH)

    // The hello's first output regroups twelve loose rows into one turn: fewer
    // rows, a new tail, and 440px less content. The engine clamps.
    act(() => {
      shrinkTo(state, SH - 440)
      view.rerender(props(regrouped(0)))
    })
    // The reply grows before the clamp's scroll event has dispatched.
    act(() => {
      state.scrollHeight = SH - 440 + 220
      view.rerender(props(regrouped(1)))
    })
    expect(el.scrollTop).toBe(SH - 440 + 220 - CH)
    expect(view.result.current.getFollow()).toBe(true)
  })

  it('reads the late scroll event of that clamp as ours, not as the reader leaving', () => {
    const { el, state, view, props } = mount('regroup-late-scroll')
    act(() => {
      shrinkTo(state, SH - 440)
      view.rerender(props(regrouped(0)))
    })
    // Content grows again with no pin yet, then the clamp's scroll event lands.
    act(() => { state.scrollHeight = SH - 440 + 220 })
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(view.result.current.getFollow()).toBe(true)
    act(() => {
      state.scrollHeight = SH - 440 + 400
      view.rerender(props(regrouped(2)))
    })
    expect(el.scrollTop).toBe(SH - 440 + 400 - CH)
  })

  it('keeps following when the clamp is followed by chrome mounting below', () => {
    // The proof run's first loss, before any reply: a 7px reprice clamped the
    // follower, then the setup tray mounted under the transcript (44px) before
    // either scroll event dispatched. The reader sits at the bottom of the box
    // they were followed in, 44px above the new one.
    const { el, state, view, props } = mount('regroup-tray')
    act(() => {
      shrinkTo(state, SH - 7)
      state.clientHeight = CH - 44
      view.rerender(props(loose(12)))
    })
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(view.result.current.getFollow()).toBe(true)
    act(() => {
      state.scrollHeight = SH + 200
      view.rerender(props([...loose(12), { id: 'hello' }]))
    })
    expect(el.scrollTop).toBe(SH + 200 - (CH - 44))
  })

  it('carries the reader back when the clamp came and went inside the commit', () => {
    const { el, state, view, props } = mount('regroup-in-commit')
    act(() => {
      view.rerender(props(regrouped(1), () => {
        // The unmount-time measure: clamped to the top of what is left...
        shrinkTo(state, CH - 40)
        // ...then the regrouped rows land, taller than before.
        state.scrollHeight = SH + 100
      }))
    })
    expect(el.scrollTop).toBe(SH + 100 - CH)
    expect(view.result.current.getFollow()).toBe(true)
    // And the clamp's own scroll event, a frame later, is ours.
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(view.result.current.getFollow()).toBe(true)
  })

  it('keeps a follower in the scripted steps when a landing card lifts them inside the commit', () => {
    // Nothing runs during the first run's scripted steps, so off our write reads
    // as the reader leaving. A card landing in the transcript settled a row
    // above the follower and native scroll anchoring lifted them 7px within the
    // same commit that grew the content: follow was released before the hello.
    const { el, state, view, props } = mount('scripted-anchoring', false)
    act(() => {
      view.rerender(props([...loose(12), { id: 'privacy-card' }], () => {
        state.scrollTop += 7
        state.scrollHeight += 107
      }))
    })
    expect(view.result.current.getFollow()).toBe(true)
    expect(el.scrollTop).toBe(SH + 107 - CH)
  })

  it('keeps a follower in the scripted steps when anchoring lifts them between commits', () => {
    // The same lift, applied by the engine at frame time rather than inside a
    // commit (a hand-off animation was running): its scroll event arrives on
    // its own, and the next commit's pin found the follower off our write.
    const { el, state, view, props } = mount('scripted-anchoring-frame', false)
    act(() => {
      state.scrollTop += 7
      state.scrollHeight += 107
      el.dispatchEvent(new Event('scroll'))
    })
    act(() => { view.rerender(props([...loose(12), { id: 'privacy-card' }])) })
    expect(view.result.current.getFollow()).toBe(true)
    expect(el.scrollTop).toBe(SH + 107 - CH)
  })

  it('leaves a reader who touched the scroller during the commit where they are', () => {
    const { el, state, view, props } = mount('regroup-in-commit-input')
    act(() => {
      view.rerender(props(regrouped(1), () => {
        // Rendering yielded and the reader's wheel landed before the commit.
        el.dispatchEvent(new WheelEvent('wheel', { deltaY: -200, bubbles: true, cancelable: true }))
        state.scrollTop -= 200
        state.scrollHeight = SH + 100
      }))
    })
    expect(el.scrollTop).toBe(SH - CH - 200)
  })

  it('still lets a reader who scrolled up go when the transcript regroups', () => {
    const { el, state, view, props } = mount('regroup-reader')
    const bottom = SH - CH
    act(() => { state.scrollTop = bottom - 300; el.dispatchEvent(new Event('scroll')) })
    expect(view.result.current.getFollow()).toBe(false)
    act(() => {
      state.scrollHeight = SH - 100
      view.rerender(props(regrouped(0)))
    })
    act(() => {
      state.scrollHeight = SH + 200
      view.rerender(props(regrouped(1)))
    })
    expect(el.scrollTop).toBe(bottom - 300)
    expect(view.result.current.getFollow()).toBe(false)
  })

  it('releases a reader whose small scroll-up the regroup clamp swallowed', () => {
    const { el, state, view, props } = mount('regroup-wheel-up')
    // A short wheel up, then the regroup clamps them to the shrunk bottom before
    // the scroll event dispatches: the clamp erased the evidence, the input did not.
    act(() => {
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: -30, bubbles: true, cancelable: true }))
      state.scrollTop -= 30
      shrinkTo(state, SH - 440)
      view.rerender(props(regrouped(0)))
    })
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(view.result.current.getFollow()).toBe(false)
    act(() => {
      state.scrollHeight = SH - 440 + 220
      view.rerender(props(regrouped(1)))
    })
    expect(el.scrollTop).toBe(SH - 440 - CH)
  })
})
