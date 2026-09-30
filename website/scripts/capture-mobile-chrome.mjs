/** Real built dashboard + isolated API fixtures: one navigation chrome on every route.
 * Usage: npm run build && node scripts/capture-mobile-chrome.mjs <output-dir>
 * Phone (320x700, 390x844, 390x520, light+dark): the shared 72px captioned rail,
 * current-crew chooser + Add menu, rail scrolling with pinned crew/Search, scrim /
 * Escape / swipe / Back dismissal, history semantics, the chat drawer's real
 * Sessions pane, a mocked visualViewport keyboard, and page scrolling. Desktop
 * (1440x900): the inset workspace frame and grabber on every route. Clicks the
 * real nav between routes. Loopback fixture server only; no gateway, no network.
 * Native touch/keyboards and real remote crews are NOT exercised.
 */
import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright'
import { expect } from '@playwright/test'
import { serveDist, DEFAULT_DIST } from './lib/serve-dist.mjs'
import { stubDashboardApi, json } from './lib/stub-dashboard-api.mjs'

const OUT = process.argv[2]
assert(OUT, 'Pass an output directory (use KIROCREW_SCRATCH for local captures)')
mkdirSync(OUT, { recursive: true })
const results = []
const failures = []
const notes = []
// A failed measurement is recorded and the walk continues, so one run reports
// every defect; the process still exits non-zero if any check failed.
const check = (name, pass, detail) => {
  if (pass) { results.push(name); return }
  console.log(`FAIL: ${name}${detail === undefined ? '' : ' ' + JSON.stringify(detail)}`)
  failures.push({ name, detail })
}
const note = text => { notes.push(text); console.log(`NOTE: ${text}`) }

const now = 1790622000
const slots = ['Mobile chrome review', 'Review the release plan', 'Investigate a failing test', 'Plan the next iteration'].map((title, i) => ({
  key: `mobile-${i}`, title, running: false, last_message: 'Ready for review.',
  messages: 2, agent: 'kirocrew', memory_mode: 'persistent', folder_id: '',
  modified: now - i * 600, source_links: [], source_links_total: 0,
}))
const detail = key => ({
  running: false, has_more: false, total: 2, queue: [], project: '',
  messages: [
    { role: 'user', content: `Open ${slots.find(s => s.key === key)?.title ?? 'this session'}.`, ts: now - 100 },
    { role: 'assistant', content: 'The rail, drawer and Sessions list here are the real dashboard components.', ts: now - 50 },
  ],
})
const jobs = Array.from({ length: 16 }, (_, i) => ({
  id: `job-${i}`, name: `Scheduled check ${i + 1}`, schedule: i % 2 ? 'every 1d' : '0 9 * * 1',
  message: 'Summarise the overnight results and post the digest.', enabled: i % 5 !== 4,
  agent: 'kirocrew', last_status: i % 4 === 1 ? 'error' : 'ok', last_error: i % 4 === 1 ? 'HTTP 502 from upstream' : '',
  last_run_ts: now - 3600 * (i + 1), next_run_ts: now + 3600 * (i + 1), has_result: true, folder_id: '',
}))
const kinds = ['markdown', 'widget', 'html', 'svg', 'json', 'text']
const artifacts = Array.from({ length: 36 }, (_, i) => ({
  slug: `note-${i}`, name: `Design note ${i + 1}`, kind: kinds[i % kinds.length], source: 'chat',
  session_title: 'Docs session', description: 'Layout evidence fixture', tags: ['docs'], version: 1 + (i % 4),
  pinned: i === 0, created_at: '2026-08-20T10:00:00.000000+00:00',
  updated_at: `2026-09-${String(1 + (i % 28)).padStart(2, '0')}T10:00:00.000000+00:00`,
}))
// demo-app ships in website/dist/apps, so its row opens a real AppHost page.
// The padding apps only make the phone rail taller than short viewports.
const installed = (name, displayName, icon) => ({
  name, displayName, version: '1.0.0', enabled: true, installedAt: '2026-07-20T10:00:00Z',
  origin: 'registry', resources: 'gateway', lifecycle: 'gateway',
  manifest: { name, version: '1.0.0', displayName, description: `${displayName} fixture`, author: 'kirocrew', tags: [],
    ui: { entry: 'index.mjs', pages: [{ route: `/apps/${name}`, label: displayName, icon, group: 'Apps' }] } },
})
const apps = [installed('demo-app', 'Demo App', 'Sparkles'), installed('oncall-radar', 'Oncall', 'Bell'),
  installed('issue-radar', 'Issues', 'Radar'), installed('secretary', 'Secretary', 'Calendar'),
  installed('research-lab', 'Research Notebook', 'Search'), installed('ops-board', 'Ops Board', 'LayoutDashboard')]
const registry = Array.from({ length: 14 }, (_, i) => ({
  name: `catalog-app-${i}`, displayName: `Catalog app ${i + 1}`, description: 'An app from the fixture catalog.',
  author: 'kirocrew', version: '1.0.0', tags: ['fixture'], icon: 'Package', repo: `https://example.invalid/app-${i}`,
}))

