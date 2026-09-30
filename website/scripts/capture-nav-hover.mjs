/** Verify collapsed navigation hover timing/placement on the real built SPA.
 * Usage: node scripts/capture-nav-hover.mjs "$KIROCREW_SCRATCH/nav-hover"
 * API data is isolated fixture data; no live gateway or remote connection.
 */
import assert from 'node:assert/strict'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { expect } from '@playwright/test'
import { serveDist } from './lib/serve-dist.mjs'
import { stubDashboardApi, json } from './lib/stub-dashboard-api.mjs'

const out = process.argv[2]
assert(out, 'Pass a screenshot output directory')
mkdirSync(out, { recursive: true })
const host = await serveDist()
let browser
try {
  browser = await chromium.launch()
  for (const theme of ['dark', 'light']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-US' })
    try {
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', e => errors.push(e.message))
      await page.route('**/*', route => new URL(route.request().url()).origin === host.base ? route.fallback() : route.abort())
      await stubDashboardApi(page, {
        theme,
        slots: [{ key: 'hover-demo', title: 'Navigation layout', running: false, messages: 2, agent: 'kirocrew', memory_mode: 'persistent', folder_id: '', modified: 1790622000 }],
        localStorageEntries: { 'mc-nav': '1', 'mc-active-slot-chat': 'hover-demo' },
        extra: async (path, route) => {
          if (path !== '/api/chat/slots/hover-demo') return false
          await json(route, { running: false, has_more: false, total: 2, queue: [], project: '', messages: [
            { role: 'user', content: 'Keep navigation compact, with labels on hover.', ts: 1790621900 },
            { role: 'assistant', content: 'Hover an icon to see its label directly beside the tile after 100ms.', ts: 1790621950 },
          ] })
          return true
        },
      })
      await page.goto(`${host.base}/chat`)
      const nav = page.locator('#dashboard-navigation')
      const row = nav.getByRole('button', { name: 'Settings', exact: true })
      const tile = row.locator('.app-icon-nav')
      const tip = page.getByRole('tooltip').filter({ hasText: 'Settings' })
      await expect(row).toBeVisible()
      await expect(nav).toHaveAttribute('data-compact', 'true')
      await expect.poll(() => nav.evaluate(el => Math.round(el.getBoundingClientRect().width))).toBe(74)
      await expect.poll(() => row.evaluate(el => getComputedStyle(el).transform)).toBe('none')
      await page.clock.install()
      await page.clock.pauseAt(new Date(Date.now() + 1000))
      const r = await row.boundingBox()
      const hover = () => page.mouse.move(r.x + r.width / 2, r.y + r.height / 2)
      await page.mouse.move(700, 400)
      const rest = await tile.boundingBox()
      const restFill = await tile.evaluate(el => getComputedStyle(el).backgroundColor)
      await expect(tile).toHaveCSS('box-shadow', 'none')
      await hover()
      // Tile answers immediately, even before the delayed tooltip exists.
      await expect(tile).not.toHaveCSS('box-shadow', 'none')
      await expect.poll(() => tile.evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe(restFill)
      await expect(tip).toHaveCount(0)
      await page.clock.runFor(50)
      await page.mouse.move(700, 400)
      await page.clock.runFor(200)
      await expect(tip).toHaveCount(0)
      await hover()
      await page.clock.runFor(99)
      await expect(tip).toHaveCount(0)
      await page.clock.runFor(1)
      await expect(tip).toHaveCount(1)
      await page.clock.runFor(200)
      await expect(tip).toHaveCSS('opacity', '1')
      await expect(tile).toHaveCSS('opacity', '1')
      assert.deepEqual(await tile.boundingBox(), rest, `${theme}: hover never resizes or moves the icon tile`)
      const t = await tip.boundingBox()
      assert(Math.abs(t.x - (rest.x + rest.width)) < 0.5, `${theme}: tip is flush with icon tile, ignoring rail padding`)
      assert(Math.abs(t.y + t.height / 2 - r.y - r.height / 2) < 0.5, `${theme}: row and tip centers align`)
      assert(t.x + t.width < 1440, `${theme}: label stays on screen`)
      await expect(tip.locator('.app-icon-nav, svg')).toHaveCount(0)
      assert(await row.evaluate(el => {
        const r = el.getBoundingClientRect()
        return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2))
      }), `${theme}: original icon stays clickable`)
      await page.screenshot({ path: join(out, `nav-hover-${theme}.png`), clip: { x: 0, y: 0, width: 620, height: 900 } })
      await page.mouse.move(700, 400)
      await page.clock.runFor(200)
      await expect(tip).toHaveCount(0)
      await expect(tile).toHaveCSS('opacity', '0.7')
      await expect(tile).toHaveCSS('box-shadow', 'none')
      await expect(tile).toHaveCSS('background-color', restFill)
      // Hover must not replace selected-state paint with the neutral fill.
      const active = nav.locator('.nav-item.nav-active').first()
      const activeTile = active.locator('.app-icon-nav')
      const selectedFill = await activeTile.evaluate(el => getComputedStyle(el).backgroundColor)
      const a = await active.boundingBox()
      await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
      await page.clock.runFor(200)
      await expect(activeTile).toHaveCSS('background-color', selectedFill)
      await expect(activeTile).not.toHaveCSS('box-shadow', 'none')
      const selectedTip = page.getByRole('tooltip')
      await expect(selectedTip).toHaveCSS('opacity', '1')
      const selectedBox = await activeTile.boundingBox()
      const selectedLabel = await selectedTip.boundingBox()
      assert(Math.abs(selectedLabel.x - selectedBox.x - selectedBox.width) < 0.5, `${theme}: selected label has no tile gap`)
      await page.screenshot({ path: join(out, `nav-hover-selected-${theme}.png`), clip: { x: 0, y: Math.max(0, a.y - 20), width: 360, height: a.height + 40 } })
      await page.mouse.move(700, 400)
      await page.clock.runFor(200)
      await row.focus()
      await expect(tip).toHaveCount(1)
      await page.keyboard.press('Escape')
      await page.clock.runFor(200)
      await expect(tip).toHaveCount(0)
      // Start a delayed hover, then expand before its deadline. Neither the
      // pending timer nor the old portal may survive the layout change.
      await page.getByTestId('navigation-grabber').focus()
      await hover()
      await page.clock.runFor(50)
      await page.keyboard.press('End')
      await page.clock.runFor(200)
      await expect(nav).toHaveAttribute('data-compact', 'false')
      await expect(tip).toHaveCount(0)
      assert.deepEqual(errors, [], `${theme}: no page errors`)
      console.log(`PASS ${theme}: 100ms intent, cancelled pass, flush placement, immediate tile hover, stable geometry, selected fill preserved, pointer access, focus, Escape, expansion cleanup`)
    } finally {
      await context.close()
    }
  }
} finally {
  if (browser) await browser.close()
  await new Promise(resolve => host.srv.close(resolve))
}
