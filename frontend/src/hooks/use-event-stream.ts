import { useCallback, useEffect, useRef, useState } from 'react'

import { authHeaders, eventStreamUrl, type RiskEvent } from '@/lib/api'

export type StreamStatus = 'connecting' | 'live' | 'disconnected'

export interface EventStreamState {
  readonly status: StreamStatus
  readonly events: RiskEvent[]
  readonly latestSequence: number
  /** Reconnect attempts since the last successful connection. */
  readonly retries: number
  readonly clear: () => void
}

/** Wait this long before reconnecting after a drop. */
const RECONNECT_MS = 2000

/**
 * Parse a chunk of SSE text into the `data:` payloads it contains.
 *
 * Exported for the tests, which is the honest reason it is a separate function:
 * frame parsing is the part with edge cases (multi-line data, comments,
 * `\r\n` line endings) and it is far easier to assert against directly than
 * through a mocked network stack.
 */
export function parseFrames(block: string): string[] {
  const payloads: string[] = []
  for (const frame of block.split(/\n\n/)) {
    const lines = frame.split(/\r?\n/)
    const data = lines
      .filter((line) => line.startsWith('data:'))
      // Per the SSE grammar a single leading space after the colon is part of
      // the delimiter, not the payload.
      .map((line) => line.slice(5).replace(/^ /, ''))
    // A frame carrying only `id:`/`event:`/a `:` comment - the heartbeat - has
    // no data lines and is correctly skipped here.
    if (data.length > 0) payloads.push(data.join('\n'))
  }
  return payloads
}

/**
 * Subscribe to the live risk event stream.
 *
 * ## Why `fetch` and not `EventSource`
 *
 * `EventSource` cannot set request headers. That is a hard limitation of the
 * API, not a gap in this code, and it collides with Phase 10: the stream is
 * authenticated, and the only ways to hand `EventSource` a credential are a
 * cookie (which reintroduces CSRF) or a token in the query string (which lands
 * in access logs and browser history). Reading the response body as a stream
 * lets the token travel in an `Authorization` header like every other request.
 *
 * It also removes a bug this hook used to have. The server names each frame
 * with an SSE `event:` field, so `EventSource` dispatches only to per-type
 * listeners and `onmessage` never fires - a new event type on the server would
 * have been delivered and silently dropped here. Parsing frames ourselves means
 * every frame is handled regardless of its name.
 *
 * ## Two things it has to get right
 *
 * **No duplicates on reconnect.** Every event carries a monotonic `sequence`.
 * The highest one rendered is tracked and anything at or below it is dropped,
 * so a replayed backlog cannot double up rows the user is already looking at.
 * Belt and braces: the reconnect URL also carries the cursor, so the server
 * usually does not send them in the first place.
 *
 * **A silent stream is not a live one.** A dead backend looks exactly like a
 * quiet one unless something says otherwise, so any read error flips the badge
 * to `disconnected` immediately rather than leaving a stale LIVE on screen.
 */
export function useEventStream(maxEvents = 200): EventStreamState {
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const [events, setEvents] = useState<RiskEvent[]>([])
  const [retries, setRetries] = useState(0)
  // The cursor is held twice on purpose. The ref is what the read loop uses -
  // it closes over its first render and would otherwise compare against a stale
  // number. The state copy is what callers see, so a consumer can key an effect
  // on it and actually be re-run.
  const [latestSequence, setLatestSequence] = useState(0)
  const latestRef = useRef(0)

  const clear = useCallback(() => {
    setEvents([])
  }, [])

  useEffect(() => {
    let cancelled = false
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()

    const ingest = (raw: string) => {
      let parsed: RiskEvent
      try {
        parsed = JSON.parse(raw) as RiskEvent
      } catch {
        // A malformed frame is not worth tearing the stream down for.
        return
      }
      if (typeof parsed.sequence !== 'number' || parsed.sequence <= latestRef.current) return
      latestRef.current = parsed.sequence
      setLatestSequence(parsed.sequence)
      setStatus('live')
      setEvents((current) => [parsed, ...current].slice(0, maxEvents))
    }

    const scheduleReconnect = () => {
      if (cancelled) return
      setStatus('disconnected')
      reconnectTimer = setTimeout(() => {
        setRetries((count) => count + 1)
        void connect()
      }, RECONNECT_MS)
    }

    async function connect(): Promise<void> {
      if (cancelled) return
      setStatus((current) => (current === 'live' ? current : 'connecting'))

      let response: Response
      try {
        response = await fetch(eventStreamUrl(latestRef.current), {
          headers: authHeaders({ accept: 'text/event-stream' }),
          signal: controller.signal,
        })
      } catch {
        scheduleReconnect()
        return
      }

      if (!response.ok || !response.body) {
        // 401 and 403 land here too. Retrying is still right: the auth provider
        // redirects to the login form on a 401 from any request, and this hook
        // must not be the thing that decides to give up.
        scheduleReconnect()
        return
      }

      if (!cancelled) {
        setStatus('live')
        setRetries(0)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      // Frames can be split across chunks, so incomplete text is held here
      // until its terminating blank line arrives.
      let buffer = ''

      try {
        for (;;) {
          const { done, value } = await reader.read()
          if (done || cancelled) break

          buffer += decoder.decode(value, { stream: true })
          const boundary = buffer.lastIndexOf('\n\n')
          if (boundary === -1) continue

          const complete = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          for (const payload of parseFrames(complete)) ingest(payload)
        }
      } catch {
        /* read failed; handled by the reconnect below */
      }

      if (!cancelled) scheduleReconnect()
    }

    void connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      controller.abort()
    }
  }, [maxEvents])

  return { status, events, latestSequence, retries, clear }
}
