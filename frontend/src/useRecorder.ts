import { useCallback, useEffect, useRef, useState } from 'react'

const MIME_TYPE = 'audio/webm;codecs=opus'

export type RecorderState = 'idle' | 'starting' | 'recording'

function describeError(cause: unknown): string {
  if (cause instanceof DOMException && cause.name === 'NotAllowedError') {
    return 'Microphone access was denied. Allow it in the browser, then try again.'
  }
  if (cause instanceof DOMException && cause.name === 'NotFoundError') {
    return 'No microphone was found.'
  }
  return cause instanceof Error ? cause.message : String(cause)
}

/** Records the microphone. Each blob is one part of a single continuous webm file. */
export function useRecorder(options: {
  /** How often to deliver audio while recording. Omit to get it all when stopped. */
  chunkMs?: number
  onChunk?: (blob: Blob) => void
  /** Called after the last chunk, with the whole recording. */
  onStop?: (recording: Blob) => void
}) {
  const [state, setState] = useState<RecorderState>('idle')
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const timerRef = useRef<number | undefined>(undefined)
  const optionsRef = useRef(options)

  useEffect(() => {
    optionsRef.current = options
  })

  const start = useCallback(async () => {
    setError(null)
    setState('starting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream, { mimeType: MIME_TYPE })
      const parts: Blob[] = []
      recorder.ondataavailable = (event) => {
        if (event.data.size === 0) return
        parts.push(event.data)
        optionsRef.current.onChunk?.(event.data)
      }
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop())
        window.clearInterval(timerRef.current)
        setState('idle')
        optionsRef.current.onStop?.(new Blob(parts, { type: 'audio/webm' }))
      }
      recorderRef.current = recorder
      recorder.start(optionsRef.current.chunkMs)
      const startedAt = Date.now()
      setSeconds(0)
      timerRef.current = window.setInterval(
        () => setSeconds(Math.floor((Date.now() - startedAt) / 1000)),
        250,
      )
      setState('recording')
    } catch (cause) {
      setError(describeError(cause))
      setState('idle')
    }
  }, [])

  const stop = useCallback(() => recorderRef.current?.stop(), [])

  // Leaving the screen must not leave the microphone on.
  useEffect(
    () => () => {
      window.clearInterval(timerRef.current)
      const recorder = recorderRef.current
      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = null
        recorder.stream.getTracks().forEach((track) => track.stop())
        recorder.stop()
      }
    },
    [],
  )

  return { state, seconds, error, start, stop }
}
