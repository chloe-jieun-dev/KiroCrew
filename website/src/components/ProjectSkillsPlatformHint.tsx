import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import { i18nT } from '../i18n/t'
import ErrorNotice from './ErrorNotice'

// — One muted line on the Skills page when project skills cannot load here.
//
// The gateway sets `platform_unsupported` when a chat has a project open,
// project skills are on, and the platform lacks the safe project walk (Windows).
// It shares the trust list's ['skill-trust'] query, so it costs no extra fetch.

interface TrustSnapshot {
  platform_unsupported?: boolean
}

export default function ProjectSkillsPlatformHint() {
  const { data, error } = useQuery<TrustSnapshot>({
    queryKey: ['skill-trust'],
    // Defensive call: many suites partial-mock the api client.
    queryFn: () => Promise.resolve(api.skillTrust?.()).then(r => (r ?? {}) as TrustSnapshot),
    staleTime: 60 * 1000,
  })

  if (error) {
    // No hand-off: the Skills tab can hold an unsaved Create Skill draft and an
    // unsaved skill edit, which opening a chat would navigate away from.
    return (
      <ErrorNotice
        message={i18nT('components.projectSkillsPlatformHint.load_failed')}
        className="mb-3"
        testId="project-skills-platform-hint-error"
      />
    )
  }

  if (data?.platform_unsupported !== true) return null

  return (
    <p className="mb-3 text-[11px] text-muted" data-testid="project-skills-platform-hint">
      {i18nT('components.projectSkillsPlatformHint.text')}
    </p>
  )
}
