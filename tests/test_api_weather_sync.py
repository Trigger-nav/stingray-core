"""Weather-sync tests (ticket B1 design 6, plan addition #2):
- conditional-GET/If-Modified-Since behaviour against a real (if minimal)
  HTTP server, not a mock of urllib itself -- exercises the real request
  path.
- the hot-swap's off-event-loop requirement: a slow old-pool
  `shutdown(wait=True)` must not stall the event loop or delay publishing
  the new pool.

No pytest-asyncio dependency -- async behaviour is driven with a plain
`asyncio.run()` inside an ordinary `def test_...():`, matching this
project's "no new dependency without a real need" bias.
"""

from __future__ import annotations

import asyncio
import http.server
import shutil
import threading
import time
from email.utils import formatdate
from urllib.parse import urlsplit

import pytest

from api.config import Settings
from api.state import AppState
from api.weather_sync import sync_once_blocking


class _FakeCloudHandler(http.server.BaseHTTPRequestHandler):
    payload = b"fake npz bytes"
    last_modified = formatdate(timeval=1_700_000_000, usegmt=True)

    def do_GET(self):  # noqa: N802 -- stdlib method name
        # ticket R1: real requests carry a ?pack=... query string --
        # compare the bare path only.
        if urlsplit(self.path).path != "/v1/weather/latest.npz":
            self.send_response(404)
            self.end_headers()
            return
        if_modified_since = self.headers.get("If-Modified-Since")
        if if_modified_since == self.last_modified:
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Last-Modified", self.last_modified)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format, *args):  # noqa: A002 -- silence test output
        pass


@pytest.fixture
def fake_cloud_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeCloudHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


def test_sync_once_downloads_and_writes_atomically(tmp_path, fake_cloud_server):
    port = fake_cloud_server.server_address[1]
    npz_path = tmp_path / "weather.npz"
    config = Settings(cloud_weather_url=f"http://127.0.0.1:{port}")
    last_modified = sync_once_blocking(config, "med", str(npz_path), last_modified=None)
    assert last_modified == _FakeCloudHandler.last_modified
    assert npz_path.read_bytes() == _FakeCloudHandler.payload
    assert not (tmp_path / "weather.npz.tmp").exists()


def test_sync_once_conditional_get_is_a_no_op_when_unchanged(tmp_path, fake_cloud_server):
    port = fake_cloud_server.server_address[1]
    npz_path = tmp_path / "weather.npz"
    config = Settings(cloud_weather_url=f"http://127.0.0.1:{port}")
    first = sync_once_blocking(config, "med", str(npz_path), last_modified=None)
    npz_path.write_bytes(b"should not be overwritten")
    second = sync_once_blocking(config, "med", str(npz_path), last_modified=first)
    assert second is None
    assert npz_path.read_bytes() == b"should not be overwritten"


def test_sync_once_returns_none_and_does_not_raise_when_unreachable(tmp_path):
    config = Settings(cloud_weather_url="http://127.0.0.1:1")  # nothing listens here
    npz_path = tmp_path / "weather.npz"
    result = sync_once_blocking(config, "med", str(npz_path), last_modified=None)
    assert result is None
    assert not npz_path.exists()


class _SlowShutdownExecutorStub:
    """Stands in for the "old" `ProcessPoolExecutor` during a hot-swap --
    a `.shutdown(wait=True)` that takes real wall-clock time, so the test
    can prove nothing else stalls while it runs."""

    def __init__(self, delay_s: float):
        self.delay_s = delay_s
        self.shutdown_finished_at: float | None = None

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        time.sleep(self.delay_s)
        self.shutdown_finished_at = time.time()


def test_hot_swap_does_not_block_the_event_loop_during_old_pool_shutdown(tmp_path):
    async def run() -> None:
        npz_path = tmp_path / "weather.npz"
        shutil.copy("data/weather/ecmwf_western_med.npz", npz_path)
        config = Settings(weather_npz_path=str(npz_path), pool_size=1)
        state = AppState(config)
        try:
            slow_stub = _SlowShutdownExecutorStub(delay_s=1.5)
            state.executor_holder.executor = slow_stub
            # force "changed" regardless of real mtime
            state._last_weather_mtimes = dict.fromkeys(state.region_packs, -1.0)

            t0 = time.time()
            swap_task = asyncio.create_task(state._check_and_swap())

            quick_finished_at: list[float] = []

            async def quick() -> None:
                await asyncio.sleep(0.05)
                quick_finished_at.append(time.time())

            quick_task = asyncio.create_task(quick())

            await swap_task
            swap_returned_at = time.time()
            await quick_task

            # both the quick coroutine and _check_and_swap itself return
            # promptly -- neither waits for the slow old-pool shutdown.
            assert quick_finished_at[0] - t0 < 0.5
            assert swap_returned_at - t0 < 0.5
            # the new pool is already published by the time _check_and_swap
            # returns -- new submissions have somewhere to go immediately.
            assert state.executor_holder.executor is not slow_stub

            # the slow shutdown does eventually complete, just in the
            # background, off the event loop.
            await asyncio.sleep(2.0)
            assert slow_stub.shutdown_finished_at is not None
        finally:
            state.shutdown()

    asyncio.run(run())
