export type RecommendationState =
  | "HANG_NOW"
  | "WAIT"
  | "DO_NOT_HANG"

export interface WeatherConditions {
  rainfallMm: number
  temperatureC: number
  humidityPercent: number
  windSpeedMs: number
  forecast: string
  psi: number
  psiBand:
    | "Good"
    | "Moderate"
    | "Unhealthy"
    | "Very Unhealthy"
    | "Hazardous"
}

export interface StationSources {
  rainfall: string
  humidity: string
  temperature: string
  wind: string
}

export interface DryingRecommendation {
  postalCode: string
  areaName: string
  score: number
  recommendation: RecommendationState
  summary: string
  percentileDescription?: string
  updatedAt: string
  conditions: WeatherConditions
  rainVeto: boolean
  airQualityWarning: boolean
  stationSources?: StationSources
}

export interface LaundrySession {
  id: string
  postalCode: string
  startedAt: string
  expiresAt: string
  active: boolean
}