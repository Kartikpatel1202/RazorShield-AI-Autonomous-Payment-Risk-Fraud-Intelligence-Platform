/** Display formatting. Never rounds a value the reader needs precisely. */

const CURRENCY_FORMATTERS = new Map<string, Intl.NumberFormat>()

/** Money, in the transaction's own currency. */
export function formatAmount(value: number, currency = 'INR'): string {
  let formatter = CURRENCY_FORMATTERS.get(currency)
  if (!formatter) {
    formatter = new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    })
    CURRENCY_FORMATTERS.set(currency, formatter)
  }
  return formatter.format(value)
}

const COMPACT = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })
const PLAIN = new Intl.NumberFormat('en-US')

export function formatCount(value: number): string {
  return PLAIN.format(value)
}

/** Compact form for axis labels, where space is the constraint. */
export function formatCompact(value: number): string {
  return COMPACT.format(value)
}

/**
 * A fraud probability, as a percentage.
 *
 * Deliberately keeps two decimals: 0.20029 is 20.03%, and rounding it to 20%
 * would erase the distinction the disagreement rule turns on.
 */
export function formatProbability(value: number | null | undefined): string {
  if (value === null || value === undefined) return '--'
  const percent = value * 100
  // Never round up to a certainty the model did not express. 0.99996 is
  // "99.99%", not "100.00%" - on a risk console that distinction is the
  // difference between a measured probability and a claim of certainty.
  if (value < 1 && percent.toFixed(2) === '100.00') return '99.99%'
  if (value > 0 && percent.toFixed(2) === '0.00') return '<0.01%'
  return `${percent.toFixed(2)}%`
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '--'
  return `${(value * 100).toFixed(digits)}%`
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return 'not measured'
  if (ms < 1) return `${ms.toFixed(3)} ms`
  return `${ms.toFixed(2)} ms`
}

const DATE_TIME = new Intl.DateTimeFormat('en-GB', {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'UTC',
})

const DATE_ONLY = new Intl.DateTimeFormat('en-GB', {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  timeZone: 'UTC',
})

/** Timestamps are shown in UTC, matching how the backend stores them. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '--'
  return `${DATE_TIME.format(parsed)} UTC`
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '--'
  return DATE_ONLY.format(parsed)
}

/** Turn REASON_CODE_LIKE_THIS into "Reason code like this". */
export function humanizeCode(code: string): string {
  const lower = code.replace(/_/g, ' ').toLowerCase()
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

/** Shorten a digest for display while keeping it recognisable. */
export function shortDigest(digest: string, length = 16): string {
  return digest.length <= length ? digest : `${digest.slice(0, length)}...`
}
