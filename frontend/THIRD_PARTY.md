# Third-party code

Code adapted into this frontend from other projects, and the notices that come
with it.

## phase.rs

<https://github.com/phase-rs/phase> — a Magic engine and client in Rust and
TypeScript, dual licensed MIT / Apache-2.0. Used here under the MIT option.

Adapted files:

| here | there |
| --- | --- |
| `src/lib/fanGeometry.ts` | `client/src/components/card/fanGeometry.ts` |
| `src/components/ManaCost.tsx` | symbol vocabulary from `client/src/components/mana/ManaSymbol.tsx` |
| `src/components/PhaseIcons.tsx` | `PHASE_ICONS` from `client/src/components/controls/PhaseStopBar.tsx` |
| `src/lib/gameLog.ts` | timeline/divider model from `client/src/viewmodel/logFormatting.ts` |
| `src/lib/arcPath.ts` | `client/src/components/targeting/arcPath.ts` |
| `src/lib/groupPermanents.ts` | the single / staggered / collapsed-at-five rule from `client/src/components/board/groupRenderMode.ts` |
| `src/lib/phaseInfo.ts` | the five-part grouping and next-step map from `client/src/hooks/usePhaseInfo.ts` |
| `src/components/TurnBanner.tsx` | the sweep-and-punch composition of `client/src/components/animation/TurnBanner.tsx`, in CSS rather than Framer Motion |
| `src/lib/combatFx.ts` | `applyCardSlam` and `applyScreenShake` from `client/src/components/animation/` |
| `src/lib/tableFx.ts`, `src/components/TableFx.tsx` | the parabolic cast arc of `client/src/components/animation/CastArcAnimation.tsx` and the fragment model of `DeathShatter.tsx`, driven from snapshot diffs and requestAnimationFrame |

None is a verbatim copy. `fanGeometry.ts` drops their `compact` profile, which
existed for a mobile hand the vault does not have. `ManaCost.tsx` takes only the
symbol tables — which codes exist and how the composite ones are spelled — and
draws the pips in CSS rather than fetching Scryfall's SVGs, since every other
image in the vault is proxied and cached locally and a LAN instance should not
need the internet to show a mana cost. `PhaseIcons.tsx` keeps the glyphs and
rekeys them from their phase names to Forge's `PhaseType`, which splits combat
damage into a first-strike step. `gameLog.ts` takes the timeline
algorithm -- boundaries folding into dividers, a heading never drawn without
content under it -- and none of their categories, because Forge's
`GameLogEntryType` already supplies those. `arcPath.ts` is the curve the
combat and stack lines are drawn with, kept whole including its handling of
coincident endpoints. `groupPermanents.ts` keeps their thresholds and the rule
that an attacker stays its own card while blockers are declared; what counts as
"identical" is ours, from the fields Forge sends.

Their engine is not used. The vault plays through Forge; see
`docker/forge-bridge/README.md` for how, and DECISIONS.md ADR-031 for why the
rules stay in an external engine and phase.rs was measured rather than adopted.

### MIT License

Copyright (c) 2024-2026 phase.rs contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
