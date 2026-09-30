/** Real built dashboard + isolated API fixtures, not prototype markup.
 * Usage: npm run build && node scripts/capture-grabber-layout.mjs <output-dir>
 * Verifies navigation drag/keyboard/persistence, actual session switching,
 * embedded SPA round trips, mobile drawers and simulated native insets.
 * Native controls and SSH/SSM are NOT exercised. Not a CI E2E replacement.
 */
import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { expect } from '@playwright/test'
import { serveDist } from './lib/serve-dist.mjs'
import { stubDashboardApi, json } from './lib/stub-dashboard-api.mjs'

const OUT = process.argv[2]
assert(OUT, 'Pass an output directory (use KIROCREW_SCRATCH for local captures)')
mkdirSync(OUT, { recursive: true })
const viewport = { width: 1440, height: 900 }
const results = []
const check = (name, pass) => { assert(pass, name); results.push(name) }
const slots = ['Navigation layout', 'Review the release plan', 'Investigate a failing test', 'Plan the next iteration'].map((title, i) => ({
  key: `layout-${i}`, title, running: false, last_message: 'Ready for review.',
  messages: 2, agent: 'kirocrew', memory_mode: 'persistent', folder_id: '',
  modified: 1790622000 - i * 600, source_links: [], source_links_total: 0,
}))
const detail = key => ({
  running: false, has_more: false, total: 2, queue: [], project: '',
  messages: [
    { role: 'user', content: `Let's work on ${slots.find(s => s.key === key)?.title.toLowerCase() ?? 'this task'}.`, ts: 1790621900 },
    { role: 'assistant', content: 'The session list and navigation here are the real dashboard components.\n\n- Existing destinations and session actions remain available.\n- Drag the navigation edge to resize it.\n- Collapse to icons without leaving this conversation.', ts: 1790621950 },
  ],
})
const host = await serveDist()
const remote = await serveDist()
let browser
try {
  browser = await chromium.launch()
  const remotePort = Number(new URL(remote.base).port)
  const crew = {
    id: 'build-box', name: 'Build Box', ssh_host: 'build-box-example', remote_port: 5476,
    local_port: remotePort, ttl: '20h', remote_bin: '', connection_method: 'ssh',
    was_connected: true,
    status: { instance_id: 'build-box', state: 'connected', local_port: remotePort, remote_port: 5476, token_ttl_remaining: 72000 },
  }
  async function scenario(theme, platform = null) {
    const context = await browser.newContext({ viewport, deviceScaleFactor: 1, locale: 'en-US', recordVideo: { dir: OUT, size: viewport } })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    // Only these two in-process origins serve documents/assets. API requests
    // are intercepted below and the fixture cannot reach a real gateway.
    await page.route('**/*', route => {
      const url = new URL(route.request().url())
      return [host.base, remote.base].includes(url.origin) ? route.fallback() : route.abort()
    })
    await stubDashboardApi(page, {
      theme, slots, preserveStorage: true,
      localStorageEntries: { 'mc-active-slot-chat': 'layout-0' },
      extra: async (path, route) => {
        const isRemote = new URL(route.request().url()).origin === remote.base
        if (path === '/api/instances') {
          await json(route, { active: true, instances: isRemote ? [] : [crew], warm_set_cap: 3,
            sso: { state: 'ok', seconds_remaining: 72000, expires_at: null, reason: 'valid' } })
          return true
        }
        if (/^\/api\/instances\/[^/]+\/(connect|refresh-token)$/.test(path)) {
          await json(route, { ...crew.status, token: 'fixture-only' }); return true
        }
        if (path.startsWith('/api/instances/')) { await json(route, { ok: true }); return true }
        const slot = /^\/api\/chat\/slots\/(layout-\d)$/.exec(path)
        if (slot) { await json(route, detail(slot[1])); return true }
        return false
      },
    })
    if (platform) await page.addInitScript(p => {
      // The embedded page gets native facts through the real host relay.
      if (window.parent !== window) return
      window.kirocrew = { isElectron: true, platform: p, linuxFrameless: p === 'linux' }
      window.electronAPI = { onFullScreenChanged: cb => { window.setFixtureFullscreen = cb; return () => {} } }
    }, platform)
    const nav = page.locator('#dashboard-navigation')
    const grip = page.getByTestId('navigation-grabber')
    const identity = page.getByTestId('navigation-crew-switcher')
    async function settle(root = page) {
      await expect(root.locator('#dashboard-navigation .nav-item').first()).toBeVisible()
      await expect.poll(() => root.locator('#dashboard-navigation .nav-item').evaluateAll(items => items.every(el => {
        const t = getComputedStyle(el).transform
        return t === 'none' || t === 'matrix(1, 0, 0, 1, 0, 0)'
      }))).toBe(true)
    }
    async function width(value, root = page) {
      await expect.poll(async () => Math.round((await root.locator('#dashboard-navigation').boundingBox()).width)).toBe(value)
    }
    async function startRowTracking() {
      const rows = page.locator('.sidebar-inner [data-slot-key]')
      await expect.poll(() => rows.evaluateAll(items => items.length > 0 && items.every(el => {
        const t = getComputedStyle(el).transform
        return t === 'none' || t === 'matrix(1, 0, 0, 1, 0, 0)'
      }))).toBe(true)
      return page.evaluateHandle(() => {
        const panel = document.querySelector('.sidebar-inner')
        const rows = [...panel.querySelectorAll('[data-slot-key]')]
        const initialX = rows.map(row => row.getBoundingClientRect().x - panel.getBoundingClientRect().x)
        const probe = { active: true, frames: 0, maxDrift: 0, sameRows: true }
        const sample = () => {
          if (!probe.active) return
          probe.frames++
          const panelX = panel.getBoundingClientRect().x
          rows.forEach((row, i) => {
            probe.sameRows &&= row.isConnected
            probe.maxDrift = Math.max(probe.maxDrift, Math.abs(row.getBoundingClientRect().x - panelX - initialX[i]))
          })
          requestAnimationFrame(sample)
        }
        requestAnimationFrame(sample)
        return probe
      })
    }
    async function finishRowTracking(probe, label) {
      try {
        // Include the release animation, not only the pointer-down interval.
        const result = await probe.evaluate(p => new Promise(resolve => {
          let frames = 24
          const next = () => {
            if (--frames) requestAnimationFrame(next)
            else { p.active = false; resolve(p) }
          }
          requestAnimationFrame(next)
        }))
        check(`${theme}: ${label} carries the same session rows without horizontal lag`, result.frames > 1 && result.sameRows && result.maxDrift < 0.5)
      } finally {
        await probe.evaluate(p => { p.active = false })
        await probe.dispose()
      }
    }
    async function shoot(name) {
      await settle()
      await page.screenshot({ path: join(OUT, name + '.png') })
    }
    try {
      await page.goto(host.base + '/chat', { waitUntil: 'domcontentloaded' })
      await expect(identity).toContainText('Local')
      await expect(page.locator('.sidebar-inner')).toBeVisible()
      await width(236)
      await settle()
      const geom = await page.evaluate(() => {
        const nav = document.querySelector('#dashboard-navigation')
        const surface = document.querySelector('.dashboard-surface')
        const header = document.querySelector('header.topbar-glass')
        const s = getComputedStyle(surface)
        return { y: nav.getBoundingClientRect().y, edge: nav.getBoundingClientRect().right,
          surfaceX: surface.getBoundingClientRect().x, surfaceRight: surface.getBoundingClientRect().right,
          dockX: document.querySelector('#activity-bar-slot').getBoundingClientRect().x,
          surfaceBottom: surface.getBoundingClientRect().bottom, shellBottom: nav.closest('[data-testid="dashboard-shell"]').getBoundingClientRect().bottom,
          top: s.borderTopWidth, radius: s.borderTopLeftRadius, rightRadius: s.borderTopRightRadius, rightBorder: s.borderRightWidth,
          chrome: getComputedStyle(nav).backgroundColor === getComputedStyle(header).backgroundColor,
          left: parseFloat(getComputedStyle(header).paddingLeft), right: parseFloat(getComputedStyle(header).paddingRight) }
      })
      check(`${theme}/${platform}: continuous chrome and inset page`, geom.chrome && geom.y >= 42 && geom.edge === geom.surfaceX && geom.top === '1px' && geom.radius === '8px')
      check(`${theme}/${platform}: subtle right frame with no bottom gap`, geom.rightRadius === '8px' && geom.rightBorder === '1px' && Math.abs(geom.dockX - geom.surfaceRight - 6) < 0.5 && geom.surfaceBottom === geom.shellBottom)
      check(`${theme}/${platform}: desktop chooser only in navigation`, await page.locator('.instance-tab-bar-inline:visible').count() === 0)
      check(`${theme}/${platform}: whole grabber receives pointer hits`, await grip.evaluate(el => {
        const r = el.getBoundingClientRect()
        return [r.left + 1, r.right - 1].every(x => el.contains(document.elementFromPoint(x, r.y + r.height / 2)))
      }))
      if (platform) {
        check(`${platform}: caption clearance`, platform === 'darwin' ? geom.left >= 84 : geom.right >= (platform === 'win32' ? 138 : 108))
        if (platform === 'darwin') {
          await page.evaluate(() => window.setFixtureFullscreen(true))
          await expect(page.getByTestId('dashboard-shell')).toHaveClass(/mac-fullscreen/)
          await page.evaluate(() => window.setFixtureFullscreen(false))
          await expect(page.getByTestId('dashboard-shell')).not.toHaveClass(/mac-fullscreen/)
        }
      } else {
        await shoot(`expanded-${theme}`)
        // Persist the same element handles across actual browser pointer events.
        const gripNode = await grip.elementHandle()
        const navNode = await nav.elementHandle()
        const rect = await grip.boundingBox()
        const navMotion = await startRowTracking()
        await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2)
        await page.mouse.down()
        await page.mouse.move(rect.x + rect.width / 2 - 150, rect.y + rect.height / 2, { steps: 15 })
        await expect(nav).toHaveAttribute('data-dragging', 'true')
        check(`${theme}: direct manipulation has no grid transition`, await page.getByTestId('dashboard-shell').evaluate(el => getComputedStyle(el).transitionDuration) === '0s')
        await page.mouse.up()
        await finishRowTracking(navMotion, 'navigation drag')
        await width(74)
        check(`${theme}: persistent nodes`, await gripNode.evaluate(el => el === document.querySelector('[data-testid="navigation-grabber"]')) && await navNode.evaluate(el => el === document.querySelector('#dashboard-navigation')))
        await expect(grip).toBeFocused()
        const icon = page.locator('#dashboard-navigation .nav-active > .nav-icon-frame > .app-icon-nav').first()
        const tile = await icon.boundingBox()
        check(`${theme}: compact selection is 36px square`, tile.width === 36 && tile.height === 36)
        await page.mouse.move(900, 700)
        check(`${theme}: pointer drag leaves no exterior grabber ring`, await page.getByTestId('navigation-grabber-handle').evaluate(el => getComputedStyle(el).boxShadow) === 'none')
        await shoot(`compact-${theme}`)
        await grip.press('Enter'); await width(236)
        await expect.poll(async () => (await page.getByTestId('navigation-grabber-handle').boundingBox()).width).toBe(6)
        check(`${theme}: keyboard focus thickens the grip without a ring`, await page.getByTestId('navigation-grabber-handle').evaluate(el => getComputedStyle(el).boxShadow) === 'none')
        await grip.press('End'); await width(300)
        await grip.press('ArrowLeft'); await width(284)
        await page.reload(); await width(284)
        await grip.press('Home'); await width(74)
        await grip.press('Enter'); await width(284)
        check(`${theme}: keyboard and reload retain width`, true)
        const sessionHandle = await page.locator('.sidebar-resize-handle').boundingBox()
        const sessionWidth = (await page.locator('.sidebar-inner').boundingBox()).width
        const sessionMotion = await startRowTracking()
        await page.mouse.move(sessionHandle.x + sessionHandle.width / 2, sessionHandle.y + sessionHandle.height / 2)
        await page.mouse.down()
        await page.mouse.move(sessionHandle.x + sessionHandle.width / 2 + 100, sessionHandle.y + sessionHandle.height / 2, { steps: 15 })
        await page.mouse.up()
        await finishRowTracking(sessionMotion, 'Sessions resize')
        await expect.poll(async () => Math.round((await page.locator('.sidebar-inner').boundingBox()).width)).toBe(Math.round(sessionWidth + 100))
        await page.locator('.sidebar-inner').getByText(slots[1].title, { exact: true }).click()
        await expect(page.getByText("Let's work on review the release plan.", { exact: true })).toBeVisible()
        check(`${theme}: actual sidebar switches the transcript`, true)
      }
      // A SECOND actual built SPA, cross-origin, with the real ready/model and
      // switch messages. No blank iframe or hand-written remote-page mock.
      await identity.click()
      await expect(page.getByRole('menuitem', { name: 'Add remote crew', exact: true })).toBeVisible()
      await expect.poll(() => page.getByRole('menu').evaluate(el => getComputedStyle(el).opacity)).toBe('1')
      await page.screenshot({ path: join(OUT, `crew-switcher-local-${platform ?? theme}.png`) })
      await page.getByRole('menuitemradio', { name: /Build Box/ }).click()
      const frame = page.frameLocator('iframe[title="Build Box"]')
      const remoteIdentity = frame.getByTestId('navigation-crew-switcher')
      await expect(remoteIdentity).toContainText('Build Box')
      await expect(frame.locator('.sidebar-inner')).toBeVisible()
      await settle(frame)
      await page.screenshot({ path: join(OUT, `remote-${platform ?? theme}.png`) })
      const iframe = await page.locator('iframe[title="Build Box"]').elementHandle()
      await remoteIdentity.click()
      await expect(frame.getByRole('menuitem', { name: 'Add remote crew', exact: true })).toBeVisible()
      await expect.poll(() => frame.getByRole('menu').evaluate(el => getComputedStyle(el).opacity)).toBe('1')
      await expect.poll(() => frame.getByRole('menu').evaluate(el => {
        const r = el.getBoundingClientRect()
        return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2))
      })).toBe(true)
      await page.screenshot({ path: join(OUT, `crew-switcher-remote-${platform ?? theme}.png`) })
      await frame.getByRole('menuitem', { name: 'Add remote crew', exact: true }).click()
      await expect(frame.locator('[data-setting-key="remote-crew-add"]')).toBeVisible()
      check(`${theme}/${platform}: embedded add action opens this crew's real setup form`, true)
      await remoteIdentity.click()
      await frame.getByRole('menuitemradio', { name: /Local/ }).click()
      await expect(identity).toBeVisible()
      check(`${theme}/${platform}: remote identity and local return preserve iframe`, await iframe.evaluate(el => el === document.querySelector('iframe[title="Build Box"]')))
      if (!platform) {
        for (const mobileWidth of [390, 320]) {
          await page.setViewportSize({ width: mobileWidth, height: 844 })
          await expect(grip).toHaveCount(0)
          check(`${theme}/${mobileWidth}: no document overflow`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
          await page.getByTestId('mobile-topbar-sessions-toggle').click()
          await expect(page.locator('.mobile-sessions-overlay .sidebar-inner')).toBeVisible()
          await expect.poll(async () => Math.round((await page.locator('.mobile-sessions-overlay').boundingBox()).x)).toBe(0)
          await page.screenshot({ path: join(OUT, `mobile-${mobileWidth}-${theme}.png`) })
          // The sessions drawer covers the toolbar while open; dismiss through
          // its uncovered scrim, not the obscured toggle or an unsupported key.
          await page.mouse.click(mobileWidth - 8, 500)
          await expect(page.locator('.mobile-sessions-overlay')).toHaveCount(0)
          check(`${theme}/${mobileWidth}: actual mobile session drawer`, true)
        }
      }
      check(`${theme}/${platform}: no page errors`, errors.length === 0)
    } finally {
      const video = page.video()
      await context.close()
      const videoPath = join(OUT, `${theme}-${platform ?? 'browser'}.webm`)
      await video.saveAs(videoPath)
      await video.delete()
      console.log(`Recording: ${videoPath}`)
    }
  }
  async function firstRemoteCrew(disabled) {
    const context = await browser.newContext({ viewport, deviceScaleFactor: 1, locale: 'en-US' })
    const page = await context.newPage()
    const writes = []
    page.on('request', request => {
      const path = new URL(request.url()).pathname
      if (request.method() !== 'GET' && /^\/api\/(instances|cloud|config)\b/.test(path)) writes.push(path)
    })
    try {
      await page.route('**/*', route => new URL(route.request().url()).origin === host.base ? route.fallback() : route.abort())
      await stubDashboardApi(page, {
        slots, localStorageEntries: { 'mc-nav': disabled ? '1' : '0' },
        extra: async (path, route) => {
          if (path === '/api/instances') {
            await json(route, disabled ? { error: 'instances feature is disabled (set instances.enabled=true)' } : { active: true, instances: [], warm_set_cap: 3 }, disabled ? 403 : 200)
            return true
          }
          if (path === '/api/cloud/launches') { await json(route, { jobs: [] }); return true }
          return false
        },
      })
      await page.goto(host.base + '/chat', { waitUntil: 'domcontentloaded' })
      const chooser = page.getByRole('button', { name: 'Local — Switch crew', exact: true })
      await expect(chooser).toBeVisible()
      await chooser.click()
      await expect(page.getByRole('menuitemradio', { name: /Local/ })).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(chooser).toBeFocused()
      await expect.poll(() => chooser.evaluate(el => getComputedStyle(el).outlineStyle)).toBe('none')
      await expect.poll(() => chooser.evaluate(el => getComputedStyle(el).boxShadow)).toBe('none')
      await expect.poll(() => chooser.evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)')
      check(`first crew/${disabled ? 'collapsed' : 'expanded'}: keyboard return uses a background cue without an outline or underline`, true)
      await page.screenshot({ path: join(OUT, `crew-focus-${disabled ? 'collapsed' : 'expanded'}.png`) })
      await chooser.press('Enter')
      await page.getByRole('menuitem', { name: 'Add remote crew', exact: true }).click()
      if (disabled) {
        await expect(page.getByRole('button', { name: 'Enable remote crew management', exact: true })).toBeVisible()
      } else {
        await expect(page.locator('[data-setting-key="remote-crew-add"]')).toBeVisible()
      }
      check(`first crew/${disabled ? 'disabled' : 'enabled'}: setup remains reachable without automatic writes`, writes.length === 0)
    } finally {
      await context.close()
    }
  }
  await firstRemoteCrew(false)
  await firstRemoteCrew(true)
  async function transcriptScroll(theme, touch = false) {
    const context = await browser.newContext({ viewport, deviceScaleFactor: 1, locale: 'en-US', hasTouch: touch })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    const messages = Array.from({ length: 16 }, (_, i) => [
      { role: 'user', content: `Scroll check ${i + 1}: review the session layout.`, ts: 1790621900 + i * 60 },
      { role: 'assistant', content: `Review ${i + 1} complete.\n\n- The header stays in place.\n- The composer stays available.\n- Only the transcript scrolls.`, ts: 1790621930 + i * 60 },
    ]).flat()
    try {
      await page.route('**/*', route => new URL(route.request().url()).origin === host.base ? route.fallback() : route.abort())
      await stubDashboardApi(page, {
        theme, slots, localStorageEntries: { 'mc-active-slot-chat': 'layout-0' },
        extra: async (path, route) => {
          if (path === '/api/chat/slots/layout-0') {
            await json(route, { running: false, has_more: false, total: messages.length, queue: [], messages })
            return true
          }
          return false
        },
      })
      await page.goto(host.base + '/chat', { waitUntil: 'domcontentloaded' })
      const scroller = page.locator('.chat-container')
      await expect(scroller).toBeVisible()
      await expect.poll(() => scroller.evaluate(el => el.scrollHeight - el.clientHeight)).toBeGreaterThan(500)
      await page.mouse.move(20, 20)
      const css = await scroller.evaluate(el => {
        const style = getComputedStyle(el)
        const track = getComputedStyle(el, '::-webkit-scrollbar-track')
        return { width: style.scrollbarWidth, gutter: style.scrollbarGutter, overflowY: style.overflowY,
          overflowX: style.overflowX, overscroll: style.overscrollBehavior,
          color: style.scrollbarColor, muted: style.getPropertyValue('--muted'),
          trackStart: track.marginBlockStart, trackEnd: track.marginBlockEnd,
          coarse: matchMedia('(pointer: coarse)').matches }
      })
      check(`${theme}/${touch}: transcript keeps its scroll contract`, css.width === 'thin' && css.gutter === 'stable' && css.overflowY === 'auto' && css.overflowX === 'hidden' && css.overscroll === 'contain')
      check(`${theme}/${touch}: track ends clear the frame`, css.trackStart === '8px' && css.trackEnd === '8px')
      check(`${theme}/${touch}: scrollbar idle visibility follows pointer type`, css.coarse === touch && (touch ? !css.color.startsWith('rgba(0, 0, 0, 0)') : css.color.startsWith('rgba(0, 0, 0, 0)')))
      const dock = page.getByTestId('composer-dock-root')
      const beforeDock = await dock.boundingBox()
      const beforeHeader = await page.locator('header.topbar-glass').boundingBox()
      if (!touch) {
        await scroller.hover()
        await expect.poll(() => scroller.evaluate(el => getComputedStyle(el).scrollbarColor.startsWith('rgba(0, 0, 0, 0)'))).toBe(false)
        await scroller.focus()
        await page.mouse.move(20, 20)
        await expect.poll(() => scroller.evaluate(el => getComputedStyle(el).scrollbarColor.startsWith('rgba(0, 0, 0, 0)'))).toBe(false)
        check(`${theme}: hover and keyboard focus reveal the thumb`, true)
      }
      // Wheel input exercises the real follow/pin controller, rather than
      // assigning scrollTop and accidentally testing a different input path.
      await scroller.hover()
      await expect.poll(() => scroller.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeLessThan(4)
      const bottom = await scroller.evaluate(el => el.scrollTop)
      await page.mouse.wheel(0, -600)
      await expect.poll(() => scroller.evaluate(el => el.scrollTop)).toBeLessThan(bottom - 100)
      await expect(page.getByRole('button', { name: 'Scroll to bottom', exact: true })).toBeVisible()
      const containers = await page.evaluate(() => ({ page: document.scrollingElement.scrollTop, main: document.querySelector('#main-content').scrollTop }))
      const afterDock = await dock.boundingBox()
      const afterHeader = await page.locator('header.topbar-glass').boundingBox()
      check(`${theme}/${touch}: only the transcript moves`, containers.page === 0 && containers.main === 0 && Math.abs(afterDock.y - beforeDock.y) < 1 && afterHeader.y === beforeHeader.y)
      await page.screenshot({ path: join(OUT, `transcript-scroll-${theme}-${touch ? 'touch' : 'pointer'}.png`) })
      await page.getByRole('button', { name: 'Scroll to bottom', exact: true }).click()
      await expect.poll(() => scroller.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeLessThan(4)
      check(`${theme}/${touch}: jump to bottom still works`, true)
      check(`${theme}/${touch}: transcript has no page errors`, errors.length === 0)
    } finally {
      await context.close()
    }
  }
  await transcriptScroll('dark')
  await transcriptScroll('light')
  await transcriptScroll('dark', true)
  async function updateLayers(theme, screenWidth, compact = false) {
    const context = await browser.newContext({ viewport: { width: screenWidth, height: 900 }, deviceScaleFactor: 1, locale: 'en-US' })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    let releaseStatus
    const statusReady = new Promise(resolve => { releaseStatus = resolve })
    const label = `${theme}/${screenWidth}/${compact ? 'compact' : 'expanded'}`
    try {
      await page.route('**/*', route => new URL(route.request().url()).origin === host.base ? route.fallback() : route.abort())
      await stubDashboardApi(page, {
        theme, slots, localStorageEntries: { 'mc-active-slot-chat': 'layout-0', 'mc-nav': compact ? '1' : '0' },
        extra: async (path, route) => {
          if (path === '/api/status') {
            // Release the real status read only after the underlying drawer is
            // open, so the probe covers an already-visible sidebar on phones.
            await statusReady
            await json(route, { sessions: slots.length, crons: 0, lessons: 0, uptime: 120, version: '0.5.0',
              update_available: true, update_latest_version: '9.9.9', update_can_apply: true })
            return true
          }
          const slot = /^\/api\/chat\/slots\/(layout-\d)$/.exec(path)
          if (slot) { await json(route, detail(slot[1])); return true }
          if (path === '/api/update/check') { await json(route, { changes: '' }); return true }
          if (/^\/api\/update\/(apply|arm|approve)$/.test(path)) {
            throw new Error('A layout check must never request an update')
          }
          return false
        },
      })
      // Drive the actual preload subscription for the downloaded prompt. No
      // modal markup or application store is replaced by this fixture.
      await page.addInitScript(() => {
        window.updateAPI = {
          onState: cb => { window.emitFixtureUpdate = cb; return () => { delete window.emitFixtureUpdate } },
          getInfo: async () => ({ autoDownload: false }),
        }
      })
      await page.goto(host.base + '/chat', { waitUntil: 'domcontentloaded' })
      const mobile = screenWidth < 768
      if (mobile) {
        await page.getByTestId('mobile-topbar-sessions-toggle').click()
        await expect.poll(async () => Math.round((await page.locator('.mobile-sessions-overlay').boundingBox()).x)).toBe(0)
      }
      await expect(page.locator('.sidebar-inner')).toBeVisible()
      releaseStatus()
      for (const kind of ['available', 'downloaded']) {
        if (kind === 'downloaded') {
          await expect.poll(() => page.evaluate(() => typeof window.emitFixtureUpdate)).toBe('function')
          await page.evaluate(() => window.emitFixtureUpdate({ state: 'downloaded', version: '9.9.10' }))
        }
        const dialog = page.getByRole('dialog', { name: kind === 'available' ? 'Update available' : 'Update ready', exact: true })
        await expect(dialog).toBeVisible()
        // Test paint order, not just CSS numbers: the backdrop must intercept
        // the sidebar/rail, and the dialog itself must win where they overlap.
        await expect.poll(() => dialog.evaluate(panel => {
          const backdrop = panel.parentElement
          const rect = panel.getBoundingClientRect()
          const sidebar = document.querySelector('.sidebar-inner').getBoundingClientRect()
          const nav = document.querySelector('#dashboard-navigation')
          const points = [[rect.left + 4, rect.top + rect.height / 2],
            [sidebar.left + sidebar.width / 2, 700]]
          if (nav) {
            const rail = nav.getBoundingClientRect()
            points.push([rail.left + rail.width / 2, 700])
          }
          return points.every(([x, y]) => backdrop.contains(document.elementFromPoint(x, y)))
            && panel.contains(document.elementFromPoint(rect.left + 4, rect.top + rect.height / 2))
        })).toBe(true)
        check(`${label}: ${kind} modal and backdrop cover both sidebars`, true)
        await expect.poll(() => dialog.evaluate(panel => getComputedStyle(panel.parentElement).opacity)).toBe('1')
        await page.screenshot({ path: join(OUT, `modal-${kind}-${theme}-${screenWidth}-${compact ? 'compact' : 'expanded'}.png`) })
        await dialog.getByRole('button', { name: 'Dismiss', exact: true }).click()
        await expect(dialog).toHaveCount(0)
      }
      if (!mobile) {
        const nav = page.locator('#dashboard-navigation')
        const grip = page.getByTestId('navigation-grabber')
        const dot = nav.getByRole('status', { name: /^Update available$/i })
        for (const [key, width] of [['Home', 74], ['End', 300], ['Home', 74]]) {
          await grip.press(key)
          await expect.poll(async () => Math.round((await nav.boundingBox()).width)).toBe(width)
          const position = await dot.evaluate(el => {
            const row = el.closest('.nav-item')
            const icon = row.querySelector('.app-icon-nav')
            const d = el.getBoundingClientRect(), r = row.getBoundingClientRect(), i = icon.getBoundingClientRect()
            return { iconAnchored: el.parentElement.classList.contains('nav-icon-frame'),
              iconTop: d.top - i.top, iconRight: i.right - d.right,
              rowRight: r.right - d.right, center: (d.top + d.bottom - r.top - r.bottom) / 2,
              parentOpacity: getComputedStyle(el.parentElement).opacity }
          })
          check(`${label}/${key}: Settings dot follows its collapsed icon or expanded row`, width === 74
            ? position.iconAnchored && Math.abs(position.iconTop - 4) < 0.5 && Math.abs(position.iconRight - 4) < 0.5 && position.parentOpacity === '1'
            : !position.iconAnchored && Math.abs(position.rowRight - 8) < 0.5 && Math.abs(position.center) < 0.5)
        }
        await page.mouse.move(900, 700)
        await page.screenshot({ path: join(OUT, `settings-dot-${theme}-${screenWidth}-${compact ? 'compact' : 'expanded'}.png`) })
      }
      check(`${label}: update prompts never raise a page error`, errors.length === 0)
    } finally {
      releaseStatus()
      await context.close()
    }
  }
  await updateLayers('dark', 1440)
  await updateLayers('dark', 1440, true)
  await updateLayers('light', 390)
  await updateLayers('light', 320)
  await scenario('dark')
  await scenario('light')
  for (const platform of ['darwin', 'win32', 'linux']) await scenario('dark', platform)
  writeFileSync(join(OUT, 'checks.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(`PASS: ${results.length} checks; screenshots and videos in ${OUT}`)
} finally {
  await browser?.close()
  host.srv.close()
  remote.srv.close()
}