const host = await serveDist()
let browser
const fixtures = async (path, route) => {
  const s = /^\/api\/chat\/slots\/(mobile-\d)$/.exec(path)
  if (s) { await json(route, detail(s[1])); return true }
  const table = {
    '/api/crons': { jobs }, '/api/cron-folders': [], '/api/crons/history': { runs: [] },
    '/api/artifacts': { artifacts }, '/api/artifact-folders': { folders: [] }, '/api/artifacts/session-docs': { docs: [] },
    '/api/apps': apps, '/api/apps/registry': { apps: registry, serverPlatform: { os: 'linux', arch: 'x64' } },
    '/api/apps/registries': { registries: [] }, '/api/models': { models: [], default: 'auto' },
  }
  if (path in table) { await json(route, table[path]); return true }
  const record = /^\/api\/apps\/([^/]+)$/.exec(path)
  const app = record && apps.find(a => a.name === record[1])
  if (app) { await json(route, app); return true }
  return false
}

async function open({ viewport, theme, touch = false, mockViewport = false }) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1, locale: 'en-US', hasTouch: touch, isMobile: false })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('console', m => { if (m.type() === 'error') console.log(`CONSOLE(${theme}/${viewport.width}): ${m.text().slice(0, 200)}`) })
  await page.route('**/*', route => {
    const url = new URL(route.request().url())
    if (url.origin !== host.base) return route.abort()
    // serve-dist has no .mjs MIME entry, and module scripts are strict: serve
    // the installed fixture app's real bundle from dist with a JS type.
    if (/^\/apps\/[^/]+\/ui\/[^/]+\.mjs$/.test(url.pathname)) {
      return route.fulfill({ contentType: 'text/javascript', body: readFileSync(join(DEFAULT_DIST, url.pathname)) })
    }
    return route.fallback()
  })
  await stubDashboardApi(page, { theme, slots, localStorageEntries: { 'mc-active-slot-chat': 'mobile-0' }, extra: fixtures })
  if (mockViewport) await page.addInitScript(() => {
    // A controllable visualViewport: a software keyboard shrinks it and iOS
    // moves its origin. The shell and chat drawers must follow both.
    let custom = null
    class FakeViewport extends EventTarget {
      get height() { return custom ? custom.height : innerHeight }
      get offsetTop() { return custom ? custom.top : 0 }
      get width() { return innerWidth }
      get offsetLeft() { return 0 }
      get pageTop() { return scrollY + this.offsetTop }
      get pageLeft() { return scrollX }
      get scale() { return 1 }
    }
    const fake = new FakeViewport()
    Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => fake })
    window.__setViewport = (height, top) => {
      custom = height == null ? null : { height, top }
      fake.dispatchEvent(new Event('resize')); fake.dispatchEvent(new Event('scroll'))
    }
  })
  const cdp = touch ? await context.newCDPSession(page) : null
  return { context, page, errors, cdp }
}

const railPanel = (page, chat) => chat
  ? page.locator('.mobile-sessions-overlay')
  : page.locator('div.fixed:has(> [data-testid="mobile-nav-rail"])')
async function settled(locator) {
  let last = null
  await expect.poll(async () => {
    const b = await locator.boundingBox()
    const key = b && `${Math.round(b.x)},${Math.round(b.y)},${Math.round(b.width)},${Math.round(b.height)}`
    const stable = key !== null && key === last && Math.round(b.x) === 0
    last = key
    return stable
  }, { intervals: [80] }).toBe(true)
}
async function closed(page, chat) {
  await expect(railPanel(page, chat)).toHaveCount(0)
  await expect(page.getByTestId(chat ? 'mobile-split-drawer' : 'nav-backdrop')).toHaveCount(0)
}
async function openDrawer(page, chat) {
  await page.getByTestId(chat ? 'mobile-topbar-sessions-toggle' : 'mobile-topbar-navigation-toggle').tap()
  await settled(railPanel(page, chat))
}
async function swipe(cdp, page, x0, x1, y) {
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y }] })
  for (let i = 1; i <= 12; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + (x1 - x0) * i / 12, y }] })
    await page.waitForTimeout(16)
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
}
const path = page => new URL(page.url()).pathname

