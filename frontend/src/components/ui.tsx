/** Small shared presentational pieces. Deliberately plain: no component library. */

import type { ReactNode } from 'react'

import { ApiError } from '../lib/api'
import { colorPips } from '../lib/format'

export function Panel({ title, children, actions }: {
  title?: string
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    <section className="card-surface p-3 sm:p-4">
      {(title || actions) && (
        <header className="mb-3 flex items-center gap-2">
          {title && <h2 className="text-sm font-semibold text-slate-200">{title}</h2>}
          {actions && <div className="ml-auto flex gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Button({
  children,
  onClick,
  type = 'button',
  variant = 'primary',
  disabled,
  className = '',
  title,
}: {
  children: ReactNode
  onClick?: () => void
  type?: 'button' | 'submit'
  variant?: 'primary' | 'ghost' | 'danger'
  disabled?: boolean
  className?: string
  /** A tooltip -- the keyboard shortcut, mostly. */
  title?: string
}) {
  const styles = {
    primary: 'bg-sky-500 text-slate-950 hover:bg-sky-400 disabled:bg-slate-700 disabled:text-slate-400',
    ghost: 'border border-vault-line text-slate-300 hover:bg-slate-800 disabled:text-slate-600',
    danger: 'bg-rose-600 text-white hover:bg-rose-500 disabled:bg-slate-700',
  }[variant]
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`tap rounded-lg px-3 py-2 text-sm font-medium transition ${styles} ${className}`}
    >
      {children}
    </button>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-400">
      {label}
      {children}
    </label>
  )
}

export const inputClass =
  'tap w-full rounded-lg border border-vault-line bg-slate-900 px-3 py-2 text-sm text-slate-100 ' +
  'placeholder:text-slate-600 focus:border-sky-500 focus:outline-none'

export function Pips({ identity }: { identity: string }) {
  return (
    <span className="flex gap-0.5">
      {colorPips(identity).map((pip, index) => (
        <span
          key={`${pip.label}-${index}`}
          className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold ${pip.className}`}
        >
          {pip.label}
        </span>
      ))}
    </span>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  // A 422 carries per-field detail; "Request validation failed" alone is useless.
  const fields =
    error instanceof ApiError && error.code === 'validation_error'
      ? ((error.detail.fields as Array<{ loc: string[]; msg: string }> | undefined) ?? [])
      : []
  return (
    <div className="rounded-lg border border-rose-800 bg-rose-950/50 px-3 py-2 text-sm text-rose-200">
      {message}
      {fields.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-xs text-rose-300">
          {fields.map((field, index) => (
            <li key={index}>
              <code className="text-rose-200">{field.loc.filter((p) => p !== 'body').join('.')}</code>
              : {field.msg}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-8 text-center text-sm text-slate-500">{children}</p>
}

export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card-surface px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="text-lg font-semibold text-slate-100">{value}</p>
      {hint && <p className="text-[11px] text-slate-500">{hint}</p>}
    </div>
  )
}
