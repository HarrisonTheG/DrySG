import { CloudSun } from "lucide-react"

export function EmptyState() {
  return (
    <section className="rounded-3xl border border-dashed border-slate-300 bg-white/70 px-6 py-12 text-center">
      <div className="mx-auto grid size-14 place-items-center rounded-2xl bg-blue-100 text-blue-700"><CloudSun className="size-7" /></div>
      <h2 className="mt-4 text-xl font-bold text-slate-950">Check your local drying conditions</h2>
      <p className="mx-auto mt-2 max-w-lg leading-7 text-slate-600">
        Enter a Singapore postal code to get a location-aware recommendation using nearby observations, the NEA forecast and regional PSI.
      </p>
      <p className="mt-4 text-xs text-slate-400">Mock demo: endings 0-2 = Hang Now, 3 = PSI warning, 4-6 = Wait, 7-9 = rain veto.</p>
    </section>
  )
}