/** Everything a phone rail must satisfy, measured from the live DOM. */
async function auditRail(page, label, chat, expectedActive) {
  const m = await page.evaluate(({ chat }) => {
    const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
    const rails = [...document.querySelectorAll('[data-testid="mobile-nav-rail"]')].filter(visible)
    const rail = rails[0]
    if (!rail) return { rails: 0 }
    const r = rail.getBoundingClientRect()
    const panel = chat ? document.querySelector('.mobile-sessions-overlay') : rail.parentElement
    const p = panel.getBoundingClientRect()
    const scroller = rail.querySelector('[data-testid="mobile-nav-rail-scroll"]')
    const s = scroller.getBoundingClientRect()
    const search = rail.querySelector('[data-testid="mobile-nav-rail-search"]').getBoundingClientRect()
    const chooser = rail.querySelector('.crew-navigation-switcher button')
    const c = chooser?.getBoundingClientRect()
    const tiles = [...rail.querySelectorAll('.nav-item')].map(el => {
      const t = el.getBoundingClientRect()
      const cap = el.querySelector(':scope > span[aria-hidden="true"]')
      const cr = cap?.getBoundingClientRect()
      return { name: el.getAttribute('aria-label'), w: t.width, h: t.height, active: el.classList.contains('nav-active'),
        caption: cap?.textContent ?? null,
        m: cap && { sw: cap.scrollWidth, cw: cap.clientWidth, sh: cap.scrollHeight, ch: cap.clientHeight, t: t.top, b: t.bottom, ct: cr.top, cb: cr.bottom, cl: cr.left, crr: cr.right, l: t.left, r: t.right },
        captionFits: !!cap && cap.scrollWidth <= cap.clientWidth + 0.5 && cap.scrollHeight <= cap.clientHeight + 0.5
          && cr.left >= t.left - 0.5 && cr.right <= t.right + 0.5 && cr.top >= t.top - 0.5 && cr.bottom <= t.bottom + 0.5 }
    })
    const tile = rail.querySelector('.nav-item.nav-active .app-icon-nav')
    const tr = tile?.getBoundingClientRect()
    const vv = window.visualViewport
    const bottomLimit = vv ? vv.offsetTop + vv.height : innerHeight
    const shell = document.querySelector('[data-testid="dashboard-shell"]')
    return {
      rails: rails.length, railWidth: r.width, railBg: getComputedStyle(rail).backgroundColor,
      shellBg: getComputedStyle(shell).backgroundColor,
      panel: { x: p.x, top: p.top, bottom: p.bottom, width: p.width }, bottomLimit, vvTop: vv ? vv.offsetTop : 0,
      scroll: { top: s.top, bottom: s.bottom, sh: scroller.scrollHeight, ch: scroller.clientHeight, sw: scroller.scrollWidth, cw: scroller.clientWidth },
      search: { top: search.top, bottom: search.bottom, w: search.width, h: search.height },
      chooser: c ? { top: c.top, bottom: c.bottom, name: chooser.getAttribute('aria-label') } : null,
      tiles, activeTile: tr ? { w: tr.width, h: tr.height, bg: getComputedStyle(tile).backgroundColor } : null,
      brand: document.querySelectorAll('[data-testid="mobile-nav-rail-home"]').length,
      choosers: [...document.querySelectorAll('button')].filter(b => visible(b) && /Switch crew/.test(b.getAttribute('aria-label') ?? '')).length,
      docOverflow: document.documentElement.scrollWidth - innerWidth,
    }
  }, { chat })
  const L = `${label}/${chat ? 'chat' : 'shell'}`
  check(`${L}: exactly one visible rail, 72px, chrome paint`, m.rails === 1 && m.railWidth === 72 && m.railBg === m.shellBg, m)
  check(`${L}: drawer flush to the safe edges`, Math.abs(m.panel.x) < 0.5 && Math.abs(m.panel.top - m.vvTop) < 1 && Math.abs(m.panel.bottom - m.bottomLimit) < 1 && (chat || m.panel.width === 72), m.panel)
  check(`${L}: current crew chooser replaces the brand, once`, m.brand === 0 && m.choosers === 1 && m.chooser && m.chooser.top >= m.panel.top && m.chooser.bottom <= m.scroll.top + 0.5, m.chooser)
  check(`${L}: Search pinned below the scroll region inside the drawer`, m.search.top >= m.scroll.bottom - 0.5 && m.search.bottom <= m.bottomLimit + 0.5 && m.search.w === 64 && m.search.h === 56, m.search)
  check(`${L}: rail never scrolls horizontally, page never overflows`, m.scroll.sw <= m.scroll.cw && m.docOverflow <= 0, m.scroll)
  const bad = m.tiles.filter(t => t.w !== 64 || t.h < 56 || !t.captionFits)
  check(`${L}: every tile is 64px wide, at least 56px high, with its full caption visible`, m.tiles.length >= 8 && bad.length === 0,
    { bad: bad.length, of: m.tiles.length, sample: bad.slice(0, 1).map(t => ({ name: t.name, w: t.w, h: t.h, ...t.m })),
      captions: bad.map(t => `${t.caption}: content ${t.m?.sh}px in ${t.m?.ch}px box, bottom ${t.m ? t.m.b - t.m.cb : '?'}px from tile edge`) })
  const active = m.tiles.filter(t => t.active).map(t => t.name)
  check(`${L}: selection is a 36px tile on ${expectedActive}`, active.length === 1 && active[0] === expectedActive && m.activeTile?.w === 36 && m.activeTile?.h === 36 && m.activeTile.bg !== 'rgba(0, 0, 0, 0)', { active, tile: m.activeTile })
  return m
}

