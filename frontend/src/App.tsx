import { useCallback, useEffect, useState } from 'react'
import { UploadScreen } from './UploadScreen'
import { ViewerScreen } from './ViewerScreen'
import './App.css'

/** The route is `#/jobs/<id>`, so a finished job can be reopened or bookmarked. */
function jobIdFromHash(): string | null {
  return window.location.hash.match(/^#\/jobs\/([\w-]+)/)?.[1] ?? null
}

export default function App() {
  const [jobId, setJobId] = useState(jobIdFromHash)

  useEffect(() => {
    const onHashChange = () => setJobId(jobIdFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const onDone = useCallback((id: string) => {
    window.location.hash = `#/jobs/${id}`
  }, [])

  if (jobId) return <ViewerScreen key={jobId} jobId={jobId} />
  return <UploadScreen onDone={onDone} />
}
