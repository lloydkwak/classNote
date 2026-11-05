import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import {
  createJob,
  createLiveJob,
  finishLiveJob,
  getStatus,
  sendChunk,
  type JobState,
  type JobStatus,
} from './api'
import { useRecorder } from './useRecorder'

const STEPS: { state: JobState; label: string }[] = [
  { state: 'extracting_pdf', label: '슬라이드 읽는 중' },
  { state: 'transcribing', label: '녹음 받아쓰는 중' },
  { state: 'aligning', label: '음성과 슬라이드 맞추는 중' },
  { state: 'writing_notes', label: '노트 작성 중' },
]
const POLL_MS = 1500
const LIVE_POLL_MS = 400
const CHUNK_MS = Number(import.meta.env.VITE_LIVE_CHUNK_SECONDS ?? 60) * 1000

// The empty value uses the server's default for the mode.
const SPEECH_MODELS = [
  { value: '', label: '기본값' },
  { value: 'small', label: 'small (빠름)' },
  { value: 'large-v3-turbo', label: 'large-v3-turbo (더 정확, 느림)' },
]

function formatTimer(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function isPdf(file: File) {
  return file.name.toLowerCase().endsWith('.pdf')
}

function messageOf(cause: unknown) {
  return cause instanceof Error ? cause.message : String(cause)
}

function DropZone(props: {
  title: string
  hint: string
  accept: string
  file: File | null
  onFiles: (files: File[]) => void
}) {
  const [dragging, setDragging] = useState(false)

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    props.onFiles([...event.dataTransfer.files])
  }

  return (
    <label
      className={`dropzone${dragging ? ' dragging' : ''}${props.file ? ' filled' : ''}`}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <input
        type="file"
        accept={props.accept}
        onChange={(event) => props.onFiles([...(event.target.files ?? [])])}
      />
      <strong>{props.title}</strong>
      <span>{props.file ? props.file.name : props.hint}</span>
    </label>
  )
}

function liveStatusText(status: JobStatus | null, stopped: boolean, chunksSent: number) {
  if (!status || status.status === 'preparing') return '슬라이드 준비 중…'
  if (stopped) {
    return `마무리하는 중… 지금까지 노트 ${status.note_count}개`
  }
  if (status.status === 'ready') return '녹음할 준비가 됐습니다.'
  if (chunksSent === 0) return '녹음 중…'
  return `${chunksSent}개 구간 중 ${status.chunks_processed}개 처리 완료 · 노트 ${status.note_count}개`
}

