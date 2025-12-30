#!/usr/bin/env python3
"""
Uvicorn Performance Benchmark Script

This script tests various aspects of uvicorn's performance to establish
a baseline before implementing optimizations.

Usage:
    python benchmark_uvicorn.py [--host HOST] [--port PORT]

    Or run server separately:
    Terminal 1: uvicorn benchmark_uvicorn:simple_app --host 127.0.0.1 --port 8000
    Terminal 2: python benchmark_uvicorn.py --no-server
"""

import argparse
import asyncio
import statistics
import sys
import time
from typing import Any

import httpx


# Simple ASGI application for testing
async def simple_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI app that returns a simple response."""
    assert scope["type"] == "http"

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", b"13"),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"Hello, World!",
        }
    )


async def json_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """ASGI app that returns JSON response."""
    assert scope["type"] == "http"

    import json

    data = {"message": "Hello, World!", "status": "ok", "timestamp": time.time()}
    body = json.dumps(data).encode()

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )


async def echo_body_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """ASGI app that echoes the request body."""
    assert scope["type"] == "http"

    body = b""
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/octet-stream"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )


class BenchmarkResults:
    """Store and display benchmark results."""

    def __init__(self, name: str):
        self.name = name
        self.latencies: list[float] = []
        self.errors = 0
        self.start_time: float = 0
        self.end_time: float = 0

    def add_latency(self, latency: float) -> None:
        self.latencies.append(latency)

    def add_error(self) -> None:
        self.errors += 1

    def set_times(self, start: float, end: float) -> None:
        self.start_time = start
        self.end_time = end

    def print_results(self) -> None:
        """Print benchmark results."""
        total_time = self.end_time - self.start_time
        total_requests = len(self.latencies) + self.errors
        successful = len(self.latencies)

        print(f"\n{'=' * 70}")
        print(f"Benchmark: {self.name}")
        print(f"{'=' * 70}")
        print(f"Total Requests:      {total_requests:>10,}")
        print(f"Successful:          {successful:>10,}")
        print(f"Failed:              {self.errors:>10,}")
        print(f"Total Time:          {total_time:>10.2f}s")
        print(f"Requests/sec:        {successful / total_time:>10.2f}")

        if self.latencies:
            print(f"\nLatency Statistics (ms):")
            print(f"  Min:               {min(self.latencies) * 1000:>10.2f}")
            print(f"  Max:               {max(self.latencies) * 1000:>10.2f}")
            print(f"  Mean:              {statistics.mean(self.latencies) * 1000:>10.2f}")
            print(f"  Median:            {statistics.median(self.latencies) * 1000:>10.2f}")
            if len(self.latencies) > 1:
                print(f"  Std Dev:           {statistics.stdev(self.latencies) * 1000:>10.2f}")

            sorted_latencies = sorted(self.latencies)
            p50 = sorted_latencies[int(len(sorted_latencies) * 0.50)]
            p90 = sorted_latencies[int(len(sorted_latencies) * 0.90)]
            p95 = sorted_latencies[int(len(sorted_latencies) * 0.95)]
            p99 = sorted_latencies[int(len(sorted_latencies) * 0.99)]

            print(f"\nPercentiles (ms):")
            print(f"  50th (p50):        {p50 * 1000:>10.2f}")
            print(f"  90th (p90):        {p90 * 1000:>10.2f}")
            print(f"  95th (p95):        {p95 * 1000:>10.2f}")
            print(f"  99th (p99):        {p99 * 1000:>10.2f}")
        print(f"{'=' * 70}\n")


async def benchmark_requests(
    url: str,
    num_requests: int,
    concurrency: int,
    method: str = "GET",
    body: bytes | None = None,
) -> BenchmarkResults:
    """Benchmark HTTP requests with specified concurrency."""
    results = BenchmarkResults(f"{method} {url} (concurrency={concurrency})")
    semaphore = asyncio.Semaphore(concurrency)

    async def make_request(client: httpx.AsyncClient) -> None:
        async with semaphore:
            start = time.perf_counter()
            try:
                if method == "GET":
                    response = await client.get(url)
                elif method == "POST":
                    response = await client.post(url, content=body)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                response.raise_for_status()
                latency = time.perf_counter() - start
                results.add_latency(latency)
            except Exception as e:
                results.add_error()
                # Uncomment for debugging
                # print(f"Error: {e}", file=sys.stderr)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Warmup
        print(f"Warming up with {min(100, num_requests)} requests...")
        warmup_tasks = [make_request(client) for _ in range(min(100, num_requests))]
        await asyncio.gather(*warmup_tasks)

        # Clear warmup results
        results.latencies.clear()
        results.errors = 0

        # Actual benchmark
        print(f"Running benchmark: {num_requests} requests with concurrency {concurrency}...")
        start_time = time.perf_counter()
        tasks = [make_request(client) for _ in range(num_requests)]
        await asyncio.gather(*tasks)
        end_time = time.perf_counter()

        results.set_times(start_time, end_time)

    return results


async def run_benchmarks(host: str, port: int) -> None:
    """Run all benchmark scenarios."""
    base_url = f"http://{host}:{port}"

    print("\n" + "=" * 70)
    print("UVICORN PERFORMANCE BENCHMARK")
    print("=" * 70)
    print(f"Target: {base_url}")
    print(f"Python: {sys.version}")
    print("=" * 70)

    # Wait for server to be ready
    print("\nWaiting for server to be ready...")
    max_retries = 30
    for i in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                await client.get(base_url)
            print("Server is ready!")
            break
        except Exception:
            if i == max_retries - 1:
                print("ERROR: Server failed to start", file=sys.stderr)
                sys.exit(1)
            await asyncio.sleep(0.5)

    all_results = []

    # Benchmark 1: Simple GET requests with varying concurrency
    for concurrency in [1, 10, 50, 100]:
        results = await benchmark_requests(
            base_url,
            num_requests=10000,
            concurrency=concurrency,
            method="GET",
        )
        all_results.append(results)
        results.print_results()

    # Benchmark 2: POST requests with body
    post_body = b"x" * 1024  # 1KB
    for concurrency in [10, 50]:
        results = await benchmark_requests(
            base_url,
            num_requests=5000,
            concurrency=concurrency,
            method="POST",
            body=post_body,
        )
        all_results.append(results)
        results.print_results()

    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for result in all_results:
        if result.latencies:
            rps = len(result.latencies) / (result.end_time - result.start_time)
            p99 = sorted(result.latencies)[int(len(result.latencies) * 0.99)] * 1000
            print(f"{result.name:60s} | RPS: {rps:>8.0f} | p99: {p99:>6.2f}ms")
    print("=" * 70)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Benchmark uvicorn performance")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    parser.add_argument("--no-server", action="store_true", help="Don't start server, assume it's running")

    args = parser.parse_args()

    if args.no_server:
        # Just run benchmarks against existing server
        asyncio.run(run_benchmarks(args.host, args.port))
    else:
        print("ERROR: Please run the server manually in another terminal:")
        print(f"  uvicorn benchmark_uvicorn:simple_app --host {args.host} --port {args.port}")
        print("\nThen run this benchmark with:")
        print(f"  python benchmark_uvicorn.py --no-server --host {args.host} --port {args.port}")
        sys.exit(1)


if __name__ == "__main__":
    main()
