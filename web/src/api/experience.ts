import { api } from "@/api/axios"
import type { ExperienceAlertSent } from "@/types/api"

/**
 * Email the alert an Experience replay raises at `frame`. The server holds the scenarios, so
 * the email's content comes from there; this only names the scenario and frame.
 */
export async function sendExperienceAlert(input: { email: string; scenarioId: string; frame: number }) {
  const { data } = await api.post<ExperienceAlertSent>("/experience/alert", input)
  return data
}
