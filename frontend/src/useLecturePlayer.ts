import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import {
  initialPlayback,
  transition,
  type PlaybackEvent,
  type PlaybackRange,
  type PlaybackState,
} from './playback'

const RATES = [1, 1.25, 1.5, 1.75, 2, 0.75]
const SAVE_EVERY_SECONDS = 5

/** Remembering a place is a convenience, so storage that's blocked or full must not matter. */
function readSaved(key: string) {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function save(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Nothing to do: the listener just won't be offered this spot next time.
  }
}

/** Connects the state machine to an audio element. Commands run as they're produced. */
export function useLecturePlayer(jobId: string, audioRef: RefObject<HTMLAudioElement | null>) {
  // Events arrive every frame, so the current state must be readable without waiting for a render.
  const stateRef = useRef<PlaybackState>(initialPlayback)
  const [playback, setPlayback] = useState(initialPlayback)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const positionKey = `lecture-position:${jobId}`
  const [saved] = useState(() => Number(readSaved(positionKey)) || 0)
  const [rate, setRateState] = useState(() => Number(readSaved('lecture-rate')) || 1)
  const lastSavedRef = useRef(0)

  const send = useCallback(
    (event: PlaybackEvent) => {
      const { state, commands } = transition(stateRef.current, event)
      stateRef.current = state
      setPlayback(state)
      const audio = audioRef.current
      for (const command of commands) {
        if (!audio) continue
        if (command.type === 'seek') audio.currentTime = command.time
        // A refused play, as on autoplay rules, must not leave the player claiming to play.
        if (command.type === 'play') void audio.play().catch(() => audio.dispatchEvent(new Event('pause')))
        if (command.type === 'pause') audio.pause()
      }
    },
    [audioRef],
  )

  // timeupdate fires about 4 times a second, which would let a range run into another
  // page's speech, so check every frame while playing.
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    let frame = 0
    const tick = () => {
      send({ type: 'time', time: audio.currentTime })
      frame = requestAnimationFrame(tick)
    }
    const startTicking = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(tick)
    }
    const stopTicking = () => cancelAnimationFrame(frame)
    const onPlaying = () => {
      send({ type: 'playing' })
      startTicking()
    }
    const onPause = () => {
      send({ type: 'paused' })
      stopTicking()
    }
    const onEnded = () => {
      send({ type: 'ended' })
      stopTicking()
    }
    const onSeeked = () => send({ type: 'seeked' })
    const onTimeUpdate = () => {
      setTime(audio.currentTime)
      if (Math.abs(audio.currentTime - lastSavedRef.current) >= SAVE_EVERY_SECONDS) {
        lastSavedRef.current = audio.currentTime
        save(positionKey, String(audio.currentTime))
      }
    }
    const onDuration = () => setDuration(Number.isFinite(audio.duration) ? audio.duration : 0)
    audio.playbackRate = rate
    audio.addEventListener('playing', onPlaying)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnded)
    audio.addEventListener('seeked', onSeeked)
    audio.addEventListener('timeupdate', onTimeUpdate)
    audio.addEventListener('durationchange', onDuration)
    onDuration()
    return () => {
      stopTicking()
      audio.removeEventListener('playing', onPlaying)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnded)
      audio.removeEventListener('seeked', onSeeked)
      audio.removeEventListener('timeupdate', onTimeUpdate)
      audio.removeEventListener('durationchange', onDuration)
    }
  }, [audioRef, send, positionKey, rate])

  const playPage = useCallback(
    (page: number, ranges: PlaybackRange[]) => send({ type: 'playPage', page, ranges }),
    [send],
  )
  const playLecture = useCallback(
    (from?: number) => send({ type: 'playLecture', from }),
    [send],
  )
  const seek = useCallback(
    (target: number) => {
      setTime(target)
      send({ type: 'seek', time: target })
    },
    [send],
  )
  const jumpToRange = useCallback((index: number) => send({ type: 'jumpToRange', index }), [send])
  const toggle = useCallback(() => send({ type: 'toggle' }), [send])
  const cycleRate = useCallback(() => {
    setRateState((current) => {
      const next = RATES[(RATES.indexOf(current) + 1) % RATES.length]
      save('lecture-rate', String(next))
      return next
    })
  }, [])

  return {
    playback,
    time,
    duration,
    rate,
    /** Where the listener left off, if anywhere, as of when the page opened. */
    savedTime: saved,
    playPage,
    playLecture,
    seek,
    jumpToRange,
    toggle,
    cycleRate,
  }
}
