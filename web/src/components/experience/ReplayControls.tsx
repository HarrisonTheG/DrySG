import { BellRing, Pause, Play, RotateCcw, StepBack, StepForward } from "lucide-react"
import { FRAME_MINUTES, SPEEDS, formatSgt, type Speed } from "@/lib/experience"
import type { ExperienceScenario } from "@/types/experience"

interface Props {
  scenario: ExperienceScenario
  frame: number
  playing: boolean
  speed: Speed
  onPlay: () => void
  onPause: () => void
  onRestart: () => void
  onStep: (by: number) => void
  onSeek: (frame: number) => void
  onSpeed: (speed: Speed) => void
}

const button =
  "grid size-10 place-items-center rounded-2xl border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50 disabled:opacity-40"

export function ReplayControls({ scenario, frame, playing, speed, onPlay, onPause, onRestart, onStep, onSeek, onSpeed }: Props) {
  const last = scenario.frames.length - 1
  const at = (index: number) => formatSgt(scenario.frames[index].updatedAt, "time")

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm" aria-label="Replay controls">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold uppercase tracking-wide text-slate-500">Replaying</div>
          <div className="text-2xl font-bold tracking-tight text-slate-950">{at(frame)}</div>
          <div className="text-sm text-slate-500">{formatSgt(scenario.frames[frame].updatedAt, "date")} · SGT</div>
        </div>

        <div className="flex items-center gap-2">
          <button type="button" className={button} onClick={onRestart} aria-label="Restart replay"><RotateCcw className="size-4" /></button>
          <button type="button" className={button} onClick={() => onStep(-1)} disabled={frame === 0} aria-label="Previous reading"><StepBack className="size-4" /></button>
          {playing ? (
            <button type="button" className="grid size-12 place-items-center rounded-2xl bg-blue-600 text-white transition hover:bg-blue-700" onClick={onPause} aria-label="Pause">
              <Pause className="size-5" />
            </button>
          ) : (
            <button type="button" className="grid size-12 place-items-center rounded-2xl bg-blue-600 text-white transition hover:bg-blue-700" onClick={onPlay} aria-label={frame >= last ? "Play again" : "Play"}>
              <Play className="size-5" />
            </button>
          )}
          <button type="button" className={button} onClick={() => onStep(1)} disabled={frame === last} aria-label="Next reading"><StepForward className="size-4" /></button>
        </div>
      </div>

      <div className="mt-5">
        <input
          type="range"
          min={0}
          max={last}
          value={frame}
          onChange={(event) => onSeek(Number(event.target.value))}
          aria-label="Replay time"
          aria-valuetext={at(frame)}
          className="w-full accent-blue-600"
        />
        {/* Bells mark where the live alert rules would have emailed. */}
        <div className="relative mx-2 h-6" aria-hidden="true">
          {scenario.alerts.map((alert) => (
            <span
              key={alert.frame}
              title={`Alert at ${at(alert.frame)}`}
              className={`absolute top-0 grid size-6 -translate-x-1/2 place-items-center rounded-full ${
                frame >= alert.frame ? "bg-red-100 text-red-700" : "bg-slate-100 text-slate-400"
              }`}
              style={{ left: `${(alert.frame / last) * 100}%` }}
            >
              <BellRing className="size-3.5" />
            </span>
          ))}
        </div>
        <div className="flex justify-between text-xs text-slate-400">
          <span>{at(0)}</span>
          <span>{FRAME_MINUTES}-minute steps</span>
          <span>{at(last)}</span>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2 text-sm text-slate-500">
        Speed
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={speed === s}
            onClick={() => onSpeed(s)}
            className={`rounded-xl px-3 py-1 text-sm font-semibold transition ${speed === s ? "bg-slate-950 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
          >
            {s}×
          </button>
        ))}
      </div>
    </section>
  )
}
