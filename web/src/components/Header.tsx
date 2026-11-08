import { CloudSun, Shirt } from "lucide-react"
import { Link } from "react-router-dom"

export function Header() {
  return (
    <header className="sticky top-0 z-50 border-b border-slate-200/80 bg-white/75 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
        <Link to="/" className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-2xl bg-blue-600 text-white shadow-sm">
            <CloudSun className="size-5" />
          </div>
          <div>
            <div className="text-lg font-bold tracking-tight text-slate-950">DrySG</div>
            <div className="text-xs text-slate-500">Singapore laundry weather assistant</div>
          </div>
        </Link>

        <nav className="flex items-center gap-2">
          <a
            href="/#methodology"
            className="hidden rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 sm:block"
          >
            How it works
          </a>
          <Link
            to="/experience"
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-3.5 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700"
          >
            <Shirt className="size-4" />
            Experience
          </Link>
        </nav>
      </div>
    </header>
  )
}