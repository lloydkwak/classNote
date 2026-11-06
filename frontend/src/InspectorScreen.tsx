import { useEffect, useRef, useState } from 'react'
import { audioUrl, pdfUrl, type Alignment } from './api'
import { SlideSheet } from './SlideSheet'
import { formatTime } from './format'

interface Context {
  dataset: { revision: number; [key: string]: unknown }
  predictions: Record<string, Alignment>
  segments: { start: number; end: number; text: string }[]
  detailed_notes: unknown
}

export function InspectorScreen({ jobId }: { jobId: string }) {
  const [context, setContext] = useState<Context | null>(null)
  const [json, setJson] = useState('')
  const [message, setMessage] = useState('불러오는 중… EVALUATION_MODE=1이 필요합니다.')
  const [page, setPage] = useState(1)
  const [pageCount, setPageCount] = useState(1)
  const [time, setTime] = useState(0)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(30)
  const [pages, setPages] = useState('1')
  const [status, setStatus] = useState('draft')
  const [author, setAuthor] = useState('')
  const audio = useRef<HTMLAudioElement>(null)

  const reload = async () => {
    setBusy(true)
    try {
      const response = await fetch(`/api/jobs/${jobId}/evaluation`)
      if (!response.ok) throw new Error(`평가 도구를 불러오지 못했습니다 (${response.status})`)
      const value: Context = await response.json()
      setContext(value)
      setJson(JSON.stringify(value.dataset, null, 2))
      setConfirmed(false)
      setMessage(`라벨 revision ${value.dataset.revision}`)
    } catch (failure) { setMessage(String(failure)) }
    finally { setBusy(false) }
  }

  useEffect(() => { void reload() }, [jobId])

  const save = async () => {
    setBusy(true)
    try {
      const response = await fetch(`/api/jobs/${jobId}/evaluation`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset: JSON.parse(json), confirm_human_verification: confirmed }),
      })
      if (!response.ok) throw new Error(JSON.stringify(await response.json()))
      const dataset = await response.json()
      setJson(JSON.stringify(dataset, null, 2))
      setConfirmed(false)
      setMessage(`저장 완료: revision ${dataset.revision}`)
    } catch (failure) { setMessage(String(failure)) }
    finally { setBusy(false) }
  }

  const addLabel = () => {
    try {
      const dataset = JSON.parse(json)
      const label = {
        id: `label-${Date.now()}`, start, end,
        pages: pages.trim() ? pages.split(',').map((value) => Number(value.trim())) : [],
        review: { status, source: 'human', author,
          reviewed_at: status === 'verified' ? new Date().toISOString() : null, note: '' },
      }
      dataset.alignment_labels.push(label)
      setJson(JSON.stringify(dataset, null, 2))
      setConfirmed(false)
      setMessage('라벨을 추가했습니다. 겹치는 구간은 JSON에서 수정하거나 분할한 뒤 저장하세요.')
    } catch (failure) { setMessage(String(failure)) }
  }

  return <main className="inspector">
    <header><h1>Alignment Inspector · 개발자 평가</h1>
      <a href={`#/jobs/${jobId}/debug`}>정렬 차트</a>{' · '}<a href={`#/jobs/${jobId}`}>강의 뷰어</a>
    </header>
    <p role="status">{message}</p>
    <div className="inspector-columns">
      <section>
        <label>PDF 페이지 <input type="number" min={1} max={pageCount} value={page}
          onChange={(event) => setPage(Math.max(1, Math.min(pageCount, Number(event.target.value))))} /></label>
        <div className="inspector-slide"><SlideSheet file={pdfUrl(jobId)} page={page}
          onPageCount={setPageCount} onError={setMessage} /></div>
        <audio ref={audio} controls src={audioUrl(jobId)} preload="metadata"
          onTimeUpdate={() => setTime(audio.current?.currentTime ?? 0)} />
        <p>현재 {formatTime(time)} · 예측 비교 (실험값은 라벨이 아닙니다)</p>
        <ul>{Object.entries(context?.predictions ?? {}).map(([variant, alignment]) => {
          const window = alignment.windows.find((item) => item.start <= time && time < item.end)
          return <li key={variant}>{variant}: {window ? window.page ?? 'off-topic' : '구간 없음'}
            {window && ` (confidence ${window.confidence.toFixed(2)})`}</li>
        })}</ul>
        <div className="inspector-transcript" aria-label="시간별 전사문">
          {context?.segments.map((segment, index) => <p key={index}>
            <button onClick={() => {
              if (audio.current) audio.current.currentTime = segment.start
              setTime(segment.start); setStart(segment.start); setEnd(segment.end)
            }}>{formatTime(segment.start)}–{formatTime(segment.end)}</button>{' '}{segment.text}
          </p>)}
        </div>
        <details><summary>상세 노트·탈락 항목·실행 정보</summary>
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(context?.detailed_notes ?? null, null, 2)}</pre>
        </details>
      </section>
      <section>
        <h2>정답 라벨</h2>
        <p>빈 페이지 = off-topic, 여러 페이지는 쉼표로 구분. uncertain은 기본 평가에서 제외됩니다.</p>
        <div className="inspector-fields">
          <label>시작(초)<input type="number" min={0} step="0.1" value={start} onChange={(event) => setStart(Number(event.target.value))} /></label>
          <label>종료(초)<input type="number" min={0} step="0.1" value={end} onChange={(event) => setEnd(Number(event.target.value))} /></label>
          <label>페이지<input value={pages} onChange={(event) => setPages(event.target.value)} /></label>
          <label>상태<select value={status} onChange={(event) => setStatus(event.target.value)}>
            {['draft', 'provisional', 'uncertain', 'verified'].map((value) => <option key={value}>{value}</option>)}
          </select></label>
          <label>검토자<input value={author} onChange={(event) => setAuthor(event.target.value)} /></label>
          <button disabled={!context || busy} onClick={addLabel}>구간 라벨 추가</button>
        </div>
        <p>기존 구간 수정·분할, transitions, meaning_units, matches, meaning_review_ranges는 아래 JSON에서 편집합니다.</p>
        <label htmlFor="evaluation-json">버전 관리 평가 JSON</label>
        <textarea id="evaluation-json" rows={30} value={json} spellCheck={false}
          onChange={(event) => { setJson(event.target.value); setConfirmed(false) }} />
        <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          verified로 지정한 라벨과 의미 단위를 직접 확인했습니다.</label>
        <p><button disabled={busy || !context} onClick={() => void save()}>저장</button>{' '}
          <button disabled={busy} onClick={() => void reload()}>서버에서 재로드 (미저장 편집 취소)</button></p>
      </section>
    </div>
  </main>
}
