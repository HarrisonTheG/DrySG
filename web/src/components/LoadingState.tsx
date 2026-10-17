export function LoadingState() {
  return (
    <section className="space-y-4" aria-label="Loading recommendation">
      <div className="h-64 animate-pulse rounded-3xl bg-slate-200" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-36 animate-pulse rounded-3xl bg-slate-200" />)}
      </div>
    </section>
  )
}