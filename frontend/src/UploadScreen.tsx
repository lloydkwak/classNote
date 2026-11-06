import { useRef, useState, type DragEvent } from 'react'
import { createJob, type Density, type AnnotationMode } from './api'
import { LiveUploadScreen } from './LiveUploadScreen'
import { Brand, DensityControl, Icon, readPreference, savePreference } from './ui'
import { fileSize, validateFile } from './fileRules'

function FileCard({ kind, file, onFile, onError }: { kind:'pdf'|'audio'; file:File|null; onFile:(f:File|null)=>void; onError:(e:string|null)=>void }) {
  const input=useRef<HTMLInputElement>(null)
  const [dragging,setDragging]=useState(false)
  const choose=(files:File[])=>{
    const selected=files[0];if(!selected)return
    const error=validateFile(selected.name,selected.size,kind);onError(error);if(!error)onFile(selected)
  }
  const drop=(e:DragEvent)=>{e.preventDefault();setDragging(false);choose([...e.dataTransfer.files])}
  return <div className={`file-card ${dragging?'dragging':''} ${file?'filled':''}`} onDragOver={e=>{e.preventDefault();setDragging(true)}} onDragLeave={()=>setDragging(false)} onDrop={drop}>
    <span className="file-icon"><Icon name={kind==='pdf'?'note':'audio'} size={28}/></span>
    <label className="file-picker"><input ref={input} type="file" aria-label={kind==='pdf'?'PDF 슬라이드 선택':'강의 녹음 선택'} accept={kind==='pdf'?'.pdf':'.m4a,.mp3,.wav,.ogg,.flac,.aac,.mp4,.webm,.opus'} onChange={e=>{choose([...(e.target.files??[])]);e.target.value=''}}/>
      <strong>{file?file.name:kind==='pdf'?'PDF 슬라이드':'강의 녹음'}</strong>
      <span>{file?`${fileSize(file.size)} · ${kind==='pdf'?'PDF':file.name.split('.').pop()?.toUpperCase()}`:'끌어다 놓거나 클릭해서 선택'}</span>
      <small>{file?'클릭하여 교체':kind==='pdf'?'PDF · 최대 100MB':'M4A, MP3, WAV 등 · 최대 1GB'}</small>
    </label>
    {file?<button className="icon-button remove-file" aria-label={kind==='pdf'?'PDF 삭제':'녹음 삭제'} onClick={()=>onFile(null)}><Icon name="close" size={17}/></button>:<span className="file-add" aria-hidden="true">+</span>}
  </div>
}
export function UploadScreen({onDone}:{onDone:(id:string)=>void}) {
  const [pdf,setPdf]=useState<File|null>(null),[audio,setAudio]=useState<File|null>(null)
  const [density,setDensity]=useState<Density>(readPreference('classnote-density','balanced') as Density)
  const [annotationMode,setAnnotationMode]=useState<AnnotationMode>('legacy')
  const [grounding,setGrounding]=useState(true),[live,setLive]=useState(false)
  const [error,setError]=useState<string|null>(null),[progress,setProgress]=useState<number|null>(null),[uploading,setUploading]=useState(false)
  const recent=readPreference('classnote-job','')
  const start=async()=>{
    if(!pdf||!audio||uploading)return;setError(null);setUploading(true);setProgress(null)
    try {const id=await createJob(pdf,audio,'',{visualGrounding:grounding,annotationMode,onProgress:setProgress});savePreference('classnote-density',density);savePreference('classnote-job',id);onDone(id)}
    catch {setError('파일을 업로드하지 못했습니다. 연결을 확인하고 다시 시도해 주세요.')} finally {setUploading(false)}
  }
  return <div className="landing"><header className="site-header"><Brand/><nav aria-label="주 메뉴">{recent&&<a href={`#/jobs/${recent}`}>최근 강의 이어보기 ↗</a>}<button className="text-button" onClick={()=>setLive(!live)}>{live?'파일 업로드':'실시간 녹음'}</button></nav></header>
    {live?<><button className="back-link" onClick={()=>setLive(false)}>← 파일 업로드로 돌아가기</button><LiveUploadScreen onDone={onDone}/></>:<main>
      <section className="hero" aria-labelledby="hero-title"><div className="eyebrow"><span/> YOUR LECTURE. EVERY DETAIL.</div><h1 id="hero-title">강의 녹음과<br/><span>슬라이드만 올리세요.</span></h1><p>교수님의 설명을 듣고, 중요한 내용을<br className="mobile-break"/> 슬라이드 위에 필기로 정리합니다.</p></section>
      <section className="upload-panel" aria-label="강의 파일 업로드"><div className="panel-heading"><span><Icon name="spark" size={18}/> 새로운 강의 노트</span><span className="step-caption">01 — 파일 준비</span></div>
        <fieldset disabled={uploading} className="upload-fields"><legend className="sr-only">PDF와 녹음 파일</legend><div className="file-grid"><FileCard kind="pdf" file={pdf} onFile={setPdf} onError={setError}/><FileCard kind="audio" file={audio} onFile={setAudio} onError={setError}/></div></fieldset>
        <div className="upload-preferences"><span>필기를 얼마나 자세하게 할까요?</span><DensityControl value={density} onChange={setDensity} disabled={uploading}/></div>
        <details className="advanced"><summary>필기 설정</summary><label><input type="checkbox" checked={grounding} disabled={uploading} onChange={e=>setGrounding(e.target.checked)}/> 슬라이드 요소에 정밀하게 연결 <span className="muted">· 추가 시간이 필요할 수 있어요</span></label><label>필기 방식 <select aria-label="필기 방식" value={annotationMode} disabled={uploading} onChange={e=>setAnnotationMode(e.target.value as AnnotationMode)}><option value="legacy">기본 필기</option><option value="ai_planner">학습 포인트 중심 · 실험 기능</option><option value="visual_agent">학습 포인트 + 화면 검토 · 실험 기능</option></select></label><p className="muted">실험 기능은 시간이 더 필요하며, 연결이 불확실한 표시는 생략할 수 있어요.</p></details>
        {error&&<div className="error-box" role="alert">{error}</div>}
        <button className="primary generate" onClick={start} disabled={!pdf||!audio||uploading}><Icon name={uploading?'upload':'spark'}/>{uploading?progress===null?'파일 전송 준비 중…':`파일 업로드 중 ${progress}%`:'AI 필기 생성하기'}<Icon name="arrow" size={19}/></button>
        <p className="upload-hint" role="status">{!pdf||!audio?'PDF와 녹음 파일을 모두 선택하면 시작할 수 있어요.':'준비됐어요. 강의 속 중요한 순간을 노트로 남겨보세요.'}</p>
      </section>
      <section className="benefits" aria-label="classNote로 복습하기">{[['shield','발화에 근거한 필기','교수님의 설명과 필기의 출처를 함께 확인하세요.'],['clock','필요한 순간, 다시 듣기','페이지와 필기를 눌러 연결된 강의로 돌아가세요.'],['note','슬라이드 그대로, 더 풍부하게','원본을 보존한 필기 PDF와 상세 노트를 받아보세요.']].map(([icon,title,description])=><article key={title}><Icon name={icon}/><h2>{title}</h2><p>{description}</p></article>)}</section>
      <section className="product-glimpse" aria-label="필기 표현 예시"><div><span className="eyebrow">LESS REWINDING. MORE UNDERSTANDING.</span><h2>다시 들을 순간까지,<br/>한 장의 노트에.</h2><p>짧은 필기는 슬라이드에, 긴 설명은 상세 노트에.<br/>읽다가 궁금해지면 원래 강의를 바로 들어보세요.</p></div><div className="sample-paper"><small>필기 표현 예시 · 수학 강의</small><h3>Additive inverse</h3><p className="sample-formula">z + (−z) = 0</p><span className="sample-underline"/><p className="sample-note">실수부와 허수부가<br/>각각 상쇄되는 값</p><span className="sample-evidence"><Icon name="play" size={12}/> 발화 근거와 함께 복습</span></div></section>
    </main>}<footer className="site-footer"><Brand/><span>강의의 맥락이 남는 필기.</span></footer></div>
}
