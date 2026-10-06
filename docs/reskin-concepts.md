# Three looks for TapDemo

## Pick Toy Workshop first

**Toy Workshop is my pick:** friendly toy bricks, a clear aiming space, and one small twist: stitches on the goal flag.

**Cost:** 3-4 engineering days + 2 art days, including a proper font. Accept starts concept 1. Request a named change first to choose Jewel Vault or Sugar Mist.

[Figure: comparison]

## Our screen now

[Figure: before-aim]

Three real captures: aiming, instructions and the existing close-up power fixture. The fixture stages all powers and the goal beside the pocket; it is not the normal level layout.

Every concept keeps the captured grid, hit points, fog boundary, cavity, ball, aim dots and header. The goal stays hidden while aiming. The card still says three balls, 15 bounces each, and Tap to Start. Full phone PNGs accompany the PDF; board enlargements omit only empty fog margins.

## 1. Toy Workshop

**Reference: Toon Blast, Peak Games.** Its chunky coloured cubes, shallow highlights and soft corners are readable even on a crowded board. Those simple shapes make a burst feel satisfying. That is our visual reading of a successful game, not evidence that art alone causes sales.

[Figure: ref-toon]

Our version uses green, yellow and pink toy blocks for **1, 2 and 3 hits**, with the number always visible. Dark icon bricks keep Bomb, lightning, Ping, Ghost, speed chevrons and the goal flag distinct from ordinary bricks. The red ball and bright aim dots remain the focus.

**One small twist - Stitched flag:** four tiny stitches along the goal flag's top edge. Only the flag artwork changes.

[Figure: toy-board]

Mint fog stays light against the deep green cavity. Short flecks and a bright stepped edge make it read as hidden territory, including in greyscale. The cream card uses dark text and clear icon badges.

**Build cost:** 3-4 engineering days + 2 art days. Bake tile corners, shallow highlights, fog flecks, flag stitches and icon rims into BMP sprites. Add real font rendering for the smooth labels shown here. No live rounded-rectangle or gradient API is needed.

## 2. Jewel Vault

**Reference: Royal Match, Dream Games.** Saturated enamel pieces, bright edge highlights and gold framing give small tiles weight. Distinct piece silhouettes and clean separation keep the board readable while boosters provide visual excitement. This is an art rationale, not a revenue attribution.

[Figure: ref-royal]

Our bricks keep **blue 1, gold 2 and purple 3**, now set in thin gold rims with corner facets. The play cavity is deep blue; pale blue fog has tiny diamond flecks. The ball, aim dots, header and cream instructions card stay in their captured positions.

**One small twist - Heartbeat flag:** the goal's halo gives two gentle pulses together. The nested rings show the pulse's bright moment in this static mockup.

[Figure: royal-board]

The goal retains its flag silhouette. Every power has its own coloured rim and existing icon; numbers stay off icon bricks. The stepped fog edge remains visible independently of colour.

**Build cost:** 4-6 engineering days + 3 art days. Bake enamel gradients, facets and gold rims into BMPs; use the same font feature as concept 1. Animate two halo frames with the existing visual clock and sprite selection. No shader or dynamic gradient feature is required.

## 3. Sugar Mist

**Reference: Candy Crush Saga, King.** Glossy highlights and rounded candy shapes make pieces feel touchable. Strong colour and silhouette differences support fast scanning, while bright booster effects suggest reward. These qualities plausibly support its appeal; the chart does not establish their causal impact.

[Figure: ref-candy]

Our **1-hit blue pillows, 2-hit gold capsules and 3-hit pink pillows** retain their numbers and cell bounds. We keep rectangular gameplay hit areas. Raspberry labels, a pale pink header and a milk-coloured card carry the same material language into the UI.

**One small twist - Sugar etch:** tiny paired diagonal scratches in the fog, like etched sugar glass. This is only a fog texture detail.

[Figure: sugar-board]

Lilac fog stays far lighter than the plum cavity; its scratches and stepped edge provide the shape cue. Dark power tiles keep the icons and glow legible against the candy colours. The red ball and white dotted aim remain unchanged in position.

