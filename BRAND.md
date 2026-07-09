# Stingray Marine Technology — brand quick reference

Source assets: `~/Downloads/Stingray_LogoSuite/` (CMYK/RGB/Pantone, EPS/SVG/PNG/JPEG, light & dark variants). One cleaned SVG (Adobe payload stripped) lives in-repo at `prototype/assets/stingray_logo.svg` (on-dark gradient variant).

## Palette (extracted from RGB vector masters)

| Role | Hex | Notes |
|---|---|---|
| Deep navy | `#0E1138` | Primary dark; wordmark on light backgrounds (flat), brand background |
| Teal | `#00A19A` | Primary accent; flat single-colour teal |
| Gradient start | `#53B2B9` | Radial gradient centre (mark + wordmark, grad variants) |
| Gradient end | `#0C807E` | Radial gradient edge |
| On dark | `#FFFFFF` + gradient | Arc device white, wordmark teal-gradient |

## Usage in the demo (`prototype/stingray_planner.html`)

- Header: `assets/stingray_logo.svg` + "MARINE TECHNOLOGY" letterspaced in dim text.
- CSS variables mapped to brand: `--acc: #00A19A` (was cyan), backgrounds shifted to navy family around `#0E1138`.
- Chart (ECDIS-style, light) keeps navigation-conventional colours — brand teal only appears as the selected-route colour.

Wix site (stingraymarinetechnology.com) is the other live surface — keep tagline wording aligned: "Passage & Performance Optimisation through AI Innovation".
