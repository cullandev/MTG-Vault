/**
 * Combat, felt: an attacker lunges at what it hits and comes back; the table
 * shakes with the blow.
 *
 * Both routines are adapted from phase.rs (`client/src/components/animation/
 * CardSlamAnimation.tsx` and `ScreenShake.tsx`), MIT licensed, Copyright (c)
 * 2024-2026 phase.rs contributors -- see frontend/THIRD_PARTY.md. They animate
 * the real DOM element with the independent `translate` and `scale`
 * properties, so they compose with whatever `transform` the card already
 * carries (a tapped card is rotated) instead of overwriting it.
 *
 * A collapsed stack draws one card for a swarm, so several hits in one step
 * can land on the same element; the WeakSet lets the first slam run and the
 * caller fall back to a floating number for the rest.
 */

const inFlight = new WeakSet<HTMLElement>()

export const SLAM_FLIGHT_MS = 200

export function applyCardSlam(element: HTMLElement, targetX: number, targetY: number, onImpact?: () => void): boolean {
  if (inFlight.has(element)) return false
  inFlight.add(element)

  const rect = element.getBoundingClientRect()
  const dx = targetX - (rect.x + rect.width / 2)
  const dy = targetY - (rect.y + rect.height / 2)
  // Stop short of the target's centre: the hit reads as contact, not overlap.
  const reach = 0.72
  const flight = SLAM_FLIGHT_MS
  const jitter = 300
  const back = 250
  const total = flight + jitter + back
  const start = performance.now()
  let impacted = false
  const originalZ = element.style.zIndex
  element.style.zIndex = '100'

  const frame = (now: number) => {
    const elapsed = now - start
    if (elapsed >= total) {
      element.style.translate = ''
      element.style.scale = ''
      element.style.zIndex = originalZ
      inFlight.delete(element)
      return
    }
    if (elapsed < flight) {
      const t = elapsed / flight
      const eased = t * t
      element.style.translate = `${dx * reach * eased}px ${dy * reach * eased}px`
      element.style.scale = `${1 + 0.12 * eased}`
    } else if (elapsed < flight + jitter) {
      if (!impacted) {
        impacted = true
        onImpact?.()
      }
      const jt = (elapsed - flight) / jitter
      const decay = 1 - jt
      const osc = Math.sin(jt * Math.PI * 6) * 8 * decay
      element.style.translate = `${dx * reach + osc}px ${dy * reach + osc * 0.5}px`
      element.style.scale = `${1 + decay * 0.04}`
    } else {
      const rt = (elapsed - flight - jitter) / back
      const eased = 1 - (1 - rt) * (1 - rt)
      element.style.translate = `${dx * reach * (1 - eased)}px ${dy * reach * (1 - eased)}px`
      element.style.scale = ''
    }
    requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
  return true
}

export type ShakeIntensity = 'light' | 'medium' | 'heavy'

const SHAKES: Record<ShakeIntensity, { amplitude: number; duration: number; oscillations: number }> = {
  light: { amplitude: 2, duration: 150, oscillations: 4 },
  medium: { amplitude: 4, duration: 250, oscillations: 5 },
  heavy: { amplitude: 8, duration: 350, oscillations: 6 },
}

/** How hard a hit of this size shakes the table. */
export function shakeFor(damage: number): ShakeIntensity {
  return damage >= 8 ? 'heavy' : damage >= 4 ? 'medium' : 'light'
}

export function applyScreenShake(element: HTMLElement, intensity: ShakeIntensity): void {
  const { amplitude, duration, oscillations } = SHAKES[intensity]
  const start = performance.now()
  const frame = (now: number) => {
    const progress = (now - start) / duration
    if (progress >= 1) {
      element.style.translate = ''
      return
    }
    const decay = 1 - progress
    const angle = progress * oscillations * Math.PI * 2
    element.style.translate = `${Math.sin(angle) * amplitude * decay}px ${Math.sin(angle) * amplitude * 0.5 * decay}px`
    requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
}
