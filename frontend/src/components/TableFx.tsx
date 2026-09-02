import { useEffect, useRef } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'

import { cardNames, type BoardCard, type StackItem } from '../lib/boardCard'
import { anchorRect, cardRect, rememberCard } from '../lib/cardPositions'
import {
  SHATTER_MS,
  arcPoint,
  departedPermanents,
  fragmentAt,
  generateFragments,
  stackArrivals,
  stackDepartures,
  type Point,
} from '../lib/tableFx'
import type { BoardState } from './PlayMat'

/**
 * The cast arc and the death shatter, driven from what the snapshots say.
 *
 * Cast: a spell or ability that has just arrived on the stack flies from
 * where its card was -- your hand, a permanent whose ability was activated,
 * or the opponent's plate when its card was never shown -- up and over to
 * the stack panel, glowing brighter as it lands. When it lands, the card's
 * remembered position becomes the stack, so the zone animation the card
 * already has carries it from the stack to the battlefield when it
 * resolves; an instant or sorcery instead shrinks and fades where it sat.
 *
 * Death: a permanent that was on a battlefield and is on none breaks into a
 * dozen pieces of its own art that fly apart, spin and fall, flashing red as
 * they go -- gold, if it was exiled. The card is already gone from the DOM by
 * the time the snapshot says so; the pieces are cut from the art the board
 * had cached for it, drawn where the card was last seen.
 *
 * Both are ports of phase.rs animations (MIT; frontend/THIRD_PARTY.md),
 * imperative and requestAnimationFrame-driven here rather than Framer
 * Motion, and both are skipped under prefers-reduced-motion.
 */
export default function TableFx({
  board,
  version,
  tableRef,
}: {
  board: BoardState | null
  version: number
  tableRef: React.RefObject<HTMLElement | null>
}) {
  const previous = useRef<BoardState | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    const before = previous.current
    previous.current = board
    if (!board || !before || board.gameOver) return
    // A new game: nothing on this board came from the last one.
    if (board.turn < before.turn) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return

    const stackRect = stackAnchor(tableRef.current)

    for (const item of stackArrivals(before.stackItems, board.stackItems)) {
      if (item.trigger) continue
      const from = item.sourceId !== undefined ? cardRect(item.sourceId) : undefined
      const start = from ?? (item.by ? anchorRect(item.by) : undefined)
      if (!start) continue
      flyToStack(item, centre(start), stackRect, queryClient, board.players)
    }

    const stillOn = new Set<number>()
    for (const seat of board.players) for (const c of seat.battlefieldCards ?? []) stillOn.add(c.id)
    for (const item of stackDepartures(before.stackItems, board.stackItems)) {
      if (item.trigger || item.sourceId === undefined || stillOn.has(item.sourceId)) continue
      // Resolved without becoming a permanent: gone in a breath, at the stack.
      fadeAtStack(item, stackRect, queryClient)
    }

    for (const gone of departedPermanents(before, board)) {
      const rect = cardRect(gone.card.id)
      if (!rect || rect.width === 0) continue
      const image = imageFor(gone.card, queryClient)
      void loadImage(image).then((img) => shatter(rect, gone.card, img, gone.to === 'exile'))
    }
  }, [board, version, tableRef, queryClient])

  return null
}

interface Resolved {
  image_url?: string | null
}

function imageFor(card: BoardCard, queryClient: QueryClient): string | null {
  return queryClient.getQueryData<Resolved>(['card-resolve', cardNames(card).art])?.image_url ?? null
}

function centre(rect: DOMRect): Point {
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
}

/** The stack panel, or failing that the top middle of the table. */
function stackAnchor(table: HTMLElement | null): DOMRect {
  const panel = document.querySelector<HTMLElement>('[data-stack-panel]')
  if (panel) return panel.getBoundingClientRect()
  const t = table?.getBoundingClientRect()
  if (t) return new DOMRect(t.left + t.width / 2 - 40, t.top + 24, 80, 112)
  return new DOMRect(window.innerWidth / 2 - 40, 80, 80, 112)
}

