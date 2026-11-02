import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import MockAdapter from "axios-mock-adapter"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { api } from "@/api/axios"
import { PENDING_KEY, SESSION_KEY, storeSession } from "@/lib/storage"
import { HomePage } from "@/pages/HomePage"
import { EMAIL, POSTAL, apiError, recommendation, renderAt, session } from "@/test/fixtures"

const mock = new MockAdapter(api)
beforeEach(() => mock.onGet("/recommendation").reply(200, recommendation))
afterEach(() => mock.reset())

const stored = (key: string) => JSON.parse(localStorage.getItem(key) ?? "null")

async function searchPostal(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/where are you drying/i), POSTAL)
  await user.click(screen.getByRole("button", { name: /check conditions/i }))
}

describe("HomePage monitoring", () => {
  it("asks an unverified email to confirm, then starts monitoring", async () => {
    const user = userEvent.setup()
    renderAt("/", "/", <HomePage />)
    await searchPostal(user)
    await screen.findByText("Is your laundry outside?")

    mock.onPost("/laundry-sessions").replyOnce(403, apiError("EMAIL_NOT_VERIFIED"))
    mock.onPost("/users").replyOnce(202, { email: EMAIL, verified: false })
    await user.type(screen.getByLabelText(/email for alerts/i), EMAIL)
    await user.click(screen.getByRole("button", { name: "Start monitoring" }))

    await screen.findByText("Check your inbox")
    expect(screen.getByText(EMAIL)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /resend email \(available in/i })).toBeDisabled()
    expect(stored(PENDING_KEY)).toMatchObject({ email: EMAIL, postalCode: POSTAL })

    // Pressing "confirmed" before opening the link only shows a note; no second email.
    mock.onPost("/laundry-sessions").replyOnce(403, apiError("EMAIL_NOT_VERIFIED"))
    await user.click(screen.getByRole("button", { name: /i've confirmed/i }))
    await screen.findByText(/not confirmed yet/i)
    expect(mock.history.post.filter((request) => request.url === "/users")).toHaveLength(1)

    mock.onPost("/laundry-sessions").replyOnce(201, session)
    await user.click(screen.getByRole("button", { name: /i've confirmed/i }))
    await screen.findByText("Monitoring active")
    expect(stored(SESSION_KEY)).toMatchObject({ id: session.id })
    expect(stored(PENDING_KEY)).toBeNull()
  })

  it("stays on the form when the confirmation email cannot be sent", async () => {
    const user = userEvent.setup()
    renderAt("/", "/", <HomePage />)
    await searchPostal(user)
    await screen.findByText("Is your laundry outside?")

    mock.onPost("/laundry-sessions").replyOnce(403, apiError("EMAIL_NOT_VERIFIED"))
    mock.onPost("/users").replyOnce(503, apiError("EMAIL_UNAVAILABLE"))
    await user.type(screen.getByLabelText(/email for alerts/i), EMAIL)
    await user.click(screen.getByRole("button", { name: "Start monitoring" }))

    await waitFor(() => expect(mock.history.post).toHaveLength(2))
    await waitFor(() => expect(screen.getByRole("button", { name: "Start monitoring" })).toBeEnabled())
    expect(screen.queryByText("Check your inbox")).not.toBeInTheDocument()
    expect(stored(PENDING_KEY)).toBeNull()
  })

  it("reopens a stored session and clears it once it has ended", async () => {
    storeSession(session)
    mock.onGet(`/laundry-sessions/${session.id}`).reply(200, { ...session, active: false })
    renderAt("/", "/", <HomePage />)

    expect(screen.getByLabelText(/where are you drying/i)).toHaveValue(POSTAL)
    await waitFor(() => expect(stored(SESSION_KEY)).toBeNull())
    expect(await screen.findByText("Is your laundry outside?")).toBeInTheDocument()
  })

  it("shows a missing reading as a dash instead of crashing", async () => {
    mock.reset()
    mock.onGet("/recommendation").reply(200, {
      ...recommendation,
      degraded: true,
      conditions: { ...recommendation.conditions, temperatureC: null, psi: null, psiBand: null },
    })
    const user = userEvent.setup()
    renderAt("/", "/", <HomePage />)
    await searchPostal(user)

    expect(await screen.findByText("Estimate")).toBeInTheDocument()
    expect(screen.getByText("PSI —")).toBeInTheDocument()
    expect(screen.getByText("—")).toBeInTheDocument()
  })
})
