import { api } from "@/api/axios"
import type { UserStatus } from "@/types/api"

/** Email a verification link (no email is sent if the address is already verified). */
export async function registerEmail(email: string) {
  const { data } = await api.post<UserStatus>("/users", { email })
  return data
}

export async function verifyEmail(email: string, token: string) {
  const { data } = await api.post<UserStatus>("/users/verify", { email, token })
  return data
}
