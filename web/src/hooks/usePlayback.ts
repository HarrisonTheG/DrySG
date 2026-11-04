import { useCallback, useEffect, useState } from "react"

/** Steps through `frameCount` frames, one every `stepMs`, stopping on the last. */
export function usePlayback(frameCount: number, stepMs: number) {
  const last = frameCount - 1
  const [frame, setFrame] = useState(0)
  const [wantsPlay, setWantsPlay] = useState(false)
  // Playback ends by itself on the last frame, without an effect to flip the state.
  const playing = wantsPlay && frame < last

  useEffect(() => {
    if (!playing) return
    const timer = setInterval(() => setFrame((f) => Math.min(f + 1, last)), stepMs)
    return () => clearInterval(timer)
  }, [playing, stepMs, last])

  const seek = useCallback((to: number) => setFrame(Math.max(0, Math.min(to, last))), [last])
  const restart = useCallback(() => {
    setFrame(0)
    setWantsPlay(true)
  }, [])
  const play = useCallback(() => {
    // Pressing play on the last frame replays from the start.
    if (frame >= last) setFrame(0)
    setWantsPlay(true)
  }, [frame, last])
  const pause = useCallback(() => setWantsPlay(false), [])
  const step = useCallback(
    (by: number) => {
      setWantsPlay(false)
      seek(frame + by)
    },
    [frame, seek],
  )

  return { frame, playing, seek, step, play, pause, restart }
}
