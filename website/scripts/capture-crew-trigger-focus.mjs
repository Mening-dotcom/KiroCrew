/** Real built SPA + isolated API fixtures. No live gateway or connections.
 * Usage: node scripts/capture-crew-trigger-focus.mjs <output-dir>
 * Checks pointer, touch and keyboard crew-menu return without an underline.
 */
import assert from 'node:assert/strict'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { expect } from '@playwright/test'
import { serveDist } from './lib/serve-dist.mjs'
import { stubDashboardApi, json } from './lib/stub-dashboard-api.mjs'

const out = process.argv[2]
assert(out, 'Pass a capture directory')
mkdirSync(out, { recursive: true })
const host = await serveDist()
let browser
let checks = 0
try {
  browser = await chromium.launch()
  for (const theme of ['light', 'dark']) {
    for (const mode of ['collapsed', 'expanded', 'mobile']) {
      const mobile = mode === 'mobile'
      const context = await browser.newContext({
        viewport: { width: mobile ? 390 : 1440, height: 900 },
        deviceScaleFactor: 1, locale: 'en-US', hasTouch: mobile,
      })
      try {
        const page = await context.newPage()
        const errors = []
        page.on('pageerror', error => errors.push(error.message))
        await page.route('**/*', route => new URL(route.request().url()).origin === host.base ? route.fallback() : route.abort())
        await stubDashboardApi(page, {
          theme,
          slots: [{ key: 'focus-demo', title: 'Navigation layout', running: false, messages: 2, agent: 'kirocrew', memory_mode: 'persistent', folder_id: '', modified: 1790622000 }],
          localStorageEntries: { 'mc-nav': mode === 'expanded' ? '0' : '1', 'mc-active-slot-chat': 'focus-demo' },
          extra: async (path, route) => {
            const fixtures = {
              '/api/instances': { active: true, instances: [], warm_set_cap: 3 },
              '/api/models': { models: [], default: 'auto' },
              '/api/chat/slots/focus-demo': {
                running: false, has_more: false, total: 2, queue: [], project: '',
                messages: [
                  { role: 'user', content: 'Keep the crew icon uncluttered.', ts: 1790621900 },
                  { role: 'assistant', content: 'Open the crew menu with the current crew icon.', ts: 1790621950 },
                ],
              },
            }
            if (!(path in fixtures)) return false
            await json(route, fixtures[path])
            return true
          },
        })
        await page.goto(host.base + '/chat', { waitUntil: 'domcontentloaded' })
        if (mobile) await page.getByTestId('mobile-topbar-sessions-toggle').tap()
        const trigger = page.getByTestId('navigation-crew-switcher').filter({ visible: true })
        await expect(trigger).toBeVisible()
        // Radix restores focus after a menu selection; test that actual path,
        // not just an unfocused icon whose styles could never show the bug.
        if (mobile) await trigger.tap()
        else await trigger.click()
        const local = page.getByRole('menuitemradio', { name: /Local/ })
        await expect(local).toBeVisible()
        if (mobile) await local.tap()
        else await local.click()
        await expect(page.getByRole('menu')).toHaveCount(0)
        await expect(trigger).toBeFocused()
        await page.mouse.move(900, 700)
        await expect.poll(() => trigger.evaluate(el => getComputedStyle(el).boxShadow)).toBe('none')
        await expect.poll(() => trigger.evaluate(el => getComputedStyle(el).outlineStyle)).toBe('none')
        await expect.poll(() => trigger.evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)')
        checks += 3
        if (!mobile) {
          await trigger.hover()
          await expect.poll(() => trigger.evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)')
          await page.mouse.move(900, 700)
          await expect.poll(() => trigger.evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)')
          checks += 2
        }
        await page.screenshot({ path: join(out, `${theme}-${mode}-click.png`) })
        // A real Tab round trip keeps the keyboard cue separate from pointer
        // restoration and proves focus was not blurred to hide the shadow.
        await page.keyboard.press('Tab')
        await page.keyboard.press('Shift+Tab')
        await expect(trigger).toBeFocused()
        await page.mouse.move(900, 700)
        await expect.poll(() => trigger.evaluate(el => el.matches(':focus-visible'))).toBe(true)
        await expect.poll(() => trigger.evaluate(el => getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)')
        await expect.poll(() => trigger.evaluate(el => getComputedStyle(el).boxShadow)).toBe('none')
        checks += 3
        await trigger.press('Enter')
        await expect(page.getByRole('menuitem', { name: 'Add remote crew', exact: true })).toBeVisible()
        await page.keyboard.press('Escape')
        await expect(trigger).toBeFocused()
        await expect.poll(() => trigger.evaluate(el => getComputedStyle(el).boxShadow)).toBe('none')
        checks += 2
        assert.deepEqual(errors, [])
        checks++
        console.log(`PASS ${theme}/${mode}: click/touch, keyboard return, hover and no page errors`)
      } finally {
        await context.close()
      }
    }
  }
  console.log(`PASS ${checks} focused browser checks`)
} finally {
  await browser?.close()
  await new Promise(resolve => host.srv.close(resolve))
}
