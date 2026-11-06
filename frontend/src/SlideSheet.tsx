import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import type { Annotation, NoteType, PageAnnotations } from './api'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

// Served from public/, so PDFs with CJK fonts render with no network at the venue.
export const PDF_OPTIONS = {
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
const GUTTER_RATIO = 0.28

const toPoints = (stroke: [number, number][]) => stroke.map(([x, y]) => `${x},${y}`).join(' ')

/** Stays the same when notes are added and renumbered, so drawn notes stay on screen. */
export function noteKey(note: Annotation) {
  return note.provenance?.action_id??`${note.start}:${note.note||note.action_reason||'강의 요소 강조'}:${note.agent_action?.target_ids.join(',')??''}`
}

// Seconds for each part of a note being written.
const MARK_SECONDS = 0.7
const ARROW_SECONDS = 0.4
const LINE_SECONDS = 0.45

export function isPlaying(note: Annotation, time: number) {
  return time >= note.start && time < note.end
}

/** Draws one slide with its handwritten notes, as large as fits in the parent. */
export function SlideSheet(props: {
  zoom?: number
  original?: boolean
  file: string
  page: number
  annotations?: PageAnnotations
  noteFont?: string
  time?: number
  selectedId?: string | null
  dimInactive?: boolean
  /** Keys of notes to animate as if being written by hand. */
  writing?: Set<string>
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

  const slideWidth=annotations?.width??960
  const slideHeight=annotations?.height??slideWidth/aspect
  const gutter=props.original?0:annotations?.gutter??slideWidth*GUTTER_RATIO
  const totalWidth=slideWidth+2*gutter
  const totalHeight=annotations?.output_height??slideHeight
  const ratio=totalWidth/slideWidth
  const sheetWidth = Math.floor(Math.min(stage.width, stage.height * totalWidth/totalHeight)*(props.zoom??1))

  return (
    <div className="stage" ref={stageRef}>
      <div className={`sheet${props.dimInactive ? ' has-active' : ''}`} style={{ width: sheetWidth,height:sheetWidth*totalHeight/totalWidth }}>
        <div
          className="page-frame"
          style={{ position:'absolute',left:`${gutter/totalWidth*100}%`,width:`${slideWidth/totalWidth*100}%`,top:0 }}
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
                width={sheetWidth / ratio}
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
                  aria-label={`${TYPE_LABELS[note.type]}: ${note.note||note.action_reason||'강의 요소 강조'}`}
                  onClick={() => onPlayNote(note)}
                />
              )),
            )}
        </div>
        {annotations && (
          <svg
            className={`ink font-${props.noteFont ?? 'pretendard'}`}
            viewBox={`${-annotations.gutter} 0 ${annotations.width + 2 * annotations.gutter} ${
              totalHeight
            }`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {notes.map((note) => {
              const { sketch } = note
              const key = noteKey(note)
              // Notes arriving together are written one after another.
              const queue = notes.filter((item) => props.writing?.has(noteKey(item)))
              const position = queue.findIndex((item) => noteKey(item) === key)
              const hasMark = sketch.strokes.length > 0
              const textStart = (hasMark ? MARK_SECONDS + ARROW_SECONDS : 0)
              const before = queue
                .slice(0, Math.max(position, 0))
                .reduce(
                  (total, item) =>
                    total +
                    (item.sketch.strokes.length > 0 ? MARK_SECONDS + ARROW_SECONDS : 0) +
                    item.sketch.lines.length * LINE_SECONDS,
                  0,
                )
              const style = {
                '--mark-delay': `${before}s`,
                '--arrow-delay': `${before + MARK_SECONDS}s`,
                '--mark-seconds': `${MARK_SECONDS}s`,
                '--arrow-seconds': `${ARROW_SECONDS}s`,
                '--line-seconds': `${LINE_SECONDS}s`,
              } as CSSProperties
              return (
                <g
                  key={key}
                  className={`ink-note type-${note.type}${
                    selectedId === note.id ? ' selected' : ''
                  }${isPlaying(note, time) ? ' playing' : ''}${position >= 0 ? ' writing' : ''}`}
                  style={style}
                  onClick={onPlayNote && (() => onPlayNote(note))}
                >
                  {sketch.strokes.map((stroke, index) => (
                    <polyline
                      key={index}
                      className="mark"
                      style={{strokeWidth:sketch.stroke_width??1.6,opacity:sketch.stroke_opacity??1,stroke:sketch.mark_color?`rgb(${sketch.mark_color.map(v=>v*255).join(',')})`:undefined}}
                      pathLength={1}
                      points={toPoints(stroke)}
                    />
                  ))}
                  {sketch.arrow.map((stroke, index) => (
                    <polyline
                      key={index}
                      className="arrow"
                      style={{stroke:sketch.mark_color?`rgb(${sketch.mark_color.map(v=>v*255).join(',')})`:undefined}}
                      pathLength={1}
                      points={toPoints(stroke)}
                    />
                  ))}
                  <rect
                    className="note-hit"
                    x={sketch.x}
                    y={sketch.y}
                    width={sketch.width}
                    height={sketch.height}
                  />
                  {sketch.lines.map((line, index) => (
                    <text
                      key={index}
                      lang={note.language}
                      fontSize={sketch.font_size}
                      x={sketch.x}
                      y={sketch.y + index * sketch.line_height + (sketch.placement?.baseline_offset??sketch.font_size)}
                      style={{ animationDelay: `${before + textStart + index * LINE_SECONDS}s`,fill:sketch.text_color?`rgb(${sketch.text_color.map(v=>v*255).join(',')})`:undefined }}
                    >
                      {line.split(/([=+−α-ωΑ-Ω()\[\]{}→←↔⇒⇔-]+)/).map((run,i)=><tspan key={i} style={/^[=+−α-ωΑ-Ω()\[\]{}→←↔⇒⇔-]+$/.test(run)?{fontFamily:'serif'}:undefined}>{run}</tspan>)}
                    </text>
                  ))}
                </g>
              )
            })}
          </svg>
        )}
        {onPlayNote&&notes.map(note=><button key={note.id} className="note-access" aria-label={`필기 재생: ${note.note||note.action_reason||'강의 요소 강조'}`}
          style={{left:`${(note.sketch.x+gutter)/totalWidth*100}%`,top:`${note.sketch.y/totalHeight*100}%`,width:`${note.sketch.width/totalWidth*100}%`,height:`${note.sketch.height/totalHeight*100}%`}}
          onClick={()=>onPlayNote(note)}/>)}
      </div>
    </div>
  )
}
