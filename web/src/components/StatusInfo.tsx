import { Clock3, Database, MapPinned } from "lucide-react"
import { formatDistanceToNowStrict } from "date-fns"
import type { DryingRecommendation } from "@/types/api"

export function StatusInfo({ recommendation }: { recommendation: DryingRecommendation }) {
  const ageMinutes = Math.floor((Date.now() - new Date(recommendation.updatedAt).getTime()) / 60_000)
  const stale = ageMinutes > 10

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="grid gap-4 text-sm sm:grid-cols-3">
        <div className="flex items-start gap-3">
          <Clock3 className="mt-0.5 size-4 text-slate-400" />
          <div>
            <div className="font-semibold text-slate-800">Data freshness</div>
            <div className={stale ? "text-amber-700" : "text-slate-500"}>
              {stale ? "Weather data may be delayed" : `Updated ${formatDistanceToNowStrict(new Date(recommendation.updatedAt), { addSuffix: true })}`}
            </div>
          </div>
        </div>

        <div className="flex items-start gap-3">
          <MapPinned className="mt-0.5 size-4 text-slate-400" />
          <div>
            <div className="font-semibold text-slate-800">Station mapping</div>
            <div className="text-slate-500">
              Rain {recommendation.stationSources?.rainfall ?? "-"}, humidity {recommendation.stationSources?.humidity ?? "-"}, temp {recommendation.stationSources?.temperature ?? "-"}, wind {recommendation.stationSources?.wind ?? "-"}
            </div>
          </div>
        </div>

        <div className="flex items-start gap-3">
          <Database className="mt-0.5 size-4 text-slate-400" />
          <div>
            <div className="font-semibold text-slate-800">Shared snapshot</div>
            <div className="text-slate-500">Weather is ingested centrally and reused across users.</div>
          </div>
        </div>
      </div>
    </section>
  )
}