const TILE_W = 80
const TILE_H = 112
const ARC_HEIGHT = 100
const CAST_MS = 400
const FADE_MS = 300

/** A small card, fixed to the viewport, that the effects fly and dissolve. */
function makeTile(name: string, image: string | null, mine: boolean): HTMLDivElement {
  const tile = document.createElement('div')
  tile.setAttribute('aria-hidden', 'true')
  tile.style.cssText =
    `position:fixed;left:0;top:0;width:${TILE_W}px;height:${TILE_H}px;pointer-events:none;z-index:95;` +
    'border-radius:6px;overflow:hidden;background:#0f172a;will-change:translate,scale,opacity;'
  if (image) {
    const img = document.createElement('img')
    img.src = image
    img.alt = ''
    img.draggable = false
    img.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;'
    tile.appendChild(img)
  } else {
    const label = document.createElement('span')
    label.textContent = name
    label.style.cssText =
      'display:flex;width:100%;height:100%;align-items:center;justify-content:center;padding:4px;' +
      'text-align:center;font-size:10px;color:#e2e8f0;background:rgba(0,0,0,.7);'
    tile.appendChild(label)
  }
  tile.dataset.glow = mine ? 'var(--pm-accent-glow, rgba(56,189,248,.6))' : 'rgba(169,179,196,.6)'
  document.body.appendChild(tile)
  return tile
}

function place(tile: HTMLDivElement, at: Point, scale: number): void {
  tile.style.translate = `${at.x - TILE_W / 2}px ${at.y - TILE_H / 2}px`
  tile.style.scale = `${scale}`
}

function flyToStack(item: StackItem, from: Point, stackRect: DOMRect, queryClient: QueryClient, players: BoardState['players']): void {
  const name = item.source || item.text
  const image = item.sourceId !== undefined ? imageFor(cardFor(item, players), queryClient) : null
  const tile = makeTile(name, image, item.mine === true)
  const glow = tile.dataset.glow ?? ''
  const to = centre(stackRect)
  const start = performance.now()
  place(tile, from, 1)

  const frame = (now: number) => {
    const t = Math.min((now - start) / CAST_MS, 1)
    const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
    place(tile, arcPoint(from, to, ARC_HEIGHT, eased), 1 + 0.1 * Math.sin(eased * Math.PI))
    // The glow intensifies at the destination, as theirs does.
    tile.style.boxShadow = `0 0 ${4 + 20 * eased}px ${glow}`
    if (t < 1) {
      requestAnimationFrame(frame)
      return
    }
    // The card now lives at the stack: when it resolves onto the battlefield,
    // the zone animation flies it from here rather than from the hand.
    if (item.sourceId !== undefined) rememberCard(item.sourceId, new DOMRect(to.x - TILE_W / 2, to.y - TILE_H / 2, TILE_W, TILE_H))
    const fade = tile.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: 'ease-out', fill: 'forwards' })
    fade.onfinish = () => tile.remove()
  }
  requestAnimationFrame(frame)
  window.setTimeout(() => tile.remove(), CAST_MS + 600)
}

function fadeAtStack(item: StackItem, stackRect: DOMRect, queryClient: QueryClient): void {
  const name = item.source || item.text
  const image = imageFor({ id: item.sourceId ?? -1, name: item.source ?? name }, queryClient)
  const tile = makeTile(name, image, item.mine === true)
  const at = centre(stackRect)
  place(tile, at, 1)
  tile.style.boxShadow = `0 0 12px ${tile.dataset.glow ?? ''}`
  const anim = tile.animate(
    [
      { opacity: 1, scale: '1' },
      { opacity: 0, scale: '0.3' },
    ],
    { duration: FADE_MS, easing: 'ease-in', fill: 'forwards' },
  )
  anim.onfinish = () => tile.remove()
  window.setTimeout(() => tile.remove(), FADE_MS + 200)
}

