import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

const tones = {
  info: { card: "border-blue-200 bg-blue-50", icon: "bg-blue-100 text-blue-700" },
  success: { card: "border-emerald-200 bg-emerald-50", icon: "bg-emerald-100 text-emerald-700" },
  error: { card: "border-red-200 bg-red-50", icon: "bg-red-100 text-red-700" },
  neutral: { card: "border-slate-200 bg-white", icon: "bg-slate-100 text-slate-600" },
}

interface Props {
  tone: keyof typeof tones
  icon: LucideIcon
  title: string
  spin?: boolean
  children?: ReactNode
}

/** The single centred card used by the /verify and /stop pages. */
export function StatusCard({ tone, icon: Icon, title, spin, children }: Props) {
  const style = tones[tone]
  return (
    <section className={`mx-auto max-w-lg rounded-3xl border p-6 shadow-sm sm:p-8 ${style.card}`} aria-live="polite">
      <div className={`grid size-12 place-items-center rounded-2xl ${style.icon}`}>
        <Icon className={`size-6 ${spin ? "animate-spin" : ""}`} />
      </div>
      <h1 className="mt-4 text-2xl font-bold tracking-tight text-slate-950">{title}</h1>
      <div className="mt-2 text-sm leading-6 text-slate-600">{children}</div>
    </section>
  )
}

export const primaryButton =
  "inline-flex h-12 items-center justify-center rounded-2xl bg-slate-950 px-5 font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
export const linkButton = "font-semibold text-blue-700 hover:underline"
