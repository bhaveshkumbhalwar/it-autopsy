import { useEffect, useState } from 'react'

const KEY = 'agent-autopsy.include-demo'
const EVENT = 'agent-autopsy:include-demo'

/**
 * Whether demo/sample executions count towards totals, charts and analytics.
 *
 * Off by default: the numbers on the dashboard should describe the user's own
 * runs. The choice persists in localStorage and is shared across pages through
 * a window event, so toggling it in one place updates the others.
 */
export function readIncludeDemo(): boolean {
  try {
    return localStorage.getItem(KEY) === 'true'
  } catch {
    return false
  }
}

export function writeIncludeDemo(value: boolean): void {
  try {
    localStorage.setItem(KEY, String(value))
  } catch {
    /* private mode: keep the in-memory value only */
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: value }))
}

export function useIncludeDemo(): [boolean, (value: boolean) => void] {
  const [includeDemo, setIncludeDemo] = useState<boolean>(readIncludeDemo)

  useEffect(() => {
    const onChange = (event: Event) => setIncludeDemo((event as CustomEvent<boolean>).detail ?? readIncludeDemo())
    window.addEventListener(EVENT, onChange)
    return () => window.removeEventListener(EVENT, onChange)
  }, [])

  return [includeDemo, (value: boolean) => writeIncludeDemo(value)]
}
