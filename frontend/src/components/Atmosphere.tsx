import type { CSSProperties } from 'react'

import type { Realm } from '../lib/playmats'

/**
 * The realm's weather: embers rising off a forge floor, snow, fireflies,
 * shafts of light, mist lying across the ground.
 *
 * A port of the atmosphere layer from the owner's playmat package (Claude
 * Design), typed and with the keyframes it needs living in index.css. Purely
 * decorative and pointer-transparent: nothing here is information, so it
 * respects prefers-reduced-motion by not animating at all.
 *
 * Particle positions come from a deterministic hash, not Math.random, so the
 * layer draws the same on every render and the board redrawing every 700 ms
 * does not reshuffle the sky.
 */
export default function Atmosphere({ realm }: { realm: Realm }) {
  const seed = realm.id.length * 7
  const rnd = (i: number, k: number) => {
    const x = Math.sin(i * 12.9898 + k * 78.233 + seed) * 43758.5453
    return x - Math.floor(x)
  }
  const layers: { key: string; style: CSSProperties }[] = []
  const layer = (key: string, style: CSSProperties) => layers.push({ key, style })
  const mist = (key: string, color: string, top: string, dur: number) =>
    layer(key, {
      left: '-10%',
      right: '-10%',
      top,
      height: '38%',
      filter: 'blur(28px)',
      opacity: 0.7,
      background: `radial-gradient(ellipse 30% 60% at 20% 50%, ${color} 0%, transparent 70%), radial-gradient(ellipse 35% 70% at 55% 40%, ${color} 0%, transparent 70%), radial-gradient(ellipse 28% 60% at 85% 60%, ${color} 0%, transparent 70%)`,
      animation: `pmMist ${dur}s ease-in-out infinite alternate`,
    })

  switch (realm.id) {
    case 'ashenmaw':
      mist('m1', 'rgba(255,90,31,0.22)', '55%', 14)
      for (let i = 0; i < 38; i++) {
        layer(`e${i}`, {
          left: `${rnd(i, 1) * 100}%`,
          bottom: -10,
          width: 2 + rnd(i, 2) * 4,
          height: 2 + rnd(i, 2) * 4,
          borderRadius: '50%',
          background: '#ffb057',
          boxShadow: '0 0 8px 2px rgba(255,120,40,.8)',
          ['--dx' as string]: `${(rnd(i, 3) - 0.5) * 220}px`,
          animation: `pmRise ${9 + rnd(i, 4) * 9}s linear ${-rnd(i, 5) * 18}s infinite`,
        })
      }
      break
    case 'rimehold':
      for (let i = 0; i < 70; i++) {
        layer(`s${i}`, {
          left: `${rnd(i, 1) * 100}%`,
          top: -10,
          width: 2 + rnd(i, 2) * 4,
          height: 2 + rnd(i, 2) * 4,
          borderRadius: '50%',
          background: 'rgba(255,255,255,.95)',
          boxShadow: '0 0 6px rgba(255,255,255,.7)',
          ['--dx' as string]: `${(rnd(i, 3) - 0.5) * 160}px`,
          animation: `pmFall ${12 + rnd(i, 4) * 14}s linear ${-rnd(i, 5) * 26}s infinite`,
        })
      }
      layer('aur', {
        inset: 0,
        background: 'linear-gradient(180deg, rgba(120,200,180,.18) 0%, rgba(160,120,220,.14) 30%, transparent 55%)',
        animation: 'pmGlow 9s ease-in-out infinite',
      })
      break
    case 'silverwood':
      for (let i = 0; i < 34; i++) {
        layer(`f${i}`, {
          left: `${rnd(i, 1) * 100}%`,
          top: `${rnd(i, 2) * 100}%`,
          width: 3,
          height: 3,
          borderRadius: '50%',
          background: '#eaf5d8',
          boxShadow: '0 0 10px 3px rgba(200,240,190,.7)',
          ['--dx' as string]: `${(rnd(i, 3) - 0.5) * 90}px`,
          ['--dy' as string]: `${(rnd(i, 4) - 0.5) * 70}px`,
          animation: `pmFloat ${7 + rnd(i, 5) * 8}s ease-in-out ${-rnd(i, 6) * 12}s infinite`,
        })
      }
      for (let i = 0; i < 5; i++) {
        layer(`r${i}`, {
          top: '-20%',
          left: `${10 + i * 18}%`,
          width: 90,
          height: '140%',
          background: 'linear-gradient(180deg, rgba(220,245,230,.35), transparent 70%)',
          filter: 'blur(10px)',
          animation: `pmRay ${8 + i * 1.7}s ease-in-out ${-i * 2}s infinite`,
        })
      }
      mist('m1', 'rgba(180,230,210,0.2)', '58%', 18)
      break
    case 'deephold':
      layer('forge', {
        inset: 0,
        background: 'radial-gradient(ellipse 60% 50% at 50% 100%, rgba(214,138,58,.45) 0%, transparent 65%)',
        animation: 'pmGlow 3.4s ease-in-out infinite',
      })
      for (let i = 0; i < 22; i++) {
        layer(`e${i}`, {
          left: `${25 + rnd(i, 1) * 50}%`,
          bottom: -10,
          width: 3,
          height: 3,
          borderRadius: '50%',
          background: '#ffcf7a',
          boxShadow: '0 0 6px 1px rgba(255,170,60,.9)',
          ['--dx' as string]: `${(rnd(i, 3) - 0.5) * 160}px`,
          animation: `pmRise ${7 + rnd(i, 4) * 7}s linear ${-rnd(i, 5) * 14}s infinite`,
        })
      }
      mist('m1', 'rgba(120,90,60,0.25)', '60%', 22)
      break
    case 'kingsfall':
      mist('m1', 'rgba(210,215,225,0.35)', '50%', 16)
      mist('m2', 'rgba(190,200,215,0.28)', '30%', 24)
      for (let i = 0; i < 18; i++) {
        layer(`d${i}`, {
          left: `${rnd(i, 1) * 100}%`,
          top: `${rnd(i, 2) * 100}%`,
          width: 2,
          height: 2,
          borderRadius: '50%',
          background: 'rgba(230,230,240,.8)',
          ['--dx' as string]: `${(rnd(i, 3) - 0.5) * 120}px`,
          ['--dy' as string]: `${(rnd(i, 4) - 0.5) * 80}px`,
          animation: `pmFloat ${12 + rnd(i, 5) * 10}s ease-in-out ${-rnd(i, 6) * 15}s infinite`,
        })
      }
      break
    default:
      // Greenhollow: pollen in the afternoon light, and the light itself.
      for (let i = 0; i < 26; i++) {
        layer(`p${i}`, {
          left: `${rnd(i, 1) * 100}%`,
          top: `${20 + rnd(i, 2) * 70}%`,
          width: 3 + rnd(i, 7) * 3,
          height: 3 + rnd(i, 7) * 3,
          borderRadius: '50%',
          background: 'rgba(255,240,180,.9)',
          boxShadow: '0 0 8px 2px rgba(255,220,120,.6)',
          ['--dx' as string]: `${(rnd(i, 3) - 0.5) * 100}px`,
          ['--dy' as string]: `${(rnd(i, 4) - 0.5) * 60}px`,
          animation: `pmFloat ${9 + rnd(i, 5) * 8}s ease-in-out ${-rnd(i, 6) * 14}s infinite`,
        })
      }
      for (let i = 0; i < 4; i++) {
        layer(`r${i}`, {
          top: '-20%',
          left: `${55 + i * 11}%`,
          width: 120,
          height: '140%',
          background: 'linear-gradient(180deg, rgba(255,230,160,.32), transparent 65%)',
          filter: 'blur(14px)',
          animation: `pmRay ${10 + i * 2}s ease-in-out ${-i * 3}s infinite`,
        })
      }
      mist('m1', 'rgba(255,235,190,0.18)', '62%', 20)
  }

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden motion-reduce:hidden" aria-hidden>
      {layers.map(({ key, style }) => (
        <div key={key} style={{ position: 'absolute', ...style }} />
      ))}
    </div>
  )
}
