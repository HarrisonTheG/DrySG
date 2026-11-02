import { zodResolver } from "@hookform/resolvers/zod"
import { BellRing, CheckCircle2, Mail, MailCheck, Square } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { format } from "date-fns"
import { useNow } from "@/hooks/useNow"
import type { PendingStart } from "@/lib/storage"
import type { LaundrySession, RecommendationState } from "@/types/api"

const schema = z.object({ email: z.string().email("Enter a valid email address") })
type Values = z.infer<typeof schema>

/** The backend allows one verification email per address per minute. */
const RESEND_INTERVAL_MS = 60_000

interface Props {
  session?: LaundrySession
  /** Current conditions, to warn when starting now would not alert straight away. */
  rainVeto?: boolean
  recommendation?: RecommendationState
  pending?: PendingStart
  notConfirmedYet?: boolean
  isStarting?: boolean
  isStopping?: boolean
  isResending?: boolean
  onStart: (email: string) => Promise<void>
  onStop: () => Promise<void>
  onConfirmed: () => Promise<void>
  onResend: () => Promise<void>
  onChangeEmail: () => void
}

export function MonitoringCard({
  session,
  rainVeto,
  recommendation,
  pending,
  notConfirmedYet,
  isStarting,
  isStopping,
  isResending,
  onStart,
  onStop,
  onConfirmed,
  onResend,
  onChangeEmail,
}: Props) {
  const { register, handleSubmit, formState: { errors } } = useForm<Values>({ resolver: zodResolver(schema) })

  if (session?.active) {
    return (
      <section className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5 shadow-sm">
        <div className="flex items-start gap-3">
          <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-emerald-100 text-emerald-700">
            <CheckCircle2 className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold uppercase tracking-wide text-emerald-700">Monitoring active</div>
            <h3 className="mt-1 text-xl font-bold text-slate-950">Your laundry is outside</h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              We are watching {session.areaName} and will email <span className="font-medium text-slate-800">{session.email}</span> if the backend recommendation deteriorates.
            </p>
            <div className="mt-4 grid gap-2 text-sm text-slate-600 sm:grid-cols-2">
              <div>Started <span className="font-medium text-slate-900">{format(new Date(session.startedAt), "h:mm a")}</span></div>
              <div>Auto-expires <span className="font-medium text-slate-900">{format(new Date(session.expiresAt), "h:mm a")}</span></div>
            </div>
            <button
              type="button"
              onClick={onStop}
              disabled={isStopping}
              className="mt-5 inline-flex items-center gap-2 rounded-2xl border border-emerald-300 bg-white px-4 py-2.5 text-sm font-semibold text-emerald-800 transition hover:bg-emerald-100 disabled:opacity-60"
            >
              <Square className="size-4" />
              {isStopping ? "Stopping..." : "Laundry is back inside"}
            </button>
          </div>
        </div>
      </section>
    )
  }

  if (pending) {
    return (
      <CheckInbox
        pending={pending}
        notConfirmedYet={notConfirmedYet}
        isStarting={isStarting}
        isResending={isResending}
        onConfirmed={onConfirmed}
        onResend={onResend}
        onChangeEmail={onChangeEmail}
      />
    )
  }

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-blue-100 text-blue-700">
          <BellRing className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-xl font-bold text-slate-950">Is your laundry outside?</h3>
          <p className="mt-1 text-sm leading-6 text-slate-600">
            Start monitoring and DrySG will email you if rain begins or the recommendation changes to a worse state.
          </p>
          {(rainVeto || recommendation === "DO_NOT_HANG") && (
            <p className="mt-3 rounded-2xl bg-amber-100 px-3 py-2 text-sm leading-6 text-amber-900">
              {rainVeto
                ? "It's raining now or rain is forecast. Start monitoring when you hang your laundry. We'll still email you if rain returns after a dry spell."
                : "Drying conditions are poor right now. We'll email you if rain starts, or if conditions improve and then get worse again."}
            </p>
          )}
          <form onSubmit={handleSubmit(({ email }) => onStart(email))} className="mt-5">
             <label htmlFor="email" className="mb-2 block text-sm font-semibold text-slate-800">Email for alerts</label>
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="relative flex-1">
                <Mail className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                <input id="email" type="email" placeholder="you@example.com" {...register("email")} className="h-12 w-full rounded-2xl border border-slate-300 pl-10 pr-4 outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100" />
              </div>
              <button type="submit" disabled={isStarting} className="h-12 rounded-2xl bg-slate-950 px-5 font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60">
                {isStarting ? "Starting..." : "Start monitoring"}
              </button>
            </div>
            {errors.email && <p className="mt-2 text-sm text-red-600">{errors.email.message}</p>}
          </form>
        </div>
      </div>
    </section>
  )
}

function CheckInbox({
  pending,
  notConfirmedYet,
  isStarting,
  isResending,
  onConfirmed,
  onResend,
  onChangeEmail,
}: Pick<Props, "notConfirmedYet" | "isStarting" | "isResending" | "onConfirmed" | "onResend" | "onChangeEmail"> & { pending: PendingStart }) {
  const now = useNow(1000)
  const waitSeconds = Math.max(0, Math.ceil((pending.sentAt + RESEND_INTERVAL_MS - now) / 1000))
  const countdown = `${Math.floor(waitSeconds / 60)}:${String(waitSeconds % 60).padStart(2, "0")}`

  return (
    <section className="rounded-3xl border border-blue-200 bg-blue-50 p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-blue-100 text-blue-700">
          <MailCheck className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold uppercase tracking-wide text-blue-700">Confirm your email</div>
          <h3 className="mt-1 text-xl font-bold text-slate-950">Check your inbox</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            We sent a confirmation link to <span className="font-medium text-slate-800">{pending.email}</span>. Open it, then come back here, or start monitoring from that page.
          </p>
          {notConfirmedYet && (
            <p className="mt-3 rounded-2xl bg-amber-100 px-3 py-2 text-sm text-amber-900">
              Not confirmed yet. Check your inbox, including the junk folder.
            </p>
          )}
          <button
            type="button"
            onClick={onConfirmed}
            disabled={isStarting}
            className="mt-5 h-12 rounded-2xl bg-slate-950 px-5 font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
          >
            {isStarting ? "Starting..." : "I've confirmed – start monitoring"}
          </button>
          <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <button
              type="button"
              onClick={onResend}
              disabled={waitSeconds > 0 || isResending}
              className="font-semibold text-blue-700 hover:underline disabled:cursor-not-allowed disabled:text-slate-400 disabled:no-underline"
            >
              {isResending ? "Sending..." : waitSeconds > 0 ? `Resend email (available in ${countdown})` : "Resend email"}
            </button>
            <button type="button" onClick={onChangeEmail} className="font-semibold text-slate-600 hover:underline">
              Use a different email
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}
