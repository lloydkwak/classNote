import { useEffect, useMemo, useRef, useState } from 'react'
import {
  annotatedPdfUrl,
  audioUrl,
  getAlignment,
  getAnnotations,
  pdfUrl,
  type AlignedWindow,
  type Annotation,
  type Annotations,
} from './api'
import { formatTime } from './format'
import { isPlaying, SlideSheet, TYPE_LABELS } from './SlideSheet'

/** The page being explained at this time. Off-topic moments stay on the page before them. */
function pageAtTime(windows: AlignedWindow[], time: number) {
  let page: number | null = null
  for (const window of windows) {
    if (window.start > time) break
    if (window.page !== null) page = window.page
  }
  return page
}

export function ViewerScreen({ jobId }: { jobId: string }) {
  const [annotations, setAnnotations] = useState<Annotations | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pageCount, setPageCount] = useState(0)
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [time, setTime] = useState(0)
  const [hasAudio, setHasAudio] = useState(true)
  const [follow, setFollow] = useState(true)
  const [showNotes, setShowNotes] = useState(true)
  const [windows, setWindows] = useState<AlignedWindow[]>([])
  const audioRef = useRef<HTMLAudioElement>(null)
  const activeCardRef = useRef<HTMLButtonElement>(null)
  const currentDotRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    getAnnotations(jobId)
      .then((loaded) => {
        setAnnotations(loaded)
        // Open where the notes are, not on a title slide that may have none.
        if (loaded.pages.length > 0) setPage(loaded.pages[0].page)
      })
      .catch((cause) => setError(cause.message))
    // Following needs this, but the slides and notes work without it.
    getAlignment(jobId)
      .then((alignment) => setWindows(alignment.windows))
      .catch(() => setWindows([]))
  }, [jobId])

  const pageAnnotations = useMemo(
    () => annotations?.pages.find((item) => item.page === page),
    [annotations, page],
  )
  const notes = pageAnnotations?.notes ?? []

  const annotatedPages = useMemo(
    () => new Set(annotations?.pages.map((item) => item.page)),
    [annotations],
  )

  const activeNote = notes.find((note) => isPlaying(note, time))

  useEffect(() => {
    if (follow) activeCardRef.current?.scrollIntoView({ block: 'nearest' })
  }, [follow, activeNote?.id])

  useEffect(() => {
    currentDotRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [page, pageCount])

  const goToPage = (next: number) => {
    // Choosing a page by hand means stopping following, or the next tick would undo it.
    setFollow(false)
    setPage(Math.min(Math.max(next, 1), pageCount || 1))
  }

  const onTimeUpdate = (audio: HTMLAudioElement) => {
    setTime(audio.currentTime)
    if (!follow || audio.paused) return
    const target = pageAtTime(windows, audio.currentTime)
    if (target !== null) setPage(target)
  }

  const playNote = (note: Annotation) => {
    setSelectedId(note.id)
    const audio = audioRef.current
    if (!audio) return
    audio.currentTime = note.start
    void audio.play()
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement) return
      if (event.key === 'ArrowRight') {
        setFollow(false)
        setPage((value) => Math.min(value + 1, pageCount || 1))
      }
      if (event.key === 'ArrowLeft') {
        setFollow(false)
        setPage((value) => Math.max(value - 1, 1))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pageCount])

  return (
    <div className={`viewer${showNotes ? '' : ' notes-hidden'}`}>
      <header className="toolbar">
        <a className="brand" href="#/">
          LectureNote
        </a>
        <div className="pager">
          <button aria-label="이전 페이지" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
            ‹
          </button>
          <span>
            {page} / {pageCount || '…'}
          </span>
          <button
            aria-label="다음 페이지"
            disabled={page >= pageCount}
            onClick={() => goToPage(page + 1)}
          >
            ›
          </button>
        </div>
        <div className="toolbar-actions">
          <label className="switch">
            <input
              type="checkbox"
              checked={follow}
              onChange={(event) => setFollow(event.target.checked)}
            />
            따라가기
          </label>
          <label className="switch">
            <input
              type="checkbox"
              checked={showNotes}
              onChange={(event) => setShowNotes(event.target.checked)}
            />
            노트 목록
          </label>
          <a className="button" href={`#/jobs/${jobId}/debug`}>
            정렬 결과
          </a>
          <a className="button" href={annotatedPdfUrl(jobId)} download="annotated.pdf">
            annotated.pdf 다운로드
          </a>
        </div>
      </header>

      {annotations?.incomplete && (
        <p className="banner" role="status">
          LLM 사용 한도나 일시적인 오류로 일부 슬라이드에 아직 노트가 없습니다. 나중에 같은 파일을
          다시 올리면 이어서 만듭니다.
        </p>
      )}

      <section className="slide-pane">
        <SlideSheet
          file={pdfUrl(jobId)}
          page={page}
          annotations={pageAnnotations}
          time={time}
          selectedId={selectedId}
          dimInactive={follow && Boolean(activeNote)}
          onPlayNote={playNote}
          onPageCount={setPageCount}
          onError={setError}
        />
        <nav className="page-dots" aria-label="페이지">
          {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => {
            const hasNotes = annotatedPages.has(number)
            return (
              <button
                key={number}
                ref={number === page ? currentDotRef : undefined}
                className={`${number === page ? 'current' : ''}${hasNotes ? ' has-notes' : ''}`}
                aria-current={number === page ? 'page' : undefined}
                aria-label={hasNotes ? `${number}쪽, 노트 있음` : `${number}쪽`}
                onClick={() => goToPage(number)}
              >
                {number}
              </button>
            )
          })}
        </nav>
      </section>

      {showNotes && (
        <aside className="notes-pane">
          <h2>{page}쪽 노트</h2>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {!error && annotations && notes.length === 0 && (
            <p className="muted">이 페이지에는 노트가 없습니다.</p>
          )}
          <ul>
            {notes.map((note) => (
              <li key={note.id}>
                <div
                  className={`note-card${selectedId === note.id ? ' selected' : ''}${
                    isPlaying(note, time) ? ' playing' : ''
                  }`}
                >
                  <button
                    ref={note.id === activeNote?.id ? activeCardRef : undefined}
                    className="note-play"
                    onClick={() => playNote(note)}
                  >
                    <span className="note-meta">
                      <span className={`badge type-${note.type}`}>{TYPE_LABELS[note.type]}</span>
                      {hasAudio && (
                        <span className="timestamp">
                          ▶ {formatTime(note.start)}–{formatTime(note.end)}
                        </span>
                      )}
                    </span>
                    <span lang={note.language}>{note.note}</span>
                  </button>
                  <details className="note-quote">
                    <summary>교수님 원문</summary>
                    <blockquote>{note.quote}</blockquote>
                  </details>
                </div>
              </li>
            ))}
          </ul>
        </aside>
      )}

      <footer className="player">
        {hasAudio ? (
          <audio
            ref={audioRef}
            controls
            preload="metadata"
            src={audioUrl(jobId)}
            onTimeUpdate={(event) => onTimeUpdate(event.currentTarget)}
            onError={() => setHasAudio(false)}
          />
        ) : (
          <p className="muted">이 작업에는 오디오가 없습니다.</p>
        )}
      </footer>
    </div>
  )
}
