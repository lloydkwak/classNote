import type { ReactNode } from 'react'
import type { Density } from './api'

export function Icon({ name, size=22 }: { name: string; size?: number }) {
  const paths: Record<string,ReactNode> = {
    note: <><path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5M8 12h8M8 16h5"/></>,
    audio: <><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0014 0v-2M12 19v3M8 22h8"/></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
    spark: <path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM20 2v4M18 4h4"/>,
    check: <path d="m5 12 4 4L20 5"/>,close: <path d="m6 6 12 12M6 18 18 6"/>,
    upload: <path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>,play: <path d="m9 5 11 7-11 7z"/>,
    download: <path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>,
    shield: <><path d="m12 2 8 3v7c0 5-8 10-8 10S4 17 4 12V5z"/><path d="m8 11 3 3 5-5"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]??paths.note}</svg>
}
export function Brand() {
  return <a className="brand" href="#/" aria-label="classNote 홈"><span className="brand-icon"><Icon name="note" size={21}/></span>class<span>Note</span></a>
}
export function DensityControl({ value, onChange, disabled=false }: { value: Density; onChange: (d: Density)=>void; disabled?: boolean }) {
  return <div className="segmented" role="group" aria-label="필기 밀도">{([['minimal','간단히'],['balanced','균형 있게'],['detailed','자세하게']] as const).map(([key,label])=><button key={key} type="button" disabled={disabled} aria-pressed={value===key} onClick={()=>onChange(key)}>{label}</button>)}</div>
}
export function readPreference(key: string, fallback: string) {
  try { return localStorage.getItem(key)??fallback } catch { return fallback }
}
export function savePreference(key: string, value: string) {
  try { localStorage.setItem(key,value) } catch { /* Storage may be disabled. */ }
}
