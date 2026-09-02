/** Display helpers. Money is integer cents everywhere until it reaches the screen. */

const WUBRG: Record<string, { label: string; className: string }> = {
  W: { label: 'W', className: 'bg-amber-100 text-amber-950' },
  U: { label: 'U', className: 'bg-sky-300 text-sky-950' },
  B: { label: 'B', className: 'bg-slate-700 text-slate-100' },
  R: { label: 'R', className: 'bg-rose-400 text-rose-950' },
  G: { label: 'G', className: 'bg-emerald-400 text-emerald-950' },
}

export function money(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '—'
  return `$${(cents / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function bytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`
}

/** Render an ISO timestamp in the viewer's locale, or an em dash when absent. */
/** Local DATE only — for list rows where a time with seconds is just noise. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso.slice(0, 10) : parsed.toLocaleDateString()
}

export function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleString()
}

export function colorPips(identity: string): Array<{ label: string; className: string }> {
  if (!identity) return [{ label: 'C', className: 'bg-slate-600 text-slate-100' }]
  return [...identity].map((letter) => WUBRG[letter] ?? { label: letter, className: 'bg-slate-600' })
}

/** "3" from a mana value of 3, "3.5" only when a card genuinely has a half mana value. */
export function manaValue(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

export const RARITY_CLASS: Record<string, string> = {
  common: 'text-slate-300',
  uncommon: 'text-slate-200',
  rare: 'text-amber-300',
  mythic: 'text-orange-400',
  special: 'text-fuchsia-300',
  bonus: 'text-fuchsia-300',
}
