import { useEffect, useMemo, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import {
  annotatedPdfUrl,
  audioUrl,
  getAnnotations,
  pdfUrl,
  type Annotation,
  type Annotations,
  type NoteType,
} from './api'

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

function formatTime(seconds: number) {
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** Each side of the page gets this share of its width to write in. Matches layout.py. */
const GUTTER_RATIO = 0.24
const SHEET_WIDTH_RATIO = 1 + 2 * GUTTER_RATIO

const toPoints = (stroke: [number, number][]) => stroke.map(([x, y]) => `${x},${y}`).join(' ')

function isPlaying(note: Annotation, time: number) {
  return time >= note.start && time < note.end
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

  const goToPage = (next: number) => {
    setPage(Math.min(Math.max(next, 1), pageCount || 1))
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
      if (event.key === 'ArrowRight') setPage((value) => Math.min(value + 1, pageCount || 1))
      if (event.key === 'ArrowLeft') setPage((value) => Math.max(value - 1, 1))
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
        <a className="button" href={annotatedPdfUrl(jobId)} download="annotated.pdf">
          Download annotated.pdf
        </a>
      </header>

      {annotations?.incomplete && (
        <p className="banner" role="status">
          Some slides have no notes yet because the LLM's daily limit ran out. Upload the same files
          again later to continue from here.
        </p>
      )}

      <section className="slide-pane">
        <div className="stage" ref={stageRef}>
          <div className="sheet">
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
        <nav className="page-dots" aria-label="Pages with notes">
          {[...annotatedPages]
            .sort((a, b) => a - b)
            .map((number) => (
              <button
                key={number}
                className={number === page ? 'current' : undefined}
                onClick={() => goToPage(number)}
              >
                {number}
              </button>
            ))}
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
            onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
            onError={() => setHasAudio(false)}
          />
        ) : (
          <p className="muted">This job has no audio.</p>
        )}
      </footer>
    </div>
  )
}
