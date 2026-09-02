/**
 * The single place the frontend talks to the backend.
 *
 * Every request carries the `X-Requested-With` header the API requires on unsafe
 * methods (ADR-013), and every failure is turned into an `ApiError` carrying the
 * server's error envelope so pages can show the real reason rather than "something
 * went wrong".
 */

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: Record<string, unknown>

  constructor(status: number, code: string, message: string, detail: Record<string, unknown>) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }

}

type Query = Record<string, string | number | boolean | null | undefined>

function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue
    params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

async function toError(response: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = response.statusText || `HTTP ${response.status}`
  let detail: Record<string, unknown> = {}
  try {
    const body = await response.json()
    if (body?.error) {
      code = body.error.code ?? code
      message = body.error.message ?? message
      detail = body.error.detail ?? {}
    }
  } catch {
    // A non-JSON error body (a proxy error page, say) keeps the status text.
  }
  return new ApiError(response.status, code, message, detail)
}

async function request<T>(method: string, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    method,
    credentials: 'same-origin',
    headers: {
      'X-Requested-With': 'MTGVault',
      ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(init.headers ?? {}),
    },
  })
  if (!response.ok) throw await toError(response)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

async function requestText(path: string): Promise<string> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'X-Requested-With': 'MTGVault' },
  })
  if (!response.ok) throw await toError(response)
  return response.text()
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>('GET', withQuery(path, query)),
  /** GET an endpoint that returns plain text (deck exports), not JSON. */
  getText: (path: string, query?: Query) => requestText(withQuery(path, query)),
  post: <T>(path: string, body?: unknown) =>
    request<T>('POST', path, body === undefined ? {} : { body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>('PATCH', path, { body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, { body: form }),
  /** Absolute URL for a file download; the browser handles it, not fetch. */
  downloadUrl: (path: string, query?: Query) => withQuery(path, query),
}
