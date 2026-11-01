import { render } from "@testing-library/react"
import type { ReactNode } from "react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { SWRConfig } from "swr"
import type { ApiErrorBody, ApiErrorCode, DryingRecommendation, LaundrySession } from "@/types/api"

export const EMAIL = "you@example.com"
export const POSTAL = "570150"

export const recommendation: DryingRecommendation = {
  postalCode: POSTAL,
  areaName: "Bishan",
  score: 82,
  recommendation: "HANG_NOW",
  summary: "Good drying conditions.",
  percentileDescription: "Drier than 82% of Singapore dry-weather readings in 2024",
  updatedAt: new Date().toISOString(),
  conditions: {
    rainfallMm: 0,
    temperatureC: 31.2,
    humidityPercent: 65,
    windSpeedMs: 2.1,
    forecast: "Partly Cloudy (Day)",
    psi: 40,
    psiBand: "Good",
  },
  rainVeto: false,
  airQualityWarning: false,
  degraded: false,
}

export const session: LaundrySession = {
  id: "2b1f7a0e-8c4d-4f7e-9a51-3c2d1e0f9b8a",
  postalCode: POSTAL,
  email: EMAIL,
  areaName: "Bishan",
  startedAt: new Date().toISOString(),
  expiresAt: new Date(Date.now() + 12 * 3600_000).toISOString(),
  active: true,
}

export function apiError(code: ApiErrorCode, message: string = code): ApiErrorBody {
  return { error: { code, message } }
}

/** Render `element` at `path`, with a stand-in home page so redirects to "/" are visible. */
export function renderAt(path: string, routePath: string, element: ReactNode) {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePath} element={element} />
          <Route path="*" element={<div>Home page</div>} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}
