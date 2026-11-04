import type { LaundrySession } from "@/types/api"

export const SESSION_KEY = "drysg.active-session"
export const PENDING_KEY = "drysg.pending-start"

/** Matches the backend's verification link lifetime. */
const PENDING_LIFETIME_MS = 24 * 60 * 60 * 1000

/** A start that is waiting for the user to confirm their email. */
export interface PendingStart {
  email: string
  postalCode: string
  /** When the latest verification email was sent (ms since epoch). */
  sentAt: number
}

function read<T>(key: string): T | undefined {
  try {
    const value = localStorage.getItem(key)
    return value ? (JSON.parse(value) as T) : undefined
  } catch {
    return undefined
  }
}

function write(key: string, value: unknown) {
  try {
    if (value === undefined) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Storage can be blocked (private mode); the page still works without it.
  }
}

export function readStoredSession() {
  return read<LaundrySession>(SESSION_KEY)
}

export function storeSession(session: LaundrySession | undefined) {
  write(SESSION_KEY, session)
}

export function readPendingStart() {
  const pending = read<PendingStart>(PENDING_KEY)
  if (!pending || Date.now() - pending.sentAt > PENDING_LIFETIME_MS) return undefined
  return pending
}

export function storePendingStart(pending: PendingStart | undefined) {
  write(PENDING_KEY, pending)
}

export const EXPERIENCE_EMAIL_KEY = "drysg.experience-email"

/** The email last used for Experience live alerts, so it is not retyped each visit. */
export function readExperienceEmail() {
  return read<string>(EXPERIENCE_EMAIL_KEY) ?? ""
}

export function storeExperienceEmail(email: string) {
  write(EXPERIENCE_EMAIL_KEY, email || undefined)
}
