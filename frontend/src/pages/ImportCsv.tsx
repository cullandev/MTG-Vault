import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import { invalidateCollection } from '../lib/invalidate'
import type { CsvImportResult } from '../lib/types'
import { Button, ErrorNote, Field, Panel, inputClass } from '../components/ui'
import CardName from '../components/CardName'
import { useToast } from '../components/toast'

const FLAVOURS = [
  { value: '', label: 'Detect automatically' },
  { value: 'moxfield', label: 'Moxfield' },
  { value: 'archidekt', label: 'Archidekt' },
  { value: 'deckbox', label: 'Deckbox' },
  { value: 'native', label: 'MTG Vault export' },
]

export default function ImportCsv() {
  const [file, setFile] = useState<File | null>(null)
  const [flavour, setFlavour] = useState('')
  const [result, setResult] = useState<CsvImportResult | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const toast = useToast()


  const run = useMutation({
    mutationFn: (dryRun: boolean) => {
      if (!file) throw new Error('Choose a CSV file first')
      const form = new FormData()
      form.set('file', file)
      form.set('dry_run', String(dryRun))
      if (flavour) form.set('flavour', flavour)
      return api.upload<CsvImportResult>('/api/collection/import', form)
    },
    onSuccess: (data) => {
      setResult(data)
      if (!data.dry_run) {
        toast(`Imported ${data.matched} cards ✓ (one batch — undoable in History)`)
        invalidateCollection(queryClient)
      }
      void queryClient.invalidateQueries({ queryKey: ['audit'] })
    },
  })

  const undo = useMutation({
    mutationFn: (batchId: string) => api.post(`/api/audit/batches/${batchId}/revert`),
    onSuccess: () => {
      setResult(null)
      toast('Import undone ✓')
      void queryClient.invalidateQueries({ queryKey: ['collection'] })
      void queryClient.invalidateQueries({ queryKey: ['audit'] })
    },
  })

  return (
    <div className="space-y-3">
      <Panel title="Import a collection CSV">
        <p className="mb-3 text-xs text-slate-500">
          Exports from Moxfield, Archidekt and Deckbox are understood directly. The import
          runs as a preview first — nothing is written until you confirm.
        </p>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="CSV file">
            <input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null)
                setResult(null)
              }}
              className="text-xs text-slate-300 file:mr-2 file:rounded-lg file:border-0 file:bg-slate-800 file:px-3 file:py-2 file:text-slate-200"
            />
          </Field>
          <Field label="Format">
            <select value={flavour} onChange={(e) => setFlavour(e.target.value)} className={inputClass}>
              {FLAVOURS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => run.mutate(true)} disabled={!file || run.isPending}>
            {run.isPending ? 'Working…' : 'Preview'}
          </Button>
          <Button
            onClick={() => run.mutate(false)}
            disabled={!file || run.isPending || !result?.dry_run}
          >
            {run.isPending && run.variables === false ? 'Importing…' : 'Import for real'}
          </Button>
        </div>

        <ErrorNote error={run.error} />
      </Panel>

      {result && (
        <Panel
          title={result.dry_run ? 'Preview' : 'Imported'}
          actions={
            result.batch_id ? (
              <button
                onClick={() => undo.mutate(result.batch_id!)}
                className="text-xs text-slate-400 hover:text-rose-300"
              >
                Undo this import
              </button>
            ) : undefined
          }
        >
          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <Counter label="Rows read" value={result.rows_seen} />
            <Counter label="Matched" value={result.matched} />
            <Counter label="Added" value={result.added} tone={result.added ? 'good' : undefined} />
            <Counter
              label="Needs attention"
              value={result.ambiguous.length + result.unmatched.length}
              tone={result.ambiguous.length + result.unmatched.length ? 'warn' : undefined}
            />
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            Detected format: <span className="text-slate-300">{result.flavour}</span>
          </p>

          {result.errors.length > 0 && (
            <div className="mt-3 space-y-1">
              <h3 className="text-xs font-semibold text-amber-200">Line problems</h3>
              {result.errors.map((message) => (
                <p key={message} className="text-xs text-amber-200/80">
                  {message}
                </p>
              ))}
            </div>
          )}

          <RowList title="Could not be matched" rows={result.unmatched} />
          <RowList title="Ambiguous — pick a printing" rows={result.ambiguous} />

          {result.preview.length > 0 && (
            <div className="mt-4">
              <h3 className="mb-1 text-xs font-semibold text-slate-300">
                First {result.preview.length} matches
              </h3>
              <ul className="space-y-0.5 text-xs text-slate-400">
                {result.preview.map((row, index) => {
                  const resolved = row.resolved as Record<string, unknown> | undefined
                  return (
                    <li key={index}>
                      {String(row.quantity)}× <CardName name={String(resolved?.name ?? row.name)} />{' '}
                      <span className="text-slate-600">
                        {String(resolved?.set_code ?? '').toUpperCase()}{' '}
                        {String(resolved?.collector_number ?? '')}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}

          <ErrorNote error={undo.error} />
        </Panel>
      )}
    </div>
  )
}

function Counter({ label, value, tone }: { label: string; value: number; tone?: 'good' | 'warn' }) {
  const colour =
    tone === 'good' ? 'text-emerald-300' : tone === 'warn' ? 'text-amber-300' : 'text-slate-100'
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`text-lg font-semibold ${colour}`}>{value.toLocaleString()}</p>
    </div>
  )
}

function RowList({ title, rows }: { title: string; rows: Array<Record<string, unknown>> }) {
  if (rows.length === 0) return null
  return (
    <div className="mt-4">
      <h3 className="mb-1 text-xs font-semibold text-amber-200">
        {title} ({rows.length})
      </h3>
      <ul className="space-y-0.5 text-xs text-slate-400">
        {rows.slice(0, 50).map((row, index) => (
          <li key={index}>
            line {String(row.line_no)}: {String(row.quantity)}× <CardName name={String(row.name)} />
            {row.set_code ? ` (${String(row.set_code).toUpperCase()})` : ''}
          </li>
        ))}
      </ul>
      {rows.length > 50 && (
        <p className="mt-1 text-[11px] text-slate-600">…and {rows.length - 50} more.</p>
      )}
    </div>
  )
}
