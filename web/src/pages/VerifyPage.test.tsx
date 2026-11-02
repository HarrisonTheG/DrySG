import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import MockAdapter from "axios-mock-adapter"
import { afterEach, describe, expect, it } from "vitest"
import { api } from "@/api/axios"
import { PENDING_KEY, SESSION_KEY, storePendingStart } from "@/lib/storage"
import { VerifyPage } from "@/pages/VerifyPage"
import { EMAIL, POSTAL, apiError, renderAt, session } from "@/test/fixtures"

const mock = new MockAdapter(api)
afterEach(() => mock.reset())

const LINK = `/verify?${new URLSearchParams({ email: EMAIL, token: "tok" })}`
const renderVerify = (path = LINK) => renderAt(path, "/verify", <VerifyPage />)

describe("VerifyPage", () => {
  it("confirms the email and starts the monitoring this browser was waiting on", async () => {
    storePendingStart({ email: EMAIL, postalCode: POSTAL, sentAt: Date.now() })
    mock.onPost("/users/verify").reply(200, { email: EMAIL, verified: true })
    mock.onPost("/laundry-sessions").reply(201, session)
    const user = userEvent.setup()
    renderVerify()

    await screen.findByText("Email confirmed")
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ email: EMAIL, token: "tok" })
    await user.click(screen.getByRole("button", { name: `Start monitoring ${POSTAL}` }))

    await screen.findByText("Home page")
    expect(JSON.parse(mock.history.post[1].data)).toEqual({ email: EMAIL, postalCode: POSTAL })
    expect(localStorage.getItem(SESSION_KEY)).toContain(session.id)
    expect(localStorage.getItem(PENDING_KEY)).toBeNull()
  })

  it("only links home when nothing is waiting in this browser", async () => {
    mock.onPost("/users/verify").reply(200, { email: EMAIL, verified: true })
    renderVerify()
    await screen.findByText("Email confirmed")
    expect(screen.queryByRole("button", { name: /start monitoring/i })).not.toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Go to DrySG" })).toBeInTheDocument()
  })

  it("offers a new link when the token is invalid or expired", async () => {
    mock.onPost("/users/verify").reply(400, apiError("INVALID_TOKEN"))
    mock.onPost("/users").reply(202, { email: EMAIL, verified: false })
    const user = userEvent.setup()
    renderVerify()

    await screen.findByText("Link invalid or expired")
    expect(screen.getByRole("textbox")).toHaveValue(EMAIL)
    await user.click(screen.getByRole("button", { name: "Send a new link" }))
    await screen.findByText("Check your inbox")
    expect(screen.getByText(EMAIL)).toBeInTheDocument()
  })

  it("treats a link without a token as invalid without calling the API", () => {
    renderVerify(`/verify?email=${encodeURIComponent(EMAIL)}`)
    expect(screen.getByText("Link invalid or expired")).toBeInTheDocument()
    expect(mock.history.post).toHaveLength(0)
  })

  it("lets the user retry when the service is unavailable", async () => {
    mock.onPost("/users/verify").replyOnce(503, apiError("DATA_UNAVAILABLE", "Service is busy"))
    mock.onPost("/users/verify").replyOnce(200, { email: EMAIL, verified: true })
    const user = userEvent.setup()
    renderVerify()

    await screen.findByText("Service is busy")
    await user.click(screen.getByRole("button", { name: "Try again" }))
    await screen.findByText("Email confirmed")
    await waitFor(() => expect(mock.history.post).toHaveLength(2))
  })
})
