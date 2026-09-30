import { Fragment, useCallback, useMemo, type ReactNode } from 'react'
import { Plus, X } from 'lucide-react'
import type { usePanelTabs, ViewKind, PanelTab, TabKind } from '../../hooks/usePanelTabs'
import { PINNED_VIEWS } from '../../hooks/usePanelTabs'
import { usePanelTabDescriptors, panelTabDescriptor, isPanelTabKind, type PanelTabDescriptor } from '../../hooks/panelTabRegistry'
import { appIcon } from '../../apps/appIcons'
import {
  newMenuSections, NEW_MENU_LABEL_KEY, kindIcon,
  type SidePanelWithholdable, type SidePanelLeadingTab,
} from '../chat/SidePanel'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
} from '../../components/ui/dropdown-menu'
import { useDevMode } from '../../hooks/useDevMode'
import { useTerminalEnabled } from '../../utils/terminalRegistry'
import { i18nT } from '../../i18n/t'

/**
 * The Crew Members page's PAGE-LEVEL tab bar.
 *
 * Deliberately a DIFFERENT visual language from the chat page's `side-panel-strip`
 * (elevated band, bottom-fused browser-chip tabs). This bar is a flat, full-width
 * row of text labels with a coloured underline under the active tab — the
 * intentional "this is not Sessions" treatment. It spans the whole content area
 * rather than sitting inside a docked side column, so switching tabs swaps the
 * WHOLE main region (Option 1: each tab fills the content area, Chat included).
 *
 * It drives the same `usePanelTabs` controller the page's SidePanel body reads,
 * so the bar and the body never disagree about which tab is active. The three
 * crewmate leading tabs (Notes / Work log / Dashboard) plus a synthetic Chat
 * leading tab are the fixed head of the bar; dynamic panel tabs (opened
 * documents, terminals, apps) follow; the `+` opens the same view catalog the
 * SidePanel's own `+` menu offers, gated identically by `hiddenViews`.
 */

export interface MembersPageTabBarProps {
  tabsCtl: ReturnType<typeof usePanelTabs>
  /** The active tab id AS THE BAR SHOULD SHOW IT — the SidePanel resolves a
   *  fallback when the stored focus is on a withheld/absent tab, so the host
   *  passes the SidePanel's reported `onActiveTabChange` value here rather than
   *  `tabsCtl.activeId`, keeping bar and body in lockstep. */
  activeId: string | null
  /** The fixed head tabs (Chat / Notes / Work log / Dashboard), in bar order.
   *  These are never closable and never draggable — they are the bar's identity. */
  leadingTabs: readonly SidePanelLeadingTab[]
  /** Views this page withholds — same set handed to SidePanel.hiddenViews — so
   *  the `+` menu never offers a view the page cannot feed. */
  hiddenViews?: ReadonlySet<SidePanelWithholdable>
  /** The chat slot's project dir, for a Terminal opened from the `+`. */
  projectDir?: string
  /** GUARDED close for a dynamic (document) tab — routes through the SidePanel's
   *  dirty confirmation so an unsaved file buffer is not silently discarded.
   *  Falls back to the raw controller close only if absent. */
  onCloseTab?: (id: string) => void
}

/** One flat text tab. Active = accent text with an underline indicator; inactive
 *  = muted text with a hover wash. A closable (dynamic) tab shows a close button
 *  on hover / when active. A leading tab may carry a trailing `badge` (the
 *  Schedules tab's live/total count). */
function BarTab({ label, icon, badge, active, closable, onSelect, onClose, testId }: {
  label: string
  icon?: ReactNode
  badge?: ReactNode
  active: boolean
  closable: boolean
  onSelect: () => void
  onClose?: () => void
  testId?: string
}) {
  return (
    <div
      role="tab"
      aria-selected={active}
      aria-label={label}
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelect() } }}
      onAuxClick={(e) => { if (closable && e.button === 1 && onClose) { e.preventDefault(); onClose() } }}
      title={label}
      data-testid={testId}
      // Flat text tab: no box, no elevated band. The underline is a bottom
      // border on the active tab; inactive tabs reserve the same 2px so the row
      // does not shift when the active one moves. -mb-px drops the tab's border
      // onto the bar's own hairline so the active underline sits on that line.
      className={`group relative flex items-center gap-1.5 h-10 px-1 -mb-px border-b-2 cursor-pointer select-none shrink-0 transition-colors ${
        active
          ? 'border-accent text-accent'
          : 'border-transparent text-muted hover:text-text'
      }`}
    >
      {icon && <span className="shrink-0 flex items-center [&_svg]:w-4 [&_svg]:h-4">{icon}</span>}
      <span className="text-[13px] font-medium whitespace-nowrap max-w-[220px] truncate">{label}</span>
      {badge != null && <span className="shrink-0 flex items-center">{badge}</span>}
      {closable && onClose && (
        <button
          onClick={(e) => { e.stopPropagation(); onClose() }}
          className={`shrink-0 -mr-0.5 flex items-center justify-center w-[18px] h-[18px] rounded-full transition-all bg-transparent border-none cursor-pointer text-muted hover:text-text hover:bg-bg-hover ${active ? 'opacity-70' : 'opacity-0 group-hover:opacity-70'}`}
          title={i18nT('pages.chat.sidePanel.close_tab')}
          aria-label={i18nT('pages.chat.sidePanel.close_tab')}
        >
          <X size={12} />
        </button>
      )}
    </div>
  )
}

