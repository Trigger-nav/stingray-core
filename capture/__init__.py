"""Logging service (ticket B1/B5) -- NMEA 2000 bus ingestion, PGN decode,
and local telemetry storage. Runs as an independent, long-running process
from the planner service (api/), sharing only the local SQLite store
(design 8) -- a planner-service restart never interrupts logging, and
vice versa. See docs/plans/ticket-B1.md.
"""
