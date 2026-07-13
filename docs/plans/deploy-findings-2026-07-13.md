# First real cloud deploy — findings (Hetzner CPX21, 2026-07-13)

Live deploy completed successfully: api.stingraymarinetechnology.com serving
over Caddy TLS, cron installed, demo at trigger-nav.github.io/stingray-demo
running end-to-end against it. Checklist items **verified live** this deploy:
Linux PyInstaller build on the target box; `install.sh` incl. the data/ copy
fix; Ubuntu 24.04 `libeccodes-dev` apt route (cfgrib imports and fetches
cleanly — the ticket-0.5 gap is closed); real Let's Encrypt issuance via the
runbook's Caddy path; full end-to-end plan over public TLS.

Three defects found live, in priority order — fix before relying on cron:

1. **Runbook ordering bug (fresh-box crash-loop).** `data/weather/*.npz` is
   gitignored, so a fresh clone has no weather file; `install.sh` copies
   nothing into `/opt/stingray/data/weather/`, and the service crash-loops on
   `FileNotFoundError` (status=3) until a fetch runs — but the ingest-venv
   setup is section 6, *after* the first service start in section 5. Fix
   options (do at least the first two): reorder the runbook (ingest venv +
   one fetch before install/start); make the startup failure message explicit
   ("no weather file at <path> — run ingest.fetch_grib_* first") instead of a
   bare traceback; consider `install.sh` warning when the weather dir is empty.

2. **ECMWF cycle selection is wrong for its publication schedule.**
   `latest_available_cycle_utc(delay_h=5.0)` suits NOMADS but ECMWF open data
   publishes ~8–9h after cycle time → picked today's unpublished 12z at
   17:16 UTC and died on `HTTP 404` for the `.index`. Additionally the ECMWF
   `oper` stream only has **00z/12z** cycles — the shared 6-hourly rounding
   can select a 06z/18z that never exists for it. Fix: per-source delay and
   per-source valid-cycle set (ECMWF: {00,12}, delay ≥9h; NOMADS: {00,06,12,18},
   delay ~5h).

3. **No fallback on missing cycle → silent staleness under cron.** A 404 on
   the index should fall back to the previous valid cycle rather than
   raising — a cron job that dies leaves stale weather with nothing in the
   served payload flagging it. (Provenance already exposes the cycle, so the
   /v1/health freshness is *visible*, but the fetcher should self-heal.)
   Add a regression test with a mocked 404-then-success sequence.

Also update `deploy/README.md`'s "Manual verification checklist" to tick the
items verified above, and reflect fix #1's reordering in the runbook itself.
