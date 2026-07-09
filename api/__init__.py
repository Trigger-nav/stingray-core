"""Planner service (ticket B1) -- job-shaped FastAPI wrapper over
core.optimiser.optimise. Interactive planning only: no telemetry sync
endpoints live here, and none should be added -- vessel<->cloud telemetry
is store-and-forward messaging (TECHNICAL_ARCHITECTURE.md §7), a different
mechanism than this request/response-shaped-into-jobs API. See
docs/plans/ticket-B1.md.
"""
