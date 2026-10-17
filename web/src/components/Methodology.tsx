export function Methodology() {
  return (
    <section id="methodology" className="rounded-3xl border border-slate-200 bg-slate-950 p-6 text-slate-100 shadow-sm sm:p-8">
      <div className="max-w-4xl">
        <div className="text-sm font-semibold uppercase tracking-widest text-blue-300">Methodology</div>
        <h2 className="mt-2 text-2xl font-bold tracking-tight">Physics-based drying potential, not arbitrary weights</h2>
        <p className="mt-3 leading-7 text-slate-300">
          The backend derives vapour pressure deficit from temperature and relative humidity, applies a wind-assisted Penman-style aerodynamic factor, then converts the resulting Drying Potential into a percentile against historical Singapore dry-weather conditions.
        </p>

        {/* <div className="mt-6 grid gap-3 md:grid-cols-3">
          <div className="rounded-2xl border border-slate-700 bg-slate-900 p-4">
            <div className="text-sm text-slate-400">1. Vapour pressure</div>
            <div className="mt-2 font-mono text-sm text-white">Es(T)=0.6108×exp(17.27T/(T+237.3))</div>
            <div className="mt-2 font-mono text-sm text-white">VPD=Es(T)×(1-RH/100)</div>
          </div>
          <div className="rounded-2xl border border-slate-700 bg-slate-900 p-4">
            <div className="text-sm text-slate-400">2. Wind-assisted potential</div>
            <div className="mt-2 font-mono text-sm text-white">DP=(1+0.536u)×VPD</div>
            <div className="mt-2 text-sm text-slate-400">u is wind speed in m/s.</div>
          </div>
          <div className="rounded-2xl border border-slate-700 bg-slate-900 p-4">
            <div className="text-sm text-slate-400">3. Singapore percentile</div>
            <div className="mt-2 font-mono text-sm text-white">Score=100×F(DP)</div>
            <div className="mt-2 text-sm text-slate-400">F(DP) is the historical empirical percentile.</div>
          </div>
        </div> */}

        <div className="mt-5 rounded-2xl border border-red-900/70 bg-red-950/40 p-4 text-sm leading-6 text-red-100">
          Current rain is a hard veto: the backend returns DO NOT HANG even if the DP percentile is high. PSI is a separate air-quality warning and does not change the physical Drying Score.
        </div>
      </div>
    </section>
  )
}