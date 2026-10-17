import { CloudRain, Sparkles } from "lucide-react"
import { recommendationMeta } from "@/lib/recommendation"
import type { DryingRecommendation } from "@/types/api"

export function DryingScoreCard({ recommendation }: { recommendation: DryingRecommendation }) {
  const meta = recommendationMeta(recommendation.recommendation)

  return (
    <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
      {recommendation.rainVeto && (
        <div className="flex items-center gap-2 border-b border-red-200 bg-red-50 px-5 py-3 text-sm font-semibold text-red-800">
          <CloudRain className="size-4" />
          Rain veto active: do not hang laundry outside now.
        </div>
      )}

      <div className="grid gap-6 p-5 sm:grid-cols-[180px_1fr] sm:p-7">
        <div className="flex items-center justify-center">
          <div className="grid size-40 place-items-center rounded-full bg-slate-50 ring-8 ring-slate-100">
            <div className="text-center">
              <div className={`text-5xl font-bold tracking-tight ${meta.scoreClass}`}>{recommendation.score}</div>
              <div className="mt-1 text-sm font-medium text-slate-500">/ 100</div>
            </div>
          </div>
        </div>

        <div className="flex flex-col justify-center">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className={`inline-flex rounded-full px-3 py-1 text-xs font-bold uppercase tracking-wide ring-1 ${meta.badgeClass}`}>
              {meta.label}
            </span>
            <span className="text-sm text-slate-500">{recommendation.areaName}</span>
          </div>

          <h2 className="text-2xl font-bold tracking-tight text-slate-950">{meta.headline}</h2>
          <p className="mt-2 max-w-2xl leading-7 text-slate-600">{recommendation.summary}</p>
          <p className="mt-2 text-sm font-medium text-blue-700">{recommendation.percentileDescription}</p>

          <div className="mt-5 h-2.5 overflow-hidden rounded-full bg-slate-100">
            <div className={`h-full rounded-full transition-all ${meta.barClass}`} style={{ width: `${recommendation.score}%` }} />
          </div>

          <div className="mt-4 flex items-start gap-2 text-sm text-slate-500">
            <Sparkles className="mt-0.5 size-4 shrink-0 text-blue-500" />
            <span>
              Score is the percentile of wind-assisted drying potential against historical Singapore dry-weather conditions.
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}