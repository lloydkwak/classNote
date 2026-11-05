import { useEffect, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import type { Annotation, NoteType, PageAnnotations } from './api'

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

export const TYPE_LABELS: Record<NoteType, string> = {
  exam_hint: '시험 힌트',
  emphasis: '강조',
  explanation: '설명',
  example: '예시',
  question: '질문',
}

/** Each side of the page gets this share of its width to write in. Matches layout.py. */
const GUTTER_RATIO = 0.24
const SHEET_WIDTH_RATIO = 1 + 2 * GUTTER_RATIO

const toPoints = (stroke: [number, number][]) => stroke.map(([x, y]) => `${x},${y}`).join(' ')

export function isPlaying(note: Annotation, time: number) {
  return time >= note.start && time < note.end
}

/** Draws one slide with its handwritten notes, as large as fits in the parent. */
export function SlideSheet(props: {
  file: string
  page: number
  annotations?: PageAnnotations
  time?: number
  selectedId?: string | null
  dimInactive?: boolean
  onPlayNote?: (note: Annotation) => void
  onPageCount: (count: number) => void
  onError: (message: string) => void
}) {
  const { annotations, time = -1, selectedId, onPlayNote } = props
  const notes = annotations?.notes ?? []
  const stageRef = useRef<HTMLDivElement>(null)
  const [stage, setStage] = useState({ width: 0, height: 0 })
  const [aspect, setAspect] = useState(4 / 3) // Of the page, until its real size is known.

  useEffect(() => {
    const element = stageRef.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) =>
      setStage({ width: entry.contentRect.width, height: entry.contentRect.height }),
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const sheetWidth = Math.floor(Math.min(stage.width, stage.height * aspect * SHEET_WIDTH_RATIO))

  return (
    <div className="stage" ref={stageRef}>
      <div className={`sheet${props.dimInactive ? ' has-active' : ''}`} style={{ width: sheetWidth }}>
        <div
          className="page-frame"
          style={{ marginInline: `${(GUTTER_RATIO / SHEET_WIDTH_RATIO) * 100}%` }}
        >
          <Document
            file={props.file}
            options={PDF_OPTIONS}
            onLoadSuccess={(document) => props.onPageCount(document.numPages)}
            onLoadError={(cause) => props.onError(cause.message)}
            loading={<p className="muted">슬라이드 불러오는 중…</p>}
            error={<p className="muted">슬라이드를 불러오지 못했습니다.</p>}
          >
            {sheetWidth > 0 && (
              <Page
                pageNumber={props.page}
                width={sheetWidth / SHEET_WIDTH_RATIO}
                renderAnnotationLayer={false}
                onLoadSuccess={(page) => setAspect(page.originalWidth / page.originalHeight)}
                loading={<p className="muted">페이지 불러오는 중…</p>}
              />
            )}
          </Document>
          {onPlayNote &&
            notes.flatMap((note) =>
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
                  onClick={() => onPlayNote(note)}
                />
              )),
            )}
        </div>
        {annotations && (
          <svg
            className="ink"
            viewBox={`${-annotations.gutter} 0 ${annotations.width + 2 * annotations.gutter} ${
              annotations.height
            }`}
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
                  onClick={onPlayNote && (() => onPlayNote(note))}
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
  )
}
