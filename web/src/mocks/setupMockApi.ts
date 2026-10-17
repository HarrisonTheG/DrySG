import MockAdapter from "axios-mock-adapter"
import { addHours } from "date-fns"
import { api } from "@/api/axios"
import type {
  DryingRecommendation,
  LaundrySession,
  RecommendationState,
} from "@/types/api"

const sessions = new Map<string, LaundrySession>()

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
      return [400, { message: "Enter a valid 6-digit Singapore postal code." }]
    }
    return [200, scenarioForPostalCode(postalCode)]
  })

  mock.onPost("/laundry-sessions").reply((config) => {
    const body = JSON.parse(config.data ?? "{}")
    const { postalCode, email } = body

    if (!/^\d{6}$/.test(postalCode) || !String(email).includes("@")) {
      return [400, { message: "Invalid monitoring details." }]
    }

    const recommendation = scenarioForPostalCode(postalCode)
    const id = crypto.randomUUID()
    const session: LaundrySession = {
      id,
      postalCode,
      email,
      areaName: recommendation.areaName,
      startedAt: new Date().toISOString(),
      expiresAt: addHours(new Date(), 5).toISOString(),
      active: true,
      lastAlertState: recommendation.recommendation,
    }

    sessions.set(id, session)
    return [201, session]
  })

  mock.onDelete(/\/laundry-sessions\/.+/).reply((config) => {
    const id = config.url?.split("/").pop() ?? ""
    if (!sessions.has(id)) {
      return [404, { message: "Monitoring session not found." }]
    }
    sessions.delete(id)
    return [204]
  })
}
