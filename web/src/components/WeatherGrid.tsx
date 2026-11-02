import { CloudRain, Droplets, Thermometer, Wind } from "lucide-react"
import type { WeatherConditions } from "@/types/api"

const card = "rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"

/** Missing readings (feed unavailable) show as a dash. */
function reading(value: number | null, unit: string, digits?: number) {
  if (value === null) return "—"
  return `${digits === undefined ? value : value.toFixed(digits)}${unit}`
}

export function WeatherGrid({ conditions }: { conditions: WeatherConditions }) {
  return (
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <div className={card}>
        <CloudRain className="size-5 text-blue-600" />
        <div className="mt-5 text-sm font-medium text-slate-500">Rainfall</div>
        <div className="mt-1 text-2xl font-bold text-slate-950">{reading(conditions.rainfallMm, " mm", 1)}</div>
        <div className="mt-1 text-sm text-slate-500">{conditions.forecast ?? "Forecast unavailable"}</div>
      </div>

      <div className={card}>
        <Droplets className="size-5 text-cyan-600" />
        <div className="mt-5 text-sm font-medium text-slate-500">Humidity</div>
        <div className="mt-1 text-2xl font-bold text-slate-950">{reading(conditions.humidityPercent, "%")}</div>
        <div className="mt-1 text-sm text-slate-500">Relative humidity</div>
      </div>

      <div className={card}>
        <Thermometer className="size-5 text-orange-500" />
        <div className="mt-5 text-sm font-medium text-slate-500">Temperature</div>
        <div className="mt-1 text-2xl font-bold text-slate-950">{reading(conditions.temperatureC, "°C", 1)}</div>
        <div className="mt-1 text-sm text-slate-500">Ambient air temperature</div>
      </div>

      <div className={card}>
        <Wind className="size-5 text-indigo-500" />
        <div className="mt-5 text-sm font-medium text-slate-500">Wind</div>
        <div className="mt-1 text-2xl font-bold text-slate-950">{reading(conditions.windSpeedMs, " m/s", 1)}</div>
        <div className="mt-1 text-sm text-slate-500">Used in drying potential</div>
      </div>
    </section>
  )
}