import { Navigate, Route, Routes } from "react-router-dom"
import { Toaster } from "sonner"
import { Header } from "@/components/Header"
import { HomePage } from "@/pages/HomePage"
import { StopPage } from "@/pages/StopPage"
import { VerifyPage } from "@/pages/VerifyPage"

export default function App() {
  return (
    <div className="min-h-screen">
      <Header />
      <Toaster richColors position="top-center" />

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
        <Routes>
          <Route path="/" element={<HomePage />} />
          {/* Links in DrySG emails (see services/drysg/alerting/email_template.py). */}
          <Route path="/verify" element={<VerifyPage />} />
          <Route path="/stop" element={<StopPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer className="border-t border-slate-200 bg-white/70">
        <div className="mx-auto max-w-6xl px-4 py-6 text-sm text-slate-500 sm:px-6">
          DrySG prototype · CS5224 · Recommendations are estimates based on available environmental data.
        </div>
      </footer>
    </div>
  )
}
