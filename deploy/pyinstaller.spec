# PyInstaller spec (ticket B1 design 11) -- one `Analysis` block building
# a single `stingray` binary with `planner`/`capture` subcommands
# (deploy/stingray_cli.py). Build separately per target OS (PyInstaller
# cannot cross-compile) -- see deploy/README.md and
# deploy/build-installers.yml for the 3-OS CI matrix.
#
# `hiddenimports` below were found empirically during a real trial build
# on this machine (macOS/arm64), not guessed in advance -- PyInstaller's
# static import analysis misses uvicorn's/fastapi's/pydantic's dynamic
# imports, matching the sharp edge the implementation plan flagged as
# "budget a real trial build early." Re-run PyInstaller with `--log-level
# DEBUG` and watch for `ModuleNotFoundError` at runtime (not build time --
# PyInstaller doesn't always fail loudly) if this list needs updating
# after a core/api/capture dependency changes.

import sys
from pathlib import Path

block_cipher = None
repo_root = Path(SPECPATH).parent

hiddenimports = [
    # uvicorn's own protocol/loop implementations are selected dynamically
    # at runtime (not static imports PyInstaller's analysis can see).
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # pydantic-core is a compiled extension pulled in dynamically by
    # pydantic's own import machinery.
    "pydantic.deprecated.decorator",
    "pydantic_core",
    # capture/'s NMEA 2000 decode dependency: `nmea2000.decoder`/`.encoder`
    # load these two submodules via `importlib.import_module(...)`, not a
    # static `import` -- PyInstaller's analysis can't see that, and the
    # build succeeds silently; it only fails at *runtime*
    # (ModuleNotFoundError) the first time a decode/encode is attempted.
    # Found exactly this way during a real trial build, not guessed.
    "nmea2000.decoder_formats",
    "nmea2000.encoder_formats",
    # python-can's backend plugin discovery (a transitive nmea2000
    # dependency) is similarly dynamic.
    "can.interfaces.socketcan",
]

a = Analysis(
    [str(repo_root / "deploy" / "stingray_cli.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=[
        # RealGeography/VesselSpec defaults -- the planner subcommand
        # loads these by relative path at startup (api/config.py).
        (str(repo_root / "data"), "data"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # ingest's cfgrib/eccodes dependency is deliberately never
        # imported by the vessel-role code path (api/config.py's
        # STINGRAY_ROLE=vessel doc comment) -- excluded here so a
        # Windows/macOS build never even attempts to bundle it.
        "cfgrib",
        "xarray",
        "h5netcdf",
        "h5py",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="stingray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
