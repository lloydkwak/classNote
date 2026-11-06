import { useEffect,useState } from 'react'
import { getStatus,retryJob,type JobStatus } from './api'
import { Brand,Icon } from './ui'
import { ViewerScreen } from './ViewerScreen'

const stages=[['queued','파일 준비'],['extracting_pdf','슬라이드 읽기'],['transcribing','강의 음성 분석'],['aligning','음성과 슬라이드 연결'],['writing_notes','핵심 내용과 필기 정리'],['done','PDF 완성']]
export function JobScreen({jobId}:{jobId:string}) {
  const [status,setStatus]=useState<JobStatus|null>(null),[network,setNetwork]=useState(false),[retry,setRetry]=useState(0),[partial,setPartial]=useState(false),[retrying,setRetrying]=useState(false)
  useEffect(()=>{let cancelled=false,timer:number|undefined
    const poll=async()=>{try{const value=await getStatus(jobId);if(cancelled)return;setStatus(value);setNetwork(false);if(value.status==='done'||value.status==='error')return}catch{if(!cancelled)setNetwork(true)}if(!cancelled)timer=window.setTimeout(poll,1500)}
    void poll();return()=>{cancelled=true;window.clearTimeout(timer)}
  },[jobId,retry])
  if(status?.status==='done'||partial)return <ViewerScreen jobId={jobId}/>
  const active=stages.findIndex(([key])=>key===status?.status),failed=status?.status==='error'
  const restart=async()=>{setRetrying(true);try{await retryJob(jobId);setStatus(null);setRetry(v=>v+1)}catch{setNetwork(true)}finally{setRetrying(false)}}
  return <div className="processing-page"><header className="site-header"><Brand/><a href="#/">새 강의</a></header><main className="processing-card"><span className={`process-symbol ${failed?'failed':''}`}><Icon name={failed?'close':'spark'} size={32}/></span><div className="eyebrow">{failed?'잠시 멈춘 강의 노트':'YOUR NOTES ARE TAKING SHAPE'}</div><h1>{failed?'필기를 완성하지 못했어요.':<>강의의 중요한 순간을<br/>노트로 옮기고 있어요.</>}</h1><p>{failed?'업로드한 파일은 유지됩니다. 다시 시도하거나 지금까지의 결과를 확인해 주세요.':'이 화면을 닫아도 작업은 계속됩니다. 같은 주소로 다시 돌아올 수 있어요.'}</p>
    <ol className="process-steps">{stages.map(([key,label],i)=><li key={key} className={i<active?'finished':i===active?'active':''} aria-current={i===active?'step':undefined}><span>{i<active?<Icon name="check" size={15}/>:String(i+1).padStart(2,'0')}</span>{label}{i===active&&<small>진행 중</small>}</li>)}</ol>
    {network&&<div role="alert" className="error-box">서버와 연결하지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요.<button onClick={()=>setRetry(v=>v+1)}>연결 다시 확인</button></div>}
    {failed?<div className="retry-actions"><button className="primary" disabled={retrying} onClick={restart}>{retrying?'다시 시작 중…':'다시 시도'}</button><button onClick={()=>setPartial(true)}>지금까지 결과 보기</button></div>:<p className="processing-status" role="status"><span className="activity-dot"/>{status?stages[active]?.[1]??'분석 중':'작업 상태 확인 중'} · 실제 강의 길이에 따라 시간이 걸릴 수 있어요.</p>}
  </main></div>
}