export function UploadScreen({ onDone }: { onDone: (jobId: string) => void }) {
  const [pdf, setPdf] = useState<File | null>(null)
  const [audio, setAudio] = useState<File | null>(null)
  const [speechModel, setSpeechModel] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const [state, setState] = useState<JobState | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [liveId, setLiveId] = useState<string | null>(null)
  const [liveStatus, setLiveStatus] = useState<JobStatus | null>(null)
  const [chunksSent, setChunksSent] = useState(0)
  const [stopped, setStopped] = useState(false)
  // Parts go up one at a time, in order, however slow the network is.
  const uploadQueue = useRef<Promise<unknown>>(Promise.resolve())
  const nextChunk = useRef(0)

  const queueUpload = useCallback((send: () => Promise<unknown>) => {
    uploadQueue.current = uploadQueue.current.then(send).catch((cause) => {
      setError(messageOf(cause))
    })
  }, [])

  const recorder = useRecorder({
    chunkMs: CHUNK_MS,
    onChunk: (blob) => {
      const id = liveId
      if (!id) return
      const index = nextChunk.current++
      queueUpload(async () => {
        await sendChunk(id, index, blob)
        setChunksSent(index + 1)
      })
    },
    onStop: () => {
      const id = liveId
      if (id) queueUpload(() => finishLiveJob(id))
    },
  })
  const recording = recorder.state !== 'idle'

  // Start preparing as soon as the slides are chosen, so recording can begin at once.
  useEffect(() => {
    if (!pdf) return
    let cancelled = false
    setLiveId(null)
    setLiveStatus(null)
    createLiveJob(pdf, speechModel)
      .then((id) => {
        if (!cancelled) setLiveId(id)
      })
      .catch((cause) => {
        if (!cancelled) setError(messageOf(cause))
      })
    return () => {
      cancelled = true
    }
  }, [pdf, speechModel])

  useEffect(() => {
    if (!liveId) return
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const status = await getStatus(liveId)
        if (cancelled) return
        setLiveStatus(status)
        if (status.status === 'done') return onDone(liveId)
        if (status.status === 'error') {
          setError(status.error ?? '처리에 실패했습니다.')
          return
        }
      } catch {
        // A brief network failure shouldn't end the talk, so keep polling.
      }
      timer = window.setTimeout(poll, LIVE_POLL_MS)
    }

    poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [liveId, onDone])

  // Dropping both files on either zone works: each file goes where it belongs.
  const addFiles = (files: File[]) => {
    for (const file of files) {
      if (isPdf(file)) setPdf(file)
      else setAudio(file)
    }
  }

  const start = async () => {
    if (!pdf || !audio) return
    setUploading(true)
    setError(null)
    try {
      setJobId(await createJob(pdf, audio, speechModel))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setUploading(false)
    }
  }

  const startRecording = () => {
    setError(null)
    setStopped(false)
    setChunksSent(0)
    nextChunk.current = 0
    void recorder.start()
  }

  const stopRecording = () => {
    setStopped(true)
    recorder.stop()
  }

  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const status = await getStatus(jobId)
        if (cancelled) return
        setState(status.status)
        if (status.status === 'done') return onDone(jobId)
        if (status.status === 'error') {
          setError(status.error ?? '처리에 실패했습니다.')
          return
        }
      } catch {
        // A brief network failure shouldn't end the job, so keep polling.
      }
      timer = window.setTimeout(poll, POLL_MS)
    }

    poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [jobId, onDone])

  const reset = () => {
    setJobId(null)
    setState(null)
    setError(null)
  }

  const running = jobId !== null && state !== 'error'
  const activeIndex = STEPS.findIndex((step) => step.state === state)
  const showingLive = recording || stopped

  return (
    <main className="upload">
      <h1>LectureNote</h1>
      <p className="lede">
        강의 녹음과 슬라이드를 올리면, 교수님이 한 말을 슬라이드의 해당 위치에 손글씨 노트로
        적어 드립니다.
      </p>

      {!jobId && (
        <>
          <div className="dropzones">
            <DropZone
              title="슬라이드"
              hint="PDF를 끌어다 놓거나 클릭해서 선택하세요"
              accept=".pdf,application/pdf"
              file={pdf}
              onFiles={addFiles}
            />
            <DropZone
              title="녹음 파일"
              hint="오디오 파일을 끌어다 놓거나 클릭해서 선택하세요"
              accept="audio/*,video/*,.m4a,.mp3,.wav"
              file={audio}
              onFiles={addFiles}
            />
          </div>

          <label className="model-choice">
            음성 인식 모델
            <select
              value={speechModel}
              disabled={showingLive}
              onChange={(event) => setSpeechModel(event.target.value)}
            >
              {SPEECH_MODELS.map((model) => (
                <option key={model.value} value={model.value}>
                  {model.label}
                </option>
              ))}
            </select>
          </label>

          <div className="recorder">
            {recording ? (
              <>
                <span className="recording-dot" aria-hidden="true" />
                <span className="timer" role="timer">
                  {formatTimer(recorder.seconds)}
                </span>
                <button onClick={stopRecording} disabled={recorder.state !== 'recording'}>
                  녹음 중지
                </button>
              </>
            ) : (
              !stopped && (
                <button disabled={!liveId} onClick={startRecording}>
                  {pdf ? '실시간 녹음 시작' : '슬라이드를 먼저 선택하세요'}
                </button>
              )
            )}
          </div>
          {pdf && (
            <p className="live-status" role="status">
              {liveStatusText(liveStatus, stopped, chunksSent)}
            </p>
          )}
          {recorder.error && (
            <p className="error" role="alert">
              {recorder.error}
            </p>
          )}
          {!showingLive && (
            <button className="primary" disabled={!pdf || !audio || uploading} onClick={start}>
              {uploading ? '업로드 중…' : '시작'}
            </button>
          )}
        </>
      )}

      {running && (
        <>
          <ol className="steps">
            {STEPS.map((step, index) => (
              <li
                key={step.state}
                className={
                  index < activeIndex ? 'finished' : index === activeIndex ? 'active' : undefined
                }
              >
                {step.label}
              </li>
            ))}
          </ol>
          <p className="hint">
            노트북 CPU에서는 받아쓰기에 녹음 길이만큼 시간이 걸릴 수 있습니다.
          </p>
        </>
      )}

      {error && (
        <div className="error" role="alert">
          <p>{error}</p>
          {jobId && <button onClick={reset}>다시 시도</button>}
        </div>
      )}
    </main>
  )
}
