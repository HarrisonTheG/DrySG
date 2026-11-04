import { useEffect, useMemo, useRef, useState } from "react"
import { History, Shuffle } from "lucide-react"
import { toast } from "sonner"
import { z } from "zod"
import { AirQualityCard } from "@/components/AirQualityCard"
import { DryingScoreCard } from "@/components/DryingScoreCard"
import { WeatherGrid } from "@/components/WeatherGrid"
import { AlertLog, type AlertStatus } from "@/components/experience/AlertLog"
import { CategoryPicker } from "@/components/experience/CategoryPicker"
import { LiveAlertsCard, type LiveState } from "@/components/experience/LiveAlertsCard"
import { ReplayControls } from "@/components/experience/ReplayControls"
import { errorMessage, isApiError } from "@/api/axios"
import { sendExperienceAlert } from "@/api/experience"
import { registerEmail } from "@/api/users"
import { usePlayback } from "@/hooks/usePlayback"
import { CATEGORIES, formatSgt, frameFor, pickPostalCode, pickScenario, type Speed } from "@/lib/experience"
import { readExperienceEmail, storeExperienceEmail } from "@/lib/storage"
import type { ExperienceAlert, ExperienceCategory, ExperienceScenario } from "@/types/experience"

const emailSchema = z.string().email("Enter a valid email address")
const STEP_MS = 2000

/**
 * Replays real hours of NEA history through the real result cards. Nothing here calls the
 * API unless "Send live alerts" is switched on, and then only to confirm the email and to
 * send the alert emails.
 */
