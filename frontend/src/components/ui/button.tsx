import type { ButtonHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: 'bg-brand text-brand-contrast shadow-flat hover:bg-brand-strong active:bg-brand-strong',
  secondary:
    'border border-border-subtle bg-surface-raised text-content shadow-flat hover:border-border-strong hover:bg-surface-sunken',
  ghost: 'text-content-muted hover:bg-surface-sunken hover:text-content',
  danger: 'bg-danger text-brand-contrast shadow-flat hover:opacity-90',
}

const SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: 'gap-1.5 px-2.5 py-1.5 text-xs',
  md: 'gap-2 px-3.5 py-2 text-sm',
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      // Defaulted, because an unstyled `<button>` inside a `<form>` submits it.
      // Every accidental page reload in a console traces back to this default.
      type={type}
      className={cn(
        // No `shrink-0`: the assistant offers whole questions as buttons, and a
        // button that refuses to shrink sets the page width on a phone.
        'inline-flex max-w-full items-center justify-center rounded-lg font-medium',
        'transition-colors duration-150',
        'disabled:pointer-events-none disabled:opacity-50',
        SIZE_CLASSES[size],
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    />
  )
}
