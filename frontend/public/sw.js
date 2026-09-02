/* MTG Vault service worker (Phase 6).
 *
 * Deliberately conservative: the app's data is live collection state and must
 * NEVER be served stale, so no /api response is cached except the two immutable
 * image surfaces. Hashed build assets are cache-first (their names change on
 * every deploy); navigations are network-first with the last shell as an
 * offline fallback, so an installed app opens to something rather than a
 * browser error when the homelab is briefly unreachable.
 */

const ASSET_CACHE = 'vault-assets-v1'
// v2 retires every shell cached by the version that would store ANY navigation
// response -- including a file download. Installed clients heal on activate
// instead of needing their browser cache cleared by hand.
const SHELL_CACHE = 'vault-shell-v2'
const SHELL_KEY = '/__shell__'
const IMAGE_CACHE = 'vault-images-v1'
const IMAGE_LIMIT = 400

self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting())
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([ASSET_CACHE, SHELL_CACHE, IMAGE_CACHE])
      for (const name of await caches.keys()) {
        if (!keep.has(name)) await caches.delete(name)
      }
      await self.clients.claim()
    })(),
  )
})

async function trimCache(name, limit) {
  const cache = await caches.open(name)
  const keys = await cache.keys()
  for (let i = 0; i < keys.length - limit; i += 1) {
    await cache.delete(keys[i])
  }
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  // Navigations: network first, cached shell as the offline fallback.
  if (request.mode === 'navigate') {
    // A file download is a navigation too. Clicking the library's JSON export
    // sent `/api/collection/export` through here, and storing THAT as the
    // offline shell is how "open the app" became "download
    // mtgvault-collection.json" on every failed fetch afterwards. Nothing
    // under /api is ever the shell; downloads go straight to the network.
    if (url.pathname.startsWith('/api/')) return

    event.respondWith(
      (async () => {
        try {
          // Straight to the server, never the HTTP cache: a stale shell names
          // stale asset hashes, and those ARE cached, so one old index.html
          // resurrects an entire old build.
          const fresh = await fetch(request, { cache: 'no-store' })
          // Only a real page becomes the shell. An error envelope or an
          // attachment must never be what the app opens to offline -- the
          // asset and image branches below have always checked `ok`, and this
          // one checking nothing is the whole bug.
          const type = fresh.headers.get('Content-Type') || ''
          if (fresh.ok && type.includes('text/html')) {
            const cache = await caches.open(SHELL_CACHE)
            await cache.put(SHELL_KEY, fresh.clone())
          }
          return fresh
        } catch {
          // Scoped to our own cache: a bare caches.match searches every cache
          // in the origin and could answer a page with someone else's entry.
          const cache = await caches.open(SHELL_CACHE)
          const cached = await cache.match(SHELL_KEY)
          return cached ?? Response.error()
        }
      })(),
    )
    return
  }

  // Hashed build assets and static files: cache-first (names change on deploy).
  if (
    url.pathname.startsWith('/assets/') ||
    url.pathname.startsWith('/icons/') ||
    url.pathname.startsWith('/playmats/') ||
    url.pathname === '/manifest.webmanifest'
  ) {
    event.respondWith(
      (async () => {
        const cached = await caches.match(request)
        if (cached) return cached
        const fresh = await fetch(request)
        if (fresh.ok) {
          const cache = await caches.open(ASSET_CACHE)
          await cache.put(request, fresh.clone())
        }
        return fresh
      })(),
    )
    return
  }

  // The two immutable API surfaces: card images and set icons.
  if (url.pathname.startsWith('/api/images/') || url.pathname.startsWith('/api/set-icons/')) {
    event.respondWith(
      (async () => {
        const cached = await caches.match(request)
        if (cached) return cached
        const fresh = await fetch(request)
        if (fresh.ok) {
          const cache = await caches.open(IMAGE_CACHE)
          await cache.put(request, fresh.clone())
          void trimCache(IMAGE_CACHE, IMAGE_LIMIT)
        }
        return fresh
      })(),
    )
  }
  // Everything else under /api falls through to the network untouched.
})
