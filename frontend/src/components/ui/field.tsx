import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/**
 * Form controls, in one place.
 *
 * Every filter, every simulator input and the login form use these, so a
 * control looks and focuses the same wherever it appears. Each one owns its
 * `<label>` and wires `htmlFor` from a generated id: a filter with a floating
 * text node beside it is unreachable by a screen reader and unclickable by
 * anyone aiming at the word rather than the box.
 */

const CONTROL = cn(
  'w-full rounded-lg border border-border-subtle bg-surface-raised px-2.5 py-1.5',
  'text-sm text-content shadow-flat transition-colors',
  'placeholder:text-content-faint',
  'hover:border-border-strong',
  'focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/25',
  'disabled:cursor-not-allowed disabled:bg-surface-sunken disabled:opacity-60',
)

function Label({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return (
    <label
      htmlFor={htmlFor}
      className="text-[0.7rem] font-semibold tracking-[0.06em] text-content-muted uppercase"
    >
      {children}
    </label>
  )
}

export function TextField({
  label,
  hint,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string | undefined }) {
  const id = useId()
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <Label htmlFor={id}>{label}</Label>
      <input id={id} className={CONTROL} {...props} />
      {hint ? <p className="text-xs text-content-faint">{hint}</p> : null}
    </div>
  )
}

export interface SelectOption {
  readonly value: string
  readonly label: string
}

export function SelectField({
  label,
  options,
  placeholder = 'Any',
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & {
  label: string
  options: readonly SelectOption[]
  /** The empty choice. Pass `null` for a select with no "unset" state. */
  placeholder?: string | null | undefined
}) {
  const id = useId()
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <Label htmlFor={id}>{label}</Label>
      <select id={id} className={cn(CONTROL, 'pr-7')} {...props}>
        {placeholder === null ? null : <option value="">{placeholder}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  )
}

/**
 * A pair of inputs presented as one range.
 *
 * Two separate fields labelled "Min" and "Max" leave the reader to work out
 * what they bound; this keeps the quantity name attached to both halves.
 */
export function RangeField({
  label,
  suffix,
  from,
  to,
  onFrom,
  onTo,
  type = 'number',
  step,
  min,
  max,
  placeholderFrom = 'min',
  placeholderTo = 'max',
  className,
}: {
  label: string
  suffix?: string | undefined
  from: string
  to: string
  onFrom: (value: string) => void
  onTo: (value: string) => void
  type?: 'number' | 'date' | undefined
  step?: number | undefined
  min?: number | undefined
  max?: number | undefined
  placeholderFrom?: string | undefined
  placeholderTo?: string | undefined
  className?: string | undefined
}) {
  const fromId = useId()
  const toId = useId()
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <Label htmlFor={fromId}>
        {label}
        {suffix ? <span className="ml-1 normal-case text-content-faint">{suffix}</span> : null}
      </Label>
      <div className="flex items-center gap-1.5">
        <input
          id={fromId}
          type={type}
          step={step}
          min={min}
          max={max}
          value={from}
          placeholder={placeholderFrom}
          onChange={(event) => onFrom(event.target.value)}
          aria-label={`${label} from`}
          className={cn(CONTROL, 'numeric min-w-0')}
        />
        <span aria-hidden="true" className="text-xs text-content-faint">
          –
        </span>
        <input
          id={toId}
          type={type}
          step={step}
          min={min}
          max={max}
          value={to}
          placeholder={placeholderTo}
          onChange={(event) => onTo(event.target.value)}
          aria-label={`${label} to`}
          className={cn(CONTROL, 'numeric min-w-0')}
        />
      </div>
    </div>
  )
}

/**
 * A row of mutually exclusive choices.
 *
 * `aria-pressed` rather than a radio group: these are filters that take effect
 * immediately, and announcing them as a form control the reader must submit
 * would misdescribe what happens.
 */
export function SegmentedControl<T extends string | number>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string
  value: T
  options: readonly { readonly value: T; readonly label: string }[]
  onChange: (value: T) => void
  className?: string | undefined
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn(
        'inline-flex max-w-full flex-wrap items-center gap-0.5 rounded-lg border border-border-subtle bg-surface-sunken p-0.5',
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'rounded-md px-2.5 py-1 text-xs font-semibold transition-colors',
              active
                ? 'bg-surface-raised text-content shadow-flat'
                : 'text-content-muted hover:text-content',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
