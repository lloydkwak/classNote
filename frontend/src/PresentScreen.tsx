import { useEffect, useMemo, useRef, useState } from 'react'
import { getAnnotations, pdfUrl, type Annotation, type Annotations } from './api'
import { formatTime } from './format'
import { noteKey, SlideSheet } from './SlideSheet'

const POLL_MS = 1500
const NEW_NOTE_NOTICE_MS = 6000

function enterFullscreen() {
  document.documentElement.requestFullscreen?.().catch(() => {
    // Refused or unsupported: the screen still fills the browser window.
  })
}

/** Lets the talk happen on this screen, with notes writing themselves onto the slides. */
export function PresentScreen(props: {
  jobId: string
  seconds: number
  recording: boolean
  finished: boolean
  statusText: string
  error: string | null
  onStop: () => void
  onOpenResult: () => void
}) {
  const { jobId, finished } = props
  const [annotations, setAnnotations] = useState<Annotations | null>(null)
  const [page, setPage] = useState(1)
  const [pageCount, setPageCount] = useState(0)
  const [writing, setWriting] = useState<Set<string>>(new Set())
  const [newPages, setNewPages] = useState<number[]>([])
  const [isFullscreen, setIsFullscreen] = useState(false)
  const seen = useRef(new Set<string>())
  const writingPage = useRef(page)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    const poll = async () => {
      try {
        const loaded = await getAnnotations(jobId)
        if (!cancelled) setAnnotations(loaded)
      } catch {
        // Nothing yet, or a brief failure: the next poll covers it.
      }
      if (!cancelled && !finished) timer = window.setTimeout(poll, POLL_MS)
    }
    void poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [jobId, finished])

  const pageAnnotations = useMemo(
    () => annotations?.pages.find((item) => item.page === page),
    [annotations, page],
  )

  // A note is written the first time its slide is on screen, and is then simply there.
  useEffect(() => {
    const unseen = (pageAnnotations?.notes ?? [])
      .map((note: Annotation) => noteKey(note))
      .filter((key) => !seen.current.has(key))
    unseen.forEach((key) => seen.current.add(key))
    const samePage = writingPage.current === page
    writingPage.current = page
    if (unseen.length > 0 || !samePage) {
      setWriting((current) => new Set([...(samePage ? current : []), ...unseen]))
    }
  }, [pageAnnotations, page])

  // Tell the presenter about new notes on other slides, without covering this one for long.
  const announced = useRef(new Set<string>())
  useEffect(() => {
    const pages = (annotations?.pages ?? [])
      .filter((item) => item.page !== page)
      .filter((item) => item.notes.some((note) => !announced.current.has(noteKey(note))))
      .map((item) => item.page)
    for (const item of annotations?.pages ?? []) {
      item.notes.forEach((note) => announced.current.add(noteKey(note)))
    }
    if (pages.length === 0) return
    setNewPages(pages)
    const timer = window.setTimeout(() => setNewPages([]), NEW_NOTE_NOTICE_MS)
    return () => window.clearTimeout(timer)
  }, [annotations, page])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const last = pageCount || 1
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown' || event.key === 'PageDown') {
        event.preventDefault() // Includes the keys that presentation clickers send.
        setPage((value) => Math.min(value + 1, last))
      }
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp' || event.key === 'PageUp') {
        event.preventDefault()
        setPage((value) => Math.max(value - 1, 1))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pageCount])

  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement))
    onChange()
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const openResult = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    props.onOpenResult()
  }

  return (
    <div className="present">
      <SlideSheet
        file={pdfUrl(jobId)}
        page={page}
        annotations={pageAnnotations}
        noteFont={annotations?.note_font}
        writing={writing}
        onPageCount={setPageCount}
        onError={() => {}}
      />

      {newPages.length > 0 && (
        <div className="present-notice" role="status">
          {newPages.map((number) => (
            <button key={number} onClick={() => setPage(number)}>
              {number}쪽에 새 노트
            </button>
          ))}
        </div>
      )}

      <div className="present-bar">
        {props.recording && <span className="recording-dot" aria-hidden="true" />}
        <span className="timer" role="timer">
          {formatTime(props.seconds)}
        </span>
        <span className="present-status" role="status">
          {props.error ?? props.statusText}
        </span>
        <span className="present-page">
          {page} / {pageCount || '…'}
        </span>
        {!isFullscreen && <button onClick={enterFullscreen}>전체 화면</button>}
        {props.recording && <button onClick={props.onStop}>발표 종료</button>}
        {finished && (
          <button className="primary" onClick={openResult}>
            결과 보기
          </button>
        )}
      </div>
    </div>
  )
}

export { enterFullscreen }