/** Scroll the rail with the wheel and prove Settings (last) and the first tile are both reachable. */
async function railReach(page, label) {
  const scroller = page.locator('[data-testid="mobile-nav-rail-scroll"]:visible')
  const box = await scroller.boundingBox()
  const { sh, ch } = await scroller.evaluate(el => ({ sh: el.scrollHeight, ch: el.clientHeight }))
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.wheel(0, 3000)
  await expect.poll(() => scroller.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeLessThan(1)
  const hit = await page.evaluate(() => {
    const rail = [...document.querySelectorAll('[data-testid="mobile-nav-rail"]')].find(el => el.getClientRects().length)
    const settings = rail.querySelector('.nav-item[aria-label="Settings"]')
    const r = settings.getBoundingClientRect()
    const search = rail.querySelector('[data-testid="mobile-nav-rail-search"]').getBoundingClientRect()
    return { hits: settings.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)), aboveSearch: r.bottom <= search.top + 0.5 }
  })
  check(`${label}: Settings reachable by scrolling the rail${sh > ch ? ' (rail overflows)' : ''}`, hit.hits && hit.aboveSearch, hit)
  await page.mouse.wheel(0, -3000)
  await expect.poll(() => scroller.evaluate(el => el.scrollTop)).toBe(0)
  return sh > ch
}

