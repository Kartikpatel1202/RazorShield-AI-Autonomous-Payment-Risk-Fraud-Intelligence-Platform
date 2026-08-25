/**
 * The API client's credential handling.
 *
 * Tested against a stubbed `fetch` rather than through a rendered page, because
 * the questions here are about the request itself: which header carries the
 * token, what happens to the session on a 401, and whether the login call is
 * exempt from that. Those are invisible from the DOM.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiGet, apiPost, authHeaders, onUnauthorized } from '@/lib/api'
import { loadSession } from '@/lib/auth'
import { signInAs, signOut } from '@/test/session'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function headersOf(call: number = 0): Record<string, string> {
  const [, init] = fetchMock.mock.calls[call] as [string, RequestInit]
  return (init.headers ?? {}) as Record<string, string>
}

describe('the bearer token', () => {
  it('rides on the Authorization header, not the URL', async () => {
    signInAs('admin')
    await apiGet('/api/analytics/overview')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(headersOf()['authorization']).toBe('Bearer test-token-admin')
    // A credential in a query string lands in access logs, proxy logs and
    // browser history, none of which promised to keep it.
    expect(url).not.toContain('test-token-admin')
  })

  it('is read at call time, so a login takes effect on the next request', async () => {
    signOut()
    expect(authHeaders()['authorization']).toBeUndefined()

    signInAs('viewer')
    expect(authHeaders()['authorization']).toBe('Bearer test-token-viewer')
  })

  it('is omitted entirely when signed out', async () => {
    signOut()
    await apiGet('/api/analytics/overview')
    expect(headersOf()['authorization']).toBeUndefined()
  })

  it('is sent on POST as well as GET', async () => {
    signInAs('admin')
    await apiPost('/api/feedback', { transaction_id: 'TXN_1' })
    expect(headersOf()['authorization']).toBe('Bearer test-token-admin')
  })
})

describe('a 401 from the server', () => {
  it('clears the stored session', async () => {
    signInAs('admin')
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Not authenticated' }, 401))

    await expect(apiGet('/api/analytics/overview')).rejects.toBeInstanceOf(ApiError)
    expect(loadSession()).toBeNull()
  })

  it('notifies listeners once, so the app redirects instead of showing ten errors', async () => {
    signInAs('admin')
    const listener = vi.fn()
    const unsubscribe = onUnauthorized(listener)
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Not authenticated' }, 401))

    await expect(apiGet('/api/analytics/overview')).rejects.toBeInstanceOf(ApiError)

    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
  })

  it('stops notifying after unsubscribe', async () => {
    signInAs('admin')
    const listener = vi.fn()
    onUnauthorized(listener)()
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Not authenticated' }, 401))

    await expect(apiGet('/api/analytics/overview')).rejects.toBeInstanceOf(ApiError)
    expect(listener).not.toHaveBeenCalled()
  })
})

describe('the login call', () => {
  it('sends no token and does not clear the session on 401', async () => {
    // A 401 here means "wrong password", not "session expired". Firing the
    // unauthorized listeners would bounce the user off the form they are
    // standing on, mid-typing.
    signInAs('admin')
    const listener = vi.fn()
    const unsubscribe = onUnauthorized(listener)
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Invalid email or password' }, 401))

    await expect(
      apiPost('/api/auth/login', { email: 'a@b.example', password: 'x' }, { anonymous: true }),
    ).rejects.toBeInstanceOf(ApiError)

    expect(headersOf()['authorization']).toBeUndefined()
    expect(listener).not.toHaveBeenCalled()
    expect(loadSession()).not.toBeNull()
    unsubscribe()
  })
})

describe('error messages', () => {
  it('surfaces the server’s own detail', async () => {
    signInAs('admin')
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "Role 'viewer' lacks the required permission" }, 403),
    )

    await expect(apiGet('/api/metrics')).rejects.toThrow(/lacks the required permission/)
  })

  it('falls back to the status when the body is not JSON', async () => {
    signInAs('admin')
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error('not json')
      },
    } as unknown as Response)

    await expect(apiGet('/api/analytics/overview')).rejects.toThrow('Request failed (502)')
  })
})
