import { CircleAlert } from "lucide-react"

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <section className="rounded-3xl border border-red-200 bg-red-50 p-5">
      <div className="flex items-start gap-3">
        <CircleAlert className="mt-0.5 size-5 text-red-600" />
        <div>
          <h2 className="font-bold text-red-900">Unable to load conditions</h2>
          <p className="mt-1 text-sm leading-6 text-red-800">{message}</p>
          {onRetry && <button onClick={onRetry} className="mt-3 rounded-xl bg-red-700 px-3 py-2 text-sm font-semibold text-white">Try again</button>}
        </div>
      </div>
    </section>
  )
}