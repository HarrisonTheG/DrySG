import { useEffect, useState } from "react"
import { CircleAlert, CircleCheck, CircleX, Info, LoaderCircle, Shirt } from "lucide-react"
import { format } from "date-fns"
import { Link, useSearchParams } from "react-router-dom"
import { StatusCard, linkButton, primaryButton } from "@/components/StatusCard"
import { errorMessage, isApiError, type ApiError } from "@/api/axios"
import { getLaundrySession, stopLaundrySession } from "@/api/monitoring"
import { readStoredSession, storeSession } from "@/lib/storage"
import type { LaundrySession } from "@/types/api"

type State =
  | { kind: "loading" }
  | { kind: "confirm"; session: LaundrySession }
  | { kind: "stopped" }
  | { kind: "ended" }
  | { kind: "invalid" }
  | { kind: "error"; message: string }

function isBadLink(err: unknown): err is ApiError {
  return isApiError(err) && (err.status === 400 || err.status === 404)
}

/**
 * Opened from the stop link in an alert email: /stop?session=<id>.
 * Stopping needs a click, because email link scanners open links automatically.
 */
export function StopPage() {
  const [params] = useSearchParams()
  const id = params.get("session") ?? ""
  const [state, setState] = useState<State>(() => (id ? { kind: "loading" } : { kind: "invalid" }))
  const [attempt, setAttempt] = useState(0)
  const [isStopping, setIsStopping] = useState(false)

  useEffect(() => {
    if (!id) return
    let ignore = false
    getLaundrySession(id)
      .then((session) => {
        if (!ignore) setState(session.active ? { kind: "confirm", session } : { kind: "ended" })
      })
      .catch((err) => {
        if (!ignore) setState(isBadLink(err) ? { kind: "invalid" } : { kind: "error", message: errorMessage(err) })
      })
    return () => {
      ignore = true
    }
  }, [id, attempt])

  async function stop() {
    setIsStopping(true)
    try {
      await stopLaundrySession(id)
      if (readStoredSession()?.id === id) storeSession(undefined)
      setState({ kind: "stopped" })
    } catch (err) {
      setState(isBadLink(err) ? { kind: "invalid" } : { kind: "error", message: errorMessage(err) })
    } finally {
      setIsStopping(false)
    }
  }

  const home = <Link to="/" className={`mt-5 inline-block ${linkButton}`}>Go to DrySG</Link>

  switch (state.kind) {
    case "loading":
      return <StatusCard tone="neutral" icon={LoaderCircle} spin title="Loading your monitoring session…" />
    case "confirm":
      return (
        <StatusCard tone="info" icon={Shirt} title={`Stop monitoring ${state.session.areaName}?`}>
          <p>
            Started {format(new Date(state.session.startedAt), "h:mm a")} · {state.session.email}
          </p>
          <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-3">
            <button type="button" className={primaryButton} disabled={isStopping} onClick={stop}>
              {isStopping ? "Stopping..." : "Yes, my laundry is in"}
            </button>
            <Link to="/" className={linkButton}>Keep monitoring</Link>
          </div>
        </StatusCard>
      )
    case "stopped":
      return (
        <StatusCard tone="success" icon={CircleCheck} title="Monitoring stopped">
          <p>We won't email you about this laundry anymore.</p>
          {home}
        </StatusCard>
      )
    case "ended":
      return (
        <StatusCard tone="neutral" icon={Info} title="Monitoring already ended">
          <p>This monitoring session was already stopped or has expired, so there's nothing to do.</p>
          {home}
        </StatusCard>
      )
    case "invalid":
      return (
        <StatusCard tone="error" icon={CircleX} title="This link isn't valid">
          <p>We couldn't find this monitoring session. Check that you opened the full link from the email.</p>
          {home}
        </StatusCard>
      )
    case "error":
      return (
        <StatusCard tone="error" icon={CircleAlert} title="Couldn't load your session">
          <p>{state.message}</p>
          <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-3">
            <button
              type="button"
              className={primaryButton}
              onClick={() => {
                setState({ kind: "loading" })
                setAttempt((n) => n + 1)
              }}
            >
              Try again
            </button>
            <Link to="/" className={linkButton}>Go to DrySG</Link>
          </div>
        </StatusCard>
      )
  }
}
