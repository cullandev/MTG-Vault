import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { api } from '../lib/api'

interface Resolved {
  found: boolean
  oracle_id?: string
  name?: string
  type_line?: string | null
  mana_cost?: string | null
  card_id?: number | null
  image_url?: string | null
  price_cents?: number | null
}

/**
 * A card name, wherever one appears: hover shows the card, click opens it.
 *
 * Resolution is lazy (first hover or focus) and cached for the session, so a
 * page of a hundred names costs nothing until the pointer touches one. Names
 * the catalogue cannot resolve render as plain text -- no dead links.
 */
export default function CardName({
  name,
  oracleId,
  className = '',
}: {
  name: string
  oracleId?: string
  className?: string
}) {
  const [active, setActive] = useState(false)
  const [position, setPosition] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  // On touch screens the first tap peeks (there is no hover); the second opens.
  const [tapPeek, setTapPeek] = useState(false)
  const peekTimer = useRef<number | null>(null)
  const navigate = useNavigate()

  useEffect(
    () => () => {
      if (peekTimer.current !== null) window.clearTimeout(peekTimer.current)
    },
    [],
  )

  const resolved = useQuery({
    queryKey: ['card-resolve', name],
    queryFn: () => api.get<Resolved>('/api/cards/resolve', { name }),
    enabled: active,
    staleTime: Infinity,
    // A name EDHREC or an AI suggested may simply not resolve; that is data,
    // not an error worth retrying or showing.
    retry: false,
  })

  const target = oracleId ?? resolved.data?.oracle_id

  return (
    <span
      className={`relative ${className}`}
      onMouseEnter={(event) => {
        setActive(true)
        setPosition({ x: event.clientX, y: event.clientY })
      }}
      onMouseLeave={() => setActive(false)}
    >
      <button
        type="button"
        onClick={(event) => {
          const coarse = window.matchMedia('(pointer: coarse)').matches
          if (coarse && !tapPeek) {
            // First tap: show the preview where the finger landed, don't leave
            // the page. A second tap within the window opens the card.
            setActive(true)
            setTapPeek(true)
            setPosition({ x: event.clientX, y: event.clientY })
            if (peekTimer.current !== null) window.clearTimeout(peekTimer.current)
            peekTimer.current = window.setTimeout(() => {
              setTapPeek(false)
              setActive(false)
            }, 3500)
            return
          }
          if (target) {
            navigate(`/cards/${target}`)
            return
          }
          // Never hovered and not a peek: resolve now, then open if it resolved.
          void resolved.refetch().then((result) => {
            if (result.data?.found && result.data.oracle_id) {
              navigate(`/cards/${result.data.oracle_id}`)
            }
          })
        }}
        className="cursor-pointer underline decoration-slate-600 decoration-dotted underline-offset-2 hover:text-sky-200"
      >
        {name}
      </button>
      {active && resolved.data?.found && resolved.data.image_url && (
        <span
          className="pointer-events-none fixed z-50 block w-[28rem] max-w-[90vw] overflow-hidden rounded-xl border border-vault-line bg-vault-bg shadow-2xl"
          style={{
            left: Math.max(8, Math.min(position.x + 16, window.innerWidth - 464)),
            top: Math.max(8, Math.min(position.y + 12, window.innerHeight - 660)),
          }}
        >
          <img src={resolved.data.image_url} alt={name} className="block w-full" loading="lazy" />
        </span>
      )}
    </span>
  )
}

/** A comma/`+`-separated run of card names, each with the hover/click behaviour. */
export function CardNameList({
  names,
  separator = ', ',
}: {
  names: string[]
  separator?: string
}) {
  return (
    <>
      {names.map((name, index) => (
        <span key={`${name}-${index}`}>
          {index > 0 && separator}
          <CardName name={name} />
        </span>
      ))}
    </>
  )
}
