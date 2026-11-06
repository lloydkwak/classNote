export const PDF_LIMIT = 100*1024*1024
export const AUDIO_LIMIT = 1024*1024*1024
export function validateFile(name: string,size: number,kind: 'pdf'|'audio'): string|null {
  const extension=name.toLowerCase().split('.').pop()??''
  if (kind==='pdf' ? extension!=='pdf' : !['m4a','mp3','wav','ogg','flac','aac','mp4','webm','opus'].includes(extension))
    return kind==='pdf'?'PDF 파일을 선택해 주세요.':'지원하는 녹음 파일을 선택해 주세요 (M4A, MP3, WAV 등).'
  if (!size) return '빈 파일은 업로드할 수 없습니다.'
  if (size>(kind==='pdf'?PDF_LIMIT:AUDIO_LIMIT)) return kind==='pdf'?'PDF는 100MB까지 업로드할 수 있습니다.':'녹음은 1GB까지 업로드할 수 있습니다.'
  return null
}
export const fileSize=(size: number)=>size<1024*1024?`${Math.ceil(size/1024)} KB`:`${(size/1024/1024).toFixed(1)} MB`
