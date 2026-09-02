import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { api } from '../lib/api'
import { money } from '../lib/format'
import { Empty, Panel } from '../components/ui'
import HoverCardImage from '../components/HoverCardImage'

interface SetCard {
  card_id: number
  oracle_id: string
  name: string
  collector_number: string
  rarity: string
  image_url: string | null
  image_small: string | null
  price_usd_cents: number | null
  owned_count: number
}

interface SetPayload {
  set_code: string
  set_name: string
  total_numbers: number
  owned_numbers: number
  completion: number
  cards: SetCard[]
}

/**
 * The binder view: the whole set in collector order. Owned cards read
 * normally; the gaps sit greyed out exactly where they belong, which is what
 * makes a missing card feel findable rather than invisible.
 */
export default function SetDetailPage() {
  const { setCode = '' } = useParams()
  const query = useQuery({
    queryKey: ['set-cards', setCode],
    queryFn: () => api.get<SetPayload>(`/api/sets/${setCode}/cards`),
    enabled: setCode.length >= 2,
  })

  const data = query.data
  return (
    <div className="space-y-3">
      <Panel
        title={data ? data.set_name : setCode.toUpperCase()}
        actions={
          <Link to="/sets" className="text-xs text-slate-400 underline">
            All sets
          </Link>
        }
      >
        {data && (
          <div className="flex items-center gap-3">
            <span className="block h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
              <span
                className="block h-full rounded-full bg-sky-500/70"
                style={{ width: `${Math.round(data.completion * 100)}%` }}
              />
            </span>
            <span className="shrink-0 text-xs tabular-nums text-slate-300">
              {Math.round(data.completion * 100)}% · {data.owned_numbers}/{data.total_numbers}{' '}
              collected
            </span>
          </div>
        )}
      </Panel>

      {query.isError && <Empty>No such set.</Empty>}
      {query.isLoading && <Empty>Opening the binder…</Empty>}

      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {(data?.cards ?? []).map((card) => (
          <Link
            key={card.card_id}
            to={`/cards/${card.oracle_id}`}
            className={`card-surface group relative overflow-hidden transition [contain-intrinsic-size:auto_260px] [content-visibility:auto] ${
              card.owned_count > 0
                ? 'hover:border-sky-700'
                : 'opacity-40 grayscale hover:opacity-70 hover:grayscale-0'
            }`}
          >
            {card.image_url ? (
              <HoverCardImage imageUrl={card.image_url} alt={card.name} className="block">
                <img
                  src={card.image_small ?? card.image_url}
                  alt={card.name}
                  loading="lazy"
                  className="aspect-[5/7] w-full object-cover"
                />
              </HoverCardImage>
            ) : (
              <div className="flex aspect-[5/7] items-center justify-center p-2 text-center text-[10px] text-slate-500">
                {card.name}
              </div>
            )}
            <div className="flex items-center justify-between px-1.5 py-1 text-[10px] tabular-nums">
              <span className="text-slate-500">#{card.collector_number}</span>
              <span className="text-slate-400">{money(card.price_usd_cents)}</span>
            </div>
            {card.owned_count > 0 && (
              <span className="absolute right-1 top-1 rounded-full bg-emerald-500/90 px-1.5 text-[10px] font-semibold text-emerald-950">
                ×{card.owned_count}
              </span>
            )}
          </Link>
        ))}
      </div>
    </div>
  )
}
