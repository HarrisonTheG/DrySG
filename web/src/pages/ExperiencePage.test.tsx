import { act, fireEvent, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import MockAdapter from "axios-mock-adapter"
import { afterEach, describe, expect, it, vi } from "vitest"
import { api } from "@/api/axios"
import { CATEGORIES, allScenarios, formatSgt } from "@/lib/experience"
import { EXPERIENCE_EMAIL_KEY } from "@/lib/storage"
import { ExperiencePage } from "@/pages/ExperiencePage"
import { EMAIL, apiError, renderAt } from "@/test/fixtures"

const mock = new MockAdapter(api)
afterEach(() => {
  mock.reset()
  vi.useRealTimers()
})

const rainArrives = allScenarios.find((s) => s.category === "rain_arrives")!
const firstAlert = rainArrives.alerts[0]

function setup() {
  const user = userEvent.setup()
  // Always the first scenario of a category, so the tests know what they are replaying.
  vi.spyOn(Math, "random").mockReturnValue(0)
  renderAt("/experience", "/experience", <ExperiencePage />)
  return user
}

async function startRainArrives(user: ReturnType<typeof setup>) {
  await user.click(screen.getByRole("button", { name: /rain arrives/i }))
  await user.click(screen.getByRole("button", { name: "Pause" }))
  await user.click(screen.getByRole("button", { name: "Restart replay" }))
  await user.click(screen.getByRole("button", { name: "Pause" }))
}

async function stepTo(user: ReturnType<typeof setup>, frame: number) {
  for (let i = 0; i < frame; i++) await user.click(screen.getByRole("button", { name: "Next reading" }))
}

describe("ExperiencePage", () => {
  it("offers every category the data has", () => {
    setup()
    for (const { id, title } of CATEGORIES) {
      const has = allScenarios.some((s) => s.category === id)
      expect(screen.queryByRole("button", { name: new RegExp(title, "i") }) !== null).toBe(has)
    }
  })

  it("replays a real hour with the normal result cards and calls no API", async () => {
    const user = setup()
    await user.click(screen.getByRole("button", { name: /perfect day/i }))
    await user.click(screen.getByRole("button", { name: "Pause" }))

    const scenario = allScenarios.find((s) => s.category === "hang_now")!
    expect(screen.getByRole("heading", { name: new RegExp(`${scenario.area} · postal code ${scenario.postalCodes[0]}`) })).toBeInTheDocument()
    expect(screen.getByText(String(scenario.frames[0].score))).toBeInTheDocument()
    expect(screen.getByText(/real NEA readings from/i)).toHaveTextContent(formatSgt(scenario.startedAt, "dateTime"))
    expect(mock.history.get).toHaveLength(0)
    expect(mock.history.post).toHaveLength(0)
  })

  it("shows rain arriving: the veto, and the alert in the log", async () => {
    const user = setup()
    await startRainArrives(user)
    expect(screen.queryByText(/rain veto active/i)).not.toBeInTheDocument()
    expect(within(screen.getByLabelText("Alerts raised")).getByText(/no alert yet/i)).toBeInTheDocument()

    await stepTo(user, firstAlert.frame)
    expect(screen.getByText(/rain veto active/i)).toBeInTheDocument()
    const log = within(screen.getByLabelText("Alerts raised"))
    expect(log.getByText(firstAlert.reason)).toBeInTheDocument()
    expect(log.getByText(/turn on live alerts to get this by email/i)).toBeInTheDocument()
    expect(mock.history.post).toHaveLength(0) // live alerts are off
  })

  it("emails the alert when live alerts are on, and only once", async () => {
    const user = setup()
    mock.onPost("/users").reply(202, { email: EMAIL, verified: true })
    mock.onPost("/experience/alert").reply(202, { email: EMAIL, scenarioId: rainArrives.id, frame: firstAlert.frame })
    await startRainArrives(user)

    await user.type(screen.getByLabelText(/email for alerts/i), EMAIL)
    await user.click(screen.getByRole("switch"))
    await screen.findByText(/alerts raised in a replay will be emailed/i)
    expect(JSON.parse(localStorage.getItem(EXPERIENCE_EMAIL_KEY)!)).toBe(EMAIL)

    await stepTo(user, firstAlert.frame)
    await screen.findByText(`Emailed to ${EMAIL}`)
    const sends = mock.history.post.filter((r) => r.url === "/experience/alert")
    expect(sends).toHaveLength(1)
    expect(JSON.parse(sends[0].data)).toEqual({ email: EMAIL, scenarioId: rainArrives.id, frame: firstAlert.frame })

    // Stepping back and forward over the same alert does not send it again.
    await user.click(screen.getByRole("button", { name: "Previous reading" }))
    await user.click(screen.getByRole("button", { name: "Next reading" }))
    expect(mock.history.post.filter((r) => r.url === "/experience/alert")).toHaveLength(1)
  })

  it("does not send an alert the slider jumped past", async () => {
    const user = setup()
    mock.onPost("/users").reply(202, { email: EMAIL, verified: true })
    await startRainArrives(user)
    await user.type(screen.getByLabelText(/email for alerts/i), EMAIL)
    await user.click(screen.getByRole("switch"))
    await screen.findByText(/alerts raised in a replay will be emailed/i)

    const slider = screen.getByRole("slider", { name: "Replay time" })
    fireEvent.change(slider, { target: { value: String(rainArrives.frames.length - 1) } })
    expect(screen.getByText(/rain veto active/i)).toBeInTheDocument()
    expect(mock.history.post.filter((r) => r.url === "/experience/alert")).toHaveLength(0)
  })

  it("asks an unconfirmed email to confirm before any alert is sent", async () => {
    const user = setup()
    mock.onPost("/users").replyOnce(202, { email: EMAIL, verified: false })
    await startRainArrives(user)
    await user.type(screen.getByLabelText(/email for alerts/i), EMAIL)
    await user.click(screen.getByRole("switch"))
    await screen.findByText(/we sent a confirmation link/i)

    await stepTo(user, firstAlert.frame)
    expect(mock.history.post.filter((r) => r.url === "/experience/alert")).toHaveLength(0)
    expect(within(screen.getByLabelText("Alerts raised")).getByText(/turn on live alerts/i)).toBeInTheDocument()

    mock.onPost("/users").replyOnce(202, { email: EMAIL, verified: true })
    await user.click(screen.getByRole("button", { name: /i've confirmed/i }))
    await screen.findByText(/alerts raised in a replay will be emailed/i)
  })

  it("rejects a bad email without calling the API", async () => {
    const user = setup()
    await user.type(screen.getByLabelText(/email for alerts/i), "nope")
    await user.click(screen.getByRole("switch"))
    expect(await screen.findByText(/valid email address/i)).toBeInTheDocument()
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false")
    expect(mock.history.post).toHaveLength(0)
  })

  it("explains a send the server refuses", async () => {
    const user = setup()
    mock.onPost("/users").reply(202, { email: EMAIL, verified: true })
    mock.onPost("/experience/alert").reply(429, apiError("RATE_LIMITED", "An alert was just sent. Wait a few seconds"))
    await startRainArrives(user)
    await user.type(screen.getByLabelText(/email for alerts/i), EMAIL)
    await user.click(screen.getByRole("switch"))
    await screen.findByText(/alerts raised in a replay will be emailed/i)

    await stepTo(user, firstAlert.frame)
    expect(await screen.findByText(/not emailed: an alert was just sent/i)).toBeInTheDocument()
  })

  it("plays by itself and stops on the last reading", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.spyOn(Math, "random").mockReturnValue(0)
    renderAt("/experience", "/experience", <ExperiencePage />)
    await user.click(screen.getByRole("button", { name: /perfect day/i }))
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(2000 * 13)
    })
    expect(screen.getByRole("button", { name: "Play again" })).toBeInTheDocument()
    expect(screen.getByRole("slider", { name: "Replay time" })).toHaveValue("12")
  })
})
