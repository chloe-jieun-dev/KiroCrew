import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

/* ── Mock api/client BEFORE the component imports ── */
const mockApi = vi.hoisted(() => ({
  skillTrust: vi.fn(),
}))
vi.mock('../api/client', () => ({ api: mockApi }))

import ProjectSkillsPlatformHint from '../components/ProjectSkillsPlatformHint'

function Harness() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ProjectSkillsPlatformHint />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ProjectSkillsPlatformHint', () => {
  it('shows one muted line when the gateway says project skills cannot load here', async () => {
    mockApi.skillTrust.mockResolvedValue({ grants: [], platform_unsupported: true })
    render(<Harness />)
    const hint = await screen.findByTestId('project-skills-platform-hint')
    expect(hint).toHaveTextContent('Project skills (.kiro/skills) do not load on Windows yet.')
    expect(hint).toHaveClass('text-muted')
  })

  it.each([
    ['the flag is false', { grants: [], platform_unsupported: false }],
    ['an older gateway omits the flag', { grants: [] }],
    ['the endpoint is unavailable', undefined],
  ])('renders nothing when %s', async (_label, payload) => {
    mockApi.skillTrust.mockResolvedValue(payload)
    const { container } = render(<Harness />)
    await waitFor(() => expect(mockApi.skillTrust).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('says so through ErrorNotice when the status read fails', async () => {
    mockApi.skillTrust.mockRejectedValue(new Error('502 Bad Gateway'))
    render(<Harness />)
    const notice = await screen.findByTestId('project-skills-platform-hint-error')
    expect(notice).toHaveAttribute('role', 'alert')
    expect(notice).toHaveTextContent('Could not check whether project skills can load here.')
    expect(screen.queryByTestId('project-skills-platform-hint')).toBeNull()
  })
})
