import MockAdapter from "axios-mock-adapter"
import { addHours } from "date-fns"
import { api } from "@/api/axios"
import { allScenarios } from "@/lib/experience"
import type {
  ApiErrorBody,
  ApiErrorCode,
  DryingRecommendation,
  LaundrySession,
  RecommendationState,
} from "@/types/api"

// Kept in localStorage so a /verify or /stop link opened in a new tab sees the same data.
const DB_KEY = "drysg.mock-db"
const RESEND_INTERVAL_MS = 60_000
/** Matches the backend: one Experience email per address every 15 seconds. */
const EXPERIENCE_INTERVAL_MS = 15_000

interface MockUser {
  verified: boolean
  token?: string
  sentAt?: number
  experienceAt?: number
}

interface MockDb {
  users: Record<string, MockUser>
  sessions: Record<string, LaundrySession>
}

function loadDb(): MockDb {
  try {
    return JSON.parse(localStorage.getItem(DB_KEY) ?? "") as MockDb
  } catch {
    return { users: {}, sessions: {} }
  }
}

function saveDb(db: MockDb) {
  localStorage.setItem(DB_KEY, JSON.stringify(db))
}

function fail(status: number, code: ApiErrorCode, message: string): [number, ApiErrorBody] {
  return [status, { error: { code, message } }]
}

function body(data: string | undefined) {
  try {
    return JSON.parse(data ?? "{}")
  } catch {
    return {}
  }
}

const isEmail = (value: unknown) => typeof value === "string" && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value.trim())
const normalize = (email: string) => email.trim().toLowerCase()

/** The session as the API returns it: active only while running and unexpired. */
function view(session: LaundrySession): LaundrySession {
  return { ...session, active: session.active && new Date(session.expiresAt) > new Date() }
}

function scenarioForPostalCode(postalCode: string): DryingRecommendation {
  const lastDigit = Number(postalCode.slice(-1))
  const now = new Date().toISOString()

  let score = 82
  let recommendation: RecommendationState = "HANG_NOW"
  let rainfallMm = 0
  let humidityPercent = 66
  let temperatureC = 31.4
  let windSpeedMs = 3.6
  let forecast = "Partly Cloudy"
  let psi = 54
  let psiBand: DryingRecommendation["conditions"]["psiBand"] = "Moderate"
  let rainVeto = false
  let airQualityWarning = false
  let summary = "Good drying conditions. Keep an eye on the short-term forecast."

  if (lastDigit >= 4 && lastDigit <= 6) {
    score = 55
    recommendation = "WAIT"
    humidityPercent = 82
    temperatureC = 29.2
    windSpeedMs = 1.7
    forecast = "Cloudy"
    summary = "Drying is possible, but the current evaporation potential is only moderate."
  }

  if (lastDigit >= 7) {
    score = 78
    recommendation = "DO_NOT_HANG"
    rainfallMm = 2.8
    humidityPercent = 90
    temperatureC = 28.0
    windSpeedMs = 2.4
    forecast = "Thundery Showers"
    rainVeto = true
    summary = "It is raining now, so rain overrides the otherwise favourable Drying Score."
  }

  if (lastDigit === 3) {
    score = 86
    psi = 112
    psiBand = "Unhealthy"
    airQualityWarning = true
    summary = "Drying conditions are strong, but outdoor air quality is currently unhealthy."
  }

  const areas = ["Clementi", "Tampines", "Bishan", "Jurong West", "Hougang"]
  const areaName = areas[Number(postalCode[0]) % areas.length]

  return {
    postalCode,
    areaName,
    score,
    recommendation,
    summary,
    percentileDescription: `Current drying potential is better than approximately ${score}% of historical Singapore dry-weather conditions.`,
    updatedAt: now,
    rainVeto,
    airQualityWarning,
    conditions: {
      rainfallMm,
      humidityPercent,
      temperatureC,
      windSpeedMs,
      forecast,
      psi,
      psiBand,
    },
    stationSources: {
      rainfall: "S50",
      humidity: "S24",
      temperature: "S24",
      wind: "S44",
    },
  }
}

