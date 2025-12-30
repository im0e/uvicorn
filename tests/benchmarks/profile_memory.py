#!/usr/bin/env python3
"""
Uvicorn Memory Profiling Script

This script profiles memory usage of uvicorn under load to identify
memory bottlenecks and opportunities for optimization.

Usage:
    pip install memory_profiler psutil
    python profile_memory.py
"""

import gc
import os
import sys
import time
import tracemalloc
from typing import Any

try:
    import psutil
except ImportError:
    print("ERROR: psutil not installed. Run: pip install psutil", file=sys.stderr)
    sys.exit(1)


# Simple ASGI application for testing
async def simple_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI app for memory testing."""
    assert scope["type"] == "http"

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"Hello, World!",
        }
    )


def get_memory_info():
    """Get current memory usage information."""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return {
        "rss_mb": mem_info.rss / 1024 / 1024,  # Resident Set Size
        "vms_mb": mem_info.vms / 1024 / 1024,  # Virtual Memory Size
        "percent": process.memory_percent(),
    }


def format_size(size_bytes):
    """Format bytes to human readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def profile_object_creation():
    """Profile object creation in hot paths."""
    import asyncio

    print("\n" + "=" * 70)
    print("OBJECT CREATION PROFILING")
    print("=" * 70)

    # Test Event object creation overhead
    print("\nTesting asyncio.Event() creation overhead...")
    start = time.perf_counter()
    events = [asyncio.Event() for _ in range(10000)]
    duration = time.perf_counter() - start
    print(f"  Created 10,000 Event objects in {duration:.3f}s")
    print(f"  Rate: {10000 / duration:.0f} events/sec")

    mem_before = get_memory_info()
    events.clear()
    gc.collect()
    mem_after = get_memory_info()
    print(f"  Memory freed: {mem_before['rss_mb'] - mem_after['rss_mb']:.2f} MB")

    # Test dictionary copy overhead
    print("\nTesting dict.copy() overhead...")
    test_dict = {"key1": "value1", "key2": "value2", "key3": {"nested": "dict"}}
    start = time.perf_counter()
    copies = [test_dict.copy() for _ in range(100000)]
    duration = time.perf_counter() - start
    print(f"  Copied dict 100,000 times in {duration:.3f}s")
    print(f"  Rate: {100000 / duration:.0f} copies/sec")

    mem_before = get_memory_info()
    copies.clear()
    gc.collect()
    mem_after = get_memory_info()
    print(f"  Memory freed: {mem_before['rss_mb'] - mem_after['rss_mb']:.2f} MB")


def profile_tracemalloc():
    """Profile memory allocations using tracemalloc."""
    print("\n" + "=" * 70)
    print("MEMORY ALLOCATION PROFILING (tracemalloc)")
    print("=" * 70)

    tracemalloc.start()

    # Simulate request processing
    import asyncio

    async def simulate_requests(num_requests):
        """Simulate processing requests."""
        from uvicorn.protocols.http.flow_control import FlowControl

        # Simulate creating objects as done per request
        events = []
        states = []

        for _ in range(num_requests):
            # Create event per request (current implementation)
            event = asyncio.Event()
            events.append(event)

            # Copy state dict per request (current implementation)
            app_state = {"key1": "value1", "key2": "value2"}
            state_copy = app_state.copy()
            states.append(state_copy)

        return events, states

    print("\nSimulating 1,000 request object creations...")
    snapshot_before = tracemalloc.take_snapshot()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    events, states = loop.run_until_complete(simulate_requests(1000))

    snapshot_after = tracemalloc.take_snapshot()

    top_stats = snapshot_after.compare_to(snapshot_before, "lineno")

    print("\nTop 10 memory allocations:")
    for stat in top_stats[:10]:
        print(f"  {stat}")

    current, peak = tracemalloc.get_traced_memory()
    print(f"\nCurrent memory usage: {format_size(current)}")
    print(f"Peak memory usage: {format_size(peak)}")

    tracemalloc.stop()


