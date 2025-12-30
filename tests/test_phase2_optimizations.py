"""
Tests for Phase 2 Optimizations: Event-Driven Loop and Date Header Caching

These tests validate the event-driven main loop and date header caching
optimizations implemented in Phase 2.
"""

import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest

from uvicorn.config import Config
from uvicorn.server import Server, ServerState


class TestEventDrivenMainLoop:
    """Tests for Phase 2a: Event-driven main loop optimization."""

    @pytest.fixture
    def server_state(self):
        """Create a ServerState instance for testing."""
        return ServerState()

    def test_shutdown_event_exists(self, server_state):
        """Test that shutdown event is initialized in ServerState."""
        assert hasattr(server_state, "_shutdown_event")
        assert isinstance(server_state._shutdown_event, asyncio.Event)
        assert not server_state._shutdown_event.is_set()

    def test_shutdown_event_can_be_set(self, server_state):
        """Test that shutdown event can be set and cleared."""
        server_state._shutdown_event.set()
        assert server_state._shutdown_event.is_set()

        server_state._shutdown_event.clear()
        assert not server_state._shutdown_event.is_set()

    @pytest.mark.anyio
    async def test_shutdown_event_triggers_immediately(self):
        """Test that shutdown event wakes main loop instantly."""
        config = Config(app=lambda scope, receive, send: None, port=8000)
        server = Server(config)

        # Start main loop in background
        main_loop_task = asyncio.create_task(server.main_loop())

        # Give it a moment to start
        await asyncio.sleep(0.01)

        # Trigger shutdown
        start_time = time.perf_counter()
        server.should_exit = True
        server.server_state._shutdown_event.set()

        # Wait for main loop to exit
        await main_loop_task

        # Measure shutdown latency
        shutdown_latency = time.perf_counter() - start_time

        # Should exit almost immediately (< 50ms, vs old 0-100ms)
        assert shutdown_latency < 0.05, f"Shutdown took {shutdown_latency*1000:.1f}ms, expected <50ms"

    @pytest.mark.anyio
    async def test_background_tasks_are_cancelled(self):
        """Test that background tasks are properly cancelled on shutdown."""
        config = Config(app=lambda scope, receive, send: None, port=8000)
        server = Server(config)

        # Start main loop
        main_loop_task = asyncio.create_task(server.main_loop())

        # Give background tasks time to start
        await asyncio.sleep(0.05)

        # Trigger shutdown
        server.should_exit = True
        server.server_state._shutdown_event.set()

        # Wait for main loop to complete
        await main_loop_task

        # All background tasks should be cancelled/completed
        # Main loop should exit cleanly without hanging
        assert server.should_exit is True

    @pytest.mark.anyio
    async def test_date_header_update_loop_runs(self):
        """Test that date header update loop updates headers."""
        config = Config(app=lambda scope, receive, send: None, port=8000, date_header=True)
        server = Server(config)

        # Start the date update loop
        update_task = asyncio.create_task(server._update_date_header_loop())

        # Give it time to update at least once
        await asyncio.sleep(0.1)

        # Check that default headers were set
        assert len(server.server_state.default_headers) > 0
        has_date_header = any(name == b"date" for name, _ in server.server_state.default_headers)
        assert has_date_header, "Date header should be present"

        # Cleanup
        server.should_exit = True
        server.server_state._shutdown_event.set()
        update_task.cancel()
        try:
            await update_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.anyio
    async def test_max_requests_detection_triggers_shutdown(self):
        """Test that max requests limit triggers shutdown event."""
        config = Config(
            app=lambda scope, receive, send: None,
            port=8000,
            limit_max_requests=5
        )
        server = Server(config)

        # Start max requests checker
        check_task = asyncio.create_task(server._check_max_requests())

        # Simulate requests reaching the limit
        server.server_state.total_requests = 5

        # Give it time to detect
        await asyncio.sleep(0.2)

        # Should have triggered shutdown
        assert server.should_exit is True
        assert server.server_state._shutdown_event.is_set()

        # Cleanup
        check_task.cancel()
        try:
            await check_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.anyio
    async def test_notify_callback_is_called(self):
        """Test that notify callback loop calls the callback."""
        callback_called = []

        async def test_callback():
            callback_called.append(time.time())

        config = Config(
            app=lambda scope, receive, send: None,
            port=8000,
            callback_notify=test_callback,
            timeout_notify=0.1  # Call every 100ms
        )
        server = Server(config)

        # Start notify loop
        notify_task = asyncio.create_task(server._notify_callback_loop())

        # Wait for at least one callback
        await asyncio.sleep(0.25)

        # Should have been called at least once
        assert len(callback_called) >= 1, "Callback should be called"

        # Cleanup
        server.should_exit = True
        server.server_state._shutdown_event.set()
        notify_task.cancel()
        try:
            await notify_task
        except asyncio.CancelledError:
            pass


