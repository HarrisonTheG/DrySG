import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import MockAdapter from "axios-mock-adapter"
import { afterEach, describe, expect, it } from "vitest"
import { api } from "@/api/axios"
import { SESSION_KEY, storeSession } from "@/lib/storage"
import { StopPage } from "@/pages/StopPage"
import { apiError, renderAt, session } from "@/test/fixtures"

const mock = new MockAdapter(api)
afterEach(() => mock.reset())

const SESSION_URL = `/laundry-sessions/${session.id}`
const renderStop = (path = `/stop?session=${session.id}`) => renderAt(path, "/stop", <StopPage />)

describe("StopPage", () => {
  it("stops monitoring only after the user confirms", async () => {
    storeSession(session)
    mock.onGet(SESSION_URL).reply(200, session)
    mock.onDelete(SESSION_URL).reply(200, { ...session, active: false })
    const user = userEvent.setup()
    renderStop()

    await screen.findByText("Stop monitoring Bishan?")
    expect(mock.history.delete).toHaveLength(0)
    await user.click(screen.getByRole("button", { name: "Yes, my laundry is in" }))

    await screen.findByText("Monitoring stopped")
    expect(mock.history.delete).toHaveLength(1)
    expect(localStorage.getItem(SESSION_KEY)).toBeNull()
  })

  it("says when monitoring already ended", async () => {
    mock.onGet(SESSION_URL).reply(200, { ...session, active: false })
    renderStop()
    await screen.findByText("Monitoring already ended")
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it.each([
    [404, apiError("SESSION_NOT_FOUND")],
    [400, apiError("INVALID_SESSION_ID")],
  ])("rejects an unknown or malformed session (%i)", async (status, body) => {
    mock.onGet(SESSION_URL).reply(status, body)
    renderStop()
    await screen.findByText("This link isn't valid")
  })

  it("rejects a link without a session id", () => {
    renderStop("/stop")
    expect(screen.getByText("This link isn't valid")).toBeInTheDocument()
    expect(mock.history.get).toHaveLength(0)
  })
})
