import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Minimal async data fetching with explicit loading, error and empty states.
 *
 * Deliberately hand-rolled rather than pulling in a data-fetching library: the
 * console needs four states and a refetch, and every page must be able to show
 * a real error rather than an empty chart. In-flight requests are aborted when
 * the inputs change, so a slow page-1 response cannot overwrite page 2.
 */
export interface QueryState<T> {
  readonly data: T | undefined
  readonly error: Error | undefined
  readonly loading: boolean
  readonly refetch: () => void
}

export function useQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): QueryState<T> {
  const [data, setData] = useState<T | undefined>(undefined)
  const [error, setError] = useState<Error | undefined>(undefined)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  // Kept in a ref so changing the callback identity does not retrigger the
  // effect; `deps` is the deliberate, explicit trigger.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    const controller = new AbortController()
    let active = true

    setLoading(true)
    setError(undefined)

    fetcherRef
      .current(controller.signal)
      .then((result) => {
        if (active) {
          setData(result)
          setLoading(false)
        }
      })
      .catch((caught: unknown) => {
        if (!active || controller.signal.aborted) return
        setError(caught instanceof Error ? caught : new Error(String(caught)))
        setLoading(false)
      })

    return () => {
      active = false
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  const refetch = useCallback(() => setNonce((value) => value + 1), [])

  return { data, error, loading, refetch }
}
