import { afterEach, describe, expect, it } from 'vitest'
import { queryClient } from '../api/queryClient'
import { CREW_MODE_AGENT, crewModeDefaultOn, isDefaultAgentName, memoryModeAllowsCrewMode, newChatAgent } from './crewMode'

const setDefault = (value: unknown) => queryClient.setQueryData(['dashboardConfig'], { default_crew_mode: value })

describe('crewMode', () => {
  afterEach(() => { queryClient.removeQueries({ queryKey: ['dashboardConfig'] }) })

  it('runs on the existing conductor', () => {
    expect(CREW_MODE_AGENT).toBe('kirocrew-conductor')
  })

  it('treats the empty name and the configured default as the default agent', () => {
    expect(isDefaultAgentName('', 'default')).toBe(true)
    expect(isDefaultAgentName('default', 'default')).toBe(true)
    expect(isDefaultAgentName('custom-x', 'default')).toBe(false)
    // `kirocrew` is the default only while none is configured.
    expect(isDefaultAgentName('kirocrew', 'default')).toBe(false)
    expect(isDefaultAgentName('kirocrew', '')).toBe(true)
  })

  it('needs a Persistent chat, since an Incognito or Temporary one cannot open sessions', () => {
    expect(memoryModeAllowsCrewMode(undefined)).toBe(true)
    expect(memoryModeAllowsCrewMode('persistent')).toBe(true)
    expect(memoryModeAllowsCrewMode('incognito')).toBe(false)
    expect(memoryModeAllowsCrewMode('temporary')).toBe(false)
  })

  it('does not start an ephemeral new chat on the conductor', () => {
    setDefault(true)
    expect(newChatAgent('default', 'default', 'incognito')).toEqual({ agent: 'default' })
    queryClient.setQueryData(['dashboardConfig'], { default_crew_mode: true, default_memory_mode: 'temporary' })
    expect(newChatAgent('default', 'default')).toEqual({ agent: 'default' })
  })

  it('reads the Settings default from the cache, and a cold cache or a non-true value as off', () => {
    expect(crewModeDefaultOn()).toBe(false)
    setDefault('yes')
    expect(crewModeDefaultOn()).toBe(false)
    setDefault(true)
    expect(crewModeDefaultOn()).toBe(true)
  })

  it('starts a new default-agent chat on the conductor only when the default is on', () => {
    expect(newChatAgent('default', 'default')).toEqual({ agent: 'default' })
    expect(newChatAgent(undefined, undefined)).toEqual({})
    setDefault(true)
    expect(newChatAgent('default', 'default')).toEqual({ agent: CREW_MODE_AGENT, agent_kind: 'template' })
    expect(newChatAgent(undefined, undefined)).toEqual({ agent: CREW_MODE_AGENT, agent_kind: 'template' })
  })

  it('never re-points a chat that names another agent', () => {
    setDefault(true)
    expect(newChatAgent('custom-x', 'default')).toEqual({ agent: 'custom-x' })
  })
})
