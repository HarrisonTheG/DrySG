import { api } from "@/api/axios"
import type { DryingRecommendation } from "@/types/api"

export async function getRecommendation(postalCode: string) {
  const { data } = await api.get<DryingRecommendation>(
    "/recommendation",
    { params: { postalCode } },
  )

  return data
}