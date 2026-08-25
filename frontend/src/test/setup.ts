import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

import { signInAs, signOut } from './session'

// Every test starts signed in as an administrator. Without this, the console's
// Phase 10 auth guard renders the login form and every pre-existing page test
// would be asserting about a form it never meant to visit. Tests about roles
// override it by calling `signInAs` themselves.
beforeEach(() => {
  signInAs('admin')
})

afterEach(() => {
  cleanup()
  signOut()
})
