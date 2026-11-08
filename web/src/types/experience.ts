import type { DryingRecommendation } from "@/types/api"

export type ExperienceCategory =
  | "hang_now"
  | "wait"
  | "poor_drying"
  | "worsening"
  | "rain_arrives"
  | "raining"
  | "rain_clears"
  | "haze"

/** An alert the live alert rules raised at one frame of a replay. */
export interface ExperienceAlert {
  frame: number
  reason: string
  prevScore: number
  prevBand: string
  newScore: number
  newBand: string
  newVeto: boolean
  snapshotTs: string
}

/**
 * One real hour of NEA history, scored and alerted by the same code as the live service
 * (see services/scripts/build_experience_scenarios.py). `frames` are 5 minutes apart.
 */
export interface ExperienceScenario {
  id: string
  category: ExperienceCategory
  area: string
  startedAt: string
  /** Real postal codes in `area`, so a replay can be shown for a plausible address. */
  postalCodes: string[]
  frames: DryingRecommendation[]
  alerts: ExperienceAlert[]
}

export interface ExperienceData {
  version: number
  scoreMethod: string
  scenarios: ExperienceScenario[]
}
