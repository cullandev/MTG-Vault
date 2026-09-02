/** A minimal toast layer for successes that used to pass silently.
 *
 * The scanner keeps its own richer undo toast; this one is for everything else —
 * a sleeved deck, a finished import, a rename. Errors stay in ErrorNote, inline
 * where the action happened.
 */

import { createContext, useCallback, useContext, useState } from 'react'
import type { ReactNode } from 'react'

const ToastContext = createContext<(message: string) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Array<{ id: number; message: string }>>([])

  const push = useCallback((message: string) => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, message }])
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id))
    }, 3200)
  }, [])

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-16 z-40 flex flex-col items-center gap-1 px-4 sm:bottom-4">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="max-w-full truncate rounded-full border border-vault-line bg-slate-800/95 px-4 py-1.5 text-xs text-slate-100 shadow-xl backdrop-blur"
          >
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

/** `const toast = useToast(); toast('Sleeved 100 cards ✓')`
 *
 * The hook and its provider belong together; losing fast-refresh for this one
 * file is a fine trade. */
// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  return useContext(ToastContext)
}