export function setupMockApi() {
  if (import.meta.env.VITE_USE_MOCK_API !== "true") return

  const mock = new MockAdapter(api, { delayResponse: 550 })

  mock.onGet("/recommendation").reply((config) => {
    const postalCode = String(config.params?.postalCode ?? "")
    if (!/^\d{6}$/.test(postalCode)) {
      return fail(400, "INVALID_POSTAL_CODE", "Enter a valid 6-digit Singapore postal code.")
    }
    return [200, scenarioForPostalCode(postalCode)]
  })

  // Every email starts unverified. The verification link is logged to the browser console.
  mock.onPost("/users").reply((config) => {
    const { email } = body(config.data)
    if (!isEmail(email)) return fail(400, "INVALID_EMAIL", "Enter a valid email address")
    const db = loadDb()
    const address = normalize(email)
    const user = db.users[address] ?? { verified: false }
    if (user.verified) return [202, { email: address, verified: true }]
    if (user.sentAt && Date.now() - user.sentAt < RESEND_INTERVAL_MS) {
      return fail(429, "RESEND_TOO_SOON", "A verification email was just sent. Wait a minute before asking again")
    }
    const token = crypto.randomUUID()
    db.users[address] = { verified: false, token, sentAt: Date.now() }
    saveDb(db)
    const params = new URLSearchParams({ email: address, token })
    console.info(`[mock API] Verification link: ${location.origin}/verify?${params}`)
    return [202, { email: address, verified: false }]
  })

  mock.onPost("/users/verify").reply((config) => {
    const { email, token } = body(config.data)
    if (!isEmail(email)) return fail(400, "INVALID_EMAIL", "Enter a valid email address")
    const db = loadDb()
    const address = normalize(email)
    const user = db.users[address]
    if (user?.verified) return [200, { email: address, verified: true }]
    if (!user?.token || user.token !== token) {
      return fail(400, "INVALID_TOKEN", "This verification link is invalid or has expired")
    }
    db.users[address] = { verified: true }
    saveDb(db)
    return [200, { email: address, verified: true }]
  })

  // Like the real API, only verified emails get an alert, and the content comes from the scenario.
  mock.onPost("/experience/alert").reply((config) => {
    const { email, scenarioId, frame } = body(config.data)
    if (!isEmail(email)) return fail(400, "INVALID_EMAIL", "Enter a valid email address")
    const scenario = allScenarios.find((s) => s.id === scenarioId)
    if (!scenario) return fail(404, "SCENARIO_NOT_FOUND", "No such scenario")
    const alert = scenario.alerts.find((a) => a.frame === frame)
    if (!alert) return fail(400, "NO_ALERT_AT_FRAME", "This scenario raises no alert at that frame")
    const db = loadDb()
    const address = normalize(email)
    const user = db.users[address]
    if (!user?.verified) return fail(403, "EMAIL_NOT_VERIFIED", "Confirm your email before turning on live alerts")
    if (user.experienceAt && Date.now() - user.experienceAt < EXPERIENCE_INTERVAL_MS) {
      return fail(429, "RATE_LIMITED", "An alert was just sent. Wait a few seconds")
    }
    user.experienceAt = Date.now()
    saveDb(db)
    console.info(`[mock API] Experience alert to ${address}: ${alert.reason} (${scenario.area})`)
    return [202, { email: address, scenarioId, frame }]
  })

  mock.onPost("/laundry-sessions").reply((config) => {
    const { postalCode, email } = body(config.data)
    if (!/^\d{6}$/.test(postalCode ?? "")) {
      return fail(400, "INVALID_POSTAL_CODE", "Enter a valid 6-digit Singapore postal code.")
    }
    if (!isEmail(email)) return fail(400, "INVALID_EMAIL", "Enter a valid email address")
    const db = loadDb()
    const address = normalize(email)
    if (!db.users[address]?.verified) {
      return fail(403, "EMAIL_NOT_VERIFIED", "Verify your email before starting monitoring")
    }

    // Only one active session per email: starting a new one stops the previous.
    for (const session of Object.values(db.sessions)) {
      if (session.email === address) session.active = false
    }
    const recommendation = scenarioForPostalCode(postalCode)
    const id = crypto.randomUUID()
    const session: LaundrySession = {
      id,
      postalCode,
      email: address,
      areaName: recommendation.areaName,
      startedAt: new Date().toISOString(),
      expiresAt: addHours(new Date(), 12).toISOString(),
      active: true,
    }
    db.sessions[id] = session
    saveDb(db)
    return [201, session]
  })

  mock.onGet(/\/laundry-sessions\/.+/).reply((config) => {
    const id = decodeURIComponent(config.url?.split("/").pop() ?? "")
    const session = loadDb().sessions[id]
    if (!session) return fail(404, "SESSION_NOT_FOUND", "Monitoring session not found")
    return [200, view(session)]
  })

  mock.onDelete(/\/laundry-sessions\/.+/).reply((config) => {
    const id = decodeURIComponent(config.url?.split("/").pop() ?? "")
    const db = loadDb()
    const session = db.sessions[id]
    if (!session) return fail(404, "SESSION_NOT_FOUND", "Monitoring session not found")
    session.active = false
    saveDb(db)
    return [200, view(session)]
  })
}
