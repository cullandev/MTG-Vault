import type { DeckSummary } from '../lib/types'
import CardName, { CardNameList } from './CardName'

/**
 * "What this deck does and why it was built" — the generator's own summary,
 * shown on machine-built decks and on freshly generated results. Every line
 * is a counted or recorded fact from the generator, not flavour text.
 */
export default function DeckSummaryPanel({ summary }: { summary: DeckSummary }) {
  return (
    <div className="space-y-4 rounded-lg border border-vault-line p-4 text-sm">
      <div>
        <p className="text-lg font-semibold text-slate-100">{summary.headline}</p>
        <p className="mt-1.5 leading-relaxed text-slate-200">{summary.game_plan}</p>
      </div>

      {summary.mechanics.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs uppercase tracking-wide text-slate-500">Mechanics</p>
          <div className="flex flex-wrap gap-2">
            {summary.mechanics.map((mechanic) => (
              <span
                key={mechanic.tag}
                className="rounded-full border border-vault-line bg-slate-800/60 px-2.5 py-1 text-slate-200"
                title={mechanic.examples.join(', ')}
              >
                {mechanic.count} {mechanic.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {summary.key_cards.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs uppercase tracking-wide text-slate-500">Key cards</p>
          <ul className="space-y-1 text-slate-200">
            {summary.key_cards.map((card) => (
              <li key={card.name}>
                <CardName name={card.name} className="text-slate-100" />
                <span className="text-slate-400"> — {card.why}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {summary.why_picked.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs uppercase tracking-wide text-slate-500">
            Why this deck was picked
          </p>
          <ul className="list-disc space-y-1 pl-4 leading-relaxed text-slate-200">
            {summary.why_picked.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {summary.mechanics.some((m) => m.examples.length > 0) && (
        <p className="text-xs text-slate-400">
          e.g.{' '}
          <CardNameList
            names={[...new Set(summary.mechanics.flatMap((m) => m.examples))].slice(0, 6)}
          />
        </p>
      )}
    </div>
  )
}
