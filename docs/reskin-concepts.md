# Three themed looks for TapDemo

## Pick Garden Pop first

**Garden Pop is my pick:** clear a lush garden one hit at a time. Grass becomes cut turf, then bare soil, then an open path. There are no numbers on the bricks.

**Cost:** 5-7 engineering days + 4 art days, including font support and short hit effects. Accept starts concept 1. Request a named change first to choose Crystal Quarry or Sunken Reef.

[Figure: comparison]

## Our screen now

[Figure: before-aim]

These are the same genuine aiming, instructions and six-icon captures used in the first round. The game has not changed. The six-icon close-up is a staged debug fixture, not a normal level. The goal stays hidden in the aiming view.

Yotam wrote: "the concepts are not interesting enough and juicy enough" and "i look into having a theme like gardening or something". I read this as a stronger thematic concept round, with gardening and mining explored directly and a reef as a third option. These replace the first three looks.

Every mockup keeps our grid, hit points, fog boundary, cavity, ball and aim positions, header and card bounds. The ball counter still reads 3 and the level reads 10. Only ordinary-brick numbers disappear: the amount of material now shows remaining strength.

## 1. Garden Pop

**Reference: Toon Blast, Peak Games.** Its chunky volumes, bright edges and immediately readable silhouettes make a crowded board feel tactile. We carry that toy-like depth into grass and earth, rather than simply colouring square blocks. This is a visual reading of a successful game, not proof that art causes its sales.

[Figure: ref-toon]

Dense grass takes three hits. The next state has visibly cut, sparse grass. Bare earth takes one final hit, then clears into the dark playing space. The soil is still a brick until that last hit; this adds no extra hit and changes no collision rule. The card teaches the progression with three pictures.

[Figure: garden-board]

The garden has painted turf, warm wooden power holders, a glossy red ball and pale dewy mist above dark soil. The existing Bomb, lightning, Ping, Ghost and speed silhouettes remain distinct. A bright stepped mist rim still follows the captured hidden-cell edge.

**One small twist - Ladybird flag:** one tiny ladybird rests on the goal pennant's tip.

[Figure: garden-damage]

**Proposed juice:** a brief turf squash, bright contact flash and outward clipping burst, then a clean damaged tile. Aim for a 180-220 ms visual settle; do not move the grid or delay the next shot. The study shows contact, burst and settle as stills, not a working animation.

**Build cost:** 5-7 engineering days + 4 art days. Three damage sprites, themed UI/power assets, a short clipping/flash sequence, licensed font support and visual hit-event integration. No live rounded rectangles, gradient shader or particle renderer is required.

## 2. Crystal Quarry

**Reference: Royal Match, Dream Games.** Rich colour depth, enamel highlights and gold framing give pieces weight. Facets and clean outlines stay legible through the spectacle of boosters. We adapt that material language into ore and brass mining equipment.

[Figure: ref-royal]

An ore block becomes a wide glowing fissure, then loose golden sand, then clears. The large fissure is the important two-hit cue; it must not depend on subtle hairline cracks. The camera, square hit areas and existing power meanings stay the same.

[Figure: quarry-board]

Pale mineral mist contrasts with the dark cave; its diamond motes and stepped rim make hidden cells distinct. Brass holders keep the power glyphs above the detailed ore. The smooth ruby ball and white aim dots remain the focus.

**One small twist - Star rivet:** the goal flag has one small star-shaped brass rivet.

[Figure: quarry-damage]

**Proposed juice:** a tight white-gold flash, a short rock squash and a fan of blue/amber chips, settling into the next damage state. Use the same 180-220 ms target as the garden. These are static effect studies.

**Build cost:** 5-7 engineering days + 4 art days. Three ore/sand states, brass UI and power sprites, chip/flash frames, shared font support and cosmetic event integration. Facets and glows are baked artwork; no lighting or dynamic-gradient feature is needed.

## 3. Sunken Reef

**Reference: Candy Crush Saga, King.** Glossy rounded forms, saturated colours and bright specular highlights make pieces feel touchable. Strong silhouettes support fast scanning. We use that candy-like finish for coral, pearls and shells, with an underwater theme.

[Figure: ref-candy]

Full pink coral becomes a few broken pale stubs, then a sand bed, then clears. Coral coverage carries the strength cue, not colour alone. Shell power holders use the existing icons; the header and instructions use the same pearl material language.

[Figure: reef-board]

