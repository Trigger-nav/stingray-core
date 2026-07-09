"""Single entry point for the PyInstaller-built `stingray` binary (ticket
B1 design 11): `stingray planner` / `stingray capture` -- two subcommands,
one binary, so the same `pyinstaller.spec` (one `Analysis` block) serves
both OS services design 8 calls for, on all three target platforms
(Windows/macOS bridge PC, Linux cloud VM -- same artefact, contract
point 2).

Not itself imported by anything under `api/`/`capture/`/`core/` --
this is packaging glue, kept out of those packages so `python3 -m
api.main`/`python3 -m capture.service` (the normal, non-packaged dev
invocation) keep working unchanged.
"""

from __future__ import annotations

import multiprocessing
import sys


def _run_planner() -> None:
    import uvicorn

    from api.config import Settings
    from api.main import app

    # Pass the app *object*, not the "api.main:app" string form -- found
    # empirically during a real PyInstaller trial build: uvicorn's
    # string-based app loading is itself a dynamic import PyInstaller's
    # static analysis can't see (same class of gap as nmea2000's
    # decoder_formats/encoder_formats, deploy/pyinstaller.spec), and
    # fails at runtime with "Could not import module" inside the frozen
    # binary even though the exact same string works fine in a normal
    # `python3 -m` dev invocation.
    config = Settings.from_env()
    uvicorn.run(app, host=config.host, port=config.port)


def _run_capture() -> None:
    from capture.service import main as capture_main

    # capture.service.main() parses its own argv -- strip the "capture"
    # subcommand word before handing off.
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    capture_main()


def _load_env_file() -> None:
    """Loads a `.env` file (default: current working directory, which
    every service-wrapper config in deploy/ sets to the install dir) via
    python-dotenv -- already an installed transitive dependency of
    `uvicorn[standard]`, declared directly in pyproject.toml's `api`
    extras now that this module relies on it, not just hoped for.
    Deliberately the *one* mechanism used uniformly across all three
    service wrappers (systemd's `EnvironmentFile=` also works natively
    and is left in place as belt-and-braces on Linux, but launchd and
    NSSM have no equivalent native primitive -- loading `.env` here
    means the same config file format works identically on all three,
    rather than three different environment-injection mechanisms)."""
    from dotenv import load_dotenv

    load_dotenv()


def main() -> None:
    _load_env_file()
    if len(sys.argv) < 2 or sys.argv[1] not in ("planner", "capture"):
        print("usage: stingray {planner|capture} [options]", file=sys.stderr)
        raise SystemExit(2)
    if sys.argv[1] == "planner":
        _run_planner()
    else:
        _run_capture()


if __name__ == "__main__":
    # multiprocessing.freeze_support() must be the very first thing that
    # runs, before argv dispatch below -- found empirically during a real
    # PyInstaller trial build: the planner subcommand's ProcessPoolExecutor
    # workers have no separate `python.exe` to re-exec, so they re-launch
    # *this same frozen binary*; without freeze_support() intercepting
    # that re-launch first, each worker hits the "usage: stingray
    # {planner|capture}" branch below and exits immediately, and the pool
    # comes up BrokenProcessPool. freeze_support() detects the
    # multiprocessing-worker re-invocation (a special sys.argv marker) and
    # runs the worker bootstrap instead of returning here at all; for a
    # normal `stingray planner`/`stingray capture` invocation it's a no-op.
    multiprocessing.freeze_support()
    main()
