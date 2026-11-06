import { useCallback, useEffect, useState } from 'react'
import { DebugScreen } from './DebugScreen'
import { InspectorScreen } from './InspectorScreen'
import { UploadScreen } from './UploadScreen'
import { JobScreen } from './JobScreen'
import './App.css'
import './premium.css'

/** Routes are `#/jobs/<id>` and `#/jobs/<id>/debug`, so a job can be reopened or bookmarked. */
function routeFromHash() {
  const match = window.location.hash.match(/^#\/jobs\/([\w-]+)(\/(?:debug|inspect))?/)
  return { jobId: match?.[1] ?? null, debug: match?.[2] === '/debug', inspect: match?.[2] === '/inspect' }
}

export default function App() {
  const [{ jobId, debug, inspect }, setRoute] = useState(routeFromHash)

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const onDone = useCallback((id: string) => {
    window.location.hash = `#/jobs/${id}`
  }, [])

  if (jobId && inspect) return <InspectorScreen key={jobId} jobId={jobId} />
  if (jobId && debug) return <DebugScreen key={jobId} jobId={jobId} />
  if (jobId) return <JobScreen key={jobId} jobId={jobId} />
  return <UploadScreen onDone={onDone} />
}