Very pale aqua haze stays lighter than the deep teal seabed. Wave highlights and the stepped edge distinguish it from an empty cavity. The red pearl ball remains at the captured location, with the same dotted aim.

**One small twist - Bubble crown:** three tiny bubbles sit above the goal flag's tip.

[Figure: reef-damage]

**Proposed juice:** a short coral squash, pearly flash and bubble/coral-fleck pop, then the damaged state. Keep the effect local and let aiming remain readable. The 180-220 ms target is a proposal; motion has not been tested.

**Build cost:** 6-8 engineering days + 5 art days. Three coral/sand sprites, shell UI and power assets, bubble-pop frames, shared font support and hit-event integration. Pearl edges take more art and BMP-edge prototyping; no water shader, blur or physics change is proposed.

## What the charts establish

| Reference | Grossing | Free |
| --- | ---: | ---: |
| Toon Blast | 10 | 77 |
| Royal Match | 3 | 31 |
| Candy Crush Saga | 5 | 56 |

The official Apple **US iPhone Games (6014)** snapshots were fetched **6 October 2026**: grossing updated 11:51 UTC, free 11:54 UTC. All three references were grossing top ten. The free chart also included Meowdoku first, Block Out second and Block Blast twelfth; Block Out was eighteenth grossing. Rank is evidence of commercial traction, not an art-only sales explanation.

Sources: [Apple grossing games feed](https://itunes.apple.com/us/rss/topgrossingapplications/limit=100/genre=6014/json) and [Apple free games feed](https://itunes.apple.com/us/rss/topfreeapplications/limit=100/genre=6014/json). Complete dated snapshots and publisher screenshot URLs are stored beside the figures. Same-day evidence is reused for this revision.

## Engine fit and implementation scope

Today the renderer draws rectangles, circles, the debug font and whole BMP sprites. It has no real font, texture-cropping, nine-slice, rounded-rectangle or gradient API. These original painted sprite atlases were generated with imagegen and composed onto geometry recovered from the actual captures. They are concept assets, not imported game assets.

Use separate BMPs for the three damage states and each short effect frame. Select the tile from its existing hit points; only its artwork changes. Add a cosmetic hit-event lifetime to choose squash/flash/burst frames without affecting deterministic gameplay. Prebake corners, lighting and glows. No general particle system or shader feature is needed.

The smooth UI needs **licensed real-font support**, budgeted at 1-2 engineering days within each estimate. Prototype BMP transparency and edge handling before committing to these cutouts: the current API does not promise tint or alpha modulation. An opaque rim/background fallback is possible. Typography here is a system-font stand-in.

| Look | Engineering | Art |
| --- | --- | --- |
| Garden Pop | 5-7 days | 4 days |
| Crystal Quarry | 5-7 days | 4 days |
| Sunken Reef | 6-8 days | 5 days |

[Figure: costs]

These are judgement estimates for one theme, including damage states, simple cosmetic effects, assets, font integration and smoke/phone review. They exclude three simultaneous skins, localisation, delivery wait and device optimisation. Art and engineering may overlap; the bars are not a calendar schedule.

**Accepting concept 1 approves:** Garden Pop's three damage states without ordinary-brick numbers, garden UI/power/fog/ball assets, the ladybird flag, a short local hit/pop sequence and licensed font support. Keep grid, hit points, collision areas, rules, physics, touch, header/card bounds and hidden-goal logic. Validate ordinary play, all five powers, goal discovery, both zoom limits and the card; check that damage is readable without numbers and effects do not hide the aim.

## Fog and review

The palette rule needs a linear-sRGB fog-minus-cavity gap of at least **0.08**, plus a shape cue. Using the darkest pixel of each normalized fog texture against the brightest cavity pixel is conservative:

| Look | Minimum gap | Shape cue |
| --- | ---: | --- |
| Garden Pop | 0.369 | Dew + stepped rim |
| Crystal Quarry | 0.367 | Diamonds + stepped rim |
| Sunken Reef | 0.479 | Waves + stepped rim |

RGB extrema and the method are in `theme-fog-contrast.json`; these are artwork calculations, not device measurements. Nine full PNGs are 1170 x 2532, reviewed at 390 x 844. Board figures crop only blank fog margins. Damage studies and every PDF page are inspected at phone width.

**Open:** choose a theme. Animation, damage cues in motion, BMP alpha and iPhone performance need a playable check. No game, engine or palette code changes.
