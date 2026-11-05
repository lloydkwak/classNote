import { useCallback, useEffect, useState } from 'react'
import { DebugScreen } from './DebugScreen'
import { UploadScreen } from './UploadScreen'
import { ViewerScreen } from './ViewerScreen'
import './App.css'

/** Routes are `#/jobs/<id>` and `#/jobs/<id>/debug`, so a job can be reopened or bookmarked. */
function routeFromHash() {
  const match = window.location.hash.match(/^#\/jobs\/([\w-]+)(\/debug)?/)
  return { jobId: match?.[1] ?? null, debug: Boolean(match?.[2]) }
}

export default function App() {
  const [{ jobId, debug }, setRoute] = useState(routeFromHash)

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const onDone = useCallback((id: string) => {
    window.location.hash = `#/jobs/${id}`
  }, [])

  if (jobId && debug) return <DebugScreen key={jobId} jobId={jobId} />
  if (jobId) return <ViewerScreen key={jobId} jobId={jobId} />
  return <UploadScreen onDone={onDone} />
}
