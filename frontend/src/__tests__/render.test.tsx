// @vitest-environment happy-dom
// Tests run in Node by default -- most are pure logic. This file renders, so
// it opts into a DOM above and brings Testing Library's matchers with it,
// rather than every pure test paying for a setup file.
import '@testing-library/jest-dom/vitest'
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import Hand from '../components/Hand'
import PlayCard from '../components/PlayCard'
import StackPanel from '../components/StackPanel'
import PlayMat, { type BoardState, type Seat } from '../components/PlayMat'
import type { BoardCard, StackItem } from '../lib/boardCard'
import { realmById } from '../lib/playmats'

/**
 * The first tests that render anything. Four rounds of table work shipped
 * verified by wire data and the served bundle and never by eye; these assert
 * what the eye would have checked.
 */

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const card = (id: number, name: string, extra: Partial<BoardCard> = {}): BoardCard => ({ id, name, ...extra })
const noop = () => {}

describe('Hand', () => {
  it('fans every card with its own tilt', () => {
    const cards = [1, 2, 3, 4, 5].map((i) => card(i, `Card ${i}`))
    const { container } = wrap(<Hand cards={cards} playing={false} onHover={noop} hoveredId={null} />)
    const wrappers = Array.from(container.querySelectorAll<HTMLElement>('span.will-change-transform'))
    expect(wrappers).toHaveLength(5)
    const rotations = wrappers.map((w) => /rotate\(([-\d.]+)deg\)/.exec(w.style.transform)?.[1])
    expect(new Set(rotations).size).toBe(5)
    // Symmetric about the centre: the middle card is upright.
    expect(Number(rotations[2])).toBeCloseTo(0)
  })

  it('lifts the hovered card out of the fan', () => {
    const cards = [1, 2, 3].map((i) => card(i, `Card ${i}`))
    const { container } = wrap(<Hand cards={cards} playing={false} onHover={noop} hoveredId={2} />)
    const wrappers = Array.from(container.querySelectorAll<HTMLElement>('span.will-change-transform'))
    expect(wrappers[1]?.style.transform).toContain('translateY(-')
    expect(wrappers[1]?.style.zIndex).toBe('60')
  })
})

describe('PlayCard', () => {
  it('rings a card the engine could act on in emerald, and one it asks for in sky', () => {
    const { container: weak } = wrap(<PlayCard card={card(1, 'Mountain', { weak: true })} />)
    expect(weak.querySelector('button')?.className).toContain('border-emerald-500/80')
    const { container: asked } = wrap(<PlayCard card={card(2, 'Mountain', { selectable: true })} />)
    expect(asked.querySelector('button')?.className).toContain('ring-sky-400/70')
  })

  it('wears its counters and keywords', () => {
    wrap(<PlayCard card={card(3, 'Bear', { power: 2, toughness: 2, counters: { P1P1: 2 }, keywords: ['Trample'] })} />)
    expect(screen.getByText('+2/+2')).toBeInTheDocument()
    expect(screen.getByText('Trample')).toBeInTheDocument()
  })

  it('shows a card back when face down, whatever its name', () => {
    wrap(<PlayCard card={card(4, 'Secret Creature', { faceDown: true })} />)
    expect(screen.getByText('face down')).toBeInTheDocument()
    expect(screen.queryByText('Secret Creature')).toBeNull()
  })

  it('subtracts marked damage from the toughness it shows', () => {
    wrap(<PlayCard card={card(5, 'Bear', { power: 2, toughness: 4, damage: 3 })} />)
    expect(screen.getByText('2/1')).toBeInTheDocument()
  })
})

describe('StackPanel', () => {
  it('puts the top of the stack first and names its target', () => {
    const items: StackItem[] = [
      { index: 0, text: 'Giant Growth', trigger: false, source: 'Giant Growth', by: 'AI 2', mine: false, targetCards: [9], targetPlayers: [] },
      { index: 1, text: 'Lightning Bolt', trigger: false, source: 'Lightning Bolt', by: 'Me', mine: true, targetCards: [], targetPlayers: ['AI 2'] },
    ]
    const cards = new Map<number, BoardCard>([[9, card(9, 'Grizzly Bears')]])
    const { container } = wrap(<StackPanel items={items} cards={cards} players={['Me', 'AI 2']} />)
    const rows = Array.from(container.querySelectorAll('li'))
    expect(rows[0]?.textContent).toContain('Lightning Bolt')
    expect(rows[0]?.textContent).toContain('AI 2')
    expect(rows[1]?.textContent).toContain('Grizzly Bears')
    expect(screen.getByText(/top resolves first/)).toBeInTheDocument()
  })
})

const seat = (name: string, extra: Partial<Seat> = {}): Seat => ({
  name, life: 20, hand: 7, library: 50, battlefield: [], graveyard: [], commanders: [], ...extra,
})

describe('PlayMat', () => {
  it('stacks five identical lands behind one card with a count', () => {
    const lands = [1, 2, 3, 4, 5].map((i) => card(i, 'Mountain', { kind: 'land' }))
    const board: BoardState = {
      turn: 2, phase: 'MAIN1', active: 'Me', gameOver: false, stack: [],
      players: [seat('Me', { you: true, battlefieldCards: lands }), seat('AI 2')],
    }
    const { container } = wrap(<PlayMat board={board} playing={false} />)
    expect(screen.getByText('×5')).toBeInTheDocument()
    // One drawn card; the other four are behind it.
    expect(container.querySelectorAll('[data-card-id]')).toHaveLength(1)
  })

  it('marks the phases the game stops in, and says whose turn it is', () => {
    const board: BoardState = {
      turn: 2, phase: 'MAIN1', active: 'Me', gameOver: false, stack: [],
      stopsMine: ['MAIN1', 'MAIN2'],
      players: [seat('Me', { you: true }), seat('AI 2')],
    }
    const { container } = wrap(<PlayMat board={board} playing yourTurn onStop={noop} />)
    expect(screen.getByText('your turn')).toBeInTheDocument()
    const dots = container.querySelectorAll('button[title*="stops here"]')
    expect(dots).toHaveLength(2)
  })

  it('dresses the table in the chosen realm', () => {
    const board: BoardState = {
      turn: 1, phase: 'MAIN1', active: 'Me', gameOver: false, stack: [],
      players: [seat('Me', { you: true }), seat('AI 2')],
    }
    const { container } = wrap(<PlayMat board={board} playing={false} realm={realmById('rimehold')} />)
    const root = container.querySelector('[data-realm]') as HTMLElement | null
    expect(root?.dataset.realm).toBe('rimehold')
    // The pale realm's dark ink reaches the chrome through the custom property.
    expect(root?.style.getPropertyValue('--pm-ink')).toBe('#0f1c2b')
    expect(root?.style.getPropertyValue('--pm-art')).toContain('/playmats/rimehold.jpg')
  })

  it('offers the graveyard as a pile that says when the engine wants a card from it', () => {
    const board: BoardState = {
      turn: 4, phase: 'MAIN1', active: 'Me', gameOver: false, stack: [], selecting: true,
      players: [seat('Me', { you: true, graveyardCards: [card(11, 'Reanimate Me', { selectable: true })] }), seat('AI 2')],
    }
    wrap(<PlayMat board={board} playing />)
    expect(screen.getByText('pick')).toBeInTheDocument()
  })
})
