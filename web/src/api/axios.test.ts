import MockAdapter from "axios-mock-adapter"
import { afterEach, describe, expect, it } from "vitest"
import { api } from "@/api/axios"

const mock = new MockAdapter(api)
afterEach(() => mock.reset())

describe("API errors", () => {
  it("use the backend's code and message", async () => {
    mock.onGet("/recommendation").reply(503, {
      error: { code: "DATA_UNAVAILABLE", message: "Postal code lookup is unavailable" },
    })
    await expect(api.get("/recommendation")).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      code: "DATA_UNAVAILABLE",
      message: "Postal code lookup is unavailable",
    })
  })

  it("explain a response without the error body", async () => {
    mock.onGet("/recommendation").reply(502, "Bad Gateway")
    await expect(api.get("/recommendation")).rejects.toMatchObject({
      status: 502,
      code: undefined,
      message: "DrySG is having trouble right now. Try again in a moment.",
    })
  })

  it("explain a network failure", async () => {
    mock.onGet("/recommendation").networkError()
    await expect(api.get("/recommendation")).rejects.toMatchObject({
      status: undefined,
      message: "Can't reach DrySG. Check your connection and try again.",
    })
  })
})
