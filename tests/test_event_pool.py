"""
Tests for event pool optimization in ServerState.

These tests verify that the event pool correctly acquires and releases
asyncio.Event objects, reducing memory allocations under load.
"""

import asyncio

import pytest

from uvicorn.server import ServerState


class TestEventPool:
    """Test suite for event pool functionality."""

    def test_acquire_event_creates_new_when_pool_empty(self):
        """Test that acquire_event creates a new Event when pool is empty."""
        state = ServerState()
        event = state.acquire_event()

        assert isinstance(event, asyncio.Event)
        assert not event.is_set()

    def test_release_event_adds_to_pool(self):
        """Test that release_event adds Event back to pool."""
        state = ServerState()
        event = state.acquire_event()

        # Release the event
        state.release_event(event)

        # Pool should have one event
        assert len(state._event_pool) == 1
        assert state._event_pool[0] is event

    def test_acquire_reuses_pooled_event(self):
        """Test that acquire_event reuses events from pool."""
        state = ServerState()

        # Create and release an event
        event1 = state.acquire_event()
        event1.set()  # Set it to verify it gets cleared
        state.release_event(event1)

        # Acquire again - should get the same event, but cleared
        event2 = state.acquire_event()

        assert event2 is event1
        assert not event2.is_set()  # Should be cleared

    def test_pool_respects_max_size(self):
        """Test that pool doesn't grow beyond max_size."""
        state = ServerState()
        state._event_pool_max_size = 5  # Set small limit for testing

        # Release more events than the max size
        events = [state.acquire_event() for _ in range(10)]
        for event in events:
            state.release_event(event)

        # Pool should not exceed max size
        assert len(state._event_pool) == 5

    def test_event_cleared_on_release(self):
        """Test that events are cleared when released to pool."""
        state = ServerState()

        event = state.acquire_event()
        event.set()
        assert event.is_set()

        state.release_event(event)

        # Event should be cleared in the pool
        assert not state._event_pool[0].is_set()

    def test_multiple_acquire_release_cycles(self):
        """Test multiple acquire/release cycles."""
        state = ServerState()

        # First cycle
        event1 = state.acquire_event()
        state.release_event(event1)

        # Second cycle - should reuse
        event2 = state.acquire_event()
        assert event2 is event1
        state.release_event(event2)

        # Third cycle - should reuse again
        event3 = state.acquire_event()
        assert event3 is event1

    def test_concurrent_acquire_from_pool(self):
        """Test that concurrent acquires don't cause issues."""
        state = ServerState()

        # Pre-populate pool
        for _ in range(5):
            event = state.acquire_event()
            state.release_event(event)

        # Acquire multiple at once
        events = [state.acquire_event() for _ in range(10)]

        # Should have created some new ones when pool ran out
        assert len(events) == 10
        assert all(isinstance(e, asyncio.Event) for e in events)
        assert all(not e.is_set() for e in events)

    def test_pool_with_zero_max_size(self):
        """Test that pool with max_size=0 still works (no pooling)."""
        state = ServerState()
        state._event_pool_max_size = 0

        event1 = state.acquire_event()
        state.release_event(event1)

        # Pool should remain empty
        assert len(state._event_pool) == 0

        # New acquire should create new event
        event2 = state.acquire_event()
        assert event2 is not event1

    def test_server_state_default_pool_size(self):
        """Test that ServerState initializes with correct default pool size."""
        state = ServerState()

        assert state._event_pool_max_size == 1000
        assert len(state._event_pool) == 0
        assert isinstance(state._event_pool, list)

    def test_event_pool_with_async_operations(self):
        """Test event pool with actual async operations."""
        state = ServerState()

        async def run_test():
            # Acquire event
            event = state.acquire_event()

            # Use it in async operation
            async def wait_for_event():
                await event.wait()
                return "done"

            task = asyncio.create_task(wait_for_event())

            # Set the event
            event.set()
            result = await task

            assert result == "done"
            assert event.is_set()

            # Release back to pool
            state.release_event(event)

            # Acquire again - should be cleared
            event2 = state.acquire_event()
            assert event2 is event
            assert not event2.is_set()

        asyncio.run(run_test())

    def test_event_pool_isolation(self):
        """Test that different ServerState instances have separate pools."""
        state1 = ServerState()
        state2 = ServerState()

        event1 = state1.acquire_event()
        event2 = state2.acquire_event()

        state1.release_event(event1)
        state2.release_event(event2)

        # Pools should be separate
        assert state1._event_pool[0] is not state2._event_pool[0]

    def test_stress_test_pool(self):
        """Stress test the event pool with many operations."""
        state = ServerState()

        # Simulate many concurrent requests (acquire multiple at once)
        events = []
        for _ in range(100):
            event = state.acquire_event()
            events.append(event)

        # Now release them all
        for event in events:
            event.set()  # Simulate usage
            state.release_event(event)

        # Pool should contain up to max size
        assert len(state._event_pool) == min(100, state._event_pool_max_size)

        # All events in pool should be cleared
        assert all(not e.is_set() for e in state._event_pool)

        # Verify reuse works after stress test
        reused_event = state.acquire_event()
        assert reused_event in events  # Should be one we released
        assert not reused_event.is_set()  # Should be cleared
