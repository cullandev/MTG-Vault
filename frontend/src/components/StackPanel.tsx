import type { BoardCard, StackItem } from '../lib/boardCard'

/**
 * The stack, as a list you can read: what is about to resolve, who cast it,
 * and what it is pointed at.
 *
 * This is the moment auto-pass hands you -- the opponent has cast something and
 * you hold priority with mana up -- and until now the table said "stack: 1".
 * Top of the stack first, because that is what resolves next.
 */
export default function StackPanel({
  items,
  cards,
  players,
}: {
  items: StackItem[]
  /** Every card on the table, to name a target by more than its id. */
  cards: Map<number, BoardCard>
  /** Player names in seat order, so a targeted player reads as a name. */
  players: string[]
}) {
  if (items.length === 0) return null
  const ordered = [...items].reverse()

  return (
    <div data-stack-panel className="border-b border-slate-800 bg-violet-950/30 px-3 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-violet-300">
        Stack <span className="tabular-nums text-violet-400/80">({items.length})</span>
        <span className="ml-2 normal-case tracking-normal text-violet-400/70">top resolves first</span>
      </p>
      <ol className="mt-1 flex flex-col gap-1">
        {ordered.map((item, i) => {
          const targets = [
            ...item.targetCards.map((id) => cards.get(id)?.name ?? `#${id}`),
            ...item.targetPlayers.filter((name) => players.includes(name) || true),
          ]
          const top = i === 0
          return (
            <li
              key={item.index}
              className={
                'flex flex-wrap items-baseline gap-x-2 rounded border-l-2 px-2 py-0.5 text-xs ' +
                (top
                  ? 'border-violet-400 bg-violet-900/40 text-violet-100'
                  : 'border-violet-800 text-violet-300/80')
              }
            >
              <span className="font-medium">{item.source || item.text}</span>
              {item.trigger && <span className="text-[10px] uppercase text-violet-400">trigger</span>}
              {item.by && (
                <span className={item.mine ? 'text-sky-300' : 'text-slate-400'}>
                  {item.mine ? 'you' : item.by}
                </span>
              )}
              {targets.length > 0 && (
                <span className="text-slate-300">
                  → {targets.join(', ')}
                </span>
              )}
              {item.source && item.text && item.text !== item.source && (
                <span className="basis-full text-[11px] text-slate-400">{item.text}</span>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
