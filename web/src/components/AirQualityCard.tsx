import { ShieldCheck, TriangleAlert } from "lucide-react"
import type { DryingRecommendation } from "@/types/api"

export function AirQualityCard({ recommendation }: { recommendation: DryingRecommendation }) {
  const warning = recommendation.airQualityWarning

  return (
    <section className={`rounded-3xl border p-5 shadow-sm ${warning ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-slate-500">Air quality</div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-3xl font-bold text-slate-950">PSI {recommendation.conditions.psi ?? "—"}</span>
            <span className="text-sm font-semibold text-slate-600">{recommendation.conditions.psiBand ?? "Unavailable"}</span>
          </div>
        </div>
        <div className={`grid size-11 place-items-center rounded-2xl ${warning ? "bg-amber-100 text-amber-700" : "bg-emerald-100 text-emerald-700"}`}>
          {warning ? <TriangleAlert className="size-5" /> : <ShieldCheck className="size-5" />}
        </div>
      </div>

      <p className="mt-3 text-sm leading-6 text-slate-600">
        {warning
          ? "Drying conditions may be good, but outdoor air quality is unhealthy. Consider keeping laundry indoors."
          : "PSI is displayed separately because it affects outdoor suitability, not the physical Drying Score."}
      </p>
    </section>
  )
}