import { useEffect, useMemo, useRef, useState } from 'react'
import {
  annotatedPdfUrl,
  audioUrl,
  getAnnotations,
  getPageAudio,
  getDetailedNotes,
  type DetailedNotes,
  type Density,
  pdfUrl,
  type Annotation,
  type Annotations,
  type PageAudio,
} from './api'
import { formatTime } from './format'
import { LecturePlayerBar } from './LecturePlayerBar'
import { isPlaying, SlideSheet, TYPE_LABELS } from './SlideSheet'
import { useLecturePlayer } from './useLecturePlayer'

/** The page being explained at this time. Off-topic moments stay on the page before them. */
function pageAtTime(pageAudio: PageAudio, time: number) {
  let latest: { page: number; start: number } | null = null
  for (const entry of pageAudio.pages) {
    for (const range of entry.audio_ranges) {
      if (range.start <= time && (!latest || range.start > latest.start)) {
        latest = { page: entry.page, start: range.start }
      }
    }
  }
  return latest?.page ?? null
}

function pageAtFirstSpeech(pageAudio: PageAudio) {
  const heard = pageAudio.pages.filter((entry) => entry.primary_start !== null)
  if (heard.length === 0) return null
  return heard.reduce((first, entry) =>
    (entry.primary_start as number) < (first.primary_start as number) ? entry : first,
  ).page
}