function cardFor(item: StackItem, players: BoardState['players']): BoardCard {
  for (const seat of players) {
    for (const zone of [seat.handCards, seat.battlefieldCards, seat.graveyardCards, seat.exileCards, seat.commanderCards]) {
      const found = zone?.find((c) => c.id === item.sourceId)
      if (found) return found
    }
  }
  return { id: item.sourceId ?? -1, name: item.source ?? item.text }
}

/** The card's art, if it is already in the browser; nothing waits on the network. */
function loadImage(url: string | null): Promise<HTMLImageElement | null> {
  if (!url) return Promise.resolve(null)
  return new Promise((resolve) => {
    const img = new Image()
    const timer = window.setTimeout(() => resolve(null), 200)
    img.onload = () => {
      window.clearTimeout(timer)
      resolve(img)
    }
    img.onerror = () => {
      window.clearTimeout(timer)
      resolve(null)
    }
    img.src = url
  })
}

const SHATTER_PAD = 200
const FLASH_S = 0.1

function shatter(rect: DOMRect, card: BoardCard, image: HTMLImageElement | null, exiled: boolean): void {
  const width = Math.round(rect.width)
  const height = Math.round(rect.height)
  const canvas = document.createElement('canvas')
  canvas.width = width + SHATTER_PAD * 2
  canvas.height = height + SHATTER_PAD * 2
  canvas.setAttribute('aria-hidden', 'true')
  canvas.style.cssText =
    `position:fixed;left:${rect.left - SHATTER_PAD}px;top:${rect.top - SHATTER_PAD}px;` +
    `width:${canvas.width}px;height:${canvas.height}px;pointer-events:none;z-index:96;`
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  // The face, drawn once offscreen; the pieces are cut from it every frame.
  const face = document.createElement('canvas')
  face.width = width
  face.height = height
  const faceCtx = face.getContext('2d')
  if (!faceCtx) return
  if (image) {
    faceCtx.drawImage(image, 0, 0, width, height)
  } else {
    faceCtx.fillStyle = '#1e293b'
    faceCtx.fillRect(0, 0, width, height)
    faceCtx.fillStyle = '#e2e8f0'
    faceCtx.font = '600 10px system-ui, sans-serif'
    faceCtx.textAlign = 'center'
    faceCtx.fillText(cardNames(card).shown.slice(0, 18), width / 2, height / 2)
  }

  const fragments = generateFragments(width, height)
  const tint = exiled ? '245, 208, 96' : '239, 68, 68'
  document.body.appendChild(canvas)
  const start = performance.now()
  let done = false
  const finish = () => {
    if (done) return
    done = true
    canvas.remove()
  }

  const tick = (now: number) => {
    if (done) return
    const seconds = (now - start) / 1000
    const progress = Math.min((seconds * 1000) / SHATTER_MS, 1)
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    for (const f of fragments) {
      const at = fragmentAt(f, seconds)
      ctx.save()
      ctx.globalAlpha = 1 - progress
      ctx.translate(at.x + SHATTER_PAD + f.sw / 2, at.y + SHATTER_PAD + f.sh / 2)
      ctx.rotate((at.rotation * Math.PI) / 180)
      ctx.drawImage(face, f.sx, f.sy, f.sw, f.sh, -f.sw / 2, -f.sh / 2, f.sw, f.sh)
      if (seconds < FLASH_S) {
        ctx.globalCompositeOperation = 'source-atop'
        ctx.fillStyle = `rgba(${tint}, ${0.5 * (1 - seconds / FLASH_S)})`
        ctx.fillRect(-f.sw / 2, -f.sh / 2, f.sw, f.sh)
      }
      ctx.restore()
    }
    if (progress >= 1) finish()
    else requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
  window.setTimeout(finish, SHATTER_MS + 500)
}
