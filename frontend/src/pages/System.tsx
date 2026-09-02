import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import { bytes, money, when } from '../lib/format'
import type { PriceAlert, SystemStatus } from '../lib/types'
import { Button, Empty, ErrorNote, Panel, Stat, inputClass } from '../components/ui'

export default function SystemPage() {
  const status = useQuery({
    queryKey: ['system-status'],
    queryFn: () => api.get<SystemStatus>('/api/system/status'),
    refetchInterval: 60_000,
  })

  if (status.isLoading) return <Empty>Checking…</Empty>
  if (status.error) return <ErrorNote error={status.error} />
  if (!status.data) return <Empty>No status available.</Empty>

  const { database, counts, image_cache, last_import, jobs, features } = status.data

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Printings" value={counts.printings.toLocaleString()} />
        <Stat label="Distinct cards" value={counts.oracle_cards.toLocaleString()} />
        <Stat label="Copies owned" value={counts.copies.toLocaleString()} />
        <Stat
          label="Database"
          value={bytes(database.bytes)}
          hint={`WAL ${bytes(database.wal_bytes)}`}
        />
      </div>

      <BackupPanel />

      <SettingsPanel />

      <ScanHealthPanel />

      <AlertsPanel />

      <Panel title="Export">
        <p className="mb-3 text-xs text-slate-500">
          The JSON export is insurance-grade: it is readable without this application ever
          running again. The CSV re-imports here without loss.
        </p>
        <div className="flex flex-wrap gap-2">
          {/* Styled anchors, not buttons-in-anchors: nesting the two is invalid
              HTML and double tab-stops keyboard users. */}
          {(
            [
              ['Download CSV', { format: 'csv', flavour: 'native' }],
              ['CSV for Moxfield', { format: 'csv', flavour: 'moxfield' }],
              ['Download JSON', { format: 'json' }],
            ] as const
          ).map(([label, query]) => (
            <a
              key={label}
              href={api.downloadUrl('/api/collection/export', { ...query })}
              className="tap inline-block rounded-lg border border-vault-line px-3 py-2 text-sm font-medium text-slate-300 transition hover:bg-slate-800"
            >
              {label}
            </a>
          ))}
        </div>
      </Panel>

      <Panel title="Card data">
        {last_import ? (
          <dl className="grid grid-cols-2 gap-y-1 text-xs sm:grid-cols-4">
            <Detail label="Last import" value={last_import.kind} />
            <Detail label="Status" value={last_import.status} />
            <Detail label="Finished" value={when(last_import.finished_at)} />
            <Detail label="Rows written" value={last_import.rows_written.toLocaleString()} />
            <Detail label="Scryfall data dated" value={when(last_import.source_updated_at)} />
          </dl>
        ) : (
          <p className="text-xs text-slate-500">
            No Scryfall bulk import has run yet. Run{' '}
            <code className="rounded bg-slate-800 px-1">python -m app.cli import-bulk</code> in the
            app container to load the card database.
          </p>
        )}
      </Panel>

      <Panel title="Image cache">
        <p className="text-xs text-slate-400">
          {bytes(image_cache.bytes)} of {bytes(image_cache.cap_bytes)} used. Card images are
          fetched on demand and evicted least-recently-used when the cap is reached.
        </p>
      </Panel>

      <Panel title="Optional features">
        <ul className="space-y-1 text-xs">
          <FeatureRow label="AI analysis (Anthropic)" enabled={features.ai} />
          <FeatureRow label="EDHREC" enabled={features.edhrec} />
          <FeatureRow label="Commander Spellbook" enabled={features.spellbook} />
          <li className="flex items-center gap-2">
            <span className="text-slate-300">Meta sources</span>
            <span className="text-slate-500">
              {features.meta_sources.length ? features.meta_sources.join(', ') : 'none enabled'}
            </span>
          </li>
        </ul>
      </Panel>

      <Panel title="Scheduled jobs">
        {jobs.length === 0 ? (
          <p className="text-xs text-slate-500">
            No job has run yet. Jobs run on their own schedule once the stack is up.
          </p>
        ) : (
          <ul className="space-y-1 text-xs">
            {jobs.map((job, index) => (
              <li key={`${job.name}-${index}`} className="flex items-center gap-2">
                <span className="text-slate-200">{job.name}</span>
                {job.sub_source && <span className="text-slate-500">{job.sub_source}</span>}
                <span
                  className={
                    job.status === 'ok'
                      ? 'text-emerald-300'
                      : job.status === 'failed'
                        ? 'text-rose-400'
                        : 'text-amber-300'
                  }
                >
                  {job.status}
                </span>
                <span className="ml-auto text-slate-600">{when(job.started_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-slate-200">{value}</dd>
    </div>
  )
}

function FeatureRow({ label, enabled }: { label: string; enabled: boolean }) {
  return (
    <li className="flex items-center gap-2">
      <span className="text-slate-300">{label}</span>
      <span className={enabled ? 'text-emerald-300' : 'text-slate-600'}>
        {enabled ? 'enabled' : 'disabled'}
      </span>
    </li>
  )
}

function BackupPanel() {
  const backup = useMutation({
    mutationFn: () =>
      api.post<{ path: string; bytes: number; verified: boolean; mirrored: string | null }>(
        '/api/system/backup',
      ),
  })
  return (
    <Panel
      title="Backup"
      actions={
        <Button onClick={() => backup.mutate()} disabled={backup.isPending}>
          {backup.isPending ? 'Backing up…' : 'Back up now'}
        </Button>
      }
    >
      <p className="text-xs text-slate-500">
        A verified snapshot of the database (the thing to press before a risky import).
        Nightly backups run at 05:30; set <code>BACKUP_MIRROR_DIR</code> in .env to a NAS or
        second drive to keep copies off this disk.
      </p>
      {backup.data && (
        <p className={`mt-2 text-xs ${backup.data.verified ? 'text-emerald-300' : 'text-rose-300'}`}>
          {backup.data.verified ? 'Verified ✓' : 'NOT verified'} · {bytes(backup.data.bytes)} ·{' '}
          {backup.data.path}
          {backup.data.mirrored && ` · mirrored to ${backup.data.mirrored}`}
        </p>
      )}
      <ErrorNote error={backup.error} />
    </Panel>
  )
}

interface UserSettings {
  scan_sound: boolean
  scan_haptics: boolean
  scan_default_finish: 'nonfoil' | 'foil' | 'etched'
  scan_default_condition: 'NM' | 'LP' | 'MP' | 'HP' | 'DMG'
  scan_default_language: string
  library_default_view: 'grid' | 'table'
}

function SettingsPanel() {
  const queryClient = useQueryClient()
  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<UserSettings>('/api/settings'),
  })
  const update = useMutation({
    mutationFn: (changes: Partial<UserSettings>) => api.patch('/api/settings', changes),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['settings'] }),
  })
  const data = settings.data
  if (!data) return null

  return (
    <Panel title="Preferences">
      <div className="grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={data.scan_sound}
            onChange={(event) => update.mutate({ scan_sound: event.target.checked })}
          />
          <span className="text-slate-300">Scanner sound on lock-in</span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={data.scan_haptics}
            onChange={(event) => update.mutate({ scan_haptics: event.target.checked })}
          />
          <span className="text-slate-300">Scanner vibration on lock-in</span>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-slate-300">Scanned cards default to</span>
          <select
            value={data.scan_default_finish}
            onChange={(event) =>
              update.mutate({
                scan_default_finish: event.target.value as UserSettings['scan_default_finish'],
              })
            }
            className={`${inputClass} w-auto`}
          >
            <option value="nonfoil">non-foil</option>
            <option value="foil">foil</option>
            <option value="etched">etched</option>
          </select>
          <select
            value={data.scan_default_condition}
            onChange={(event) =>
              update.mutate({
                scan_default_condition: event.target
                  .value as UserSettings['scan_default_condition'],
              })
            }
            className={`${inputClass} w-auto`}
          >
            {['NM', 'LP', 'MP', 'HP', 'DMG'].map((condition) => (
              <option key={condition} value={condition}>
                {condition}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-slate-300">Library opens as</span>
          <select
            value={data.library_default_view}
            onChange={(event) =>
              update.mutate({
                library_default_view: event.target.value as UserSettings['library_default_view'],
              })
            }
            className={`${inputClass} w-auto`}
          >
            <option value="grid">grid</option>
            <option value="table">table</option>
          </select>
        </label>
      </div>
      <ErrorNote error={update.error ?? settings.error} />
    </Panel>
  )
}

interface ScanStats {
  window_days: number
  events: number
  confirmed: number
  correct: number
  unconfirmed: number
  misses: number
  first_match_accuracy: number | null
  method_mix: Record<string, number>
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  recent_misses: Array<Record<string, unknown>>
}

function ScanHealthPanel() {
  const stats = useQuery({
    queryKey: ['scan-stats'],
    queryFn: () => api.get<ScanStats>('/api/scan/stats'),
  })
  const data = stats.data
  if (!data) return null
  if (data.events === 0) return null

  return (
    <Panel title="Scanner health">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat
          label="First-match accuracy"
          value={
            data.first_match_accuracy != null
              ? `${Math.round(data.first_match_accuracy * 100)}%`
              : '—'
          }
          hint={`last ${data.window_days} days`}
        />
        <Stat label="Scans" value={data.events.toLocaleString()} hint={`${data.misses} missed`} />
        <Stat
          label="Latency p50"
          value={data.p50_latency_ms != null ? `${Math.round(data.p50_latency_ms)} ms` : '—'}
          hint={data.p95_latency_ms != null ? `p95 ${Math.round(data.p95_latency_ms)} ms` : ''}
        />
        <Stat
          label="Top method"
          value={
            Object.entries(data.method_mix).sort((a, b) => b[1] - a[1])[0]?.[0] ?? '—'
          }
        />
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        Watched so OCR degradation is visible rather than a slow surprise; details at{' '}
        <code>/api/scan/stats</code>.
      </p>
      <RecentRescans />
      <ErrorNote error={stats.error} />
    </Panel>
  )
}

interface ScanRejection {
  event_id: number
  ts: string
  proposed_name: string | null
  proposed_set: string | null
  method: string
  fuzz_score: number | null
  ocr_text: string | null
  accepted_name: string | null
  accepted_method: string | null
}

/** What Rescan dismissed and what was finally kept — the mis-scan review. */
function RecentRescans() {
  const rejections = useQuery({
    queryKey: ['scan-rejections'],
    queryFn: () => api.get<{ rejections: ScanRejection[] }>('/api/scan/rejections'),
  })
  const rows = rejections.data?.rejections ?? []
  if (rows.length === 0) return null

  return (
    <div className="mt-3">
      <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">
        Recent rescans — what was proposed vs what you kept
      </p>
      <ul className="space-y-1 text-xs">
        {rows.slice(0, 8).map((row) => (
          <li key={row.event_id} className="text-slate-400">
            <span className="text-rose-300">{row.proposed_name ?? 'nothing'}</span>
            {row.proposed_set && (
              <span className="text-slate-600"> ({row.proposed_set.toUpperCase()})</span>
            )}
            <span className="text-slate-600"> via {row.method}</span>
            {row.ocr_text && <span className="text-slate-600"> · read “{row.ocr_text}”</span>}
            {' → '}
            {row.accepted_name ? (
              <>
                <span className="text-emerald-300">{row.accepted_name}</span>
                {row.accepted_method && (
                  <span className="text-slate-600"> via {row.accepted_method}</span>
                )}
              </>
            ) : (
              <span className="text-slate-600">nothing accepted yet</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

const ALERT_DIRECTIONS = [
  { value: 'above', label: 'value rises above' },
  { value: 'below', label: 'value falls below' },
  { value: 'pct_up', label: 'value jumps by %' },
  { value: 'pct_down', label: 'value drops by %' },
] as const

function AlertsPanel() {
  const queryClient = useQueryClient()
  const alerts = useQuery({
    queryKey: ['alerts'],
    queryFn: () => api.get<{ alerts: PriceAlert[] }>('/api/alerts'),
  })
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['alerts'] })

  const [direction, setDirection] = useState<PriceAlert['direction']>('pct_up')
  const [threshold, setThreshold] = useState('10')
  const isPct = direction.startsWith('pct')

  const create = useMutation({
    mutationFn: () =>
      api.post('/api/alerts', {
        scope: 'owned',
        direction,
        threshold_cents: isPct ? null : Math.round(parseFloat(threshold) * 100),
        threshold_pct: isPct ? parseFloat(threshold) : null,
      }),
    onSuccess: refresh,
  })
  const toggle = useMutation({
    mutationFn: (alert: PriceAlert) =>
      api.patch(`/api/alerts/${alert.id}`, { active: !alert.active }),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (alertId: number) => api.delete(`/api/alerts/${alertId}`),
    onSuccess: refresh,
  })

  return (
    <Panel title="Price alerts">
      <p className="text-xs text-slate-500">
        Standing rules the nightly price job checks; hits land in your inbox on the Home
        page. Rules here watch the whole collection's value — card-specific rules are on
        each card's page.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-400">Alert me when the collection's</span>
        <select
          value={direction}
          onChange={(event) => setDirection(event.target.value as PriceAlert['direction'])}
          className={`${inputClass} w-auto`}
        >
          {ALERT_DIRECTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <input
          value={threshold}
          onChange={(event) => setThreshold(event.target.value)}
          inputMode="decimal"
          className={`${inputClass} w-24`}
          placeholder={isPct ? '%' : '$'}
        />
        <span className="text-slate-500">{isPct ? '%' : 'dollars'}</span>
        <Button
          onClick={() => create.mutate()}
          disabled={create.isPending || !parseFloat(threshold)}
        >
          Add rule
        </Button>
      </div>
      <ErrorNote error={create.error ?? alerts.error ?? toggle.error ?? remove.error} />

      {alerts.data && alerts.data.alerts.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs">
          {alerts.data.alerts.map((alert) => (
            <li key={alert.id} className="flex items-center gap-2">
              <span className={alert.active ? 'text-slate-200' : 'text-slate-600 line-through'}>
                {alert.scope === 'owned' ? 'Collection' : `Card #${alert.card_id}`}{' '}
                {ALERT_DIRECTIONS.find((option) => option.value === alert.direction)?.label}{' '}
                {alert.threshold_pct != null
                  ? `${alert.threshold_pct}%`
                  : money(alert.threshold_cents ?? 0)}
              </span>
              {alert.last_fired_at && (
                <span className="text-slate-600">last fired {when(alert.last_fired_at)}</span>
              )}
              <span className="ml-auto flex gap-1">
                <Button variant="ghost" onClick={() => toggle.mutate(alert)}>
                  {alert.active ? 'Pause' : 'Resume'}
                </Button>
                <Button variant="ghost" onClick={() => remove.mutate(alert.id)}>
                  Delete
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
