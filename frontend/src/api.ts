export type NoteType = 'explanation' | 'example' | 'emphasis' | 'exam_hint' | 'question'

type Point = [number, number]

/** How to draw a note by hand, in PDF points. annotated.pdf draws the same data. */
export interface Sketch {
  mark: 'underline' | 'circle' | 'bracket' | 'none'
  strokes: Point[][]
  arrow: Point[][]
  side: 'left' | 'right'
  x: number
  y: number
  width: number
  height: number
  font_size: number
  line_height: number
  lines: string[]
}

export interface Annotation {
  id: string
  block_ids: string[]
  bboxes: [number, number, number, number][]
  type: NoteType
  note: string
  language: string
  start: number
  end: number
  sketch: Sketch
}

export interface PageAnnotations {
  page: number
  width: number
  height: number
  gutter: number
  notes: Annotation[]
}

export interface Annotations {
  job_id: string
  pages: PageAnnotations[]
  incomplete: boolean
}

export interface AlignedWindow {
  window_id: number
  start: number
  end: number
  text: string
  page: number | null
  raw_page: number
  confidence: number
  similarities: number[]
}

export interface Alignment {
  windows: AlignedWindow[]
  pages: number[]
}

export type JobState =
  | 'queued'
  | 'extracting_pdf'
  | 'transcribing'
  | 'aligning'
  | 'writing_notes'
  | 'preparing'
  | 'ready'
  | 'recording'
  | 'done'
  | 'error'

export interface JobStatus {
  status: JobState
  error: string | null
  chunks_received: number
  chunks_processed: number
  note_count: number
}

export const pdfUrl = (jobId: string) => `/api/jobs/${jobId}/pdf`
export const audioUrl = (jobId: string) => `/api/jobs/${jobId}/audio`
export const annotatedPdfUrl = (jobId: string) => `/api/jobs/${jobId}/annotated.pdf`

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // The body wasn't JSON, so fall through to the status code.
  }
  return `Request failed (${response.status})`
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(await errorMessage(response))
  return response.json()
}

async function postForm(url: string, form?: FormData) {
  const response = await fetch(url, { method: 'POST', body: form })
  if (!response.ok) throw new Error(await errorMessage(response))
  return response.json()
}

function jobForm(pdf: File, speechModel: string) {
  const form = new FormData()
  form.append('pdf', pdf)
  if (speechModel) form.append('stt_model', speechModel)
  return form
}

export async function createJob(pdf: File, audio: File, speechModel = ''): Promise<string> {
  const form = jobForm(pdf, speechModel)
  form.append('audio', audio)
  return (await postForm('/api/jobs', form)).job_id
}

export async function createLiveJob(pdf: File, speechModel = ''): Promise<string> {
  return (await postForm('/api/jobs/live', jobForm(pdf, speechModel))).job_id
}

export async function sendChunk(jobId: string, index: number, chunk: Blob) {
  const form = new FormData()
  form.append('index', String(index))
  form.append('chunk', chunk, 'chunk.webm')
  return postForm(`/api/jobs/${jobId}/chunks`, form)
}

export const finishLiveJob = (jobId: string) => postForm(`/api/jobs/${jobId}/finish`)

export const getStatus = (jobId: string) => getJson<JobStatus>(`/api/jobs/${jobId}`)
export const getAlignment = (jobId: string) => getJson<Alignment>(`/api/jobs/${jobId}/debug`)
export const getAnnotations = (jobId: string) =>
  getJson<Annotations>(`/api/jobs/${jobId}/annotations`)
