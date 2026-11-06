export type NoteType = 'explanation' | 'example' | 'emphasis' | 'exam_hint' | 'question'

type Point = [number, number]

/** How to draw a note by hand, in PDF points. annotated.pdf draws the same data. */
export interface Sketch {
  stroke_width?: number
  stroke_opacity?: number
  placement?: { status: string; reason: string; fallback_mode?: string | null }
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
  provenance?: {
    action_id: string
    knowledge_id: string
    source_segment_ids: number[]
    target_element_id: string | null
    target_kind: string | null
    grounding_status: string
    fallback_reason: string | null
    registry_fingerprint: string
  } | null
  content_id?: string | null
  id: string
  block_ids: string[]
  bboxes: [number, number, number, number][]
  type: NoteType
  note: string
  quote: string
  language: string
  start: number
  end: number
  sketch: Sketch
}

export interface PageAnnotations {
  output_height?: number | null
  layout_metrics?: Record<string,number|null>
  page: number
  width: number
  height: number
  gutter: number
  notes: Annotation[]
}

export interface Annotations {
  layout_engine?: 'v1' | 'v2'
  ink_style?: InkStyle
  title?: string
  loss_audit?: PageAudit[]
  grounding?: { status: string; metrics: Record<string, unknown> } | null
  job_id: string
  pages: PageAnnotations[]
  incomplete: boolean
  note_font: string
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

export interface AudioRange {
  start: number
  end: number
  confidence: number
  source_window_ids: string[]
  reason: 'first_visit' | 'revisit'
}

export interface PageAudioEntry {
  page: number
  audio_ranges: AudioRange[]
  primary_start: number | null
  total_duration: number
}

export interface PageAudio {
  schema_version: number
  pages: PageAudioEntry[]
  duration: number
  unmatched_seconds: number
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
export type Density = 'minimal' | 'balanced' | 'detailed'
export type InkStyle = 'clean' | 'natural' | 'highlighter'
export interface PageAudit {
  page: number; title: string; alignment_uncertain: boolean; alignment_confidence_min: number | null
  aligned_segments: number; evidence_valid_knowledge: number; rendered_annotations: number
  reasons: Record<string,number>; extraction_unavailable: boolean
}
export interface ContentItem {
  id: string
  page: number
  type: string
  title: string
  content: string
  source_quote: string
  start: number
  end: number
  language: string
  confidence: number
}
export interface DetailedNotes {
  items: ContentItem[]
  complete: boolean
}
export const getDetailedNotes = (jobId: string) =>
  getJson<DetailedNotes>(`/api/jobs/${jobId}/detailed-notes`)
export const annotatedPdfUrl = (jobId: string, density: Density = 'balanced',layout: 'v1'|'v2' = 'v1',inkStyle: InkStyle = 'natural') =>
  `/api/jobs/${jobId}/annotated.pdf?density=${density}&layout=${layout}&ink_style=${inkStyle}`

async function errorMessage(response: Response): Promise<string> {
  if(response.status>=500)return '서버에서 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // The body wasn't JSON, so fall through to the status code.
  }
  return `요청을 처리하지 못했습니다 (${response.status})`
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

export async function createJob(pdf: File, audio: File, speechModel = '',options: {visualGrounding?:boolean;onProgress?:(percent:number)=>void} = {}): Promise<string> {
  const form = jobForm(pdf, speechModel)
  form.append('audio', audio)
  if(options.visualGrounding!==undefined)form.append('visual_grounding',String(options.visualGrounding))
  if(!options.onProgress)return (await postForm('/api/jobs', form)).job_id
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/api/jobs');xhr.responseType='json'
    xhr.upload.onprogress=e=>{if(e.lengthComputable)options.onProgress?.(Math.round(e.loaded/e.total*100))}
    xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300&&typeof xhr.response?.job_id==='string')resolve(xhr.response.job_id);else reject(new Error('업로드하지 못했습니다.'))}
    xhr.onerror=()=>reject(new Error('네트워크 연결을 확인해 주세요.'));xhr.send(form)
  })
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
export const getPageAudio = (jobId: string) => getJson<PageAudio>(`/api/jobs/${jobId}/page-audio`)
export const getAnnotations = (jobId: string, density: Density = 'balanced',layout: 'v1'|'v2' = 'v1',inkStyle: InkStyle = 'natural') =>
  getJson<Annotations>(`/api/jobs/${jobId}/annotations?density=${density}&layout=${layout}&ink_style=${inkStyle}`)
export const retryJob = (jobId:string)=>postForm(`/api/jobs/${jobId}/retry`)
