import { zodResolver } from "@hookform/resolvers/zod"
import { MapPin, Search } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

const schema = z.object({
  postalCode: z.string().regex(/^\d{6}$/, "Enter a valid 6-digit Singapore postal code"),
})

type FormValues = z.infer<typeof schema>

interface Props {
  defaultValue?: string
  loading?: boolean
  onSubmit: (postalCode: string) => void
}

export function PostalCodeForm({ defaultValue, loading, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { postalCode: defaultValue ?? "" },
  })

  return (
    <form
      onSubmit={handleSubmit(({ postalCode }) => onSubmit(postalCode))}
      className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5"
    >
      <label htmlFor="postalCode" className="mb-2 block text-sm font-semibold text-slate-900">
        Where are you drying laundry?
      </label>

      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <MapPin className="absolute left-3 top-1/2 size-5 -translate-y-1/2 text-slate-400" />
          <input
            id="postalCode"
            inputMode="numeric"
            maxLength={6}
            placeholder="e.g. 120123"
            {...register("postalCode")}
            className="h-12 w-full rounded-2xl border border-slate-300 bg-white pl-10 pr-4 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl bg-blue-600 px-5 font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-60"
        >
          <Search className="size-4" />
          {loading ? "Checking..." : "Check conditions"}
        </button>
      </div>

      {errors.postalCode && (
        <p className="mt-2 text-sm text-red-600">{errors.postalCode.message}</p>
      )}

      <p className="mt-3 text-xs leading-5 text-slate-500">
        We map your postal code to nearby NEA stations, a short-term forecast area and a PSI region.
      </p>
    </form>
  )
}