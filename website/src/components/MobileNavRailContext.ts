import { createContext, useContext, type ReactNode } from 'react'

/**
 * One main-navigation rail for every phone route, built by App from the real
 * registry, badges and active-state rules. Chat places it beside its Sessions
 * pane; other pages show it in the shell drawer. Desktop reads null.
 *
 * The host supplies its close callback and history behavior: chat replaces
 * the duplicate entry its drawer added; the shell drawer pushes ordinary
 * navigation because it added no history entry.
 */
export type MobileNavRailOptions = {
  /** Close the hosting drawer without navigating. */
  onActivate: () => void
  /** Whether navigation replaces a duplicate entry owned by the host drawer. */
  replace?: boolean
}

export type MobileNavRailRenderer = (opts: MobileNavRailOptions) => ReactNode

export const MobileNavRailContext = createContext<MobileNavRailRenderer | null>(null)

export function useMobileNavRail(): MobileNavRailRenderer | null {
  return useContext(MobileNavRailContext)
}
