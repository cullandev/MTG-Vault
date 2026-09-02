import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'

import { api } from '../lib/api'
import { RARITY_CLASS, manaValue, money } from '../lib/format'
import type { CollectionList, CollectionRow } from '../lib/types'
import { Button, Empty, ErrorNote, Field, Pips, Stat, inputClass } from '../components/ui'
import HoverCardImage from '../components/HoverCardImage'

type GroupBy = 'oracle' | 'printing' | 'copy'

interface Filters {
  q: string
  colors: string
  type_contains: string
  rarity: string
  set_code: string
  mv_min: string
  mv_max: string
  finish: string
  is_proxy: string
}

const EMPTY_FILTERS: Filters = {
  q: '',
  colors: '',
  type_contains: '',
  rarity: '',
  set_code: '',
  mv_min: '',
  mv_max: '',
  finish: '',
  is_proxy: '',
}

const SORTS = [
  { value: 'name', label: 'Name' },
  { value: 'price', label: 'Price' },
  { value: 'mana_value', label: 'Mana value' },
  { value: 'copies', label: 'Copies' },
  { value: 'added', label: 'Recently added' },
  { value: 'set', label: 'Set' },
  { value: 'rarity', label: 'Rarity' },
]

export default function Library() {
  // Filters, search and sort live in the URL, so opening a card and pressing
  // Back lands on exactly the filtered view that was left -- and a filtered
  // library can be bookmarked. Written with replace:true to keep Back a
  // single step rather than one per keystroke.
  const [params, setParams] = useSearchParams()
  const [filters, setFilters] = useState<Filters>(() => {
    const fromUrl = { ...EMPTY_FILTERS }
    for (const key of Object.keys(EMPTY_FILTERS) as Array<keyof Filters>) {
      const value = params.get(`f_${key}`)
      if (value != null) fromUrl[key] = value
    }
    return fromUrl
  })
  const [search, setSearch] = useState(params.get('q') ?? '')
  const [sort, setSort] = useState(params.get('sort') ?? 'name')
  const [descending, setDescending] = useState(params.get('desc') === '1')
  const [groupBy, setGroupBy] = useState<GroupBy>((params.get('group') as GroupBy) || 'oracle')
  const [viewChoice, setViewChoice] = useState<'grid' | 'table' | null>(
    (params.get('view') as 'grid' | 'table' | null) || null,
  )
  const [showFilters, setShowFilters] = useState(false)

  useEffect(() => {
    const next = new URLSearchParams()
    if (search) next.set('q', search)
    if (sort !== 'name') next.set('sort', sort)
    if (descending) next.set('desc', '1')
    if (groupBy !== 'oracle') next.set('group', groupBy)
    if (viewChoice) next.set('view', viewChoice)
    for (const [key, value] of Object.entries(filters)) {
      if (value) next.set(`f_${key}`, value)
    }
    setParams(next, { replace: true })
  }, [filters, search, sort, descending, groupBy, viewChoice, setParams])

  // The saved preference (System → Preferences) opens the page; an in-page
  // toggle wins for the rest of the visit.
  const savedView = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<{ library_default_view: 'grid' | 'table' }>('/api/settings'),
    staleTime: Infinity,
  })
  const view = viewChoice ?? savedView.data?.library_default_view ?? 'grid'
  const setView = setViewChoice


  // Debounced, min-two-characters search: firing one API request per keystroke
  // made every letter of "Lightning" its own collection query.
  const [debouncedSearch, setDebouncedSearch] = useState('')
  useEffect(() => {
    const trimmed = search.trim()
    const effective = trimmed.length >= 2 ? trimmed : ''
    const timer = window.setTimeout(() => setDebouncedSearch(effective), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const query = useMemo(
    () => ({
      ...filters,
      q: debouncedSearch,
      sort,
      descending,
      group_by: groupBy,
      limit: 60,
    }),
    [filters, debouncedSearch, sort, descending, groupBy],
  )

  const page = useInfiniteQuery({
    queryKey: ['collection', query],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      api.get<CollectionList>('/api/collection', { ...query, cursor: pageParam }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // Changing a filter keeps the current grid on screen while the new rows
    // load: tiles that survive the filter never unmount, so their images
    // never blank and re-decode -- the "every filter change reloads all the
    // cards" feel was this, not the network.
    placeholderData: keepPreviousData,
  })

  const rows = page.data?.pages.flatMap((p) => p.items) ?? []
  const totals = page.data?.pages[0]?.totals
  const priceNote = page.data?.pages[0]?.price_note

  function update<K extends keyof Filters>(key: K, value: Filters[K]) {
    setFilters((current) => ({ ...current, [key]: value }))
  }

  const activeFilterCount = Object.entries(filters).filter(
    ([, value]) => value !== '',
  ).length

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search names and rules text…"
          className={inputClass}
        />
        <Button variant="ghost" onClick={() => setShowFilters((open) => !open)}>
          Filters{activeFilterCount ? ` (${activeFilterCount})` : ''}
        </Button>
      </div>

      {showFilters && (
        <div className="card-surface grid grid-cols-2 gap-3 p-3 sm:grid-cols-4">
          <Field label="Colour identity (exact)">
            <input
              value={filters.colors}
              onChange={(event) => update('colors', event.target.value.toUpperCase())}
              placeholder="e.g. UB"
              className={inputClass}
            />
          </Field>
          <Field label="Type contains">
            <input
              value={filters.type_contains}
              onChange={(event) => update('type_contains', event.target.value)}
              placeholder="Creature"
              className={inputClass}
            />
          </Field>
          <Field label="Set code">
            <input
              value={filters.set_code}
              onChange={(event) => update('set_code', event.target.value.toLowerCase())}
              placeholder="znr"
              className={inputClass}
            />
          </Field>
          <Field label="Rarity">
            <select
              value={filters.rarity}
              onChange={(event) => update('rarity', event.target.value)}
              className={inputClass}
            >
              <option value="">Any</option>
              <option value="common">Common</option>
              <option value="uncommon">Uncommon</option>
              <option value="rare">Rare</option>
              <option value="mythic">Mythic</option>
            </select>
          </Field>
          <Field label="Mana value min">
            <input
              type="number"
              value={filters.mv_min}
              onChange={(event) => update('mv_min', event.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Mana value max">
            <input
              type="number"
              value={filters.mv_max}
              onChange={(event) => update('mv_max', event.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Finish">
            <select
              value={filters.finish}
              onChange={(event) => update('finish', event.target.value)}
              className={inputClass}
            >
              <option value="">Any</option>
              <option value="nonfoil">Non-foil</option>
              <option value="foil">Foil</option>
              <option value="etched">Etched</option>
            </select>
          </Field>
          <Field label="Proxies">
            <select
              value={filters.is_proxy}
              onChange={(event) => update('is_proxy', event.target.value)}
              className={inputClass}
            >
              <option value="">Include</option>
              <option value="false">Real cards only</option>
              <option value="true">Proxies only</option>
            </select>
          </Field>
          <div className="col-span-2 flex items-end sm:col-span-4">
            <Button variant="ghost" onClick={() => setFilters(EMPTY_FILTERS)}>
              Clear filters
            </Button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Copies" value={totals?.copies?.toLocaleString() ?? '—'} />
        <Stat label="Distinct cards" value={totals?.unique_cards?.toLocaleString() ?? '—'} />
        <Stat
          label="Value"
          value={money(totals?.value_cents)}
          hint={totals?.unpriced_copies ? `${totals.unpriced_copies} unpriced` : undefined}
        />
        <ExportLinks query={query} />
      </div>

      {priceNote && <p className="text-[11px] text-slate-500">{priceNote}</p>}

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <select
          value={sort}
          aria-label="Sort by"
          onChange={(event) => setSort(event.target.value)}
          className="rounded-lg border border-vault-line bg-slate-900 px-2 py-1.5 text-slate-200"
        >
          {SORTS.filter((option) => !(groupBy === 'copy' && option.value === 'copies')).map(
            (option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ),
          )}
        </select>
        <button
          onClick={() => setDescending((value) => !value)}
          aria-pressed={descending}
          className="rounded-lg border border-vault-line px-2 py-1.5 text-slate-300"
        >
          {descending ? 'Descending' : 'Ascending'}
        </button>
        <select
          value={groupBy}
          aria-label="Group by"
          onChange={(event) => {
            const next = event.target.value as GroupBy
            setGroupBy(next)
            if (next === 'copy' && sort === 'copies') setSort('name')
          }}
          className="rounded-lg border border-vault-line bg-slate-900 px-2 py-1.5 text-slate-200"
        >
          <option value="oracle">By card</option>
          <option value="printing">By printing</option>
          <option value="copy">Every copy</option>
        </select>
        <div className="ml-auto flex gap-1">
          <button
            onClick={() => setView('grid')}
            aria-pressed={view === 'grid'}
            className={`rounded-lg px-2 py-1.5 ${view === 'grid' ? 'bg-sky-500/15 text-sky-200' : 'text-slate-400'}`}
          >
            Grid
          </button>
          <button
            onClick={() => setView('table')}
            aria-pressed={view === 'table'}
            className={`rounded-lg px-2 py-1.5 ${view === 'table' ? 'bg-sky-500/15 text-sky-200' : 'text-slate-400'}`}
          >
            Table
          </button>
        </div>
      </div>

      <ErrorNote error={page.error} />

      {page.isLoading ? (
        <Empty>Loading your collection…</Empty>
      ) : rows.length === 0 ? (
        <Empty>
          Nothing here yet. <Link to="/import" className="text-sky-300 underline">Import a CSV</Link>{' '}
          or <Link to="/add" className="text-sky-300 underline">add a card</Link>.
        </Empty>
      ) : (
        <div className={page.isPlaceholderData ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
          {view === 'grid' ? <GridView rows={rows} /> : <TableView rows={rows} />}
        </div>
      )}

      {page.hasNextPage && (
        <div className="flex justify-center pt-2">
          <Button
            variant="ghost"
            onClick={() => void page.fetchNextPage()}
            disabled={page.isFetchingNextPage || page.isPlaceholderData}
          >
            {page.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </Button>
        </div>
      )}
    </div>
  )
}

/** Our own image API's size segment, swapped for grid duty (~15 KB vs ~97 KB). */
function smallImage(url: string): string {
  return url.endsWith('/normal') ? `${url.slice(0, -'/normal'.length)}/small` : url
}

const TILE_MIN = 90
const TILE_MAX = 280
const TILE_DEFAULT = 150
const TILE_KEY = 'library-tile-px'

function storedTile(): number {
  try {
    const raw = Number(localStorage.getItem(TILE_KEY))
    if (raw >= TILE_MIN && raw <= TILE_MAX) return raw
  } catch {
    // Private windows and blocked storage fall back to the default.
  }
  return TILE_DEFAULT
}

/**
 * Download links carrying the CURRENT filter set: the export endpoint takes
 * the list endpoint's filter params verbatim, so what you see is what you
 * save. Plain <a download> — the endpoint sets attachment headers.
 */
function ExportLinks({ query }: { query: Record<string, unknown> }) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value == null || value === '') continue
    if (['cursor', 'limit', 'group_by', 'sort', 'descending'].includes(key)) continue
    params.set(key, String(value))
  }
  const filterQuery = params.toString() ? `&${params.toString()}` : ''
  const filtered = filterQuery !== ''

  return (
    <div className="card-surface flex flex-col justify-center gap-1 p-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">
        Export {filtered ? 'this view' : 'everything'}
      </p>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
        <a
          href={`/api/collection/export?format=csv${filterQuery}`}
          className="text-sky-300 underline"
          download
        >
          CSV
        </a>
        <a
          href={`/api/collection/export?format=csv&flavour=moxfield${filterQuery}`}
          className="text-sky-300 underline"
          download
        >
          Moxfield CSV
        </a>
        <a
          href={`/api/collection/export?format=json${filterQuery}`}
          className="text-sky-300 underline"
          download
        >
          JSON
        </a>
      </div>
      {filtered && (
        <p className="text-[10px] text-slate-600">
          Filters applied — clear them to export the whole collection.
        </p>
      )}
    </div>
  )
}

function GridView({ rows }: { rows: CollectionRow[] }) {
  const [tile, setTile] = useState(storedTile)

  function resize(px: number) {
    setTile(px)
    try {
      localStorage.setItem(TILE_KEY, String(px))
    } catch {
      // A device that won't remember still resizes for the session.
    }
  }

  return (
    <>
    <div
      className="grid gap-2"
      style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${tile}px, 1fr))` }}
    >
      {rows.map((row) => (
        <Link
          key={row.group_key}
          to={`/cards/${row.oracle_id}`}
          className="card-surface group overflow-hidden transition hover:border-sky-700"
        >
          <HoverCardImage imageUrl={row.image_url} alt={row.name} className="block">
            <div className="aspect-[63/88] bg-slate-900">
              {row.image_url ? (
                <img
                  src={tile > 200 ? row.image_url : smallImage(row.image_url)}
                  alt={row.name}
                  loading="lazy"
                  decoding="async"
                  className="h-full w-full object-cover"
                />
              ) : (
                <div className="flex h-full items-center justify-center p-2 text-center text-[11px] text-slate-500">
                  {row.name}
                </div>
              )}
            </div>
          </HoverCardImage>
          <div className="space-y-1 p-2">
            <p className="truncate text-xs font-medium text-slate-100">{row.name}</p>
            <div className="flex items-center gap-1 text-[11px] text-slate-400">
              <Pips identity={row.color_identity} />
              <span className="ml-auto">{money(row.price_cents)}</span>
            </div>
            <div className="flex items-center justify-between text-[11px] text-slate-500">
              <span>{row.set_code?.toUpperCase()}</span>
              <span>{row.copies}×</span>
            </div>
          </div>
        </Link>
      ))}
    </div>
    {/* Card-size slider: sticky above the phone nav, tucked bottom-right on
        desktop. Bigger tiles switch to full-size images past 200px. */}
    <div
      className="sticky bottom-[calc(4.5rem+var(--safe-bottom,0px))] z-10 ml-auto flex w-fit items-center gap-2 rounded-full border border-vault-line bg-vault-panel/95 px-3 py-1.5 backdrop-blur sm:bottom-3"
    >
      <span className="text-[10px] text-slate-500">🂠</span>
      <input
        type="range"
        min={TILE_MIN}
        max={TILE_MAX}
        step={10}
        value={tile}
        onChange={(event) => resize(Number(event.target.value))}
        aria-label="Card size"
        className="h-1 w-28 accent-sky-400"
      />
      <span className="text-sm text-slate-500">🂠</span>
    </div>
    </>
  )
}

function TableView({ rows }: { rows: CollectionRow[] }) {
  return (
    <div className="card-surface overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="border-b border-vault-line text-[11px] uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2">Card</th>
            <th className="px-3 py-2">Set</th>
            <th className="px-3 py-2">Type</th>
            <th className="px-3 py-2 text-right">MV</th>
            <th className="px-3 py-2 text-right">Copies</th>
            <th className="px-3 py-2 text-right">Price</th>
            <th className="px-3 py-2 text-right">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.group_key} className="border-b border-vault-line/50 last:border-0">
              <td className="px-3 py-2">
                <Link to={`/cards/${row.oracle_id}`} className="flex items-center gap-2">
                  <Pips identity={row.color_identity} />
                  <HoverCardImage imageUrl={row.image_url} alt={row.name}>
                    <span className="text-slate-100 hover:text-sky-300">{row.name}</span>
                  </HoverCardImage>
                  {row.is_proxy && (
                    <span className="rounded bg-slate-700 px-1 text-[10px] text-slate-300">proxy</span>
                  )}
                </Link>
              </td>
              <td className={`px-3 py-2 text-xs ${RARITY_CLASS[row.rarity ?? ''] ?? 'text-slate-400'}`}>
                {row.set_code?.toUpperCase()} {row.collector_number}
              </td>
              <td className="max-w-[16rem] truncate px-3 py-2 text-xs text-slate-400">
                {row.type_line}
              </td>
              <td className="px-3 py-2 text-right text-xs text-slate-400">
                {manaValue(row.mana_value)}
              </td>
              <td className="px-3 py-2 text-right text-xs text-slate-300">
                {row.copies}
              </td>
              <td className="px-3 py-2 text-right text-xs text-slate-400">{money(row.price_cents)}</td>
              <td className="px-3 py-2 text-right text-xs text-slate-200">{money(row.value_cents)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
