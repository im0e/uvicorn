#!/usr/bin/env python3
"""
Simple Uvicorn Performance Benchmark
Runs a quick performance test to establish baseline metrics.

Usage:
    Terminal 1: uvicorn test_app:app --host 127.0.0.1 --port 8765
    Terminal 2: python simple_bench.py
"""

import asyncio
import statistics
import sys
import time

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)


async def benchmark_simple(url: str, num_requests: int, concurrency: int):
    """Run a simple benchmark."""
    print(f"\n{'=' * 70}")
    print(f"Benchmark: {num_requests:,} requests, concurrency={concurrency}")
    print(f"{'=' * 70}")

    latencies = []
    errors = 0
    semaphore = asyncio.Semaphore(concurrency)

    async def make_request(client):
        async with semaphore:
            start = time.perf_counter()
            try:
                response = await client.get(url)
                response.raise_for_status()
                latency = time.perf_counter() - start
                latencies.append(latency)
            except Exception as e:
                errors += 1

    # Warmup
    print("Warming up...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        warmup_tasks = [make_request(client) for _ in range(min(100, num_requests))]
        await asyncio.gather(*warmup_tasks)

    # Clear warmup
    latencies.clear()
    errors = 0

    # Actual benchmark
    print("Running benchmark...")
    start_time = time.perf_counter()

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [make_request(client) for _ in range(num_requests)]
        await asyncio.gather(*tasks)

    end_time = time.perf_counter()
    duration = end_time - start_time

    # Results
    successful = len(latencies)
    print(f"\nResults:")
    print(f"  Total requests:    {num_requests:,}")
    print(f"  Successful:        {successful:,}")
    print(f"  Failed:            {errors:,}")
    print(f"  Duration:          {duration:.3f}s")
    print(f"  Requests/sec:      {successful / duration:.2f}")

    if latencies:
        sorted_lat = sorted(latencies)
        p50 = sorted_lat[int(len(sorted_lat) * 0.50)]
        p90 = sorted_lat[int(len(sorted_lat) * 0.90)]
        p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
        p99 = sorted_lat[int(len(sorted_lat) * 0.99)]

        print(f"\n  Latency (ms):")
        print(f"    Min:             {min(latencies) * 1000:.2f}")
        print(f"    Mean:            {statistics.mean(latencies) * 1000:.2f}")
        print(f"    Max:             {max(latencies) * 1000:.2f}")
        print(f"    p50:             {p50 * 1000:.2f}")
        print(f"    p90:             {p90 * 1000:.2f}")
        print(f"    p95:             {p95 * 1000:.2f}")
        print(f"    p99:             {p99 * 1000:.2f}")

    print(f"{'=' * 70}\n")

    return {
        "requests": num_requests,
        "successful": successful,
        "errors": errors,
        "duration": duration,
        "rps": successful / duration,
        "p50": p50 * 1000 if latencies else 0,
        "p99": p99 * 1000 if latencies else 0,
    }


async def main():
    """Run benchmarks."""
    url = "http://127.0.0.1:8765/"

    print("=" * 70)
    print("UVICORN PERFORMANCE BASELINE TEST")
    print("=" * 70)
    print(f"Target: {url}")
    print(f"Python: {sys.version}")

    # Wait for server
    print("\nWaiting for server...")
    for i in range(30):
        try:
            async with httpx.AsyncClient() as client:
                await client.get(url, timeout=1.0)
            print("Server is ready!\n")
            break
        except Exception:
            if i == 29:
                print("ERROR: Server not responding")
                print("Please start server: uvicorn test_app:app --port 8765")
                sys.exit(1)
            await asyncio.sleep(0.5)

    results = []

    # Run benchmarks with different configurations
    configs = [
        (1000, 1),  # Sequential
        (5000, 10),  # Low concurrency
        (10000, 50),  # Medium concurrency
        (10000, 100),  # High concurrency
    ]

    for num_req, concurrency in configs:
        result = await benchmark_simple(url, num_req, concurrency)
        results.append(result)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for i, (num_req, concurrency) in enumerate(configs):
        r = results[i]
        print(
            f"Concurrency {concurrency:3d}: {r['rps']:>8.0f} req/s | p50: {r['p50']:>6.2f}ms | p99: {r['p99']:>6.2f}ms"
        )
    print("=" * 70)

    # Save results
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"baseline_results_{timestamp}.txt"
    with open(filename, "w") as f:
        f.write("UVICORN BASELINE PERFORMANCE TEST\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Python: {sys.version}\n\n")
        for i, (num_req, concurrency) in enumerate(configs):
            r = results[i]
            f.write(f"\nConcurrency {concurrency}:\n")
            f.write(f"  Requests: {r['requests']}\n")
            f.write(f"  RPS:      {r['rps']:.2f}\n")
            f.write(f"  p50:      {r['p50']:.2f}ms\n")
            f.write(f"  p99:      {r['p99']:.2f}ms\n")

    print(f"\nResults saved to: {filename}")


if __name__ == "__main__":
    asyncio.run(main())