/** Find the surface's main scroller, wheel it, and prove only it moved. */
async function probePageScroll(page, label, expectOverflow) {
  const pick = () => page.evaluate(() => {
    document.querySelectorAll('[data-probe-scroll]').forEach(el => el.removeAttribute('data-probe-scroll'))
    const surface = document.querySelector('.dashboard-surface')
    const all = [surface, ...surface.querySelectorAll('*')].filter(el => el.getClientRects().length
      && /(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 40 && el.clientHeight > 120)
    all.sort((a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth)
    const el = all[0]
    if (!el) return null
    el.setAttribute('data-probe-scroll', '1')
    const r = el.getBoundingClientRect()
    return { id: el.id || el.getAttribute('data-testid') || el.className.toString().slice(0, 80), x: r.x + r.width / 2,
      y: r.y + Math.min(r.height / 2, 160), max: el.scrollHeight - el.clientHeight, width: getComputedStyle(el).scrollbarWidth,
      workspace: el.classList.contains('workspace-scroll') }
  })
  // A route change can leave the previous page's scroller mounted for its exit
  // animation; wait until the same element wins two reads in a row.
  let info = await pick()
  for (let i = 0; i < 10; i++) {
    await page.waitForTimeout(300)
    const again = await pick()
    const same = JSON.stringify(again) === JSON.stringify(info)
    info = again
    if (same) break
  }
  if (!info) { check(`${label}: page content ${expectOverflow ? 'overflows and scrolls' : 'fits'}`, !expectOverflow); return null }
  const scroller = page.locator('[data-probe-scroll="1"]')
  const header = await page.locator('header.topbar-glass').boundingBox()
  await page.mouse.move(info.x, info.y)
  await page.mouse.wheel(0, 500)
  const moved = await expect.poll(() => scroller.evaluate(el => el.scrollTop)).toBeGreaterThanOrEqual(Math.min(100, info.max - 1)).then(() => true, () => false)
  if (!moved) {
    const hit = await page.evaluate(({ x, y }) => {
      const el = document.elementFromPoint(x, y), target = document.querySelector('[data-probe-scroll="1"]')
      return { inside: target.contains(el), hit: el?.className?.toString().slice(0, 80), top: target.scrollTop,
        sh: target.scrollHeight, ch: target.clientHeight, ox: getComputedStyle(target).overflowY }
    }, info)
    check(`${label}: overflowing ${info.id} scrolls under the wheel`, false, { info, hit })
    return info
  }
  const after = await page.evaluate(() => ({ doc: document.scrollingElement.scrollTop, overflow: document.documentElement.scrollWidth - innerWidth }))
  const header2 = await page.locator('header.topbar-glass').boundingBox()
  check(`${label}: content scrolls in ${info.id} with fixed chrome`, after.doc === 0 && after.overflow <= 0 && header2.y === header.y, { info, after })
  if (info.workspace) check(`${label}: workspace scroller uses the thin scrollbar`, info.width === 'thin', info)
  await page.mouse.wheel(0, -5000)
  await expect.poll(() => scroller.evaluate(el => el.scrollTop)).toBe(0)
  return info
}

/** The mobile header/workspace chrome, outside any drawer. */
async function auditHeader(page, label, chat) {
  const m = await page.evaluate(() => {
    const visible = el => el.getClientRects().length > 0
    const shell = document.querySelector('[data-testid="dashboard-shell"]')
    const header = shell.querySelector('header.topbar-glass')
    const surface = shell.querySelector('.dashboard-surface')
    const s = getComputedStyle(surface), sr = surface.getBoundingClientRect(), hr = header.getBoundingClientRect()
    const toggle = document.querySelector('[data-testid="mobile-topbar-navigation-toggle"]')
    return {
      mobileChrome: shell.classList.contains('mobile-chrome'), grabberShell: shell.classList.contains('grabber-shell'),
      headerPaint: getComputedStyle(header).backgroundColor === getComputedStyle(shell).backgroundColor,
      border: s.borderTopWidth, left: s.borderTopLeftRadius, right: s.borderTopRightRadius, sideBorder: s.borderRightWidth,
      x: sr.x, surfaceRight: sr.right, width: innerWidth, top: sr.top, headerBottom: hr.bottom,
      toggle: toggle && visible(toggle) ? toggle.getBoundingClientRect().toJSON() : null,
      sessions: !!document.querySelector('[data-testid="mobile-topbar-sessions-toggle"]'),
      headerImgs: [...header.querySelectorAll('img')].filter(visible).length,
      inline: [...document.querySelectorAll('.instance-tab-bar-inline')].filter(visible).length,
      choosers: [...document.querySelectorAll('button')].filter(b => visible(b) && /Switch crew/.test(b.getAttribute('aria-label') ?? '')).length,
      overflow: document.documentElement.scrollWidth - innerWidth,
    }
  })
  check(`${label}: mobile chrome paints header, 1px top border, 8px top corners, no gutter`, m.mobileChrome && !m.grabberShell && m.headerPaint && m.border === '1px'
    && m.left === '8px' && m.right === '8px' && m.sideBorder === '0px' && m.x === 0 && m.surfaceRight === m.width && Math.abs(m.top - m.headerBottom) < 0.5, m)
  check(`${label}: header has no brand, no inline or duplicate crew chooser`, m.headerImgs === 0 && m.inline === 0 && m.choosers === 0, m)
  check(`${label}: ${chat ? 'sessions toggle replaces the nav toggle' : '40px panel toggle opens navigation'}`, chat
    ? m.sessions && !m.toggle : !m.sessions && m.toggle?.width === 40 && m.toggle?.height === 40, m)
  check(`${label}: no document overflow`, m.overflow <= 0, m.overflow)
}

async function crewMenu(page, label) {
  const chooser = page.getByRole('button', { name: 'Local — Switch crew', exact: true })
  await chooser.tap()
  const add = page.getByRole('menuitem', { name: 'Add remote crew', exact: true })
  await expect(add).toBeVisible()
  const menu = page.getByRole('menu')
  await expect.poll(() => menu.evaluate(el => getComputedStyle(el).opacity)).toBe('1')
  const m = await menu.evaluate(el => {
    const r = el.getBoundingClientRect()
    const hit = (x, y) => el.contains(document.elementFromPoint(x, y))
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, vw: innerWidth, vh: innerHeight,
      hits: hit(r.x + r.width / 2, r.y + r.height / 2) && hit(r.x + 6, r.y + 6) && hit(r.right - 6, r.bottom - 6) }
  })
  check(`${label}: crew menu with Add remote crew paints above the drawer, on screen`, m.hits && m.left >= 0 && m.right <= m.vw && m.top >= 0 && m.bottom <= m.vh, m)
  await expect(page.getByRole('menuitemradio', { name: /Local/ })).toBeVisible()
  return { chooser, add, menu }
}

async function mobileScenario(theme, viewport) {
  const label = `${theme}/${viewport.width}x${viewport.height}`
  console.log(`SCENARIO ${label} ${new Date().toISOString()}`)
  const tag = `${viewport.width}x${viewport.height}-${theme}`
  const { context, page, errors, cdp } = await open({ viewport, theme, touch: true })
  const shot = name => page.screenshot({ path: join(OUT, `mobile-${name}-${tag}.png`) })
  const railTile = name => page.locator(`[data-testid="mobile-nav-rail"]:visible .nav-item[aria-label="${name}"]`)
  const tileById = id => page.locator(`[data-testid="mobile-nav-rail"]:visible [data-onboarding-nav="${id}"]`)
  try {
    await page.goto(host.base + '/chat', { waitUntil: 'domcontentloaded' })
    await expect(page.getByText('The rail, drawer and Sessions list here are the real dashboard components.')).toBeVisible()
    await auditHeader(page, `${label} /chat`, true)
    // Chat: one drawer = shared rail + the actual Sessions list.
    const len0 = await page.evaluate(() => history.length)
    await openDrawer(page, true)
    check(`${label}: chat drawer adds one Back entry`, await page.evaluate(() => history.length) === len0 + 1)
    const chatRail = await auditRail(page, label, true, 'Sessions')
    const pane = page.locator('.mobile-sessions-overlay .sidebar-inner')
    await expect(pane).toBeVisible()
    for (const s of slots) await expect(pane.getByText(s.title, { exact: true })).toBeVisible()
    const railBox = await page.locator('[data-testid="mobile-nav-rail"]:visible').boundingBox()
    const paneBox = await pane.boundingBox()
    check(`${label}: real Sessions pane sits beside the rail`, paneBox.x >= railBox.x + railBox.width - 1 && paneBox.width > 150, { railBox, paneBox })
    await shot('chat-drawer')
    await railReach(page, `${label}/chat`)
    // Back closes the chat drawer and stays on the chat.
    await page.evaluate(() => history.back())
    await closed(page, true)
    check(`${label}: Back closes the chat drawer without leaving`, path(page).startsWith('/chat'))
    await openDrawer(page, true)
    await page.touchscreen.tap(viewport.width - 12, viewport.height / 2)
    await closed(page, true)
    check(`${label}: chat scrim closes`, path(page).startsWith('/chat') && await page.evaluate(() => history.length) === len0 + 1)
    await openDrawer(page, true)
    // Commit is 20% of the drawer's own travel, so a long drag across the pane.
    await swipe(cdp, page, viewport.width - 60, 20, viewport.height / 2)
    await closed(page, true)
    check(`${label}: chat drawer swipes closed`, path(page).startsWith('/chat'))
    // Real session switch through the actual sidebar.
    await openDrawer(page, true)
    await pane.getByText(slots[1].title, { exact: true }).tap()
    await expect(page.getByText(`Open ${slots[1].title}.`, { exact: true })).toBeVisible()
    await closed(page, true)
    check(`${label}: actual sidebar row switches the transcript`, true)
    await openDrawer(page, true)
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
    if (await railPanel(page, true).count()) {
      note(`${label}: Escape does not close the phone chat Sessions drawer (ChatPage has no Escape handler for it; scrim, swipe and Back do)`)
      await page.touchscreen.tap(viewport.width - 12, viewport.height / 2)
      await closed(page, true)
    }
    // Chat -> Schedule replaces the drawer's duplicate entry: Back returns to chat.
    await openDrawer(page, true)
    const lenOpen = await page.evaluate(() => history.length)
    await tileById('schedule').tap()
    await expect.poll(() => path(page)).toBe('/schedule')
    await closed(page, false)
    check(`${label}: rail navigation from chat replaces the drawer entry`, await page.evaluate(() => history.length) === lenOpen)
    await page.evaluate(() => history.back())
    await expect.poll(() => path(page)).toMatch(/^\/chat/)
    await expect(page.getByTestId('mobile-topbar-sessions-toggle')).toBeVisible()
    await closed(page, true)
    await page.evaluate(() => history.forward())
    await expect.poll(() => path(page)).toBe('/schedule')
    check(`${label}: Back from Schedule lands on chat, Forward returns`, true)

    // Schedule: rail-only shell drawer, same rail.
    await expect(page.getByText('Scheduled check 1', { exact: true }).first()).toBeVisible()
    await auditHeader(page, `${label} /schedule`, false)
    await probePageScroll(page, `${label} /schedule`, true)
    await shot('schedule-page')
    const lenShell = await page.evaluate(() => history.length)
    await openDrawer(page, false)
    check(`${label}: shell drawer adds no history entry`, await page.evaluate(() => history.length) === lenShell)
    const schedRail = await auditRail(page, label, false, 'Schedule')
    check(`${label}: chat and Schedule show the same destinations`, JSON.stringify(schedRail.tiles.map(t => t.name)) === JSON.stringify(chatRail.tiles.map(t => t.name)),
      { chat: chatRail.tiles.map(t => t.name), schedule: schedRail.tiles.map(t => t.name) })
    const scrim = await page.getByTestId('nav-backdrop').evaluate(el => ({ opacity: getComputedStyle(el).opacity, r: el.getBoundingClientRect().toJSON() }))
    check(`${label}: scrim covers the page beside the rail`, Number(scrim.opacity) > 0.9 && scrim.r.width === viewport.width, scrim)
    await shot('schedule-drawer')
    await railReach(page, `${label}/schedule`)
    await page.keyboard.press('Escape')
    await closed(page, false)
    await openDrawer(page, false)
    await page.touchscreen.tap(viewport.width - 12, viewport.height / 2)
    await closed(page, false)
    await openDrawer(page, false)
    await swipe(cdp, page, 60, 2, viewport.height / 2)
    await closed(page, false)
    await swipe(cdp, page, 150, 290, 21)
    await settled(railPanel(page, false))
    await tileById('schedule').tap()
    await closed(page, false)
    check(`${label}: shell drawer closes by Escape, scrim, swipe, and its own active tile; opens by swipe`, path(page) === '/schedule')

    // Crew menu inside the shell drawer.
    await openDrawer(page, false)
    await crewMenu(page, `${label}/schedule`)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('menu')).toHaveCount(0)
    if (await railPanel(page, false).count() === 0) note(`${label}: one Escape closed both the crew menu and the shell drawer`)
    else await page.keyboard.press('Escape')
    await closed(page, false)

    // Walk every destination through the real rail; shell navigation pushes.
    const walk = [
      ['artifacts', '/artifacts', 'Artifacts', () => expect(page.getByText('Design note 1', { exact: true }).first()).toBeVisible(), true],
      ['apps', '/apps', null, () => expect(page.getByText('Catalog app 1', { exact: true }).first()).toBeVisible(), true],
      ['app-demo-app', '/apps/demo-app', 'Demo App', () => expect(page.locator('#main-content').getByText('Demo App', { exact: true }).first()).toBeVisible(), false],
      ['capabilities', '/capabilities', 'Agent Capabilities', () => expect(page.locator('#main-content')).toBeVisible(), false],
    ]
    let previous = '/schedule'
    for (const [id, route, activeName, ready, overflow] of walk) {
      await openDrawer(page, false)
      const len = await page.evaluate(() => history.length)
      await tileById(id).tap()
      await expect.poll(() => path(page)).toBe(route)
      await closed(page, false)
      await ready()
      check(`${label}: ${route} navigation pushes one entry`, await page.evaluate(() => history.length) === len + 1)
      await auditHeader(page, `${label} ${route}`, false)
      await probePageScroll(page, `${label} ${route}`, overflow)
      await openDrawer(page, false)
      await auditRail(page, `${label} ${route}`, false, activeName ?? await tileById(id).getAttribute('aria-label'))
      await page.keyboard.press('Escape')
      await closed(page, false)
      if (route === '/artifacts') {
        await shot('artifacts-page')
        await page.evaluate(() => history.back())
        await expect.poll(() => path(page)).toBe(previous)
        await page.evaluate(() => history.forward())
        await expect.poll(() => path(page)).toBe(route)
        check(`${label}: Back/Forward walks shell routes`, true)
      }
      if (route === '/apps/demo-app') await shot('installed-app')
      previous = route
    }
    // Settings -> About through the rail and the Settings list.
    await openDrawer(page, false)
    await railTile('Settings').tap()
    await expect.poll(() => path(page)).toMatch(/^\/settings/)
    await closed(page, false)
    await auditHeader(page, `${label} /settings`, false)
    await shot('settings')
    await page.getByRole('button', { name: /^About/ }).or(page.getByRole('link', { name: /^About/ })).first().tap()
    await expect.poll(() => path(page)).toBe('/settings/about')
    await auditHeader(page, `${label} /settings/about`, false)
    await probePageScroll(page, `${label} /settings/about`, false)
    await openDrawer(page, false)
    await auditRail(page, `${label} /settings/about`, false, 'Settings')
    await page.keyboard.press('Escape')
    await closed(page, false)
    await shot('settings-about')
    // Add remote crew from a phone route opens the real setup form.
    await openDrawer(page, false)
    const { add } = await crewMenu(page, `${label}/about`)
    await add.tap()
    await expect(page.locator('[data-setting-key="remote-crew-add"]')).toBeVisible()
    await closed(page, false)
    check(`${label}: Add remote crew opens the setup form and closes the drawer`, path(page) === '/settings/instances')
    // And back to chat, whose drawer still holds the real Sessions list.
    await openDrawer(page, false)
    await tileById('chat').tap()
    await expect.poll(() => path(page)).toMatch(/^\/chat/)
    await openDrawer(page, true)
    await expect(page.locator('.mobile-sessions-overlay .sidebar-inner').getByText(slots[0].title, { exact: true })).toBeVisible()
    check(`${label}: returning to chat keeps the real Sessions pane`, true)
    check(`${label}: no page errors`, errors.length === 0, errors)
  } finally {
    await context.close()
  }
}

