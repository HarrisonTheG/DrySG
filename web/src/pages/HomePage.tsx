import { useEffect, useState } from "react"
import { toast } from "sonner"
import { PostalCodeForm } from "@/components/PostalCodeForm"
import { DryingScoreCard } from "@/components/DryingScoreCard"
import { WeatherGrid } from "@/components/WeatherGrid"
import { AirQualityCard } from "@/components/AirQualityCard"
import { MonitoringCard } from "@/components/MonitoringCard"
import { StatusInfo } from "@/components/StatusInfo"
import { Methodology } from "@/components/Methodology"
import { EmptyState } from "@/components/EmptyState"
import { LoadingState } from "@/components/LoadingState"
import { ErrorState } from "@/components/ErrorState"
import { useRecommendation } from "@/hooks/useRecommendation"
import { errorMessage, isApiError } from "@/api/axios"
import { getLaundrySession, startLaundrySession, stopLaundrySession } from "@/api/monitoring"
import { registerEmail } from "@/api/users"
import {
  PENDING_KEY,
  SESSION_KEY,
  readPendingStart,
  readStoredSession,
  storePendingStart,
  storeSession,
  type PendingStart,
} from "@/lib/storage"
import type { LaundrySession } from "@/types/api"

export function HomePage() {
  const [session, setSession] = useState<LaundrySession | undefined>(readStoredSession)
  const [pending, setPending] = useState<PendingStart | undefined>(readPendingStart)
  // Reopen the postal code the user is monitoring (or waiting to monitor).
  const [postalCode, setPostalCode] = useState(
    () => readStoredSession()?.postalCode ?? readPendingStart()?.postalCode ?? "",
  )
  const [isStarting, setIsStarting] = useState(false)
  const [isStopping, setIsStopping] = useState(false)
  const [isResending, setIsResending] = useState(false)
  const [notConfirmedYet, setNotConfirmedYet] = useState(false)

  const { data, error, isLoading, mutate } = useRecommendation(postalCode)

  function updateSession(next: LaundrySession | undefined) {
    setSession(next)
    storeSession(next)
  }

  function updatePending(next: PendingStart | undefined) {
    setPending(next)
    storePendingStart(next)
  }

  // The stored session may have been stopped from an email link or expired meanwhile.
  useEffect(() => {
    const stored = readStoredSession()
    if (!stored) return
    let ignore = false
    getLaundrySession(stored.id)
      .then((fresh) => {
        if (ignore) return
        const next = fresh.active ? fresh : undefined
        setSession(next)
        storeSession(next)
      })
      .catch((err) => {
        if (ignore || !isApiError(err) || (err.status !== 400 && err.status !== 404)) return
        setSession(undefined)
        storeSession(undefined)
      })
    return () => {
      ignore = true
    }
  }, [])

  // Follow changes made in another tab, e.g. monitoring started from the /verify page.
  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (event.key === SESSION_KEY) setSession(readStoredSession())
      if (event.key === PENDING_KEY) setPending(readPendingStart())
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [])

  async function sendVerification(email: string, postal: string) {
    try {
      const status = await registerEmail(email)
      if (status.verified) {
        toast.info("Your email is already confirmed. Press Start monitoring again.")
        return
      }
      updatePending({ email: status.email, postalCode: postal, sentAt: Date.now() })
    } catch (err) {
      if (isApiError(err, "RESEND_TOO_SOON")) {
        // A link was sent within the last minute, so the inbox already has one.
        updatePending({ email, postalCode: postal, sentAt: Date.now() })
      } else {
        toast.error(errorMessage(err, "Could not send the confirmation email."))
      }
    }
  }

  async function handleStart(email: string, { confirming = false } = {}) {
    if (!data) return
    setIsStarting(true)
    setNotConfirmedYet(false)
    try {
      const created = await startLaundrySession({ postalCode: data.postalCode, email })
      updateSession(created)
      updatePending(undefined)
      toast.success("Laundry monitoring started.")
    } catch (err) {
      if (isApiError(err, "EMAIL_NOT_VERIFIED")) {
        if (confirming) setNotConfirmedYet(true)
        else await sendVerification(email, data.postalCode)
      } else {
        toast.error(errorMessage(err, "Could not start monitoring."))
      }
    } finally {
      setIsStarting(false)
    }
  }

  async function handleResend() {
    if (!pending) return
    setIsResending(true)
    try {
      const status = await registerEmail(pending.email)
      if (status.verified) {
        toast.success("Your email is already confirmed. You can start monitoring.")
      } else {
        updatePending({ ...pending, sentAt: Date.now() })
        toast.success("We sent you a new confirmation link.")
      }
    } catch (err) {
      if (isApiError(err, "RESEND_TOO_SOON")) updatePending({ ...pending, sentAt: Date.now() })
      toast.error(errorMessage(err, "Could not send the confirmation email."))
    } finally {
      setIsResending(false)
    }
  }

  function handleChangeEmail() {
    updatePending(undefined)
    setNotConfirmedYet(false)
  }

  async function handleStop() {
    if (!session) return
    setIsStopping(true)
    try {
      await stopLaundrySession(session.id)
      updateSession(undefined)
      toast.success("Monitoring stopped.")
    } catch (err) {
      if (isApiError(err, "SESSION_NOT_FOUND")) updateSession(undefined)
      toast.error(errorMessage(err, "Could not stop monitoring."))
    } finally {
      setIsStopping(false)
    }
  }

  return (
    <>
      <section className="mb-8 max-w-3xl">
        <div className="inline-flex rounded-full bg-blue-100 px-3 py-1 text-xs font-bold uppercase tracking-wider text-blue-700">Singapore outdoor laundry assistant</div>
        <h1 className="mt-4 text-4xl font-bold tracking-tight text-slate-950 sm:text-5xl">
          Know when to hang.<br /><span className="text-blue-600">Know when to bring it in.</span>
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
          DrySG turns nearby NEA observations and short-term forecasts into one simple recommendation, then keeps watching after you step away.
        </p>
      </section>

      <div className="space-y-5">
        <PostalCodeForm defaultValue={postalCode} loading={isLoading} onSubmit={setPostalCode} />

        {!postalCode && <EmptyState />}
        {postalCode && isLoading && <LoadingState />}
        {postalCode && error && <ErrorState message={errorMessage(error)} onRetry={() => mutate()} />}

        {data && (
          <>
            <DryingScoreCard recommendation={data} />
            <WeatherGrid conditions={data.conditions} />
            <div className="grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
              <AirQualityCard recommendation={data} />
              <MonitoringCard
                session={session}
                pending={pending}
                notConfirmedYet={notConfirmedYet}
                isStarting={isStarting}
                isStopping={isStopping}
                isResending={isResending}
                onStart={handleStart}
                onStop={handleStop}
                onConfirmed={() => (pending ? handleStart(pending.email, { confirming: true }) : Promise.resolve())}
                onResend={handleResend}
                onChangeEmail={handleChangeEmail}
              />
            </div>
            <StatusInfo recommendation={data} />
          </>
        )}

        <Methodology />
      </div>
    </>
  )
}