**Build cost:** 4-5 engineering days + 3 art days. Bake candy shapes, gloss and fog scratches into BMP sprites. Use the shared real font feature. No transparency-heavy glass shader, blur or procedural rounded rectangles are needed.

## What the charts establish

| Reference | Grossing | Free |
| --- | ---: | ---: |
| Toon Blast | 10 | 77 |
| Royal Match | 3 | 31 |
| Candy Crush Saga | 5 | 56 |

Research fetched **6 October 2026** from Apple's **US iPhone Games (6014)** feeds. Grossing updated at **11:51 UTC**; free updated at **11:54 UTC**. These are dated snapshots, not permanent ranks.

All three references are in today's grossing top ten. We also examined the free chart: Meowdoku was first, Block Out second, and Block Blast twelfth. Block Out was eighteenth grossing. We favoured Toon Blast's established block art for the closest brick treatment, and the two match games for distinct enamel and candy directions.

Sources: [Apple US grossing games feed](https://itunes.apple.com/us/rss/topgrossingapplications/limit=100/genre=6014/json) and [Apple US free games feed](https://itunes.apple.com/us/rss/topfreeapplications/limit=100/genre=6014/json). The complete 100-entry snapshots and screenshot source URLs are stored beside these figures.

## Engine fit and implementation scope

Today the renderer offers rectangles, circles, a debug font and whole BMP sprites. It has no real font API, texture cropping, nine-slice panels, rounded rectangles or gradient primitives. The mockup typography is a system-font stand-in; implementation should use an appropriately licensed font.

All three looks can bake the tile materials and fixed card into BMPs. Use separate images for each hit-point tile and each icon state. Keep fog texture within hidden cells and retain the current visibility rule. Aim dots and the ball can use the existing circles or a ball sprite.

Matching the text requires a **new real-font renderer feature**, budgeted above at 1-2 engineering days within each estimate. Prototype BMP edge handling first: the current sprite API does not promise tinted or alpha-modulated drawing. The mockups use solid coloured rims, so an opaque sprite fallback can preserve the look. No new rounded-rectangle, blur or gradient API is part of the recommendation.

| Look | Engineering | Art |
| --- | --- | --- |
| Toy Workshop | 3-4 days | 2 days |
| Jewel Vault | 4-6 days | 3 days |
| Sugar Mist | 4-5 days | 3 days |

[Figure: costs]

These are judgement estimates for one skin, including font integration, asset export, smoke captures and phone-size review. They exclude multiple skins, localisation, delivery wait and device optimisation. If all three were built, font work would be shared rather than paid three times.

**Accepting concept 1 approves:** original toy tile/icon/card/fog assets, the stitched flag, licensed font support, and their renderer integration. Keep the grid, header/card bounds, rules, physics, touch behaviour and hidden-goal logic. Validate ordinary play, all five powers, goal discovery, fit and maximum zoom, and the instructions card before landing implementation.

## Fog and visual review

The palette rule requires a linear-sRGB luminance gap of at least **0.08**, plus a shape cue. The darkest fog material used in these mockups exceeds that requirement:

| Look | Fog minus cavity | Shape cue |
| --- | ---: | --- |
| Toy Workshop | 0.424 | Flecks + stepped rim |
| Jewel Vault | 0.388 | Diamonds + stepped rim |
| Sugar Mist | 0.470 | Scratches + stepped rim |

Values are calculated from the artwork's RGB colours using the formula in the current palette rule; they are not device measurements. Exact colours and calculations are recorded in `fog-contrast.json` beside the figures.

All nine full-screen concept PNGs are **1170 x 2532**. Review at **390 x 844** checks the aiming view, the card and the six-icon fixture for each look. The figures in this PDF include enlargements; the separate PNGs preserve the full phone screen. Every final PDF page is rendered and visually reviewed before delivery.

**Open:** choose a look. These images do not demonstrate animated juice, touch feel, BMP alpha support or iPhone performance. Those are implementation checks after acceptance. No game, engine or palette code changes in this concept round.
