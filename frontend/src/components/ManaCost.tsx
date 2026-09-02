import { costShards, pipFor } from '../lib/manaCost'

/**
 * A mana cost, drawn as pips rather than printed as `{2}{R}`.
 *
 * phase.rs renders Scryfall's symbol SVGs over the network. The vault proxies
 * every image through /api/images and ships a service worker precisely so a LAN
 * instance keeps working, so these are drawn in CSS instead: no request, no
 * asset, nothing to cache. The symbol vocabulary is theirs; see lib/manaCost.
 */

const SIZES = {
  sm: { box: 13, font: 8 },
  md: { box: 16, font: 10 },
} as const

export default function ManaCost({
  cost,
  size = 'sm',
  className = '',
}: {
  cost?: string | null
  size?: keyof typeof SIZES
  className?: string
}) {
  const shards = costShards(cost)
  // A land, or a token, has no cost. Drawing nothing is better than an empty
  // row holding space open under every permanent that never had one.
  if (shards.length === 0) return null
  const { box, font } = SIZES[size]

  return (
    <span className={`inline-flex items-center gap-px align-middle ${className}`}>
      {shards.map((shard, index) => {
        const pip = pipFor(shard)
        return (
          <span
            key={`${shard}-${index}`}
            title={pip.title}
            style={{
              width: box,
              height: box,
              background: pip.background,
              fontSize: pip.glyph.length > 1 ? font - 1 : font,
            }}
            className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold leading-none text-slate-900 shadow-[0_0_0_1px_rgba(0,0,0,0.45)]"
          >
            {pip.glyph}
          </span>
        )
      })}
    </span>
  )
}
