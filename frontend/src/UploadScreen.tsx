import { useEffect, useState, type DragEvent } from 'react'
import { createJob, getStatus, type JobState } from './api'

const STEPS: { state: JobState; label: string }[] = [
  { state: 'extracting_pdf', label: 'Reading the slides' },
  { state: 'transcribing', label: 'Transcribing the recording' },
  { state: 'aligning', label: 'Matching speech to slides' },
  { state: 'writing_notes', label: 'Writing notes' },
]
const POLL_MS = 1500

function isPdf(file: File) {
  return file.name.toLowerCase().endsWith('.pdf')
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

export function UploadScreen({ onDone }: { onDone: (jobId: string) => void }) {
  const [pdf, setPdf] = useState<File | null>(null)
  const [audio, setAudio] = useState<File | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [state, setState] = useState<JobState | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      setJobId(await createJob(pdf, audio))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setUploading(false)
    }
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
          <button className="primary" disabled={!pdf || !audio || uploading} onClick={start}>
            {uploading ? 'Uploading…' : 'Start'}
          </button>
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
