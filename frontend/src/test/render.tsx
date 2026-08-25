import { render, type RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'

import { AuthProvider } from '@/components/auth/auth-provider'

/**
 * Render a component inside the contexts the app relies on.
 *
 * The auth provider is included because pages read the session directly - the
 * live page, for one, hides the simulator controls unless the caller holds
 * `simulator:control`. Rendering a page without it throws, which is correct
 * behaviour and a poor test failure. `setup.ts` puts an admin session in
 * storage before each test; `signInAs` overrides it where the role matters.
 *
 * Rendering `<App />` through this nests a second provider inside the one App
 * creates. That is harmless: both read the same `sessionStorage`, and the inner
 * one wins for everything below it.
 */
export function renderWithRouter(ui: ReactElement, initialPath = '/'): RenderResult {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>{ui}</AuthProvider>
    </MemoryRouter>,
  )
}
