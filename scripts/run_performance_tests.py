#!/usr/bin/env python3
"""
Comprehensive Performance Test Runner for Uvicorn Fork

This script orchestrates all performance benchmarks and generates a detailed
performance report for the uvicorn fork.

Usage:
    python scripts/run_performance_tests.py [--quick] [--output FILENAME]

Options:
    --quick         Run a quick performance test (fewer iterations)
    --output FILE   Save detailed report to specified file
    --port PORT     Port for test server (default: 8765)
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Constants
QUICK_TEST_CONFIGS = [(500, 1), (1000, 10), (2000, 50)]
FULL_TEST_CONFIGS = [(1000, 1), (5000, 10), (10000, 50), (10000, 100)]
DEFAULT_TEST_APP = "tests.benchmarks.test_app:app"
MAX_OUTPUT_PREVIEW_CHARS = 500


def print_header(text: str, char: str = "=") -> None:
    """Print a formatted header."""
    width = 80
    print(f"\n{char * width}")
    print(f"{text:^{width}}")
    print(f"{char * width}\n")


def print_section(text: str) -> None:
    """Print a section header."""
    print(f"\n{'─' * 80}")
    print(f"  {text}")
    print(f"{'─' * 80}")


class PerformanceTestRunner:
    """Orchestrates performance testing."""

    def __init__(self, port: int = 8765, quick: bool = False):
        self.port = port
        self.quick = quick
        self.results: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "python_version": sys.version,
            "quick_mode": quick,
            "tests": {},
        }
        self.server_process = None

    def start_server(self) -> bool:
        """Start the uvicorn test server."""
        print_section("Starting Test Server")
        print(f"Starting uvicorn on port {self.port}...")

        # Start server in background
        self.server_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                DEFAULT_TEST_APP,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=project_root,
        )

        # Wait for server to be ready
        if httpx is None:
            print("❌ httpx is required. Install it with: pip install httpx")
            return False

        for i in range(30):
            try:
                response = httpx.get(f"http://127.0.0.1:{self.port}/", timeout=1.0)
                if response.status_code == 200:
                    print(f"✅ Server is ready on port {self.port}")
                    time.sleep(0.5)  # Give it a moment to stabilize
                    return True
            except Exception:
                if i == 29:
                    print("❌ Server failed to start")
                    return False
                time.sleep(0.5)

        return False

    def stop_server(self) -> None:
        """Stop the test server."""
        if self.server_process:
            print_section("Stopping Test Server")
            print("Stopping uvicorn server...")
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
                print("✅ Server stopped")
            except subprocess.TimeoutExpired:
                print("⚠️  Force killing server...")
                self.server_process.kill()
                self.server_process.wait()

    def run_simple_benchmark(self) -> dict[str, Any]:
        """Run the simple benchmark test."""
        print_section("Simple Benchmark Test")

        # Import and run the simple benchmark
        sys.path.insert(0, str(project_root / "tests" / "benchmarks"))

        try:
            # We'll run a modified version inline
            if httpx is None:
                print("❌ httpx is required")
                return {}

            async def run_quick_bench():
                url = f"http://127.0.0.1:{self.port}/"
                results = {}

                # Test configurations
                configs = QUICK_TEST_CONFIGS if self.quick else FULL_TEST_CONFIGS

                for num_requests, concurrency in configs:
                    print(f"\n  Testing: {num_requests:,} requests @ concurrency {concurrency}")

                    latencies = []
                    semaphore = asyncio.Semaphore(concurrency)

                    async def make_request(client):
                        async with semaphore:
                            start = time.perf_counter()
                            try:
                                response = await client.get(url)
                                response.raise_for_status()
                                latencies.append(time.perf_counter() - start)
                            except Exception:
                                pass

                    # Warmup
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        warmup = [make_request(client) for _ in range(min(50, num_requests))]
                        await asyncio.gather(*warmup)

                    latencies.clear()

                    # Actual benchmark
                    start_time = time.perf_counter()
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        tasks = [make_request(client) for _ in range(num_requests)]
                        await asyncio.gather(*tasks)
                    duration = time.perf_counter() - start_time

                    if latencies:
                        sorted_lat = sorted(latencies)
                        p50 = sorted_lat[int(len(sorted_lat) * 0.50)] * 1000
                        p99 = sorted_lat[int(len(sorted_lat) * 0.99)] * 1000
                        rps = len(latencies) / duration

                        results[f"c{concurrency}"] = {
                            "requests": num_requests,
                            "concurrency": concurrency,
                            "rps": round(rps, 2),
                            "p50_ms": round(p50, 2),
                            "p99_ms": round(p99, 2),
                            "duration_sec": round(duration, 2),
                        }

                        print(
                            f"    RPS: {rps:>8.0f} | p50: {p50:>6.2f}ms | p99: {p99:>6.2f}ms"
                        )

                return results

            results = asyncio.run(run_quick_bench())
            print("\n✅ Simple benchmark completed")
            return results

        except Exception as e:
            print(f"❌ Error running simple benchmark: {e}")
            return {}

    def run_memory_profile(self) -> dict[str, Any]:
        """Run memory profiling test."""
        print_section("Memory Profiling Test")

        try:
            result = subprocess.run(
                [sys.executable, "tests/benchmarks/profile_memory.py"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                print("✅ Memory profiling completed")
                # Parse output for key metrics
                output = result.stdout
                metrics = {}

                # Look for key optimization metrics
                for line in output.split("\n"):
                    if "__slots__" in line.lower() and "reduction" in line.lower():
                        metrics["slots_optimization"] = line.strip()
                    elif "event" in line.lower() and "pool" in line.lower():
                        metrics["event_pooling"] = line.strip()

                # Print preview of output (first N chars)
                if output:
                    preview_length = min(len(output), MAX_OUTPUT_PREVIEW_CHARS)
                    print(f"    {output[:preview_length]}...")
                return metrics
            else:
                print(f"⚠️  Memory profiling returned error: {result.returncode}")
                return {}

        except Exception as e:
            print(f"⚠️  Could not run memory profiling: {e}")
            return {}

    def generate_report(self, output_file: str | None = None) -> None:
        """Generate a comprehensive performance report."""
        print_header("Performance Test Report", "=")

        report_lines = []
        report_lines.append("UVICORN FORK PERFORMANCE TEST REPORT")
        report_lines.append("=" * 80)
        report_lines.append(f"\nTimestamp: {self.results['timestamp']}")
        report_lines.append(f"Python Version: {self.results['python_version'][:50]}")
        report_lines.append(f"Test Mode: {'Quick' if self.quick else 'Full'}")
        report_lines.append(f"Server Port: {self.port}")

        # Simple benchmark results
        if "simple_benchmark" in self.results["tests"]:
            report_lines.append("\n" + "=" * 80)
            report_lines.append("THROUGHPUT AND LATENCY BENCHMARKS")
            report_lines.append("=" * 80)

            simple_results = self.results["tests"]["simple_benchmark"]
            report_lines.append(
                "\nConcurrency | Requests | RPS      | p50 Latency | p99 Latency"
            )
            report_lines.append("-" * 80)

            for key in sorted(simple_results.keys()):
                result = simple_results[key]
                report_lines.append(
                    f"{result['concurrency']:>11} | "
                    f"{result['requests']:>8,} | "
                    f"{result['rps']:>8.0f} | "
                    f"{result['p50_ms']:>11.2f}ms | "
                    f"{result['p99_ms']:>11.2f}ms"
                )

        # Memory profiling results
        if "memory_profile" in self.results["tests"]:
            report_lines.append("\n" + "=" * 80)
            report_lines.append("MEMORY OPTIMIZATION METRICS")
            report_lines.append("=" * 80)

            for key, value in self.results["tests"]["memory_profile"].items():
                report_lines.append(f"\n  {key}: {value}")

        # Summary
        report_lines.append("\n" + "=" * 80)
        report_lines.append("SUMMARY")
        report_lines.append("=" * 80)

        if "simple_benchmark" in self.results["tests"]:
            simple_results = self.results["tests"]["simple_benchmark"]
            if simple_results:
                # Get highest concurrency result
                highest_c = max(
                    simple_results.values(), key=lambda x: x["concurrency"]
                )
                report_lines.append(
                    f"\n  Maximum Stable Concurrency: {highest_c['concurrency']}"
                )
                report_lines.append(
                    f"  Peak Throughput: {highest_c['rps']:,.0f} req/s"
                )

                # Get best latency (usually at low concurrency)
                lowest_c = min(
                    simple_results.values(), key=lambda x: x["concurrency"]
                )
                report_lines.append(
                    f"  Best p50 Latency: {lowest_c['p50_ms']:.2f}ms (concurrency={lowest_c['concurrency']})"
                )

        report_lines.append("\n" + "=" * 80)
        report_lines.append("KEY FEATURES OF THIS FORK")
        report_lines.append("=" * 80)
        report_lines.append("\n  ✅ Event object pooling for reduced allocations")
        report_lines.append("  ✅ __slots__ optimization for memory efficiency")
        report_lines.append("  ✅ Conditional app_state copying")
        report_lines.append("  ✅ Event-driven main loop")
        report_lines.append("  ✅ Date header caching")
        report_lines.append("\n" + "=" * 80 + "\n")

        # Print to console
        report_text = "\n".join(report_lines)
        print(report_text)

        # Save to file if requested
        if output_file:
            output_path = Path(output_file)
            output_path.write_text(report_text)
            print(f"\n📄 Report saved to: {output_file}")

            # Also save JSON version
            json_file = output_path.with_suffix(".json")
            with open(json_file, "w") as f:
                json.dump(self.results, f, indent=2)
            print(f"📄 JSON data saved to: {json_file}")

    def run(self, output_file: str | None = None) -> bool:
        """Run all performance tests."""
        print_header("Uvicorn Fork Performance Testing Suite")

        try:
            # Start server
            if not self.start_server():
                print("❌ Failed to start test server")
                return False

            # Run benchmarks
            print_section("Running Performance Benchmarks")

            self.results["tests"]["simple_benchmark"] = self.run_simple_benchmark()

            # Stop server before memory profiling
            self.stop_server()

            # Run memory profiling (standalone)
            self.results["tests"]["memory_profile"] = self.run_memory_profile()

            # Generate report
            self.generate_report(output_file)

            print_header("✅ Performance Testing Complete!", "=")
            return True

        except KeyboardInterrupt:
            print("\n\n⚠️  Testing interrupted by user")
            return False

        except Exception as e:
            print(f"\n❌ Error during testing: {e}")
            import traceback

            traceback.print_exc()
            return False

        finally:
            # Ensure server is stopped
            if self.server_process and self.server_process.poll() is None:
                self.stop_server()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run comprehensive performance tests for uvicorn fork"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run quick tests (fewer iterations, faster completion)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Save detailed report to file (e.g., performance_report.txt)",
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="Port for test server (default: 8765)"
    )

    args = parser.parse_args()

    # Check dependencies
    try:
        import httpx
    except ImportError:
        print("❌ Error: httpx is required for performance testing")
        print("   Install it with: pip install httpx")
        sys.exit(1)

    # Run tests
    runner = PerformanceTestRunner(port=args.port, quick=args.quick)
    success = runner.run(output_file=args.output)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
