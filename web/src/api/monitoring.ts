import { api } from "@/api/axios"
import type { LaundrySession, StartLaundrySessionInput } from "@/types/api"

export async function startLaundrySession(input: StartLaundrySessionInput) {
  const { data } = await api.post<LaundrySession>(
    "/laundry-sessions",
    input,
  )
  return data
}

export async function getLaundrySession(id: string) {
  const { data } = await api.get<LaundrySession>(
    `/laundry-sessions/${encodeURIComponent(id)}`,
  )
  return data
}

export async function stopLaundrySession(id: string) {
  const { data } = await api.delete<LaundrySession>(
    `/laundry-sessions/${encodeURIComponent(id)}`,
  )
  return data
}
