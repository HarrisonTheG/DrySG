import { Bell, CheckCircle2, CircleAlert, Loader2 } from "lucide-react"
import { formatSgt } from "@/lib/experience"
import type { ExperienceAlert } from "@/types/experience"

export type AlertStatus =
  | { kind: "shown" }
  | { kind: "sending" }
  | { kind: "sent"; email: string }
  | { kind: "not-sent"; message: string }

interface Props {
  alerts: ExperienceAlert[]
  statuses: Record<number, AlertStatus>
}

function StatusLine({ status }: { status: AlertStatus }) {
  switch (status.kind) {
    case "sending":
      return <span className="flex items-center gap-1.5 text-slate-600"><Loader2 className="size-3.5 animate-spin" />Sending the email...</span>
    case "sent":
      return <span className="flex items-center gap-1.5 text-emerald-700"><CheckCircle2 className="size-3.5" />Emailed to {status.email}</span>
    case "not-sent":
      return <span className="flex items-center gap-1.5 text-amber-800"><CircleAlert className="size-3.5" />Not emailed: {status.message}</span>
    case "shown":
      return <span className="text-slate-500">Turn on live alerts to get this by email.</span>
  }
}

/** The alerts raised so far in the replay, newest first. */
export function AlertLog({ alerts, statuses }: Props) {
  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm" aria-label="Alerts raised">
      <h3 className="flex items-center gap-2 text-lg font-bold text-slate-950"><Bell className="size-4 text-blue-600" />Alerts raised</h3>
      {alerts.length === 0 ? (
        <p className="mt-2 text-sm leading-6 text-slate-600">No alert yet. DrySG only emails when conditions get worse than when you started.</p>
      ) : (
        <ol className="mt-3 space-y-3">
          {[...alerts].reverse().map((alert) => (
            <li key={alert.frame} className="rounded-2xl bg-red-50 p-3 text-sm">
              <div className="font-semibold text-red-900">{alert.newVeto ? "Rain: bring your laundry in" : `Now ${alert.newBand}`}</div>
              <div className="mt-0.5 leading-5 text-slate-700">{alert.reason}</div>
              <div className="mt-1 text-xs text-slate-500">{formatSgt(alert.snapshotTs, "time")} SGT</div>
              <div className="mt-2 text-xs font-medium"><StatusLine status={statuses[alert.frame] ?? { kind: "shown" }} /></div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
