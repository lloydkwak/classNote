import { useEffect, useMemo, useRef, useState } from 'react'
import {
  annotatedPdfUrl,
  audioUrl,
  getAnnotations,
  getPageAudio,
  getDetailedNotes,
  retryJob,
  type DetailedNotes,
  type Density,
  type InkStyle,
  pdfUrl,
  type Annotation,
  type Annotations,
  type PageAudio,
} from './api'
import { formatTime } from './format'
import { LecturePlayerBar } from './LecturePlayerBar'
import { isPlaying, SlideSheet, TYPE_LABELS } from './SlideSheet'
import { useLecturePlayer } from './useLecturePlayer'
import { Brand,DensityControl,Icon,readPreference,savePreference } from './ui'
import { Document,Page } from 'react-pdf'
import { PDF_OPTIONS } from './SlideSheet'

import {clampPage,exportPdfPageNumber,pageAtTime} from './pageNavigation'

export function ViewerScreen({ jobId }: { jobId: string }) {
  const [annotations, setAnnotations] = useState<Annotations | null>(null)
  const [detailed, setDetailed] = useState<DetailedNotes | null>(null)
  const [detailStatus, setDetailStatus] = useState('상세 노트 불러오는 중…')
  const [density, setDensity] = useState<Density>(()=>{
    const saved=readPreference('classnote-density','balanced')
    return ['minimal','balanced','detailed'].includes(saved)?saved as Density:'balanced'
  })
  const [inkStyle,setInkStyle]=useState<InkStyle>('natural')
  const [layout,setLayout]=useState<'v1'|'v2'>('v2')
  const [original,setOriginal]=useState(false)
  const [zoom,setZoom]=useState(1)
  const [busy,setBusy]=useState(true)
  const [mobileTab,setMobileTab]=useState('slide')
  const [reload,setReload]=useState(0)
  const [retrying,setRetrying]=useState(false)
  const [densityError, setDensityError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pageCount, setPageCount] = useState(0)
  // UI 0 is Overview, while source API pages remain 1..N (source_slide_index=page-1).
  const [page, setPage] = useState(0)
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
    setBusy(true)
    void getAnnotations(jobId, density,layout,inkStyle).then((value) => {
      if (!cancelled) {setAnnotations(value);setBusy(false);savePreference('classnote-density',density)}
    }).catch((failure: Error) => { if (!cancelled) {setDensityError(failure.message);setBusy(false)} })
    return () => { cancelled = true }
  }, [jobId, density,layout,inkStyle,reload])

  useEffect(() => {
    // Slides and audio work without notes, and notes work without page audio.
    let cancelled = false
    void getPageAudio(jobId).then(value=>{if(!cancelled)setPageAudio(value)}).catch(()=>{})
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
    setPage(clampPage(next,pageCount))
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
        setPage((value) => Math.max(value - 1, 0))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pageCount, togglePlayback])

  return (
    <div className={`viewer workspace${showNotes ? '' : ' notes-hidden'} mobile-${mobileTab}`}>
      <header className="toolbar">
        <Brand/>
        <div className="workspace-title"><small>LECTURE WORKSPACE</small><strong title={annotations?.title}>{annotations?.title||'나의 강의 노트'}</strong></div>
        <div className="toolbar-actions"><DensityControl value={density} onChange={setDensity} disabled={!detailed||busy}/>
          <a className={`button primary download${busy||densityError?' disabled':''}`} aria-disabled={busy||Boolean(densityError)} onClick={e=>{if(busy||densityError)e.preventDefault()}} href={annotatedPdfUrl(jobId,density,layout,inkStyle)} download="classNote.pdf"><Icon name="download" size={17}/><span>PDF 다운로드</span></a>
        </div>
      </header>
      <nav className="workspace-mobile-tabs" aria-label="작업공간 패널">{[['slide','슬라이드'],['pages','페이지'],['notes','상세 노트']].map(([key,label])=><button key={key} aria-pressed={mobileTab===key} onClick={()=>setMobileTab(key)}>{label}</button>)}</nav>
      <aside className="thumbnail-pane"><div className="rail-title">개요 · 슬라이드 <span>{pageCount}</span></div>
        <button className={`thumbnail ${page===0?'current':''}`} aria-label="Overview 열기" aria-current={page===0?'page':undefined} onClick={()=>{goToPage(0);setMobileTab('slide')}}>
          <span className="thumbnail-page"><Document file={annotatedPdfUrl(jobId,density,layout,inkStyle)} options={PDF_OPTIONS}><Page pageNumber={1} width={112} renderTextLayer={false} renderAnnotationLayer={false}/></Document></span><span><b>Overview</b><small>강의 전체 개요</small></span>
        </button>
        <Document file={pdfUrl(jobId)} options={PDF_OPTIONS} onLoadSuccess={d=>setPageCount(d.numPages)} loading={<p className="muted">미리보기 준비 중…</p>}>
        {Array.from({length:pageCount},(_,i)=>i+1).map(number=><button key={number} className={`thumbnail ${page===number?'current':''}`} aria-label={`${number}쪽으로 이동`} aria-current={page===number?'page':undefined} onClick={()=>{goToPage(number);setMobileTab('slide')}}><span className="thumbnail-page"><Page pageNumber={number} width={112} renderTextLayer={false} renderAnnotationLayer={false} loading=""/></span><span><b>{String(number).padStart(2,'0')}</b><small>{annotatedPages.has(number)?'필기 있음':'슬라이드'}</small></span></button>)}
      </Document></aside>
      <section className="slide-pane"><div className="canvas-toolbar"><div className="segmented"><button aria-pressed={!original} onClick={()=>setOriginal(false)}>AI 필기</button><button disabled={page===0} aria-pressed={original} onClick={()=>setOriginal(true)}>원본</button></div>
        <div className="pager">
          <button aria-label="이전 페이지" disabled={page <= 0} onClick={() => goToPage(page - 1)}>
            ‹
          </button>
          <span>
            {page===0?'Overview':`슬라이드 ${page}`} / {pageCount || '…'}
          </span>
          <button
            aria-label="다음 페이지"
            disabled={page >= pageCount}
            onClick={() => goToPage(page + 1)}
          >
            ›
          </button>
        </div>
        <div className="canvas-controls"><button aria-label="축소" disabled={zoom<=.6} onClick={()=>setZoom(v=>v-.2)}>−</button><button onClick={()=>setZoom(1)}>{zoom===1?'맞춤':`${Math.round(zoom*100)}%`}</button><button aria-label="확대" disabled={zoom>=2} onClick={()=>setZoom(v=>v+.2)}>+</button></div>
      </div><div className="view-options"><label>펜 스타일 <select aria-label="펜 스타일" value={inkStyle} disabled={busy} onChange={e=>setInkStyle(e.target.value as InkStyle)}><option value="clean">Clean Ink</option><option value="natural">Natural Ink</option><option value="highlighter">Highlighter</option></select></label><label>배치 <select aria-label="필기 배치" value={layout} disabled={busy} onChange={e=>setLayout(e.target.value as 'v1'|'v2')}><option value="v2">새 배치</option><option value="v1">기존 배치</option></select></label><label className="switch">
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
        </div>

      {annotations?.incomplete && (
        <p className="banner" role="status">
          일부 구간의 설명을 아직 완성하지 못했습니다. 업로드한 파일은 유지됩니다.{' '}
          <button disabled={retrying} onClick={async()=>{setRetrying(true);try{await retryJob(jobId);window.location.reload()}catch{setDensityError('남은 설명을 다시 분석하지 못했습니다. 잠시 후 다시 시도해 주세요.');setRetrying(false)}}}>{retrying?'다시 시작 중…':'남은 설명 다시 분석'}</button>
        </p>
      )}

      {busy&&<p className="render-status" role="status"><span className="activity-dot"/> 필기 배치를 준비하고 있어요…</p>}
      {densityError&&<div role="alert" className="error-box">{densityError}<button onClick={()=>setReload(v=>v+1)}>다시 불러오기</button></div>}
        <div className="mobile-density"><DensityControl value={density} onChange={setDensity} disabled={!detailed||busy}/></div>
        {page===0?<SlideSheet file={annotatedPdfUrl(jobId,density,layout,inkStyle)} page={exportPdfPageNumber(0)} original zoom={zoom}
          onPageCount={()=>{}} onError={setError}/>:<SlideSheet
          file={pdfUrl(jobId)}
          page={page}
          annotations={original?undefined:pageAnnotations}
          zoom={zoom}
          original={original}
          noteFont={annotations?.note_font}
          time={time}
          selectedId={selectedId}
          dimInactive={follow && Boolean(activeNote)}
          onPlayNote={playNote}
          onPageCount={setPageCount}
          onError={setError}
        />}
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
        <p className="canvas-caption">{page===0?'Lecture Overview · 원본 슬라이드 번호와 오디오 연결은 유지됩니다.':`${page}쪽 · ${original?'원본 슬라이드':'필기를 누르면 연결된 강의를 재생합니다.'}`}</p>
      </section>

      {showNotes && (
        <aside className="notes-pane">
          {page===0&&<section className="overview-topics"><span className="eyebrow">LECTURE OVERVIEW</span><h2>{annotations?.overview?.title??'강의 전체 개요'}</h2>
            {!annotations?.overview?.study&&annotations?.overview?.entries.map(entry=><article key={entry.source_slide_index}><h4>{entry.title}</h4><p>{entry.text}</p>
              <button onClick={()=>goToPage(entry.source_page)}>원본 슬라이드 {entry.source_page} 보기</button>{' '}
              {hasAudio&&entry.audio_verified?<button onClick={()=>{goToPage(entry.source_page);player.playLecture(entry.start)}}>▶ {formatTime(entry.start)}–{formatTime(entry.end)}</button>:<p className="muted">오디오/페이지 연결 확인 필요</p>}
            </article>)}
            {annotations?.overview?.study&&<div className="study-overview"><p>{annotations.overview.study.introduction.text}</p>
              {([['학습 목표',annotations.overview.study.objectives],['핵심 개념',annotations.overview.study.concepts],['개념 연결',annotations.overview.study.connections],['복습 포인트',annotations.overview.study.review_points]] as const).map(([title,points])=>points.length>0&&<section key={title}><h3>{title}</h3>{points.map((point,n)=><article key={n}>{point.title&&<h4>{point.title}</h4>}<p>{point.text}</p></article>)}</section>)}
              {annotations.overview.study.formulas.length>0&&<section><h3>꼭 기억할 공식</h3>{annotations.overview.study.formulas.map((formula,n)=><article key={n}><strong>{formula.expression}</strong><p>{formula.meaning}</p></article>)}</section>}
              {annotations.overview.study.examples.length>0&&<section><h3>대표 예제</h3>{annotations.overview.study.examples.map((example,n)=><article key={n}><h4>{example.title}</h4><p>{example.given} → {example.result}</p><p>{example.explanation}</p></article>)}</section>}
            </div>}
            {annotations?.overview?.status==='insufficient_evidence'&&<p>요약을 확정할 발화 근거가 충분하지 않습니다. 원본 슬라이드와 오디오를 먼저 확인해 주세요.</p>}
          </section>}
          <div hidden={page===0}>
          <div className="notes-heading"><span className="eyebrow">YOUR LECTURE NOTES</span><h2>{page}쪽의 핵심 설명</h2></div>
          {annotations?.loss_audit?.filter(a=>a.page===page).map(a=><div className="source-status" key={a.page}>
            {a.alignment_uncertain&&<p className="uncertain">페이지와 음성의 연결을 확인해 주세요. 이 구간의 정렬 확신이 낮습니다.</p>}
            {!a.evidence_valid_knowledge&&a.aligned_segments>0&&<p>{a.extraction_unavailable?'강의 구간은 있지만 설명 추출을 완료하지 못했습니다.':Object.keys(a.reasons).length?'강의 구간은 있지만 근거 검증을 통과한 필기가 없습니다.':'강의 구간은 있지만 추출된 상세 노트가 없습니다.'} 아래 오디오에서 원문을 확인할 수 있어요.</p>}
            <details><summary>이 페이지의 분석 내역</summary><p>연결된 발화 {a.aligned_segments} · 검증된 설명 {a.evidence_valid_knowledge} · 표시된 필기 {a.rendered_annotations}</p><a href={`#/jobs/${jobId}/inspect`}>연결 구간 확인하기 ↗</a></details>
          </div>)}
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
                    <span lang={note.language}>{note.note||note.action_reason||'강의에서 설명한 요소 강조'}</span>
                    {note.provenance?.target_kind==='figure'&&<small className="muted">그림 영역과 연결 · 내부 기호의 의미는 확인이 필요해요.</small>}
                  </button>
                  <details className="note-quote">
                    <summary>교수님 원문</summary>
                    <blockquote>{note.quote}</blockquote>
                    {note.provenance && (
                      <p className="muted">
                        {note.provenance.grounding_status === 'accepted'
                          ? note.provenance.target_kind==='figure'?'그림 영역과 연결되었습니다. 그림 내부 기호와 의미는 추가 확인이 필요합니다.':'슬라이드의 연결 위치가 확인된 필기입니다.'
                          : '정확한 슬라이드 위치는 확인되지 않았습니다. 발화 근거를 함께 확인해 주세요.'}
                      </p>
                    )}
                  </details>
                </div>
              </li>
            ))}
          </ul>
          </div></aside>
      )}

      <audio ref={audioRef} preload="metadata" src={audioUrl(jobId)} onError={() => setHasAudio(false)} />
      {hasAudio ? (
        <LecturePlayerBar
          overview={page===0}
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
