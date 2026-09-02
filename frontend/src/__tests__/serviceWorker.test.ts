/**
 * The service worker's offline shell.
 *
 * A file download is a navigation. The library's JSON export answers one with
 * `Content-Disposition: attachment`, and the first version of this worker
 * stored ANY navigation response as the offline shell -- so one click on
 * "Export JSON" replaced the app with a 1,130-card collection dump, and every
 * failed fetch afterwards downloaded `mtgvault-collection.json` instead of
 * opening the vault. These pin the three rules that stop it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

// The real worker, verbatim: a copy of it here would pass while the shipped
// file stayed broken, which is exactly the failure these tests exist to catch.
import SW_SOURCE from '../../public/sw.js?raw'

interface FetchEvent {
  request: Request
  respondWith: (response: Promise<Response> | Response) => void
}

class FakeCache {
  entries = new Map<string, Response>()
  async put(key: Request | string, value: Response) {
    this.entries.set(typeof key === 'string' ? key : key.url, value)
  }
  async match(key: Request | string) {
    return this.entries.get(typeof key === 'string' ? key : key.url)
  }
  async keys() {
    return [...this.entries.keys()]
  }
  async delete(key: string) {
    return this.entries.delete(key)
  }
}

/** Load sw.js against a stub global scope and hand back its fetch handler. */
function loadWorker(networkResponse: Response | Error) {
  const caches = new Map<string, FakeCache>()
  const handlers: Record<string, (event: never) => void> = {}

  const scope = {
    addEventListener: (name: string, handler: (event: never) => void) => {
      handlers[name] = handler
    },
    skipWaiting: () => Promise.resolve(),
    clients: { claim: () => Promise.resolve() },
    location: { origin: 'https://vault.test' },
    caches: {
      open: async (name: string) => {
        if (!caches.has(name)) caches.set(name, new FakeCache())
        return caches.get(name)!
      },
      keys: async () => [...caches.keys()],
      delete: async (name: string) => caches.delete(name),
      match: async () => undefined,
    },
    fetch: vi.fn(async () => {
      if (networkResponse instanceof Error) throw networkResponse
      return networkResponse
    }),
    URL,
    Response,
  }

  new Function('self', 'caches', 'fetch', 'URL', 'Response', SW_SOURCE)(
    scope,
    scope.caches,
    scope.fetch,
    URL,
    Response,
  )

  return { handlers, caches, scope }
}

function navigate(url: string): [FetchEvent, () => Promise<Response | undefined>] {
  let settled: Promise<Response> | Response | undefined
  const request = new Request(url)
  Object.defineProperty(request, 'mode', { value: 'navigate' })
  const event: FetchEvent = {
    request,
    respondWith: (response) => {
      settled = response
    },
  }
  return [event, async () => (settled ? await settled : undefined)]
}

function shellOf(caches: Map<string, FakeCache>) {
  for (const [name, cache] of caches) {
    if (name.startsWith('vault-shell')) return cache.entries.get('/__shell__')
  }
  return undefined
}

describe('the offline shell', () => {
  let attachment: Response

  beforeEach(() => {
    attachment = new Response('{"items":[]}', {
      headers: {
        'Content-Type': 'application/json',
        'Content-Disposition': 'attachment; filename="mtgvault-collection.json"',
      },
    })
  })

  it('never becomes a file download', async () => {
    const { handlers, caches, scope } = loadWorker(attachment)
    const [event] = navigate('https://vault.test/api/collection/export?format=json')
    handlers.fetch!(event as never)

    // Not intercepted at all: an export goes straight to the network.
    expect(scope.fetch).not.toHaveBeenCalled()
    expect(shellOf(caches)).toBeUndefined()
  })

  it('never becomes an error envelope', async () => {
    const error = new Response('{"error":{"code":"not_found"}}', {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
    const { handlers, caches } = loadWorker(error)
    const [event, settled] = navigate('https://vault.test/decks/78')
    handlers.fetch!(event as never)
    await settled()

    expect(shellOf(caches)).toBeUndefined()
  })

  it('is the last page that actually loaded', async () => {
    const page = new Response('<!doctype html><title>MTG Vault</title>', {
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    })
    const { handlers, caches } = loadWorker(page)
    const [event, settled] = navigate('https://vault.test/library')
    handlers.fetch!(event as never)
    await settled()

    const shell = shellOf(caches)
    expect(shell).toBeDefined()
    expect(await shell!.text()).toContain('MTG Vault')
  })

  it('is served when the homelab is unreachable', async () => {
    const page = new Response('<!doctype html><title>MTG Vault</title>', {
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    })
    const { handlers, caches } = loadWorker(page)
    const [warm, warmed] = navigate('https://vault.test/library')
    handlers.fetch!(warm as never)
    await warmed()

    // Same caches, but the network is now down.
    const offline = loadWorker(new TypeError('Failed to fetch'))
    for (const [name, cache] of caches) {
      const target = await offline.scope.caches.open(name)
      for (const [key, value] of cache.entries) await target.put(key, value)
    }
    const [event, settled] = navigate('https://vault.test/decks/78')
    offline.handlers.fetch!(event as never)
    const response = await settled()

    expect(response).toBeDefined()
    expect(await response!.text()).toContain('MTG Vault')
  })

  it('retires the shell cache that could hold a download', () => {
    // The version bump is what heals an already-poisoned client on activate,
    // rather than asking someone to clear their browser cache by hand.
    expect(SW_SOURCE).toContain("SHELL_CACHE = 'vault-shell-v2'")
  })
})