/** A short viewport and a mocked keyboard: the rail must stay reachable and above the keyboard. */
async function keyboardScenario(theme) {
  const label = `${theme}/keyboard`
  console.log(`SCENARIO ${label} ${new Date().toISOString()}`)
  const { context, page, errors } = await open({ viewport: { width: 390, height: 844 }, theme, touch: true, mockViewport: true })
  try {
    for (const [route, chat] of [['/schedule', false], ['/chat', true]]) {
      await page.goto(host.base + route, { waitUntil: 'domcontentloaded' })
      await expect(page.getByTestId(chat ? 'mobile-topbar-sessions-toggle' : 'mobile-topbar-navigation-toggle')).toBeVisible()
      await openDrawer(page, chat)
      for (const [height, top] of [[544, 0], [500, 40]]) {
        await page.evaluate(([h, t]) => window.__setViewport(h, t), [height, top])
        await expect.poll(async () => Math.round((await railPanel(page, chat).boundingBox()).height)).toBe(height)
        const m = await auditRail(page, `${label} ${route} vv=${height}@${top}`, chat, chat ? 'Sessions' : 'Schedule')
        if (!chat) {
          const s = await page.getByTestId('nav-backdrop').boundingBox()
          check(`${label} ${route} vv=${height}@${top}: scrim follows the visual viewport`, Math.abs(s.y - top) < 1 && Math.abs(s.y + s.height - top - height) < 1, s)
        }
        check(`${label} ${route} vv=${height}@${top}: rail scrolls inside the shortened drawer`, m.scroll.sh > m.scroll.ch, m.scroll)
        await railReach(page, `${label} ${route} vv=${height}@${top}`)
        await page.screenshot({ path: join(OUT, `mobile-keyboard-${chat ? 'chat' : 'schedule'}-${height}-${top}-${theme}.png`) })
      }
      await page.evaluate(() => window.__setViewport(null))
      await expect.poll(async () => Math.round((await railPanel(page, chat).boundingBox()).height)).toBe(844)
      check(`${label} ${route}: drawer returns to full height when the keyboard closes`, true)
    }
    check(`${label}: no page errors`, errors.length === 0, errors)
  } finally {
    await context.close()
  }
}