export function ExperiencePage() {
  const [scenario, setScenario] = useState<ExperienceScenario>()
  const [postalCode, setPostalCode] = useState("")
  const [speed, setSpeed] = useState<Speed>(1)
  const [email, setEmail] = useState(readExperienceEmail)
  const [emailError, setEmailError] = useState<string>()
  const [live, setLive] = useState<LiveState>("off")
  const [statuses, setStatuses] = useState<Record<number, AlertStatus>>({})

  const frameCount = scenario?.frames.length ?? 1
  const playback = usePlayback(frameCount, STEP_MS / speed)
  const { frame } = playback

  const liveRef = useRef(live)
  useEffect(() => {
    liveRef.current = live
  }, [live])
  const fired = useRef(new Set<number>())
  const previousFrame = useRef(0)

  function open(next: ExperienceScenario | undefined, keepPostal = false) {
    if (!next) return
    setScenario(next)
    if (!keepPostal) setPostalCode(pickPostalCode(next))
    setStatuses({})
    fired.current.clear()
    previousFrame.current = 0
    playback.restart()
  }

  function pickCategory(category: ExperienceCategory) {
    open(pickScenario(category, scenario?.id))
  }

  function setStatus(alertFrame: number, status: AlertStatus) {
    setStatuses((current) => ({ ...current, [alertFrame]: status }))
  }

  async function deliver(from: ExperienceScenario, alert: ExperienceAlert) {
    if (liveRef.current !== "ready") return setStatus(alert.frame, { kind: "shown" })
    setStatus(alert.frame, { kind: "sending" })
    try {
      const sent = await sendExperienceAlert({ email, scenarioId: from.id, frame: alert.frame })
      setStatus(alert.frame, { kind: "sent", email: sent.email })
    } catch (err) {
      if (isApiError(err, "EMAIL_NOT_VERIFIED")) setLive("needs-confirmation")
      setStatus(alert.frame, { kind: "not-sent", message: errorMessage(err, "Could not send the email.") })
    }
  }

  // An alert fires only as playback reaches its frame, never when the slider jumps past it.
  useEffect(() => {
    const advanced = frame === previousFrame.current + 1
    previousFrame.current = frame
    if (frame === 0) fired.current.clear()
    if (!advanced || !scenario) return
    const alert = scenario.alerts.find((a) => a.frame === frame)
    if (alert && !fired.current.has(frame)) {
      fired.current.add(frame)
      void deliver(scenario, alert)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame])

  /** Confirms the address with the same flow as monitoring: verified addresses get no email. */
  async function checkEmail(notifyIfPending: boolean) {
    setLive("checking")
    try {
      const status = await registerEmail(email.trim())
      setLive(status.verified ? "ready" : "needs-confirmation")
      if (status.verified) toast.success("Live alerts are on.")
      else if (notifyIfPending) toast.info("Not confirmed yet. Check your inbox, including junk.")
    } catch (err) {
      if (isApiError(err, "RESEND_TOO_SOON")) {
        setLive("needs-confirmation")
        if (notifyIfPending) toast.info("Not confirmed yet. Check your inbox, including junk.")
        return
      }
      setLive("off")
      toast.error(errorMessage(err, "Could not turn on live alerts."))
    }
  }

  function toggleLive(on: boolean) {
    if (!on) {
      setLive("off")
      return
    }
    const parsed = emailSchema.safeParse(email.trim())
    if (!parsed.success) {
      setEmailError(parsed.error.issues[0].message)
      return
    }
    setEmailError(undefined)
    storeExperienceEmail(parsed.data)
    void checkEmail(false)
  }

  const alertsSoFar = useMemo(
    () => (scenario ? scenario.alerts.filter((a) => a.frame <= frame) : []),
    [scenario, frame],
  )
  const category = scenario && CATEGORIES.find((c) => c.id === scenario.category)
  const current = scenario ? frameFor(scenario, frame, postalCode) : undefined

  return (
    <>
      <section className="mb-5 max-w-3xl">
        <div className="inline-flex items-center gap-1.5 rounded-full bg-blue-100 px-3 py-1 text-xs font-bold uppercase tracking-wider text-blue-700">
          <History className="size-3.5" />
          Experience
        </div>
        <h1 className="mt-3 text-3xl font-bold tracking-tight text-slate-950 sm:text-4xl">
          Simulate <span className="text-blue-600">real scenarios from the past.</span>
        </h1>
        <p className="mt-2 max-w-2xl leading-7 text-slate-600">
          Relive real moments from NEA's records (sunshine, showers, downpours, haze) and watch DrySG's recommendation and alerts react. Scored by the same code as the live service, with no live data needed.
        </p>
      </section>

      <CategoryPicker selected={scenario?.category} onPick={pickCategory} />

      <div className="grid items-start gap-5 lg:grid-cols-[1fr_340px]">
        <div className="space-y-5">
          {!scenario && (
            <section className="rounded-3xl border border-dashed border-slate-300 bg-white/70 px-6 py-12 text-center">
              <h2 className="text-xl font-bold text-slate-950">Pick a kind of day above</h2>
              <p className="mx-auto mt-2 max-w-md leading-7 text-slate-600">
                Each one replays a real hour of past Singapore weather. Try "Rain arrives" to watch an alert fire.
              </p>
            </section>
          )}

          {scenario && current && (
            <>
              <section className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                <div>
                  <div className="text-sm font-semibold uppercase tracking-wide text-slate-500">{category?.title}</div>
                  <h2 className="mt-1 text-xl font-bold text-slate-950">
                    {scenario.area} · postal code {postalCode}
                  </h2>
                  <p className="mt-1 text-sm text-slate-600">{category?.blurb}</p>
                  <p className="mt-1 text-sm text-slate-500">
                    Real NEA readings from {formatSgt(scenario.frames[0].updatedAt, "dateTime")}, one hour in 5-minute steps.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => setPostalCode(pickPostalCode(scenario, postalCode))}
                    className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                  >
                    <Shuffle className="size-4" />
                    Another postal code
                  </button>
                  <button
                    type="button"
                    onClick={() => pickCategory(scenario.category)}
                    className="rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                  >
                    Another like this
                  </button>
                </div>
              </section>

              <ReplayControls
                scenario={scenario}
                frame={frame}
                playing={playback.playing}
                speed={speed}
                onPlay={playback.play}
                onPause={playback.pause}
                onRestart={() => open(scenario, true)}
                onStep={playback.step}
                onSeek={(to) => {
                  playback.pause()
                  playback.seek(to)
                }}
                onSpeed={setSpeed}
              />

              <DryingScoreCard recommendation={current} />
              <WeatherGrid conditions={current.conditions} />
              <AirQualityCard recommendation={current} />
            </>
          )}
        </div>

        <aside className="space-y-5 lg:sticky lg:top-[152px]">
          <LiveAlertsCard
            state={live}
            email={email}
            emailError={emailError}
            onEmailChange={(value) => {
              setEmail(value)
              setEmailError(undefined)
            }}
            onToggle={toggleLive}
            onRecheck={() => void checkEmail(true)}
          />
          {scenario && <AlertLog alerts={alertsSoFar} statuses={statuses} />}
        </aside>
      </div>
    </>
  )
}
