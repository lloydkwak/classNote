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
  { state: 'extracting_pdf', label: 'Reading the slides' },
  { state: 'transcribing', label: 'Transcribing the recording' },
  { state: 'aligning', label: 'Matching speech to slides' },
  { state: 'writing_notes', label: 'Writing notes' },
]
const POLL_MS = 1500
const LIVE_POLL_MS = 400
const CHUNK_MS = 60_000

// The empty value uses the server's default for the mode.
const SPEECH_MODELS = [
  { value: '', label: 'Default' },
  { value: 'large-v3-turbo', label: 'large-v3-turbo (most accurate)' },
  { value: 'small', label: 'small (faster)' },
  { value: 'base', label: 'base (fastest, least accurate)' },
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
  if (!status || status.status === 'preparing') return 'Preparing the slides…'
  if (stopped) {
    return `Finishing up: ${status.note_count} notes so far…`
  }
  if (status.status === 'ready') return 'Ready to record.'
  if (chunksSent === 0) return 'Recording. The first part is sent after a minute.'
  return `Processed ${status.chunks_processed} of ${chunksSent} parts. ${status.note_count} notes so far.`
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
          setError(status.error ?? 'Processing failed.')
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
          setError(status.error ?? 'Processing failed.')
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
        Upload a lecture recording and its slides. What the professor said gets attached to the
        exact spot on each slide.
      </p>

      {!jobId && (
        <>
          <div className="dropzones">
            <DropZone
              title="Slides"
              hint="Drop a PDF here, or click to choose"
              accept=".pdf,application/pdf"
              file={pdf}
              onFiles={addFiles}
            />
            <DropZone
              title="Recording"
              hint="Drop an audio file here, or click to choose"
              accept="audio/*,video/*,.m4a,.mp3,.wav"
              file={audio}
              onFiles={addFiles}
            />
          </div>

          <label className="model-choice">
            Speech model
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
                  Stop recording
                </button>
              </>
            ) : (
              !stopped && (
                <button disabled={!liveId} onClick={startRecording}>
                  {pdf ? 'Record live' : 'Choose slides to record live'}
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
              {uploading ? 'Uploading…' : 'Start'}
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
            Transcribing on a laptop CPU can take about as long as the recording itself.
          </p>
        </>
      )}

      {error && (
        <div className="error" role="alert">
          <p>{error}</p>
          {jobId && <button onClick={reset}>Try again</button>}
        </div>
      )}
    </main>
  )
}