def profile_slots_impact():
    """Compare memory usage with and without __slots__."""
    print("\n" + "=" * 70)
    print("__slots__ MEMORY IMPACT ANALYSIS")
    print("=" * 70)

    # Class without slots
    class WithoutSlots:
        def __init__(self):
            self.scope = {}
            self.transport = None
            self.flow = None
            self.logger = None
            self.access_logger = None
            self.access_log = False
            self.default_headers = []
            self.message_event = None
            self.on_response = None
            self.disconnected = False
            self.keep_alive = True
            self.waiting_for_100_continue = False
            self.body = b""
            self.more_body = True
            self.response_started = False
            self.response_complete = False
            self.chunked_encoding = None
            self.expected_content_length = 0

    # Class with slots
    class WithSlots:
        __slots__ = (
            "scope",
            "transport",
            "flow",
            "logger",
            "access_logger",
            "access_log",
            "default_headers",
            "message_event",
            "on_response",
            "disconnected",
            "keep_alive",
            "waiting_for_100_continue",
            "body",
            "more_body",
            "response_started",
            "response_complete",
            "chunked_encoding",
            "expected_content_length",
        )

        def __init__(self):
            self.scope = {}
            self.transport = None
            self.flow = None
            self.logger = None
            self.access_logger = None
            self.access_log = False
            self.default_headers = []
            self.message_event = None
            self.on_response = None
            self.disconnected = False
            self.keep_alive = True
            self.waiting_for_100_continue = False
            self.body = b""
            self.more_body = True
            self.response_started = False
            self.response_complete = False
            self.chunked_encoding = None
            self.expected_content_length = 0

    num_objects = 10000

    # Test without slots
    print(f"\nCreating {num_objects:,} objects WITHOUT __slots__...")
    gc.collect()
    mem_before = get_memory_info()

    objects_without = [WithoutSlots() for _ in range(num_objects)]

    mem_after = get_memory_info()
    mem_used_without = mem_after["rss_mb"] - mem_before["rss_mb"]
    print(f"  Memory used: {mem_used_without:.2f} MB")
    print(f"  Per object: {(mem_used_without * 1024 * 1024) / num_objects:.2f} bytes")

    # Test with slots
    print(f"\nCreating {num_objects:,} objects WITH __slots__...")
    del objects_without
    gc.collect()
    mem_before = get_memory_info()

    objects_with = [WithSlots() for _ in range(num_objects)]

    mem_after = get_memory_info()
    mem_used_with = mem_after["rss_mb"] - mem_before["rss_mb"]
    print(f"  Memory used: {mem_used_with:.2f} MB")
    print(f"  Per object: {(mem_used_with * 1024 * 1024) / num_objects:.2f} bytes")

    # Calculate savings
    savings = mem_used_without - mem_used_with
    savings_percent = (savings / mem_used_without) * 100 if mem_used_without > 0 else 0

    print(f"\nMemory savings with __slots__:")
    print(f"  Total: {savings:.2f} MB ({savings_percent:.1f}%)")
    print(f"  Per object: {(savings * 1024 * 1024) / num_objects:.2f} bytes")


def profile_string_building():
    """Profile different string/bytes building approaches."""
    print("\n" + "=" * 70)
    print("STRING/BYTES BUILDING PERFORMANCE")
    print("=" * 70)

    # Prepare test data (simulating HTTP response headers)
    headers = [
        (b"content-type", b"text/plain"),
        (b"content-length", b"13"),
        (b"server", b"uvicorn"),
        (b"date", b"Mon, 01 Jan 2024 00:00:00 GMT"),
    ]

    iterations = 100000

    # Method 1: Using list + join (current)
    print(f"\nMethod 1: list.append + b''.join ({iterations:,} iterations)...")
    start = time.perf_counter()
    for _ in range(iterations):
        content = []
        content.append(b"HTTP/1.1 200 OK\r\n")
        for name, value in headers:
            content.extend([name, b": ", value, b"\r\n"])
        content.append(b"\r\n")
        result = b"".join(content)
    duration1 = time.perf_counter() - start
    print(f"  Time: {duration1:.3f}s ({iterations / duration1:.0f} ops/sec)")

    # Method 2: Using bytearray
    print(f"\nMethod 2: bytearray ({iterations:,} iterations)...")
    start = time.perf_counter()
    for _ in range(iterations):
        content = bytearray(b"HTTP/1.1 200 OK\r\n")
        for name, value in headers:
            content.extend(name)
            content.extend(b": ")
            content.extend(value)
            content.extend(b"\r\n")
        content.extend(b"\r\n")
        result = bytes(content)
    duration2 = time.perf_counter() - start
    print(f"  Time: {duration2:.3f}s ({iterations / duration2:.0f} ops/sec)")

    # Method 3: Pre-allocated bytearray
    print(f"\nMethod 3: Pre-allocated bytearray ({iterations:,} iterations)...")
    start = time.perf_counter()
    for _ in range(iterations):
        content = bytearray(256)  # Pre-allocate
        content[:17] = b"HTTP/1.1 200 OK\r\n"
        pos = 17
        for name, value in headers:
            content[pos : pos + len(name)] = name
            pos += len(name)
            content[pos : pos + 2] = b": "
            pos += 2
            content[pos : pos + len(value)] = value
            pos += len(value)
            content[pos : pos + 2] = b"\r\n"
            pos += 2
        content[pos : pos + 2] = b"\r\n"
        result = bytes(content[: pos + 2])
    duration3 = time.perf_counter() - start
    print(f"  Time: {duration3:.3f}s ({iterations / duration3:.0f} ops/sec)")

    print(f"\nPerformance comparison:")
    print(f"  Method 1 (current): baseline")
    print(f"  Method 2: {(duration1 / duration2 - 1) * 100:+.1f}%")
    print(f"  Method 3: {(duration1 / duration3 - 1) * 100:+.1f}%")


def main():
    """Run all memory profiling tests."""
    print("=" * 70)
    print("UVICORN MEMORY PROFILING")
    print("=" * 70)
    print(f"Python: {sys.version}")

    initial_mem = get_memory_info()
    print(f"\nInitial memory usage:")
    print(f"  RSS: {initial_mem['rss_mb']:.2f} MB")
    print(f"  VMS: {initial_mem['vms_mb']:.2f} MB")

    # Run profiling tests
    profile_object_creation()
    profile_slots_impact()
    profile_string_building()
    profile_tracemalloc()

    final_mem = get_memory_info()
    print("\n" + "=" * 70)
    print("FINAL MEMORY USAGE")
    print("=" * 70)
    print(f"RSS: {final_mem['rss_mb']:.2f} MB")
    print(f"VMS: {final_mem['vms_mb']:.2f} MB")
    print(f"Memory increase: {final_mem['rss_mb'] - initial_mem['rss_mb']:.2f} MB")
    print("=" * 70)


if __name__ == "__main__":
    main()
