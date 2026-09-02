import { useEffect, useState } from 'react'

/** The event main.tsx fires when a newer build's worker has taken over. */
export const UPDATE_EVENT = 'vault:update-ready'

/**
 * "A new build is ready" -- because the alternative was clearing the cache.
 *
 * The worker is registered per build. When a deploy lands, the new worker
 * installs, claims the page, and main.tsx fires UPDATE_EVENT. Reloading
 * automatically would be simplest, and would also end a game in progress
 * without asking; a banner with one button lets the person choose the moment.
 * Until they press it, the page keeps running the build it loaded with, which
 * is exactly the state this banner exists to make visible.
 */
export default function UpdateBanner() {
  const [ready, setReady] = useState<boolean>(() => Boolean((window as WindowWithUpdate).__vaultUpdateReady))

  useEffect(() => {
    const onReady = () => setReady(true)
    window.addEventListener(UPDATE_EVENT, onReady)
    return () => window.removeEventListener(UPDATE_EVENT, onReady)
  }, [])

  if (!ready) return null
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-3 bg-sky-500 px-3 py-1.5 text-sm text-slate-950"
    >
      <span>A new build of the vault is ready.</span>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="rounded-md bg-slate-950 px-3 py-0.5 font-medium text-sky-200 hover:bg-slate-800"
      >
        Reload
      </button>
    </div>
  )
}

export interface WindowWithUpdate extends Window {
  __vaultUpdateReady?: boolean
}
