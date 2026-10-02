/** Crew Mode: a UI over the existing conductor agent.
 *
 *  A chat is in Crew Mode exactly when it runs on CREW_MODE_AGENT, so there is
 *  no separate field anywhere: the composer switch is an ordinary agent switch,
 *  and a new chat starts in Crew Mode by being created with that agent (the
 *  agent the backend installs from `agent_files.CONDUCTOR_AGENT_FILENAME`).
 */
import { queryClient } from '../api/queryClient'

export const CREW_MODE_AGENT = 'kirocrew-conductor'

/** Whether `name` is the default agent: the switch is offered only there. The
 *  literal `kirocrew` counts only while no default agent is configured; with
 *  one configured, a chat on the `kirocrew` template is that template. */
export function isDefaultAgentName(name: string, defaultAgent: string | undefined): boolean {
  if (name === '') return true
  return defaultAgent ? name === defaultAgent : name === 'kirocrew'
}

/** Crew Mode needs a Persistent chat: an Incognito or Temporary session may not
 *  open sessions, so a conductor there could not conduct. */
export function memoryModeAllowsCrewMode(memoryMode: string | undefined): boolean {
  return (memoryMode ?? 'persistent') === 'persistent'
}

/** Settings -> Chat -> "Turn on Crew Mode for new sessions by default", read
 *  from the shared `['dashboardConfig']` cache (ChatPage and Settings keep it
 *  warm). Synchronous on purpose: an await before `createSlot` would let a slow
 *  read hand the new chat a view the user has already left. A cold cache reads
 *  as off, which is the setting's own default. */
export function crewModeDefaultOn(): boolean {
  const cfg = queryClient.getQueryData<{ default_crew_mode?: unknown; default_memory_mode?: unknown }>(['dashboardConfig'])
  return cfg?.default_crew_mode === true
    && memoryModeAllowsCrewMode(typeof cfg.default_memory_mode === 'string' ? cfg.default_memory_mode : undefined)
}

/** The agent an INTERACTIVE new chat starts on: the conductor when the Settings
 *  default is on and the chat would otherwise start on the default agent, or the
 *  agent it asked for. Only the person's own new-chat gestures call this; an app
 *  workstream or a "Chat with" row keeps exactly the agent it names. */
export function newChatAgent(
  agent: string | undefined,
  defaultAgent: string | undefined,
  memoryMode?: string,
): { agent?: string; agent_kind?: 'template' } {
  if (crewModeDefaultOn() && memoryModeAllowsCrewMode(memoryMode) && isDefaultAgentName(agent ?? '', defaultAgent)) {
    return { agent: CREW_MODE_AGENT, agent_kind: 'template' }
  }
  return agent ? { agent } : {}
}
