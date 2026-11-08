import { availableCategories } from "@/lib/experience"
import type { ExperienceCategory } from "@/types/experience"

interface Props {
  selected?: ExperienceCategory
  onPick: (category: ExperienceCategory) => void
}

/**
 * One row of buttons that stays under the header while scrolling, so the kind of day can be
 * switched without scrolling back up to the results' controls.
 */
export function CategoryPicker({ selected, onPick }: Props) {
  const categories = availableCategories()

  return (
    <nav
      aria-label="Kind of day"
      className="sticky top-[78px] z-40 mb-5 flex gap-2 overflow-x-auto rounded-2xl border border-slate-200 bg-white/90 p-2 shadow-sm backdrop-blur sm:flex-wrap sm:overflow-visible"
    >
      {categories.map(({ id, title, blurb, icon: Icon, tone, count }) => (
        <button
          key={id}
          type="button"
          aria-pressed={selected === id}
          title={`${blurb} (${count} real ${count === 1 ? "moment" : "moments"})`}
          onClick={() => onPick(id)}
          className={`inline-flex shrink-0 items-center gap-2 rounded-xl border px-3 py-2 text-[13px] font-semibold transition ${
            selected === id
              ? "border-blue-500 bg-blue-50 text-blue-900 ring-2 ring-blue-100"
              : "border-transparent bg-slate-50 text-slate-700 hover:bg-slate-100"
          }`}
        >
          <span className={`grid size-6 place-items-center rounded-lg ${tone}`}><Icon className="size-3.5" /></span>
          {title}
        </button>
      ))}
    </nav>
  )
}