export function ViewerScreen({ jobId }: { jobId: string }) {
  const [annotations, setAnnotations] = useState<Annotations | null>(null)
  const [detailed, setDetailed] = useState<DetailedNotes | null>(null)
  const [detailStatus, setDetailStatus] = useState('상세 노트 불러오는 중…')
  const [density, setDensity] = useState<Density>('balanced')
  const [densityError, setDensityError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pageCount, setPageCount] = useState(0)
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hasAudio, setHasAudio] = useState(true)
  const [follow, setFollow] = useState(true)
  const [showNotes, setShowNotes] = useState(true)
  const [pageAudio, setPageAudio] = useState<PageAudio | null>(null)
  const audioRef = useRef<HTMLAudioElement>(null)
  const player = useLecturePlayer(jobId, audioRef)
  const { time, playback, toggle: togglePlayback } = player
  const activeCardRef = useRef<HTMLButtonElement>(null)
  const currentDotRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let cancelled = false
    setDetailed(null)
    setDetailStatus('상세 노트 불러오는 중…')
    void getDetailedNotes(jobId).then((value) => {
      if (!cancelled) { setDetailed(value); setDetailStatus('') }
    }).catch((failure: Error) => { if (!cancelled) setDetailStatus(failure.message) })
    return () => { cancelled = true }
  }, [jobId])

  useEffect(() => {
    let cancelled = false
    setDensityError(null)
    void getAnnotations(jobId, density).then((value) => {
      if (!cancelled) setAnnotations(value)
    }).catch((failure: Error) => { if (!cancelled) setDensityError(failure.message) })
    return () => { cancelled = true }
  }, [jobId, density])

  useEffect(() => {
    // Slides and audio work without notes, and notes work without page audio.
    let cancelled = false
    void Promise.allSettled([getAnnotations(jobId), getPageAudio(jobId)]).then(([notes, audio]) => {
      if (cancelled) return
      if (audio.status === 'fulfilled') setPageAudio(audio.value)
      // Open where the lecture starts, not on a title slide that may never be discussed.
      const heard = audio.status === 'fulfilled' ? audio.value : null
      const first = heard ? pageAtFirstSpeech(heard) : null
      const noted = notes.status === 'fulfilled' ? notes.value.pages[0]?.page : undefined
      const opening = first ?? noted
      if (opening) setPage(opening)
    })
    return () => { cancelled = true }
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

  // While listening to one page, the listener is free to look at others.
  useEffect(() => {
    if (!follow || !pageAudio || playback.mode !== 'lecture' || playback.status !== 'playing') return
    const target = pageAtTime(pageAudio, time)
    if (target !== null) setPage(target)
  }, [follow, pageAudio, playback.mode, playback.status, time])

  const pageEntry = pageAudio?.pages.find((entry) => entry.page === page)

  const playNote = (note: Annotation) => {
    setSelectedId(note.id)
    player.playLecture(note.start)
  }

  const playThisPage = () => {
    if (pageEntry) player.playPage(page, pageEntry.audio_ranges)
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLElement && event.target.closest('input, select, textarea, button, summary, [contenteditable]')) return
      if (event.key === ' ' && !(event.target instanceof HTMLButtonElement)) {
        event.preventDefault()
        togglePlayback()
      }
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
  }, [pageCount, togglePlayback])

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
          <label>필기 밀도{' '}
            <select aria-label="필기 밀도" value={density} disabled={!detailed}
              onChange={(event) => setDensity(event.target.value as Density)}>
              <option value="minimal">핵심</option>
              <option value="balanced">균형</option>
              <option value="detailed">풍부</option>
            </select>
          </label>
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
          <a className="button" href={annotatedPdfUrl(jobId, density)} download="annotated.pdf">
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
          noteFont={annotations?.note_font}
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
          {densityError && <p role="alert">{densityError}</p>}
          <section aria-label="상세 학습 노트" className="detailed-notes">
            <h3>상세 학습 노트</h3>
            {detailStatus && <p role="status">{detailStatus}</p>}
            {detailed && !detailed.complete && <p role="status">일부 구간은 아직 추출되지 않았습니다.</p>}
            {detailed && !detailed.items.some((item) => item.page === page) && <p>이 페이지의 상세 설명이 없습니다.</p>}
            {detailed?.items.filter((item) => item.page === page).map((item) => (
              <article key={item.id} lang={item.language}
                className={notes.some((note) => note.id === selectedId && note.content_id === item.id) ? 'selected-content' : ''}>
                <h4>{item.title}</h4>
                <p style={{ whiteSpace: 'pre-wrap' }}>{item.content}</p>
                {item.confidence < 0.3 && <p className="muted">페이지 연결을 확인해 주세요.</p>}
                {hasAudio && <button onClick={() => {
                  setFollow(false)
                  const linked = notes.find((note) => note.content_id === item.id)
                  setSelectedId(linked?.id ?? null)
                  player.playLecture(item.start)
                }}>▶ {formatTime(item.start)}–{formatTime(item.end)}</button>}
                <details><summary>발화 근거</summary><blockquote>{item.source_quote}</blockquote></details>
              </article>
            ))}
          </section>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {!error && notes.length === 0 && (
            <p className="muted">
              이 페이지에는 노트가 없습니다.
              {pageEntry?.audio_ranges.length ? ' 아래에서 이 페이지 강의를 들을 수 있습니다.' : ''}
            </p>
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
                    {note.provenance && (
                      <p className="muted">
                        {note.provenance.grounding_status === 'accepted'
                          ? `슬라이드 요소 연결 확인: ${note.provenance.target_element_id}`
                          : `요소 위치 미확정 · ${note.provenance.fallback_reason ?? note.provenance.grounding_status}`}
                      </p>
                    )}
                  </details>
                </div>
              </li>
            ))}
          </ul>
        </aside>
      )}

      <audio ref={audioRef} preload="metadata" src={audioUrl(jobId)} onError={() => setHasAudio(false)} />
      {hasAudio ? (
        <LecturePlayerBar
          page={page}
          entry={pageEntry}
          pageAudio={pageAudio}
          playback={playback}
          time={time}
          duration={player.duration}
          rate={player.rate}
          savedTime={player.savedTime}
          onPlayPage={playThisPage}
          onPlayLecture={player.playLecture}
          onToggle={player.toggle}
          onSeek={player.seek}
          onJumpToRange={player.jumpToRange}
          onCycleRate={player.cycleRate}
          onGoToPage={goToPage}
        />
      ) : (
        <footer className="player">
          <p className="muted">이 작업에는 오디오가 없습니다.</p>
        </footer>
      )}
    </div>
  )
}
