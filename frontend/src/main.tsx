import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import App from './App'
import './index.css'

// There is no login screen to send anyone to (the owner deleted it -- auth is
// permanently off on this LAN instance), so a 401 is just an error like any
// other; the old handler that chased expired sessions left with the page.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Collection data changes because *this* user changed it, so refetching on
      // window focus is noise rather than freshness.
      refetchOnWindowFocus: false,
      staleTime: 30_000,
      retry: (failureCount, error) =>
        failureCount < 2 && !(error instanceof Error && error.name === 'ApiError'),
    },
  },
})

// Phase 6: the installable app. Registered only in production builds -- the
// dev server's module graph and a caching worker do not mix.
//
// Registered PER BUILD. The worker's own bytes rarely change, so a plain
// /sw.js registration never saw a new version; the old worker stayed in
// charge, and during a deploy's restart window its offline fallback served the
// previous shell -- and the previous build -- with nothing to say so. The owner
// was clearing the browser cache after every deploy to get out of that.
// A build-stamped URL is a new registration each time, so a deploy installs a
// new worker; when it takes over, the page is told and shows a banner rather
// than reloading under a game in progress.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    const hadController = Boolean(navigator.serviceWorker.controller)
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      // First install on a fresh browser is not an update; a takeover on a
      // page that already had a worker is.
      if (!hadController) return
      ;(window as Window & { __vaultUpdateReady?: boolean }).__vaultUpdateReady = true
      window.dispatchEvent(new Event('vault:update-ready'))
    })
    navigator.serviceWorker
      .register(`/sw.js?v=${__BUILD_ID__}`)
      .then((registration) => {
        // A tab left open across a deploy still learns about it.
        window.setInterval(() => void registration.update().catch(() => {}), 5 * 60_000)
      })
      .catch(() => {
        // An uninstallable environment (old browser, http) is not an error.
      })
  })
}

const container = document.getElementById('root')
if (!container) throw new Error('Missing #root element')

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