export default function MembersPageTabBar({
  tabsCtl, activeId, leadingTabs, hiddenViews, projectDir, onCloseTab,
}: MembersPageTabBarProps) {
  const { tabs, openView, openPanelTab, openTerminal, setActive, closeTab } = tabsCtl
  const closeDynamicTab = useCallback((id: string) => {
    // Prefer the SidePanel's guarded close (confirms an unsaved buffer); the raw
    // controller close is only the fallback when no host guard is wired.
    if (onCloseTab) onCloseTab(id)
    else closeTab(id)
  }, [onCloseTab, closeTab])
  // Leaving the currently-active leading tab runs ITS `onBeforeLeave` guard first
  // (the Schedules tab is the one that carries one — a dirty create form). The
  // SidePanel's own strip is hidden on this page (`hideStrip`), so the bar is the
  // only surface that switches tabs, which means the guard has to live here or a
  // bar click would silently discard the draft. Every leaving action funnels
  // through `guardLeave`: leading-tab clicks, pinned-view chips, dynamic-tab
  // clicks and the + menu.
  const activeLeaving = useCallback((): boolean | Promise<boolean> => {
    const current = leadingTabs.find(lt => lt.id === activeId)
    return current?.onBeforeLeave ? current.onBeforeLeave() : true
  }, [leadingTabs, activeId])
  const guardLeave = useCallback((run: () => void) => {
    // Synchronous when the tab being left carries no guard — the common case, and
    // the timing the page tab bar had before the Schedules draft guard existed.
    // Only the Schedules tab returns a promise (its dirty-form confirm), and only
    // then is the switch deferred to its resolution.
    const verdict = activeLeaving()
    if (verdict === true) { run(); return }
    if (verdict === false) return
    void verdict.then((ok) => { if (ok) run() })
  }, [activeLeaving])
  const selectLeading = useCallback((id: string) => {
    if (id === activeId) return
    guardLeave(() => setActive(id))
  }, [activeId, guardLeave, setActive])
  const devMode = useDevMode()
  const terminalEnabled = useTerminalEnabled()
  const allPanelTabDescriptors = usePanelTabDescriptors()
  const panelTabDescriptors = useMemo(
    () => (hiddenViews?.has('app') ? [] : allPanelTabDescriptors),
    [hiddenViews, allPanelTabDescriptors],
  )

  // The `+` menu's view catalog — the SAME grouping/gating the SidePanel's own
  // menu uses, so the two entry points can never drift on what is offered.
  const menuSections = useMemo(
    // Summary is withheld here via `hiddenViews`, so this flag never gates it;
    // pass true so the catalog is otherwise unchanged.
    () => newMenuSections({ devMode, terminalEnabled, summaryEnabled: true, hiddenViews }),
    [devMode, terminalEnabled, hiddenViews],
  )

  // Withheld views are dropped from the dynamic row too (a view the page cannot
  // feed must not appear as a chip after a chat-page open stored it in the
  // bucket). Mirrors SidePanel.isWithheld, scoped to what the bar shows.
  const isWithheld = useCallback((kind: TabKind): boolean => {
    if (!hiddenViews) return false
    if (kind === 'terminal') return hiddenViews.has('terminal')
    if (kind === 'app' || isPanelTabKind(kind)) return hiddenViews.has('app')
    if (kind === 'file' || kind === 'diff' || kind === 'folder') return hiddenViews.has('files')
    if (kind === 'artifact') return hiddenViews.has('artifacts')
    return hiddenViews.has(kind as ViewKind)
  }, [hiddenViews])

  // Pinned views (Changes / Files / Artifacts) were always-present chips on the
  // old strip. On this page they follow the head tabs, minus any the host
  // withholds (Changes is unfed here; Files/Artifacts are slot-bound and
  // withheld while the thread is unconfirmed). Selecting one opens that view.
  const pinnedViews = useMemo(
    () => (PINNED_VIEWS as ViewKind[]).filter((k) => !isWithheld(k)),
    [isWithheld],
  )

  // Dynamic tabs = opened documents / terminals / apps (not a pinned view, not
  // withheld). These follow the pinned chips.
  const dynamicTabs = useMemo(
    () => tabs.filter((t: PanelTab) => !(PINNED_VIEWS as string[]).includes(t.id) && !isWithheld(t.kind)),
    [tabs, isWithheld],
  )

  const openProjectTerminal = useCallback(() => { openTerminal({ cwd: projectDir }) }, [openTerminal, projectDir])
  const openMenuItem = useCallback((kind: ViewKind | 'terminal') => {
    guardLeave(() => { if (kind === 'terminal') openProjectTerminal(); else openView(kind) })
  }, [openProjectTerminal, openView, guardLeave])

  const tabIcon = useCallback((kind: TabKind): ReactNode => {
    if (isPanelTabKind(kind)) return appIcon(panelTabDescriptor(kind, panelTabDescriptors)?.icon)
    return kindIcon(kind)
  }, [panelTabDescriptors])

  return (
    <div
      role="tablist"
      data-testid="members-tab-bar"
      // Flat full-width bar: a single hairline along the bottom that the active
      // tab's underline lands on. No elevated background — it shares the page
      // surface, the opposite of the chat page's `bg-bg-elevated` chip strip.
      className="flex items-end gap-4 shrink-0 min-h-10 px-4 border-b border-border overflow-x-auto scrollbar-none"
    >
      {leadingTabs.map(lt => (
        <BarTab
          key={lt.id}
          label={lt.title}
          icon={lt.icon}
          badge={lt.badge}
          active={lt.id === activeId}
          closable={false}
          onSelect={() => selectLeading(lt.id)}
          testId={`members-tab-${lt.id}`}
        />
      ))}
      {pinnedViews.map(kind => (
        <BarTab
          key={kind}
          label={i18nT(NEW_MENU_LABEL_KEY[kind])}
          icon={kindIcon(kind)}
          active={kind === activeId}
          closable={false}
          onSelect={() => guardLeave(() => openView(kind))}
          testId={`members-tab-${kind}`}
        />
      ))}
      {dynamicTabs.map(t => (
        <BarTab
          key={t.id}
          label={t.title}
          icon={tabIcon(t.kind)}
          active={t.id === activeId}
          closable
          onSelect={() => guardLeave(() => setActive(t.id))}
          onClose={() => closeDynamicTab(t.id)}
          testId={`members-tab-${t.id}`}
        />
      ))}
      {/* The + opens the same view catalog the SidePanel's own menu offers. */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="flex items-center justify-center w-7 h-7 shrink-0 self-center rounded-md text-muted hover:text-text hover:bg-bg-hover data-[state=open]:bg-bg-hover data-[state=open]:text-text transition-colors bg-transparent border-none cursor-pointer"
            title={i18nT('pages.chat.sidePanel.open_side_panel_tab')}
            aria-label={i18nT('pages.chat.sidePanel.open_side_panel_tab')}
            data-testid="members-tab-add"
          >
            <Plus size={15} />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={6} className="min-w-[200px]">
          {menuSections.map((section, i) => (
            <Fragment key={section.id}>
              {i > 0 && <DropdownMenuSeparator />}
              {section.items.map(item => (
                <DropdownMenuItem
                  key={item.kind}
                  onSelect={() => openMenuItem(item.kind)}
                >
                  <span className="text-muted shrink-0">{item.icon}</span>
                  <span className="flex-1">{i18nT(NEW_MENU_LABEL_KEY[item.kind])}</span>
                </DropdownMenuItem>
              ))}
            </Fragment>
          ))}
          {panelTabDescriptors.length > 0 && (
            <Fragment key="app-panel-tabs">
              <DropdownMenuSeparator />
              {panelTabDescriptors.map((d: PanelTabDescriptor) => (
                <DropdownMenuItem
                  key={d.kind}
                  onSelect={() => guardLeave(() => openPanelTab(d))}
                >
                  <span className="text-muted shrink-0">{appIcon(d.icon)}</span>
                  <span className="flex-1">{d.menuLabel}</span>
                </DropdownMenuItem>
              ))}
            </Fragment>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
