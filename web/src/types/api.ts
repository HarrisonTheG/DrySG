export type RecommendationState = "HANG_NOW" | "WAIT" | "DO_NOT_HANG"

export type PsiBand =
  | "Good"
  | "Moderate"
  | "Unhealthy"
  | "Very Unhealthy"
  | "Hazardous"

/** A reading is null when its NEA feed was unavailable. */
export interface WeatherConditions {
  rainfallMm: number | null
  temperatureC: number | null
  humidityPercent: number | null
  windSpeedMs: number | null
  forecast: string | null
  psi: number | null
  psiBand: PsiBand | null
}

export interface StationSources {
  rainfall: string | null
  humidity: string | null
  temperature: string | null
  wind: string | null
}

export interface DryingRecommendation {
  postalCode: string
  areaName: string
  score: number
  recommendation: RecommendationState
  summary: string
  percentileDescription?: string | null
  updatedAt: string
  conditions: WeatherConditions
  rainVeto: boolean
  airQualityWarning: boolean
  stationSources?: StationSources
  /** True when a missing reading was replaced by its historical median. */
  degraded?: boolean
}

export interface LaundrySession {
  id: string
  postalCode: string
  email: string
  areaName: string
  startedAt: string
  expiresAt: string
  active: boolean
  lastAlertState?: RecommendationState
}

export interface StartLaundrySessionInput {
  postalCode: string
  email: string
}

export interface UserStatus {
  email: string
  verified: boolean
}

export type ApiErrorCode =
  | "INVALID_POSTAL_CODE"
  | "POSTAL_CODE_NOT_FOUND"
  | "INVALID_EMAIL"
  | "INVALID_REQUEST"
  | "INVALID_TOKEN"
  | "INVALID_SESSION_ID"
  | "EMAIL_NOT_VERIFIED"
  | "SESSION_NOT_FOUND"
  | "SESSION_CONFLICT"
  | "RESEND_TOO_SOON"
  | "DATA_UNAVAILABLE"
  | "EMAIL_UNAVAILABLE"
  | "INTERNAL_ERROR"

export interface ApiErrorBody {
  error: { code: ApiErrorCode; message: string }
}
