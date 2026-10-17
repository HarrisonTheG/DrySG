import { zodResolver } from "@hookform/resolvers/zod"
import { BellRing, CheckCircle2, Mail, Square } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { format } from "date-fns"
import type { LaundrySession } from "@/types/api"

const schema = z.object({ email: z.string().email("Enter a valid email address") })
type Values = z.infer<typeof schema>

interface Props {
  session?: LaundrySession
  isStarting?: boolean
  isStopping?: boolean
  onStart: (email: string) => Promise<void>
  onStop: () => Promise<void>
}

export function MonitoringCard({ session, isStarting, isStopping, onStart, onStop }: Props) {
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