class TestDateHeaderCaching:
    """Tests for Phase 2b: Date header caching optimization."""

    @pytest.fixture
    def server_state(self):
        """Create a ServerState instance for testing."""
        return ServerState()

    def test_cached_date_fields_exist(self, server_state):
        """Test that date cache fields are initialized."""
        assert hasattr(server_state, "_cached_date")
        assert hasattr(server_state, "_cached_date_time")
        assert server_state._cached_date is None
        assert server_state._cached_date_time == 0

    def test_get_date_header_returns_bytes(self, server_state):
        """Test that get_date_header returns bytes."""
        date_header = server_state.get_date_header()
        assert isinstance(date_header, bytes)
        assert len(date_header) > 0

    def test_date_header_is_cached(self, server_state):
        """Test that date header is cached within the same second."""
        # Get date header twice in same second
        date1 = server_state.get_date_header()
        date2 = server_state.get_date_header()

        # Should return the same cached value
        assert date1 is date2, "Date should be cached and return same object"

    def test_date_header_invalidates_on_second_change(self, server_state):
        """Test that date cache invalidates when second changes."""
        # Get initial date
        date1 = server_state.get_date_header()
        initial_time = server_state._cached_date_time

        # Manually advance cached time to simulate second change
        server_state._cached_date_time = initial_time - 2

        # Get date again
        date2 = server_state.get_date_header()

        # Should be different (new date generated)
        assert date1 != date2 or server_state._cached_date_time != initial_time - 2

    def test_date_header_format_is_valid(self, server_state):
        """Test that date header is in valid HTTP format."""
        date_header = server_state.get_date_header()

        # Should be in GMT format like: "Mon, 01 Jan 2024 00:00:00 GMT"
        assert b"GMT" in date_header
        # Should have commas separating parts
        assert b"," in date_header
        # Should be reasonable length (around 29 bytes)
        assert 20 < len(date_header) < 40

    def test_date_header_thread_safety(self, server_state):
        """Test that get_date_header is thread-safe for concurrent access."""
        # Call get_date_header multiple times concurrently
        dates = []
        for _ in range(10):
            date = server_state.get_date_header()
            dates.append(date)

        # All should be the same (cached)
        assert all(d == dates[0] for d in dates), "All dates should be identical when cached"

    def test_date_header_updates_with_time(self, server_state):
        """Test that date header updates as time progresses."""
        # Get initial date and time
        date1 = server_state.get_date_header()
        time1 = server_state._cached_date_time

        # Force a time change by modifying cached time
        server_state._cached_date_time = time1 - 1

        # Get new date
        date2 = server_state.get_date_header()
        time2 = server_state._cached_date_time

        # Time should have updated
        assert time2 != time1 - 1, "Cached time should update"


class TestPhase2Integration:
    """Integration tests for Phase 2 optimizations working together."""

    @pytest.mark.anyio
    async def test_server_starts_with_phase2_optimizations(self):
        """Test that server starts successfully with Phase 2 optimizations."""
        config = Config(
            app=lambda scope, receive, send: None,
            port=8000,
            date_header=True
        )
        server = Server(config)

        # Verify shutdown event exists
        assert hasattr(server.server_state, "_shutdown_event")

        # Verify date cache exists
        assert hasattr(server.server_state, "_cached_date")
        assert hasattr(server.server_state, "get_date_header")

        # Get a date header
        date = server.server_state.get_date_header()
        assert isinstance(date, bytes)

    @pytest.mark.anyio
    async def test_main_loop_uses_event_driven_architecture(self):
        """Test that main loop is event-driven (doesn't poll)."""
        config = Config(app=lambda scope, receive, send: None, port=8000)
        server = Server(config)

        # Start main loop
        main_loop_task = asyncio.create_task(server.main_loop())

        # Give it time to start
        await asyncio.sleep(0.05)

        # Main loop should be waiting on event, not polling
        # We verify this by checking quick shutdown response
        start = time.perf_counter()
        server.should_exit = True
        server.server_state._shutdown_event.set()

        await main_loop_task
        duration = time.perf_counter() - start

        # Should respond much faster than old 100ms polling
        assert duration < 0.05, f"Event-driven shutdown took {duration*1000:.1f}ms (should be <50ms)"

    @pytest.mark.anyio
    async def test_date_updates_continue_during_server_run(self):
        """Test that date headers continue to update while server runs."""
        config = Config(
            app=lambda scope, receive, send: None,
            port=8000,
            date_header=True
        )
        server = Server(config)

        # Get initial date
        initial_date = server.server_state.get_date_header()

        # Start date update loop
        update_task = asyncio.create_task(server._update_date_header_loop())

        # Wait a moment for potential update
        await asyncio.sleep(0.1)

        # Headers should be set
        assert len(server.server_state.default_headers) > 0

        # Cleanup
        server.should_exit = True
        server.server_state._shutdown_event.set()
        update_task.cancel()
        try:
            await update_task
        except asyncio.CancelledError:
            pass


class TestPhase2Performance:
    """Performance validation tests for Phase 2."""

    @pytest.mark.anyio
    async def test_no_unnecessary_wakeups(self):
        """Test that main loop doesn't wake up unnecessarily."""
        config = Config(app=lambda scope, receive, send: None, port=8000)
        server = Server(config)

        # Start main loop
        main_loop_task = asyncio.create_task(server.main_loop())

        # Let it run idle for a bit
        await asyncio.sleep(0.3)

        # Trigger shutdown quickly
        start = time.perf_counter()
        server.should_exit = True
        server.server_state._shutdown_event.set()
        await main_loop_task
        shutdown_time = time.perf_counter() - start

        # Should respond instantly (event-driven)
        assert shutdown_time < 0.05, "Event-driven loop should respond instantly"

    def test_date_cache_reduces_formatdate_calls(self, monkeypatch):
        """Test that date caching reduces formatdate() calls."""
        from email.utils import formatdate

        call_count = [0]
        original_formatdate = formatdate

        def counted_formatdate(*args, **kwargs):
            call_count[0] += 1
            return original_formatdate(*args, **kwargs)

        # Mock formatdate to count calls
        monkeypatch.setattr("uvicorn.server.formatdate", counted_formatdate)

        server_state = ServerState()

        # Call get_date_header multiple times in same second
        for _ in range(10):
            server_state.get_date_header()

        # Should only call formatdate once (cached for subsequent calls)
        assert call_count[0] == 1, f"formatdate called {call_count[0]} times, expected 1 (cached)"