async function desktopScenario(theme) {
  const label = `${theme}/1440`
  console.log(`SCENARIO ${label} ${new Date().toISOString()}`)
  const { context, page, errors } = await open({ viewport: { width: 1440, height: 900 }, theme })
  const nav = page.locator('#dashboard-navigation')
  const grip = page.getByTestId('navigation-grabber')
  try {
    await page.goto(host.base + '/chat', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.sidebar-inner').getByText(slots[0].title, { exact: true })).toBeVisible()
    const routes = [
      ['chat', /^\/chat/, null], ['schedule', /^\/schedule$/, true], ['artifacts', /^\/artifacts$/, true],
      ['apps', /^\/apps$/, true], ['app-demo-app', /^\/apps\/demo-app$/, false], ['capabilities', /^\/capabilities/, false],
      ['settings', /^\/settings/, false],
    ]
    for (const [id, route, overflow] of routes) {
      const row = id === 'settings' ? nav.locator('.nav-item').filter({ hasText: /^Settings/ }) : nav.locator(`[data-onboarding-nav="${id}"]`)
      await row.first().click()
      await expect.poll(() => path(page)).toMatch(route)
      if (id === 'settings') {
        await page.getByRole('button', { name: /^About/ }).or(page.getByRole('link', { name: /^About/ })).first().click()
        await expect.poll(() => path(page)).toBe('/settings/about')
      }
      const at = path(page)
      await expect.poll(async () => Math.round((await nav.boundingBox()).width)).toBe(236)
      const g = await page.evaluate(() => {
        const shell = document.querySelector('[data-testid="dashboard-shell"]')
        const surface = document.querySelector('.dashboard-surface'), s = getComputedStyle(surface)
        const n = document.querySelector('#dashboard-navigation').getBoundingClientRect()
        const r = surface.getBoundingClientRect()
        const grip = document.querySelector('[data-testid="navigation-grabber"]').getBoundingClientRect()
        const g = document.querySelector('[data-testid="navigation-grabber"]')
        const visible = el => el.getClientRects().length > 0
        return { shell: shell.classList.contains('grabber-shell'), mobile: shell.classList.contains('mobile-chrome'),
          tl: s.borderTopLeftRadius, tr: s.borderTopRightRadius, top: s.borderTopWidth, rb: s.borderRightWidth,
          gutter: document.querySelector('#activity-bar-slot').getBoundingClientRect().x - r.right,
          edge: n.right - r.x, bottom: shell.getBoundingClientRect().bottom - r.bottom,
          grip: [grip.left + 1, grip.right - 1].every(x => g.contains(document.elementFromPoint(x, grip.y + grip.height / 2))),
          inline: [...document.querySelectorAll('.instance-tab-bar-inline')].filter(visible).length,
          switchers: [...document.querySelectorAll('[data-testid="navigation-crew-switcher"]')].filter(visible).length,
          overflow: document.documentElement.scrollWidth - innerWidth }
      })
      check(`${label} ${at}: 8px top corners, 1px top+right frame, 6px gutter, flush bottom`, g.shell && !g.mobile && g.tl === '8px' && g.tr === '8px' && g.top === '1px' && g.rb === '1px'
        && Math.abs(g.gutter - 6) < 0.5 && Math.abs(g.edge) < 0.5 && Math.abs(g.bottom) < 0.5, g)
      check(`${label} ${at}: grabber hit strip usable, one crew chooser, no overflow`, g.grip && g.inline === 0 && g.switchers === 1 && g.overflow <= 0, g)
      await grip.focus()
      await grip.press('Home'); await expect.poll(async () => Math.round((await nav.boundingBox()).width)).toBe(74)
      await grip.press('Enter'); await expect.poll(async () => Math.round((await nav.boundingBox()).width)).toBe(236)
      check(`${label} ${at}: grabber collapses and restores the rail here`, true)
      if (overflow !== null) await probePageScroll(page, `${label} ${at}`, overflow)
      await page.mouse.move(900, 450)
      await page.screenshot({ path: join(OUT, `desktop-${at.replace(/^\//, '').replace(/\//g, '-') || 'root'}-${theme}.png`) })
    }
    // Pointer drag on a non-chat route, not only the keyboard.
    await nav.locator('[data-onboarding-nav="schedule"]').click()
    const b = await grip.boundingBox()
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2)
    await page.mouse.down()
    await page.mouse.move(b.x + b.width / 2 - 150, b.y + b.height / 2, { steps: 12 })
    await page.mouse.up()
    await expect.poll(async () => Math.round((await nav.boundingBox()).width)).toBe(74)
    await grip.press('Enter')
    await expect.poll(async () => Math.round((await nav.boundingBox()).width)).toBe(236)
    check(`${label}: pointer drag collapses the rail on Schedule`, true)
    check(`${label}: no page errors`, errors.length === 0, errors)
  } finally {
    await context.close()
  }
}

try {
  browser = await chromium.launch()
  for (const theme of ['dark', 'light']) {
    await desktopScenario(theme)
    for (const viewport of [{ width: 390, height: 844 }, { width: 320, height: 700 }, { width: 390, height: 520 }]) await mobileScenario(theme, viewport)
    await keyboardScenario(theme)
  }
} catch (error) {
  check('browser verification completed', false, error instanceof Error ? error.message : String(error))
  throw error
} finally {
  writeFileSync(join(OUT, 'checks.json'), JSON.stringify({ passed: results, failures, notes }, null, 2) + '\n')
  console.log(`${failures.length ? 'FAILED' : 'PASS'}: ${results.length} passed, ${failures.length} failed, ${notes.length} notes; evidence in ${OUT}`)
  if (failures.length) process.exitCode = 1
  await browser?.close()
  host.srv.close()
}
