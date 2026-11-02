import axios from "axios"
import type { ApiErrorBody, ApiErrorCode } from "@/types/api"

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 10_000,
  headers: {
    "Content-Type": "application/json",
  },
})

/** A failed API call: the backend's `{error: {code, message}}`, or a network failure. */
export class ApiError extends Error {
  readonly status?: number
  readonly code?: ApiErrorCode

  constructor(message: string, status?: number, code?: ApiErrorCode) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

export function isApiError(error: unknown, code?: ApiErrorCode): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code)
}

export function errorMessage(error: unknown, fallback = "Something went wrong") {
  return error instanceof Error && error.message ? error.message : fallback
}

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (!axios.isAxiosError(error)) return Promise.reject(error)
    const body = error.response?.data as Partial<ApiErrorBody> | undefined
    if (error.response && body?.error?.message) {
      return Promise.reject(
        new ApiError(body.error.message, error.response.status, body.error.code),
      )
    }
    if (error.response) {
      return Promise.reject(
        new ApiError(
          "DrySG is having trouble right now. Try again in a moment.",
          error.response.status,
        ),
      )
    }
    return Promise.reject(
      new ApiError("Can't reach DrySG. Check your connection and try again."),
    )
  },
)
