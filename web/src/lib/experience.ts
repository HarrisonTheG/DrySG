import {
  CloudLightning,
  CloudRain,
  CloudSun,
  Droplets,
  Hourglass,
  Sun,
  TrendingDown,
  Wind,
  type LucideIcon,
} from "lucide-react"
import data from "@/data/experienceScenarios.json"
import type { DryingRecommendation } from "@/types/api"
import type { ExperienceCategory, ExperienceData, ExperienceScenario } from "@/types/experience"

export const allScenarios = (data as unknown as ExperienceData).scenarios

/** Replay speed multipliers. */
export const SPEEDS = [1, 2, 4] as const
export type Speed = (typeof SPEEDS)[number]

/** The five-minute step between frames of a replay. */
export const FRAME_MINUTES = 5

export interface CategoryMeta {
  id: ExperienceCategory
  title: string
  blurb: string
  icon: LucideIcon
  /** Tailwind classes for the icon tile. */
  tone: string
}

/** In the order shown. Categories with no scenario in the data are left out. */
export const CATEGORIES: CategoryMeta[] = [
  { id: "hang_now", title: "Perfect day", blurb: "Warm, dry and breezy. DrySG says hang it out.", icon: Sun, tone: "bg-emerald-100 text-emerald-700" },
  { id: "wait", title: "Not yet", blurb: "Fair conditions. DrySG suggests waiting for better.", icon: Hourglass, tone: "bg-amber-100 text-amber-700" },
  { id: "poor_drying", title: "Damp & slow", blurb: "No rain, but humid and still. Laundry would take ages.", icon: Droplets, tone: "bg-slate-200 text-slate-700" },
  { id: "worsening", title: "Slipping", blurb: "Starts fine, then drying slows. An alert without any rain.", icon: TrendingDown, tone: "bg-amber-100 text-amber-700" },
  { id: "rain_arrives", title: "Rain arrives", blurb: "Dry, then a shower hits. Watch the alert fire.", icon: CloudRain, tone: "bg-red-100 text-red-700" },
  { id: "raining", title: "Pouring", blurb: "Rain all hour. The rain veto overrides any score.", icon: CloudLightning, tone: "bg-red-100 text-red-700" },
  { id: "rain_clears", title: "Rain clears", blurb: "Showers end and the recommendation recovers.", icon: CloudSun, tone: "bg-blue-100 text-blue-700" },
  { id: "haze", title: "Hazy air", blurb: "Good drying, but unhealthy air quality.", icon: Wind, tone: "bg-purple-100 text-purple-700" },
]

export function availableCategories(scenarios: ExperienceScenario[] = allScenarios) {
  return CATEGORIES.map((meta) => ({
    ...meta,
    count: scenarios.filter((s) => s.category === meta.id).length,
  })).filter((meta) => meta.count > 0)
}

function choose<T>(items: T[], random: () => number): T | undefined {
  return items[Math.floor(random() * items.length)]
}

/** A random scenario (optionally of one category), different from `exclude` when possible. */
export function pickScenario(
  category: ExperienceCategory | undefined,
  exclude?: string,
  random: () => number = Math.random,
  scenarios: ExperienceScenario[] = allScenarios,
) {
  const inCategory = scenarios.filter((s) => category === undefined || s.category === category)
  const fresh = inCategory.filter((s) => s.id !== exclude)
  return choose(fresh.length > 0 ? fresh : inCategory, random)
}

/** A random real postal code in the scenario's area, different from `exclude` when possible. */
export function pickPostalCode(scenario: ExperienceScenario, exclude?: string, random: () => number = Math.random) {
  const fresh = scenario.postalCodes.filter((code) => code !== exclude)
  return choose(fresh.length > 0 ? fresh : scenario.postalCodes, random) ?? ""
}

/** A frame as the normal result cards expect it, for the chosen postal code. */
export function frameFor(scenario: ExperienceScenario, index: number, postalCode: string): DryingRecommendation {
  return { ...scenario.frames[index], postalCode }
}

type Style = "time" | "date" | "dateTime"

const OPTIONS: Record<Style, Intl.DateTimeFormatOptions> = {
  time: { hour: "numeric", minute: "2-digit" },
  date: { weekday: "short", day: "numeric", month: "short", year: "numeric" },
  dateTime: { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" },
}

/** Format in Singapore time, whatever the viewer's time zone: the weather happened there. */
export function formatSgt(iso: string, style: Style) {
  return new Intl.DateTimeFormat("en-SG", { timeZone: "Asia/Singapore", ...OPTIONS[style] }).format(new Date(iso))
}
