import { splitMentions } from '../lib/cardMentions'
import CardName from './CardName'

/**
 * One line of the game log, with every card it mentions shown as
 * [Card Name]: hover shows the card, click opens it. The ids Forge appends
 * -- "Mountain (59)" -- are dropped; a person reads names, not ids.
 */
export default function LogText({ text, known }: { text: string; known: readonly string[] }) {
  const segments = splitMentions(text, known)
  return (
    <>
      {segments.map((seg, index) =>
        seg.kind === 'card' ? (
          <span key={index} className="whitespace-nowrap text-slate-200">
            [<CardName name={seg.name ?? seg.text} className="font-medium" />]
          </span>
        ) : (
          <span key={index}>{seg.text}</span>
        ),
      )}
    </>
  )
}
