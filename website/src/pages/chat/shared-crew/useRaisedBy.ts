/** Whose turn raised a pending approval or question, as one short label for its
 *  card: a teammate's first name, or the Crew owner. `''` when the viewer raised
 *  it, when the gateway answers it by its approval mode, or on a Crew where
 *  nobody else has joined, so a solo chat shows nothing. `matches` picks the
 *  card's decision by id; `null` looks for none. */
import { isUnknownPrincipal, principalName } from '../../../api/sharedCrewUi'
import { displayName } from '../../../api/sharedCrewMode'
import { useSessionDecisions, useSharedCrewViewer, useTeamAttribution } from '../../../hooks/sharedCrew/useSharedCrew'

export function useRaisedBy(sessionKey: string | null, matches: ((decisionId: string) => boolean) | null): string {
  const team = useTeamAttribution()
  const viewer = useSharedCrewViewer(team).data
  const items = useSessionDecisions(sessionKey, team && !!matches).data?.items
  if (!team || !matches || !viewer || isUnknownPrincipal(viewer)) return ''
  const decision = items?.find(d => d.state === 'pending' && matches(d.decision_id))
  const who = decision?.initiator
  if (!decision || !who || isUnknownPrincipal(who) || who.id === viewer.id || decision.settled_by === 'host') return ''
  const name = displayName(who)
  return name === principalName(who) ? name.split(/\s+/)[0] : name
}
