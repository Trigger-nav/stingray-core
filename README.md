# Stingray

Passage & performance optimisation core for superyachts. See `PRODUCT_SPEC.md`,
`TECHNICAL_ARCHITECTURE.md`, and `ROADMAP.md` at the repo root for product and
architecture context.

## Layout

- `core/` — twin, optimiser, weather: pure library code, no I/O side effects.
- `ingest/` — data acquisition (GRIB pulls, chart/bathymetry fetch), thin CLIs on top of `core`.
- `tests/` — pytest suite.
- `prototype/` — deployed HTML demo (separate repo); reference for the twin/optimiser
  structure being ported into `core/`, not production code.

## Development

Requires Python 3.11+.

```
pip install -e ".[dev]"
ruff check .
pytest
```
