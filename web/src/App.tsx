import { useState } from "react"
import { Toaster, toast } from "sonner"
import { Header } from "@/components/Header"
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
import { startLaundrySession, stopLaundrySession } from "@/api/monitoring"
import type { LaundrySession } from "@/types/api"

const STORAGE_KEY = "drysg.active-session"

function readStoredSession(): LaundrySession | undefined {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value ? JSON.parse(value) : undefined
  } catch {
    return undefined
  }
}

export default function App() {
  const [postalCode, setPostalCode] = useState("")
  const [session, setSession] = useState<LaundrySession | undefined>(() => readStoredSession())
  const [isStarting, setIsStarting] = useState(false)
  const [isStopping, setIsStopping] = useState(false)

  const { data, error, isLoading, mutate } = useRecommendation(postalCode)

  async function handleStart(email: string) {
    if (!data) return
    setIsStarting(true)
    try {
      const created = await startLaundrySession({ postalCode: data.postalCode, email })
      setSession(created)
      localStorage.setItem(STORAGE_KEY, JSON.stringify(created))
      toast.success("Laundry monitoring started.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not start monitoring.")
    } finally {
      setIsStarting(false)
    }
  }

  async function handleStop() {
    if (!session) return
    setIsStopping(true)
    try {
      await stopLaundrySession(session.id)
      setSession(undefined)
      localStorage.removeItem(STORAGE_KEY)
      toast.success("Monitoring stopped.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not stop monitoring.")
    } finally {
      setIsStopping(false)
    }
  }
  return (
    <div className="min-h-screen">
      <Header />
      <Toaster richColors position="top-center" />

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
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
          <PostalCodeForm loading={isLoading} onSubmit={setPostalCode} />

          {!postalCode && <EmptyState />}
          {postalCode && isLoading && <LoadingState />}
          {postalCode && error && <ErrorState message={error.message} onRetry={() => mutate()} />}

          {data && (
            <>
              <DryingScoreCard recommendation={data} />
              <WeatherGrid conditions={data.conditions} />
              <div className="grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
                <AirQualityCard recommendation={data} />
                <MonitoringCard session={session} isStarting={isStarting} isStopping={isStopping} onStart={handleStart} onStop={handleStop} />
              </div>
              <StatusInfo recommendation={data} />
            </>
          )}

          <Methodology />
        </div>
      </main>

      <footer className="border-t border-slate-200 bg-white/70">
        <div className="mx-auto max-w-6xl px-4 py-6 text-sm text-slate-500 sm:px-6">
          DrySG prototype · CS5224 · Recommendations are estimates based on available environmental data.
        </div>
      </footer>
    </div>
  )
}