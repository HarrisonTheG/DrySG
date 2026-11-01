import { useEffect, useRef, useState } from "react"
import { CircleAlert, CircleCheck, CircleX, LoaderCircle, Mail } from "lucide-react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { StatusCard, linkButton, primaryButton } from "@/components/StatusCard"
import { errorMessage, isApiError } from "@/api/axios"
import { startLaundrySession } from "@/api/monitoring"
import { registerEmail, verifyEmail } from "@/api/users"
import { readPendingStart, storePendingStart, storeSession } from "@/lib/storage"

type State =
  | { kind: "verifying" }
  | { kind: "verified"; email: string }
  | { kind: "invalid" }
  | { kind: "error"; message: string }

/** Opened from the verification email: /verify?email=...&token=... */
export function VerifyPage() {
  const [params] = useSearchParams()
  const email = params.get("email") ?? ""
  const token = params.get("token") ?? ""
  const [state, setState] = useState<State>(() => (email && token ? { kind: "verifying" } : { kind: "invalid" }))
  const [attempt, setAttempt] = useState(0)
  // Verifying is idempotent, but this avoids a second request in React's dev double-run.
  const requested = useRef(-1)

  useEffect(() => {
    if (!email || !token || requested.current === attempt) return
    requested.current = attempt
    verifyEmail(email, token)
      .then((status) => setState({ kind: "verified", email: status.email }))
      .catch((err) =>
        setState(
          isApiError(err) && err.status === 400
            ? { kind: "invalid" }
            : { kind: "error", message: errorMessage(err) },
        ),
      )
  }, [email, token, attempt])

  switch (state.kind) {
    case "verifying":
      return <StatusCard tone="neutral" icon={LoaderCircle} spin title="Confirming your email…" />
    case "verified":
      return <Verified email={state.email} />
    case "invalid":
      return <Invalid initialEmail={email} onVerified={(verified) => setState({ kind: "verified", email: verified })} />
    case "error":
      return (
        <StatusCard tone="error" icon={CircleAlert} title="Couldn't confirm your email">
          <p>{state.message}</p>
          <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-3">
            <button
              type="button"
              className={primaryButton}
              onClick={() => {
                setState({ kind: "verifying" })
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

function Verified({ email }: { email: string }) {
  const navigate = useNavigate()
  // A start this browser was waiting on (see MonitoringCard), for this email.
  const [pending] = useState(readPendingStart)
  const [isStarting, setIsStarting] = useState(false)
  const canStart = pending?.email === email

  async function start() {
    if (!pending) return
    setIsStarting(true)
    try {
      const session = await startLaundrySession({ email, postalCode: pending.postalCode })
      storeSession(session)
      storePendingStart(undefined)
      toast.success("Laundry monitoring started.")
      navigate("/")
    } catch (err) {
      toast.error(errorMessage(err, "Could not start monitoring."))
      setIsStarting(false)
    }
  }

  return (
    <StatusCard tone="success" icon={CircleCheck} title="Email confirmed">
      <p>
        <span className="font-medium text-slate-800">{email}</span> can now receive DrySG alerts.
        {!canStart && " Go back to DrySG to start monitoring your laundry."}
      </p>
      <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-3">
        {canStart && (
          <button type="button" className={primaryButton} disabled={isStarting} onClick={start}>
            {isStarting ? "Starting..." : `Start monitoring ${pending.postalCode}`}
          </button>
        )}
        <Link to="/" className={canStart ? linkButton : primaryButton}>Go to DrySG</Link>
      </div>
    </StatusCard>
  )
}

function Invalid({ initialEmail, onVerified }: { initialEmail: string; onVerified: (email: string) => void }) {
  const [email, setEmail] = useState(initialEmail)
  const [sentTo, setSentTo] = useState<string>()
  const [isSending, setIsSending] = useState(false)

  async function resend(event: React.FormEvent) {
    event.preventDefault()
    setIsSending(true)
    try {
      const status = await registerEmail(email)
      if (status.verified) {
        onVerified(status.email)
        return
      }
      setSentTo(status.email)
      const pending = readPendingStart()
      if (pending?.email === status.email) storePendingStart({ ...pending, sentAt: Date.now() })
    } catch (err) {
      toast.error(errorMessage(err, "Could not send the confirmation email."))
    } finally {
      setIsSending(false)
    }
  }

  if (sentTo) {
    return (
      <StatusCard tone="info" icon={Mail} title="Check your inbox">
        <p>
          We sent a new confirmation link to <span className="font-medium text-slate-800">{sentTo}</span>. It expires in 24 hours.
        </p>
        <Link to="/" className={`mt-5 inline-block ${linkButton}`}>Go to DrySG</Link>
      </StatusCard>
    )
  }

  return (
    <StatusCard tone="error" icon={CircleX} title="Link invalid or expired">
      <p>This confirmation link has expired or was replaced by a newer one. We can send you a new link.</p>
      <form onSubmit={resend} className="mt-5 flex flex-col gap-3">
        <label htmlFor="verify-email" className="sr-only">Email</label>
        <input
          id="verify-email"
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@example.com"
          className="h-12 w-full rounded-2xl border border-slate-300 bg-white px-4 outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
        />
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
          <button type="submit" className={primaryButton} disabled={isSending}>
            {isSending ? "Sending..." : "Send a new link"}
          </button>
          <Link to="/" className={linkButton}>Go to DrySG</Link>
        </div>
      </form>
    </StatusCard>
  )
}
