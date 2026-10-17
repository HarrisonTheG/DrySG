import type { RecommendationState } from "@/types/api"

export function recommendationMeta(state: RecommendationState) {
  switch (state) {
    case "HANG_NOW":
      return {
        label: "Hang now",
        headline: "Good drying conditions",
        badgeClass: "bg-emerald-100 text-emerald-800 ring-emerald-200",
        scoreClass: "text-emerald-600",
        barClass: "bg-emerald-500",
      }
    case "WAIT":
      return {
        label: "Wait",
        headline: "Conditions are only moderate",
        badgeClass: "bg-amber-100 text-amber-800 ring-amber-200",
        scoreClass: "text-amber-600",
        barClass: "bg-amber-500",
      }
    case "DO_NOT_HANG":
      return {
        label: "Do not hang",
        headline: "Keep laundry indoors",
        badgeClass: "bg-red-100 text-red-800 ring-red-200",
        scoreClass: "text-red-600",
        barClass: "bg-red-500",
      }
  }
}