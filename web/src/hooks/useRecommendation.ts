import useSWR from "swr"
import { getRecommendation } from "@/api/recommendation"

export function useRecommendation(postalCode?: string) {
  const isValid = /^\d{6}$/.test(postalCode ?? "")

  return useSWR(
    isValid ? ["recommendation", postalCode] : null,
    () => getRecommendation(postalCode!),
    {
      refreshInterval: 5 * 60 * 1000,
      dedupingInterval: 60 * 1000,
      revalidateOnFocus: true,
      shouldRetryOnError: false,
    },
  )
}