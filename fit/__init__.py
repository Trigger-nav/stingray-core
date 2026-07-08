"""Twin v1 offline fit/validate tooling (ticket 0.6).

Fits `core.twin`'s calm-water resistance, added-resistance, and SFOC
components from telemetry-shaped data. Depends on `core/` one-way (same
direction `ingest/` already depends on `core/`) plus `scipy` for real
nonlinear least-squares -- `core/` stays numpy+PyYAML only, so this
lives here rather than there. See docs/plans/ticket-0.6.md for the full
design and CLAUDE.md's gotchas for the priors' provisional status.
"""
