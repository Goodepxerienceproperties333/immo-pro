"""Pytest conftest - resolves "Event loop is closed" flakiness.

PROBLEM
-------
Many tests use `asyncio.run(_run_all())`. Each call to `asyncio.run` creates
a fresh event loop, runs the coroutine, then closes it. But the global
Motor client (`db`) imported from `server.py` is bound to the FIRST event
loop on its first await call. Once that loop is closed, every subsequent
`asyncio.run` from another test file raises `RuntimeError: Event loop is
closed` from inside Motor's PyMongo bridge.

SOLUTION
--------
At session start :
  1. Create ONE persistent event loop that lives for the whole pytest session.
  2. Monkey-patch `asyncio.run(coro)` so it dispatches to this persistent
     loop via `loop.run_until_complete(coro)` instead of creating a new loop.
Result : Motor stays bound to the same loop for the whole session and no
test triggers the "loop is closed" branch in PyMongo's async wrapper.

This is non-invasive : zero changes required in test files, and the rest
of production code is untouched.
"""
import asyncio
import pytest


_persistent_loop: asyncio.AbstractEventLoop | None = None
_original_asyncio_run = asyncio.run


def _patched_asyncio_run(coro, *, debug=None):
    """Drop-in replacement for asyncio.run that reuses the session loop.

    The persistent loop is set up by the session-scoped fixture below.
    If pytest is not driving the test (e.g., direct script execution), we
    fall back to the original asyncio.run.
    """
    global _persistent_loop
    if _persistent_loop is None or _persistent_loop.is_closed():
        return _original_asyncio_run(coro, debug=debug) if debug is not None else _original_asyncio_run(coro)
    # asyncio.run forbids being called from inside a running loop. Mirror
    # that contract for safety.
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")
    return _persistent_loop.run_until_complete(coro)


@pytest.fixture(scope="session", autouse=True)
def _session_event_loop():
    """Create one persistent event loop for the whole test session and
    monkey-patch asyncio.run to dispatch onto it."""
    global _persistent_loop
    _persistent_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_persistent_loop)
    asyncio.run = _patched_asyncio_run  # type: ignore[assignment]
    yield _persistent_loop
    # Teardown : restore original and close.
    asyncio.run = _original_asyncio_run  # type: ignore[assignment]
    try:
        # Cancel any leftover tasks so close() doesn't warn.
        pending = asyncio.all_tasks(_persistent_loop)
        for t in pending:
            t.cancel()
        if pending:
            _persistent_loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
    except Exception:
        pass
    finally:
        _persistent_loop.close()
        _persistent_loop = None
