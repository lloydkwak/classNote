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

export type JobState =
  | 'queued'
  | 'extracting_pdf'
  | 'transcribing'
  | 'aligning'
  | 'writing_notes'
  | 'done'
  | 'error'

export interface JobStatus {
  status: JobState
  error: string | null
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

export async function createJob(pdf: File, audio: File): Promise<string> {
  const form = new FormData()
  form.append('pdf', pdf)
  form.append('audio', audio)
  const response = await fetch('/api/jobs', { method: 'POST', body: form })
  if (!response.ok) throw new Error(await errorMessage(response))
  return (await response.json()).job_id
}

export const getStatus = (jobId: string) => getJson<JobStatus>(`/api/jobs/${jobId}`)
export const getAnnotations = (jobId: string) =>
  getJson<Annotations>(`/api/jobs/${jobId}/annotations`)
