import { api } from "@/api/axios"
import type { LaundrySession } from "@/types/api"

export async function startLaundrySession(input: {
  postalCode: string
  email: string
}) {
  const { data } = await api.post<LaundrySession>(
    "/laundry-sessions",
    input,
  )
  return data
}

export async function stopLaundrySession(id: string) {
  await api.delete(`/laundry-sessions/${id}`)
}