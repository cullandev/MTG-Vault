import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import { when } from '../lib/format'
import type { AuditEntry } from '../lib/types'
import { Button, Empty, ErrorNote, Panel } from '../components/ui'
import CardName from '../components/CardName'

interface AuditPage {
  items: AuditEntry[]
  next_cursor: string | null
}

const ACTION_LABEL: Record<string, string> = {
  create: 'Added',
  bulk_create: 'Added',
  delete: 'Removed',
  bulk_delete: 'Removed',
  update: 'Changed',
  revert: 'Undone',
}

const SOURCE_LABEL: Record<string, string> = {
  api: 'manual',
  csv_import: 'CSV import',
  scan: 'scan',
  revert: 'undo',
  job: 'scheduled job',
}

/** Every collection mutation, newest first, with one-click undo per batch. */
export default function AuditLog() {
  const queryClient = useQueryClient()

  const page = useInfiniteQuery({
    queryKey: ['audit'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.get<AuditPage>('/api/audit', { cursor: pageParam, limit: 50 }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })

  const revert = useMutation({
    mutationFn: (batchId: string) => api.post(`/api/audit/batches/${batchId}/revert`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['audit'] })
      void queryClient.invalidateQueries({ queryKey: ['collection'] })
    },
  })

  const entries = page.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <Panel title="History">
      <p className="mb-3 text-xs text-slate-500">
        Every change to the collection is recorded with its before and after state, so any
        mistake — a bad import, a wrong bulk add — can be undone as a unit.
      </p>

      <ErrorNote error={page.error ?? revert.error} />

      {page.isLoading ? (
        <Empty>Loading…</Empty>
      ) : entries.length === 0 ? (
        <Empty>Nothing has changed yet.</Empty>
      ) : (
        <ul className="divide-y divide-vault-line/60">
          {entries.map((entry) => {
            const summary = entry.summary ?? {}
            const card = summary.card as Record<string, unknown> | undefined
            const quantity = summary.quantity as number | undefined
            const label = card?.name ?? summary.name ?? entry.entity_type.replace('_', ' ')
            return (
              <li key={entry.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <span className="text-slate-200">
                  {ACTION_LABEL[entry.action] ?? entry.action}
                  {quantity ? ` ${quantity}×` : ''}{' '}
                  {card?.name ? <CardName name={String(card.name)} /> : String(label)}
                </span>
                <span className="text-[11px] text-slate-500">
                  {SOURCE_LABEL[entry.source] ?? entry.source} · {when(entry.ts)}
                </span>
                {entry.reverted_at ? (
                  <span className="ml-auto text-[11px] text-slate-600">undone</span>
                ) : entry.action === 'revert' ? null : (
                  <button
                    onClick={() => revert.mutate(entry.batch_id)}
                    disabled={revert.isPending}
                    className="ml-auto text-xs text-slate-400 hover:text-rose-300"
                  >
                    Undo
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {page.hasNextPage && (
        <div className="flex justify-center pt-3">
          <Button
            variant="ghost"
            onClick={() => void page.fetchNextPage()}
            disabled={page.isFetchingNextPage}
          >
            {page.isFetchingNextPage ? 'Loading…' : 'Load older'}
          </Button>
        </div>
      )}
    </Panel>
  )
}
