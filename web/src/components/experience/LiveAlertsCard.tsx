import { BellRing, Mail, MailCheck } from "lucide-react"

export type LiveState = "off" | "checking" | "needs-confirmation" | "ready"

interface Props {
  state: LiveState
  email: string
  emailError?: string
  onEmailChange: (email: string) => void
  onToggle: (on: boolean) => void
  onRecheck: () => void
}

export function LiveAlertsCard({ state, email, emailError, onEmailChange, onToggle, onRecheck }: Props) {
  const on = state !== "off"

  return (
    <section className={`rounded-3xl border p-5 shadow-sm ${state === "ready" ? "border-emerald-200 bg-emerald-50" : "border-slate-200 bg-white"}`}>
      <div className="flex items-start gap-3">
        <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-blue-100 text-blue-700"><BellRing className="size-5" /></div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 id="live-alerts-label" className="text-lg font-bold text-slate-950">Send live alerts</h3>
              <p className="mt-1 text-sm leading-6 text-slate-600">
                Get the real alert email DrySG would send when it fires during a replay. Off by default.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={on}
              aria-labelledby="live-alerts-label"
              disabled={state === "checking"}
              onClick={() => onToggle(!on)}
              className={`relative mt-1 h-7 w-12 shrink-0 rounded-full transition disabled:opacity-60 ${on ? "bg-blue-600" : "bg-slate-300"}`}
            >
              <span className={`absolute left-0.5 top-0.5 size-6 rounded-full bg-white shadow transition-transform ${on ? "translate-x-5" : ""}`} />
            </button>
          </div>

          <label htmlFor="experience-email" className="mt-4 block text-sm font-semibold text-slate-800">Email for alerts</label>
          <div className="relative mt-2">
            <Mail className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input
              id="experience-email"
              type="email"
              placeholder="you@example.com"
              value={email}
              disabled={on}
              onChange={(event) => onEmailChange(event.target.value)}
              className="h-11 w-full rounded-2xl border border-slate-300 bg-white pl-10 pr-4 outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100 disabled:bg-slate-50 disabled:text-slate-500"
            />
          </div>
          {emailError && <p className="mt-2 text-sm text-red-600">{emailError}</p>}

          {state === "checking" && <p className="mt-3 text-sm text-slate-600">Checking your email...</p>}
          {state === "ready" && (
            <p className="mt-3 flex items-start gap-2 text-sm font-medium text-emerald-800">
              <MailCheck className="mt-0.5 size-4 shrink-0" />
              <span>On. Alerts raised in a replay will be emailed to {email}.</span>
            </p>
          )}
          {state === "needs-confirmation" && (
            <div className="mt-3 rounded-2xl bg-blue-50 px-3 py-3 text-sm leading-6 text-slate-700">
              We sent a confirmation link to <span className="font-medium text-slate-900">{email}</span>. Open it, then come back
              and press the button. We only email addresses that have confirmed.
              <button
                type="button"
                onClick={onRecheck}
                className="mt-2 block rounded-xl bg-slate-950 px-3 py-2 font-semibold text-white transition hover:bg-slate-800"
              >
                I've confirmed
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
