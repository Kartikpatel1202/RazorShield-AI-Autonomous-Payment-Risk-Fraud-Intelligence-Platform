import { useEffect, useState } from 'react'

/**
 * Delay a rapidly-changing value.
 *
 * Used for the explorer search box: every keystroke would otherwise be a
 * server-side query over 20,000 rows.
 */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])

  return debounced
}
