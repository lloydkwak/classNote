import { useEffect, useMemo, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import {
  annotatedPdfUrl,
  audioUrl,
  getAlignment,
  getAnnotations,
  pdfUrl,
  type AlignedWindow,
  type Annotation,
  type Annotations,
  type NoteType,
} from './api'
import { formatTime } from './format'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

// Served from public/, so PDFs with CJK fonts render with no network at the venue.
const PDF_OPTIONS = {
  cMapUrl: '/cmaps/',
  cMapPacked: true,
  standardFontDataUrl: '/standard_fonts/',
}

const TYPE_LABELS: Record<NoteType, string> = {
  exam_hint: 'Exam hint',
  emphasis: 'Emphasis',
  explanation: 'Explanation',
  example: 'Example',
  question: 'Question',
}

/** Each side of the page gets this share of its width to write in. Matches layout.py. */
const GUTTER_RATIO = 0.24
const SHEET_WIDTH_RATIO = 1 + 2 * GUTTER_RATIO

const toPoints = (stroke: [number, number][]) => stroke.map(([x, y]) => `${x},${y}`).join(' ')

function isPlaying(note: Annotation, time: number) {
  return time >= note.start && time < note.end
}

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
  const [pageWidth, setPageWidth] = useState(0)
  const [follow, setFollow] = useState(true)
  const [windows, setWindows] = useState<AlignedWindow[]>([])
  const audioRef = useRef<HTMLAudioElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)

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

  useEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    const observer = new ResizeObserver(([entry]) => setPageWidth(entry.contentRect.width))
    observer.observe(stage)
    return () => observer.disconnect()
  }, [])

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
  const activeCardRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (follow) activeCardRef.current?.scrollIntoView({ block: 'nearest' })
  }, [follow, activeNote?.id])

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
    <div className="viewer">
      <header className="toolbar">
        <a className="brand" href="#/">
          LectureNote
        </a>
        <div className="pager">
          <button aria-label="Previous page" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
            ‹
          </button>
          <span>
            {page} / {pageCount || '…'}
          </span>
          <button
            aria-label="Next page"
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
            Follow along
          </label>
          <a className="button" href={`#/jobs/${jobId}/debug`}>
            Alignment
          </a>
          <a className="button" href={annotatedPdfUrl(jobId)} download="annotated.pdf">
            Download annotated.pdf
          </a>
        </div>
      </header>

      {annotations?.incomplete && (
        <p className="banner" role="status">
          Some slides have no notes yet because the LLM's daily limit ran out. Upload the same files
          again later to continue from here.
        </p>
      )}

      <section className="slide-pane">
        <div className="stage" ref={stageRef}>
          <div className={`sheet${follow && activeNote ? ' has-active' : ''}`}>
          <div
            className="page-frame"
            style={{
              marginInline: `${(GUTTER_RATIO / SHEET_WIDTH_RATIO) * 100}%`,
            }}
          >
            <Document
              file={pdfUrl(jobId)}
              options={PDF_OPTIONS}
              onLoadSuccess={(document) => setPageCount(document.numPages)}
              onLoadError={(cause) => setError(cause.message)}
              loading={<p className="muted">Loading slides…</p>}
            >
              {pageWidth > 0 && (
                <Page
                  pageNumber={page}
                  width={pageWidth / SHEET_WIDTH_RATIO}
                  renderAnnotationLayer={false}
                  loading={<p className="muted">Loading page…</p>}
                />
              )}
            </Document>
            {notes.flatMap((note) =>
              note.bboxes.map(([x0, y0, x1, y1], index) => (
                <button
                  key={`${note.id}-${index}`}
                  className="hit-area"
                  style={{
                    left: `${x0 * 100}%`,
                    top: `${y0 * 100}%`,
                    width: `${(x1 - x0) * 100}%`,
                    height: `${(y1 - y0) * 100}%`,
                  }}
                  aria-label={`${TYPE_LABELS[note.type]}: ${note.note}`}
                  onClick={() => playNote(note)}
                />
              )),
            )}
          </div>
          {pageAnnotations && (
            <svg
              className="ink"
              viewBox={`${-pageAnnotations.gutter} 0 ${
                pageAnnotations.width + 2 * pageAnnotations.gutter
              } ${pageAnnotations.height}`}
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              {notes.map((note) => {
                const { sketch } = note
                return (
                  <g
                    key={note.id}
                    className={`ink-note type-${note.type}${
                      selectedId === note.id ? ' selected' : ''
                    }${isPlaying(note, time) ? ' playing' : ''}`}
                    onClick={() => playNote(note)}
                  >
                    {sketch.strokes.map((stroke, index) => (
                      <polyline key={index} className="mark" points={toPoints(stroke)} />
                    ))}
                    {sketch.arrow.map((stroke, index) => (
                      <polyline key={index} className="arrow" points={toPoints(stroke)} />
                    ))}
                    <rect
                      className="note-hit"
                      x={sketch.x}
                      y={sketch.y}
                      width={sketch.width}
                      height={sketch.height}
                    />
                    <text lang={note.language} fontSize={sketch.font_size}>
                      {sketch.lines.map((line, index) => (
                        <tspan
                          key={index}
                          x={sketch.x}
                          y={sketch.y + index * sketch.line_height + sketch.font_size}
                        >
                          {line}
                        </tspan>
                      ))}
                    </text>
                  </g>
                )
              })}
            </svg>
          )}
          </div>
        </div>
        <nav className="page-dots" aria-label="Pages">
          {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => {
            const hasNotes = annotatedPages.has(number)
            return (
              <button
                key={number}
                className={`${number === page ? 'current' : ''}${hasNotes ? ' has-notes' : ''}`}
                aria-current={number === page ? 'page' : undefined}
                aria-label={hasNotes ? `Page ${number}, has notes` : `Page ${number}`}
                onClick={() => goToPage(number)}
              >
                {number}
              </button>
            )
          })}
        </nav>
      </section>

      <aside className="notes-pane">
        <h2>Notes on page {page}</h2>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {!error && annotations && notes.length === 0 && (
          <p className="muted">No notes on this page.</p>
        )}
        <ul>
          {notes.map((note) => (
            <li key={note.id}>
              <button
                ref={note.id === activeNote?.id ? activeCardRef : undefined}
                className={`note-card${selectedId === note.id ? ' selected' : ''}${
                  isPlaying(note, time) ? ' playing' : ''
                }`}
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
            </li>
          ))}
        </ul>
      </aside>

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
          <p className="muted">This job has no audio.</p>
        )}
      </footer>
    </div>
  )
}
