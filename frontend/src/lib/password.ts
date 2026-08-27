/**
 * Client-side password checks.
 *
 * These mirror `app.core.security.validate_password_strength` so the form can
 * tell the user what is wrong before a round trip. They are **not** the
 * enforcement point: the server applies the same policy independently and its
 * answer is the one that counts. Anything here is a courtesy, and the rules are
 * kept deliberately identical so the two never contradict each other in front
 * of a user.
 */

/** Kept in step with `MIN_PASSWORD_LENGTH` on the backend. */
export const MIN_PASSWORD_LENGTH = 12

/** bcrypt truncates past this, so the backend refuses it outright. */
export const MAX_PASSWORD_BYTES = 72

// Every entry is at least MIN_PASSWORD_LENGTH characters. A shorter one would
// be unreachable, because the length check runs first.
const OBVIOUS = new Set([
  'password1234',
  'passw0rd1234',
  'administrator',
  'qwertyuiop12',
  '123456789012',
  'letmein12345',
  'iloveyou1234',
  'razorshield1',
  'razorshieldai',
])

function byteLength(value: string): number {
  return new TextEncoder().encode(value).length
}

/** The first policy failure, or null if the password is acceptable. */
export function passwordProblem(password: string, email = ''): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`
  }
  if (byteLength(password) > MAX_PASSWORD_BYTES) {
    return `Password must be at most ${MAX_PASSWORD_BYTES} bytes.`
  }
  if (OBVIOUS.has(password.toLowerCase())) {
    return 'That password is too common. Choose another.'
  }
  if (new Set(password).size < 5) {
    return 'Password must use at least 5 different characters.'
  }
  const local = email.split('@')[0]?.trim().toLowerCase() ?? ''
  if (local.length >= 4 && password.toLowerCase().includes(local)) {
    return 'Password must not contain your email address.'
  }
  return null
}

export interface PasswordStrength {
  /** 0 to 4. */
  readonly score: number
  readonly label: string
  readonly tone: 'danger' | 'warning' | 'positive'
}

/**
 * A coarse strength reading for the meter.
 *
 * Length-dominated on purpose. A meter that rewards `P@ssw0rd!` over
 * `correct horse battery staple` teaches the wrong lesson, so character
 * variety contributes at most one point and length carries the rest.
 */
export function passwordStrength(password: string): PasswordStrength {
  if (!password) return { score: 0, label: 'Enter a password', tone: 'danger' }

  let score = 0
  if (password.length >= MIN_PASSWORD_LENGTH) score += 1
  if (password.length >= 16) score += 1
  if (password.length >= 20) score += 1
  if (new Set(password).size >= 10) score += 1

  if (score <= 1) return { score: Math.max(score, 1), label: 'Weak', tone: 'danger' }
  if (score === 2) return { score, label: 'Fair', tone: 'warning' }
  if (score === 3) return { score, label: 'Good', tone: 'positive' }
  return { score: 4, label: 'Strong', tone: 'positive' }
}

/** A shape check only - the server owns the real validation. */
export function looksLikeEmail(value: string): boolean {
  return /^[^\s@]{1,64}@[^\s@]{1,190}$/.test(value.trim())
}
